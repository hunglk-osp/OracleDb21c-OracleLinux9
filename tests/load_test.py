"""
load_test.py — Oracle Data Guard Load Test (with Client Failover)
Kịch bản:
  1. Insert liên tục vào Primary trong DURATION_SEC giây
  2. Nếu Primary chết → tự chuyển sang host còn lại (client failover)
  3. Theo dõi downtime, failover events
  4. Sau khi xong: so sánh số bản ghi trên cả 2 host

Chạy: uv run load_test.py
Trong lúc chạy: tắt Primary hoặc Standby để test failover
"""

import oracledb
import time
import threading
import random
import string

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich import box

# ── Cấu hình ──────────────────────────────────────────────
HOSTS         = ["192.168.1.195", "192.168.1.196"]  # Danh sách host, thử lần lượt
PORT          = 1521
SERVICE       = "orclpdb1"
USER          = "osp"
PASSWORD      = "Osp@123"
SYS_PASS      = "Oracle_4U"

DURATION_SEC  = 120      # Chạy bao nhiêu giây — đủ lâu để test tắt/bật
THREADS       = 5
TABLE         = "load_test_records"

console = Console()

# ── Stats ─────────────────────────────────────────────────
stats = {
    "inserted":      0,
    "errors":        0,
    "conn_fails":    0,
    "downtimes":     [],   # list of {"start": t, "end": t, "duration": s}
    "failovers":     [],   # list of {"from": host, "to": host, "at": t}
    "current_down":  None, # thời điểm bắt đầu downtime hiện tại
    "current_host":  HOSTS[0],  # host đang connect
    "last_error":    "",
    "lock":          threading.Lock(),
}


def make_dsn(host):
    return oracledb.makedsn(host, PORT, service_name=SERVICE)

def random_name():
    return "User_" + "".join(random.choices(string.ascii_uppercase, k=5))

def random_dept():
    return random.choice(["Engineering", "Marketing", "HR", "IT", "Finance"])


def try_connect():
    """Thử connect lần lượt từng host. Trả về (conn, host) hoặc raise nếu tất cả fail."""
    current = stats["current_host"]
    # Thử host hiện tại trước, rồi đến các host khác
    ordered = [current] + [h for h in HOSTS if h != current]
    last_err = None
    for host in ordered:
        try:
            conn = oracledb.connect(user=USER, password=PASSWORD, dsn=make_dsn(host))
            # Kiểm tra host này có phải PRIMARY không (chỉ PRIMARY mới INSERT được)
            cur = conn.cursor()
            cur.execute("SELECT SYS_CONTEXT('USERENV','DATABASE_ROLE') FROM DUAL")
            role = cur.fetchone()[0]
            cur.close()
            if role == "PRIMARY":
                return conn, host
            else:
                conn.close()
                last_err = Exception(f"{host} is {role}, not PRIMARY")
        except Exception as e:
            last_err = e
    raise last_err


def setup_table():
    conn, host = try_connect()
    cur = conn.cursor()
    cur.execute(f"""
        BEGIN
            EXECUTE IMMEDIATE 'DROP TABLE {TABLE} PURGE';
        EXCEPTION WHEN OTHERS THEN NULL;
        END;
    """)
    cur.execute(f"""
        CREATE TABLE {TABLE} (
            id         NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            name       VARCHAR2(50),
            dept       VARCHAR2(30),
            salary     NUMBER(10,2),
            created_at TIMESTAMP DEFAULT SYSTIMESTAMP
        )
    """)
    conn.commit()
    cur.close()
    conn.close()
    return host


def worker(stop_event, start_time):
    while not stop_event.is_set():
        try:
            conn, host = try_connect()

            # Phát hiện failover (đổi host)
            with stats["lock"]:
                old_host = stats["current_host"]
                if host != old_host:
                    stats["failovers"].append({
                        "from": old_host,
                        "to":   host,
                        "at":   time.time() - start_time,
                    })
                    stats["current_host"] = host

                # Vừa recover từ downtime
                if stats["current_down"] is not None:
                    down_start = stats["current_down"]
                    down_end   = time.time()
                    duration   = down_end - down_start
                    stats["downtimes"].append({
                        "start":    down_start,
                        "end":      down_end,
                        "duration": duration,
                        "at":       down_start - start_time,
                    })
                    stats["current_down"] = None

            cur = conn.cursor()
            while not stop_event.is_set():
                cur.execute(
                    f"INSERT INTO {TABLE} (name, dept, salary) VALUES (:1, :2, :3)",
                    (random_name(), random_dept(), round(random.uniform(500, 5000), 2))
                )
                conn.commit()
                with stats["lock"]:
                    stats["inserted"] += 1

            cur.close()
            conn.close()

        except Exception as e:
            with stats["lock"]:
                stats["errors"]     += 1
                stats["conn_fails"] += 1
                stats["last_error"]  = str(e)[:80]
                if stats["current_down"] is None:
                    stats["current_down"] = time.time()
            time.sleep(0.5)


def count_records(host):
    """
    Đếm records + lấy DB role thực tế trên host.
    Trả về (count, role) trong đó role = 'PRIMARY' hoặc 'PHYSICAL STANDBY'
    """
    for try_user, try_pass, try_dsn, try_mode, switch_container in [
        (USER,  PASSWORD, make_dsn(host), None, False),
        ("sys", SYS_PASS, oracledb.makedsn(host, PORT, service_name=SERVICE), oracledb.AUTH_MODE_SYSDBA, False),
        ("sys", SYS_PASS, oracledb.makedsn(host, PORT, sid="ORCL"),           oracledb.AUTH_MODE_SYSDBA, True),
    ]:
        try:
            kwargs = dict(user=try_user, password=try_pass, dsn=try_dsn)
            if try_mode:
                kwargs["mode"] = try_mode
            conn = oracledb.connect(**kwargs)
            cur  = conn.cursor()
            if switch_container:
                cur.execute("ALTER SESSION SET CONTAINER = ORCLPDB1")
            cur.execute("SELECT SYS_CONTEXT('USERENV','DATABASE_ROLE') FROM DUAL")
            role = cur.fetchone()[0]
            table_owner = f"{USER}." if try_user != USER else ""
            cur.execute(f"SELECT COUNT(*) FROM {table_owner}{TABLE}")
            count = cur.fetchone()[0]
            cur.close()
            conn.close()
            return count, role
        except Exception:
            pass
    return None, "UNREACHABLE"


def make_live_panel(elapsed, start_time):
    with stats["lock"]:
        inserted     = stats["inserted"]
        errors       = stats["errors"]
        current_down = stats["current_down"]
        current_host = stats["current_host"]
        downtimes    = list(stats["downtimes"])
        failovers    = list(stats["failovers"])
        last_error   = stats["last_error"]

    remaining = max(0, DURATION_SEC - elapsed)
    rate      = inserted / elapsed if elapsed > 0 else 0

    # Progress bar
    bar_done = int(min(elapsed / DURATION_SEC, 1.0) * 40)
    bar_left = 40 - bar_done
    bar      = f"[green]{'█' * bar_done}[/green][dim]{'░' * bar_left}[/dim]"

    # Connection status
    if current_down is not None:
        down_sec = time.time() - current_down
        conn_status = f"[bold red]DOWN {down_sec:.1f}s[/bold red]"
    else:
        conn_status = "[bold green]UP[/bold green]"

    table = Table(box=box.ROUNDED, show_header=False, padding=(0, 1))
    table.add_column("K", style="bold cyan",  width=22)
    table.add_column("V", style="white",       width=52)

    table.add_row("Progress",    f"{bar} {elapsed:.0f}/{DURATION_SEC}s  ({remaining:.0f}s còn lại)")
    table.add_row("Connected to", f"[bold]{current_host}[/bold]  {conn_status}")
    table.add_row("Hosts",       f"{', '.join(HOSTS)}  (auto-failover)")
    table.add_row("Inserted",    f"[bold green]{inserted:,}[/bold green]  ({rate:.1f} rows/s)")
    table.add_row("Errors",      f"[red]{errors}[/red]" if errors else "[green]0[/green]")
    table.add_row("Threads",     str(THREADS))

    if failovers:
        table.add_row("─" * 22, "─" * 52)
        for i, f in enumerate(failovers, 1):
            table.add_row(
                f"[bold magenta]Failover #{i}[/bold magenta]",
                f"[magenta]{f['from']} → {f['to']}[/magenta]  tại t={f['at']:.0f}s"
            )

    if downtimes:
        table.add_row("─" * 22, "─" * 52)
        for i, d in enumerate(downtimes, 1):
            table.add_row(
                f"Downtime #{i}",
                f"[red]{d['duration']:.1f}s[/red]  tại t={d['at']:.0f}s"
            )

    if last_error:
        table.add_row("Last error", f"[dim red]{last_error}[/dim red]")

    title = "[bold yellow]ORACLE DATA GUARD — LOAD TEST (Auto-Failover)[/bold yellow]"
    hint  = "[dim]Tắt bất kỳ host nào — app tự chuyển sang host còn lại[/dim]"
    return Panel(table, title=title, subtitle=hint, border_style="yellow")


# ── Main ──────────────────────────────────────────────────
console.print(Panel(
    f"[bold]Hosts:[/bold] {', '.join(HOSTS)}   [bold]Auto-failover:[/bold] ON\n"
    f"[bold]Duration:[/bold] {DURATION_SEC}s   [bold]Threads:[/bold] {THREADS}   [bold]Table:[/bold] {TABLE}\n"
    f"[bold]Logic:[/bold] Thử từng host → tìm PRIMARY → insert. Host chết → thử host kia.",
    title="[bold yellow]ORACLE DATA GUARD — LOAD TEST[/bold yellow]",
    border_style="yellow"
))

console.print("\n[cyan]Finding PRIMARY and setting up table...[/cyan]")
initial_host = setup_table()
stats["current_host"] = initial_host
console.print(f"[green]✓ PRIMARY is {initial_host} — Table '{TABLE}' ready — bắt đầu insert![/green]\n")

stop_event  = threading.Event()
start_time  = time.time()

for _ in range(THREADS):
    t = threading.Thread(target=worker, args=(stop_event, start_time))
    t.daemon = True
    t.start()

with Live(console=console, refresh_per_second=2) as live:
    while True:
        elapsed = time.time() - start_time
        live.update(make_live_panel(elapsed, start_time))
        if elapsed >= DURATION_SEC:
            break
        time.sleep(0.5)

stop_event.set()
time.sleep(1)
elapsed = time.time() - start_time

# ── Kết quả ───────────────────────────────────────────────
res = Table(box=box.ROUNDED, show_header=False, padding=(0, 1))
res.add_column("K", style="bold cyan",  width=22)
res.add_column("V", style="white",      width=40)

res.add_row("Thời gian",      f"{elapsed:.1f}s")
res.add_row("Total inserted", f"[bold green]{stats['inserted']:,}[/bold green]")
res.add_row("Total errors",   f"[red]{stats['errors']}[/red]" if stats['errors'] else "[green]0[/green]")
res.add_row("Throughput",     f"{stats['inserted']/elapsed:.1f} rows/s")

if stats["failovers"]:
    res.add_row("Failover events", f"[bold magenta]{len(stats['failovers'])}[/bold magenta]")
    for i, f in enumerate(stats["failovers"], 1):
        res.add_row(f"  Failover #{i}", f"[magenta]{f['from']} → {f['to']}[/magenta] tại t={f['at']:.0f}s")
else:
    res.add_row("Failovers", "[green]0 — không đổi host[/green]")

if stats["downtimes"]:
    total_down = sum(d["duration"] for d in stats["downtimes"])
    res.add_row("Downtime events", str(len(stats["downtimes"])))
    res.add_row("Total downtime",  f"[red]{total_down:.1f}s[/red]")
    for i, d in enumerate(stats["downtimes"], 1):
        res.add_row(f"  Downtime #{i}", f"[red]{d['duration']:.1f}s[/red] tại t={d['at']:.0f}s")
else:
    res.add_row("Downtime", "[green]0 — không mất kết nối[/green]")

console.print(Panel(res, title="[bold green]KẾT QUẢ[/bold green]", border_style="green"))

# ── So sánh sync ──────────────────────────────────────────
console.print("\n[cyan]Đợi 5s để redo logs apply...[/cyan]")
time.sleep(5)

sync = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", padding=(0, 1))
sync.add_column("Host",    style="dim",        width=18)
sync.add_column("Method",  style="white",      width=12)
sync.add_column("Rows",    style="green",      width=10)
sync.add_column("Status",  width=28)

for host in HOSTS:
    count, role = count_records(host)
    if count is not None:
        if role == "PRIMARY":
            sync.add_row(host, role, f"{count:,}", "[green]PRIMARY (RW)[/green]")
        elif "STANDBY" in role:
            sync.add_row(host, role, f"{count:,}", "[cyan]STANDBY (RO)[/cyan]")
        else:
            sync.add_row(host, role, f"{count:,}", f"[yellow]{role}[/yellow]")
    else:
        sync.add_row(host, "-", "[red]N/A[/red]", f"[red]{role}[/red]")

console.print(Panel(sync, title="[bold cyan]ROW COUNT — ALL HOSTS[/bold cyan]", border_style="cyan"))
console.print()

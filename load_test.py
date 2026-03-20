"""
load_test.py — Oracle Data Guard Load Test
Kịch bản:
  1. Insert liên tục vào Primary trong DURATION_SEC giây
  2. Theo dõi downtime nếu Primary bị tắt
  3. Sau khi xong: so sánh số bản ghi Primary vs Standby

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
PRIMARY_HOST  = "192.168.1.195"
STANDBY_HOST  = "192.168.1.196"
PORT          = 1521
SERVICE       = "orclpdb1"
USER          = "chirag"
PASSWORD      = "Tiger123"
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
    "current_down":  None, # thời điểm bắt đầu downtime hiện tại
    "last_error":    "",
    "lock":          threading.Lock(),
}


def make_dsn(host):
    return oracledb.makedsn(host, PORT, service_name=SERVICE)

def random_name():
    return "User_" + "".join(random.choices(string.ascii_uppercase, k=5))

def random_dept():
    return random.choice(["Engineering", "Marketing", "HR", "IT", "Finance"])


def setup_table():
    conn = oracledb.connect(user=USER, password=PASSWORD, dsn=make_dsn(PRIMARY_HOST))
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


def worker(stop_event, start_time):
    while not stop_event.is_set():
        try:
            conn = oracledb.connect(user=USER, password=PASSWORD, dsn=make_dsn(PRIMARY_HOST))

            # Vừa recover từ downtime
            with stats["lock"]:
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
                stats["last_error"]  = str(e)[:60]
                if stats["current_down"] is None:
                    stats["current_down"] = time.time()
            time.sleep(0.5)


def count_records(host, use_sys=False):
    try:
        if use_sys:
            dsn  = oracledb.makedsn(host, PORT, sid="ORCL")
            conn = oracledb.connect(user="sys", password=SYS_PASS, dsn=dsn, mode=oracledb.AUTH_MODE_SYSDBA)
            cur  = conn.cursor()
            cur.execute("ALTER SESSION SET CONTAINER = ORCLPDB1")
            cur.execute(f"SELECT COUNT(*) FROM {USER}.{TABLE}")
        else:
            conn = oracledb.connect(user=USER, password=PASSWORD, dsn=make_dsn(host))
            cur  = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM {TABLE}")
        count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return count
    except Exception as e:
        return f"ERROR: {e}"


def make_live_panel(elapsed, start_time):
    with stats["lock"]:
        inserted     = stats["inserted"]
        errors       = stats["errors"]
        current_down = stats["current_down"]
        downtimes    = list(stats["downtimes"])
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
    table.add_column("V", style="white",       width=42)

    table.add_row("Progress",    f"{bar} {elapsed:.0f}/{DURATION_SEC}s  ({remaining:.0f}s còn lại)")
    table.add_row("Primary",     f"{PRIMARY_HOST}  {conn_status}")
    table.add_row("Inserted",    f"[bold green]{inserted:,}[/bold green]  ({rate:.1f} rows/s)")
    table.add_row("Errors",      f"[red]{errors}[/red]" if errors else "[green]0[/green]")
    table.add_row("Threads",     str(THREADS))

    if downtimes:
        table.add_row("─" * 22, "─" * 42)
        for i, d in enumerate(downtimes, 1):
            table.add_row(
                f"Downtime #{i}",
                f"[red]{d['duration']:.1f}s[/red]  tại t={d['at']:.0f}s"
            )

    if last_error:
        table.add_row("Last error", f"[dim red]{last_error}[/dim red]")

    title = "[bold yellow]ORACLE DATA GUARD — LOAD TEST[/bold yellow]"
    hint  = "[dim]Tắt Primary (195) hoặc Standby (196) để test failover[/dim]"
    return Panel(table, title=title, subtitle=hint, border_style="yellow")


# ── Main ──────────────────────────────────────────────────
console.print(Panel(
    f"[bold]Primary:[/bold] {PRIMARY_HOST}   [bold]Standby:[/bold] {STANDBY_HOST}\n"
    f"[bold]Duration:[/bold] {DURATION_SEC}s   [bold]Threads:[/bold] {THREADS}   [bold]Table:[/bold] {TABLE}",
    title="[bold yellow]ORACLE DATA GUARD — LOAD TEST[/bold yellow]",
    border_style="yellow"
))

console.print("\n[cyan]Setting up table...[/cyan]")
setup_table()
console.print(f"[green]✓ Table '{TABLE}' ready — bắt đầu insert![/green]\n")

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
res.add_column("V", style="white",      width=30)

res.add_row("Thời gian",      f"{elapsed:.1f}s")
res.add_row("Total inserted", f"[bold green]{stats['inserted']:,}[/bold green]")
res.add_row("Total errors",   f"[red]{stats['errors']}[/red]" if stats['errors'] else "[green]0[/green]")
res.add_row("Throughput",     f"{stats['inserted']/elapsed:.1f} rows/s")

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
console.print("\n[cyan]Đợi 5s để Standby apply redo logs...[/cyan]")
time.sleep(5)

primary_count = count_records(PRIMARY_HOST)
standby_count = count_records(STANDBY_HOST, use_sys=True)

sync = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", padding=(0, 1))
sync.add_column("Server",  style="bold white", width=12)
sync.add_column("Host",    style="dim",        width=18)
sync.add_column("Rows",    style="green",      width=10)
sync.add_column("Status",  width=24)

sync.add_row("PRIMARY", PRIMARY_HOST,
    f"{primary_count:,}" if isinstance(primary_count, int) else str(primary_count),
    "[green]READ WRITE[/green]")
sync.add_row("STANDBY", STANDBY_HOST,
    f"{standby_count:,}" if isinstance(standby_count, int) else str(standby_count),
    "[cyan]READ ONLY WITH APPLY[/cyan]")

console.print(Panel(sync, title="[bold cyan]PRIMARY vs STANDBY[/bold cyan]", border_style="cyan"))

if isinstance(primary_count, int) and isinstance(standby_count, int):
    diff = primary_count - standby_count
    if diff == 0:
        console.print("\n[bold green]✓ SYNC HOÀN TOÀN — không mất data[/bold green]\n")
    else:
        console.print(f"\n[yellow]⚠ Lag {diff:,} rows — MRP đang apply, chạy check_sync.py sau vài giây[/yellow]\n")
else:
    console.print("\n[red]⚠ Không thể kết nối để check sync[/red]\n")

"""
check_dg_status.py — Oracle Data Guard Status Checker
Kiểm tra nhanh trạng thái DG, FSFO, Observer
Chạy: uv run check_dg_status.py
"""

import oracledb
import subprocess
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

PRIMARY_HOST = "192.168.1.195"
STANDBY_HOST = "192.168.1.196"
PORT         = 1521
SYS_PASS     = "Oracle_4U"

console = Console()


def sys_conn(host, sid="ORCL"):
    dsn = oracledb.makedsn(host, PORT, sid=sid)
    return oracledb.connect(user="sys", password=SYS_PASS, dsn=dsn, mode=oracledb.AUTH_MODE_SYSDBA)


def query(host, sql, sid="ORCL"):
    try:
        conn = sys_conn(host, sid)
        cur  = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        cur.close()
        conn.close()
        return cols, rows
    except Exception as e:
        return [], [("ERROR", str(e)[:80])]


def one_val(host, sql, sid="ORCL"):
    try:
        conn = sys_conn(host, sid)
        cur  = conn.cursor()
        cur.execute(sql)
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row[0] if row else "N/A"
    except Exception as e:
        return f"ERROR: {e}"


def run_dgmgrl(host, commands: list[str]) -> str:
    """Chạy dgmgrl trên host qua SSH (hoặc local nếu host=PRIMARY)"""
    script = "\n".join(commands) + "\nEXIT;"
    if host == PRIMARY_HOST:
        cmd = [
            "ssh", "-o", "StrictHostKeyChecking=no",
            f"oracle@{host}",
            f"export ORACLE_HOME=/u01/app/oracle/product/21c/dbhome_1 PATH=/u01/app/oracle/product/21c/dbhome_1/bin:$PATH ORACLE_SID=ORCL; "
            f"echo '{script}' | dgmgrl sys/{SYS_PASS}@ORCL"
        ]
    else:
        cmd = [
            "ssh", "-o", "StrictHostKeyChecking=no",
            f"oracle@{host}",
            f"export ORACLE_HOME=/u01/app/oracle/product/21c/dbhome_1 PATH=/u01/app/oracle/product/21c/dbhome_1/bin:$PATH ORACLE_SID=ORCL; "
            f"echo '{script}' | dgmgrl sys/{SYS_PASS}@ORCL_STBY"
        ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.stdout + result.stderr
    except Exception as e:
        return f"ERROR: {e}"


console.print()

# ── 1. Database Role & Status ─────────────────────────────
t1 = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", padding=(0,1))
t1.add_column("Server",   width=10)
t1.add_column("Host",     width=16)
t1.add_column("Role",     width=20)
t1.add_column("Mode",     width=22)
t1.add_column("DB Name",  width=12)
t1.add_column("Flash",    width=8)

for host, label in [(PRIMARY_HOST, "PRIMARY"), (STANDBY_HOST, "STANDBY")]:
    cols, rows = query(host, """
        SELECT DATABASE_ROLE, OPEN_MODE, DB_UNIQUE_NAME, FLASHBACK_ON
        FROM V$DATABASE
    """)
    if rows and rows[0][0] != "ERROR":
        role, mode, name, flash = rows[0]
        role_style  = "bold green"  if "PRIMARY"  in str(role) else "bold cyan"
        flash_style = "green" if str(flash) == "YES" else "red"
        t1.add_row(
            label, host,
            f"[{role_style}]{role}[/{role_style}]",
            str(mode), str(name),
            f"[{flash_style}]{flash}[/{flash_style}]"
        )
    else:
        t1.add_row(label, host, "[red]UNREACHABLE[/red]", "-", "-", "-")

console.print(Panel(t1, title="[bold]DATABASE ROLE & STATUS[/bold]", border_style="blue"))

# ── 2. MRP / RFS Processes ────────────────────────────────
t2 = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", padding=(0,1))
t2.add_column("Host",    width=16)
t2.add_column("Process", width=10)
t2.add_column("Status",  width=22)
t2.add_column("Seq#",    width=10)
t2.add_column("Block#",  width=10)

for host, label in [(PRIMARY_HOST, "Primary"), (STANDBY_HOST, "Standby")]:
    cols, rows = query(host, """
        SELECT PROCESS, STATUS, SEQUENCE#, BLOCK#
        FROM V$MANAGED_STANDBY
        WHERE PROCESS IN ('MRP0','MRPX','RFS','ARCH','LNS0','LNS1')
        ORDER BY PROCESS
    """)
    if not rows:
        t2.add_row(f"{label} ({host})", "[dim]no rows[/dim]", "-", "-", "-")
        continue
    for row in rows:
        if row[0] == "ERROR":
            t2.add_row(f"{label} ({host})", f"[red]{row[1]}[/red]", "-", "-", "-")
        else:
            proc, status, seq, blk = row
            style = "bold green" if "APPLYING" in str(status) else ("bold yellow" if "WAIT" in str(status) else "white")
            t2.add_row(f"{label}", str(proc), f"[{style}]{status}[/{style}]", str(seq), str(blk))

console.print(Panel(t2, title="[bold]MRP / RFS PROCESSES[/bold]", border_style="green"))

# ── 3. Redo Apply Lag ─────────────────────────────────────
t3 = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", padding=(0,1))
t3.add_column("Metric",  width=30)
t3.add_column("Value",   width=30)
t3.add_column("Unit",    width=10)

cols, rows = query(STANDBY_HOST, """
    SELECT NAME, VALUE, UNIT FROM V$DATAGUARD_STATS
    WHERE NAME IN (
        'transport lag', 'apply lag',
        'apply finish time', 'estimated startup time'
    )
    ORDER BY NAME
""")
if rows:
    for row in rows:
        if row[0] == "ERROR":
            t3.add_row("[red]ERROR[/red]", str(row[1]), "-")
        else:
            name, val, unit = row
            style = "green" if val in ("+00 00:00:00", "0") else "yellow"
            t3.add_row(str(name), f"[{style}]{val}[/{style}]", str(unit) if unit else "")

console.print(Panel(t3, title="[bold]REDO APPLY LAG (Standby)[/bold]", border_style="yellow"))

# ── 4. Fast-Start Failover Status ─────────────────────────
t4 = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", padding=(0,1))
t4.add_column("Property",   width=35)
t4.add_column("Value",      width=35)

cols, rows = query(PRIMARY_HOST, """
    SELECT NAME, VALUE FROM V$DATABASE_BLOCK_CORRUPTION
    WHERE ROWNUM <= 1
""")

# FSFO info từ V$DATABASE (PRIMARY)
fsfo_target = one_val(PRIMARY_HOST, "SELECT FS_FAILOVER_STATUS FROM V$DATABASE")
fsfo_current = one_val(PRIMARY_HOST, "SELECT FS_FAILOVER_CURRENT_TARGET FROM V$DATABASE")
fsfo_mode = one_val(PRIMARY_HOST, "SELECT FS_FAILOVER_MODE FROM V$DATABASE")

if "ERROR" not in str(fsfo_target):
    fsfo_style = "green" if str(fsfo_target) not in ("DISABLED", "TARGET UNDER LAG LIMIT") else "red"
    t4.add_row("FSFO Status",   f"[{fsfo_style}]{fsfo_target}[/{fsfo_style}]")
    t4.add_row("FSFO Target",   str(fsfo_current) if fsfo_current != "N/A" else "[dim]none[/dim]")
    t4.add_row("FSFO Mode",     str(fsfo_mode))
else:
    t4.add_row("FSFO Status", f"[red]{fsfo_target}[/red]")

console.print(Panel(t4, title="[bold]FAST-START FAILOVER (FSFO)[/bold]", border_style="magenta"))

# ── 5. Hint nếu FSFO chưa bật ─────────────────────────────
if "DISABLED" in str(fsfo_target) or "ERROR" in str(fsfo_target):
    console.print(Panel(
        "[yellow]FSFO chưa được enable![/yellow]\n\n"
        "Chạy:\n"
        "  [bold cyan]ansible-playbook setup_fsfo.yml[/bold cyan]\n\n"
        "Để kích hoạt Fast-Start Failover với Observer.\n"
        "Sau đó Observer sẽ tự monitor Primary và trigger failover nếu Primary down.",
        title="[bold red]ACTION REQUIRED[/bold red]",
        border_style="red"
    ))
elif "OBSERVER PRESENT" in str(fsfo_target):
    console.print(
        "\n[bold green]✓ FSFO đang hoạt động — Observer đang giám sát Primary[/bold green]\n"
        f"  Nếu Primary [bold]192.168.1.195[/bold] down > [bold]30s[/bold] → tự động failover sang [bold]192.168.1.196[/bold]\n"
    )

console.print()

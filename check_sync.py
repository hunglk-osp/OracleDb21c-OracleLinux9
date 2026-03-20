"""
check_sync.py — Kiểm tra dữ liệu có được sync từ Primary sang Standby không
Primary : 192.168.1.195:1521/orclpdb1  (READ WRITE)
Standby : 192.168.1.196:1521/orclpdb1  (READ ONLY — mở tạm để query)
"""

import oracledb

PRIMARY = {"host": "192.168.1.195", "port": 1521, "service": "orclpdb1",
           "user": "chirag", "password": "Tiger123"}

STANDBY = {"host": "192.168.1.196", "port": 1521, "service": "orclpdb1",
           "user": "sys", "password": "Oracle_4U", "mode": oracledb.AUTH_MODE_SYSDBA}


def conn_primary():
    return oracledb.connect(
        user=PRIMARY["user"], password=PRIMARY["password"],
        dsn=f"{PRIMARY['host']}:{PRIMARY['port']}/{PRIMARY['service']}"
    )

def conn_standby():
    return oracledb.connect(
        user=STANDBY["user"], password=STANDBY["password"],
        dsn=f"{STANDBY['host']}:{STANDBY['port']}/{STANDBY['service']}",
        mode=STANDBY["mode"]
    )


# ─────────────────────────────────────────────
# 1. ĐỌC DỮ LIỆU TỪ PRIMARY
# ─────────────────────────────────────────────
def read_primary():
    print("\n── PRIMARY (192.168.1.195) ───────────────────")
    with conn_primary() as conn:
        cur = conn.cursor()

        # Đếm rows trong employees
        cur.execute("SELECT COUNT(*) FROM employees")
        count = cur.fetchone()[0]
        print(f"  employees rows : {count}")

        # SCN hiện tại
        cur.execute("SELECT CURRENT_SCN FROM V$DATABASE")
        scn = cur.fetchone()[0]
        print(f"  Current SCN    : {scn}")

        # Rows mới nhất
        cur.execute("""
            SELECT id, name, dept, salary
            FROM employees
            ORDER BY id DESC
            FETCH FIRST 3 ROWS ONLY
        """)
        print("  3 rows mới nhất:")
        for row in cur:
            print(f"    ID={row[0]}  {row[1]:<20} {row[2]:<14} {row[3]:,.2f}")

    return count, scn


# ─────────────────────────────────────────────
# 2. MỞ STANDBY READ ONLY VÀ SO SÁNH
# ─────────────────────────────────────────────
def read_standby():
    print("\n── STANDBY (192.168.1.196) ───────────────────")
    with conn_standby() as conn:
        cur = conn.cursor()

        # Kiểm tra role standby
        cur.execute("SELECT DATABASE_ROLE, OPEN_MODE FROM V$DATABASE")
        role, mode = cur.fetchone()
        print(f"  Role      : {role}")
        print(f"  Open mode : {mode}")

        # Mở PDB read only để query
        try:
            cur.execute("ALTER SESSION SET CONTAINER = orclpdb1")
            cur.execute("ALTER PLUGGABLE DATABASE orclpdb1 OPEN READ ONLY")
        except Exception:
            pass  # Có thể đã mở rồi

        # Đổi sang PDB
        cur.execute("ALTER SESSION SET CONTAINER = orclpdb1")

        # Đếm rows
        try:
            cur.execute("SELECT COUNT(*) FROM chirag.employees")
            count = cur.fetchone()[0]
            print(f"  employees rows : {count}")

            cur.execute("""
                SELECT id, name, dept, salary
                FROM chirag.employees
                ORDER BY id DESC
                FETCH FIRST 3 ROWS ONLY
            """)
            print("  3 rows mới nhất:")
            for row in cur:
                print(f"    ID={row[0]}  {row[1]:<20} {row[2]:<14} {row[3]:,.2f}")
        except Exception as e:
            print(f"  Lỗi query PDB: {e}")
            count = -1

        # Apply lag
        try:
            cur.execute("ALTER SESSION SET CONTAINER = CDB$ROOT")
            cur.execute("""
                SELECT NAME, VALUE FROM V$DATAGUARD_STATS
                WHERE NAME IN ('transport lag','apply lag')
            """)
            rows = cur.fetchall()
            print("  Data Guard lag:")
            for r in rows:
                print(f"    {r[0]:<20} = {r[1]}")
        except Exception as e:
            print(f"  Không lấy được lag: {e}")

    return count


# ─────────────────────────────────────────────
# 3. SO SÁNH KẾT QUẢ
# ─────────────────────────────────────────────
def compare(primary_count, standby_count):
    print("\n── KẾT QUẢ SO SÁNH ──────────────────────────")
    if standby_count == -1:
        print("  Không kết nối được Standby.")
        return
    if primary_count == standby_count:
        print(f"  SYNC OK — cả 2 đều có {primary_count} rows")
    else:
        diff = primary_count - standby_count
        print(f"  LAG — Primary: {primary_count} rows | Standby: {standby_count} rows | Chênh: {diff} rows")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("  CHECK SYNC: Primary vs Standby")
    print("=" * 50)

    primary_count, scn = read_primary()

    try:
        standby_count = read_standby()
    except Exception as e:
        print(f"\n  Không kết nối được Standby: {e}")
        standby_count = -1

    compare(primary_count, standby_count)
    print()

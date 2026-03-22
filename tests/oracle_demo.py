"""
oracle_demo.py — Học quản lý Oracle DB cơ bản bằng Python
Thư viện: oracledb (pip install oracledb)

Kết nối: Data Guard failover — Primary 192.168.1.195, Standby 192.168.1.196
User   : osp / Osp@123
"""

import oracledb

# ─────────────────────────────────────────────
# CẤU HÌNH KẾT NỐI — Data Guard Failover DSN
# Tự động thử Primary trước, nếu chết thì kết nối Standby
# ─────────────────────────────────────────────
USER     = "osp"
PASSWORD = "Osp@123"
SERVICE  = "orclpdb1"

# Multi-host DSN chuẩn Data Guard
DSN = """(DESCRIPTION=
    (FAILOVER=ON)
    (LOAD_BALANCE=OFF)
    (ADDRESS=(PROTOCOL=TCP)(HOST=192.168.1.195)(PORT=1521))
    (ADDRESS=(PROTOCOL=TCP)(HOST=192.168.1.196)(PORT=1521))
    (CONNECT_DATA=(SERVICE_NAME=orclpdb1)))"""


def get_connection():
    conn = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    return conn


# ─────────────────────────────────────────────
# 1. TEST KẾT NỐI
# ─────────────────────────────────────────────
def test_connection():
    print("\n── 1. TEST KẾT NỐI ──────────────────────────")
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT SYS_CONTEXT('USERENV','SERVER_HOST'), DATABASE_ROLE, OPEN_MODE FROM V$DATABASE")
        host, role, mode = cur.fetchone()
        print(f"  Kết nối thành công!")
        print(f"  Oracle version : {conn.version}")
        print(f"  Server host    : {host}")
        print(f"  DB role        : {role}  ({mode})")
        print(f"  Service        : {SERVICE}")


# ─────────────────────────────────────────────
# 2. TẠO TABLE
# ─────────────────────────────────────────────
def create_table():
    print("\n── 2. TẠO TABLE ─────────────────────────────")
    with get_connection() as conn:
        cur = conn.cursor()

        # Xóa nếu đã tồn tại
        try:
            cur.execute("DROP TABLE employees PURGE")
            print("  Đã xóa table cũ.")
        except oracledb.DatabaseError:
            pass  # Chưa tồn tại thì bỏ qua

        cur.execute("""
            CREATE TABLE employees (
                id        NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                name      VARCHAR2(100)  NOT NULL,
                dept      VARCHAR2(50),
                salary    NUMBER(10,2),
                hired_at  DATE DEFAULT SYSDATE
            )
        """)
        conn.commit()
        print("  Table 'employees' đã được tạo.")


# ─────────────────────────────────────────────
# 3. INSERT DỮ LIỆU
# ─────────────────────────────────────────────
def insert_data():
    print("\n── 3. INSERT DỮ LIỆU (1000 bản ghi) ────────")
    import random

    depts = ["Engineering", "Marketing", "HR", "Finance", "IT", "Sales", "Legal", "Operations"]
    first_names = ["Nguyen", "Tran", "Le", "Pham", "Hoang", "Vu", "Dang", "Bui", "Do", "Ngo"]
    last_names  = ["Van An", "Thi Bich", "Van Cuong", "Thi Dung", "Van Em",
                   "Thi Phuong", "Van Quang", "Thi Hoa", "Van Long", "Thi Mai"]

    employees = [
        (
            f"{random.choice(first_names)} {random.choice(last_names)} {i:04d}",
            random.choice(depts),
            round(random.uniform(800, 5000), 2),
        )
        for i in range(1, 1001)
    ]

    with get_connection() as conn:
        cur = conn.cursor()
        # Insert theo batch 100 để nhanh hơn
        batch_size = 100
        for i in range(0, len(employees), batch_size):
            cur.executemany(
                "INSERT INTO employees (name, dept, salary) VALUES (:1, :2, :3)",
                employees[i:i+batch_size]
            )
            conn.commit()
            print(f"  Đã insert {min(i+batch_size, 1000)}/1000...", end="\r")
    print(f"  Đã insert 1000 nhân viên.              ")


# ─────────────────────────────────────────────
# 4. SELECT DỮ LIỆU
# ─────────────────────────────────────────────
def select_data():
    print("\n── 4. SELECT DỮ LIỆU ────────────────────────")
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, name, dept, salary, TO_CHAR(hired_at,'YYYY-MM-DD') AS hired
            FROM employees
            ORDER BY id
        """)

        print(f"  {'ID':<4} {'Tên':<20} {'Phòng':<14} {'Lương':>10}  {'Ngày vào'}")
        print(f"  {'─'*4} {'─'*20} {'─'*14} {'─'*10}  {'─'*10}")
        for row in cur:
            print(f"  {row[0]:<4} {row[1]:<20} {row[2]:<14} {row[3]:>10,.2f}  {row[4]}")


# ─────────────────────────────────────────────
# 5. UPDATE DỮ LIỆU
# ─────────────────────────────────────────────
def update_data():
    print("\n── 5. UPDATE DỮ LIỆU ────────────────────────")
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE employees
            SET salary = salary * 1.10
            WHERE dept = 'Engineering'
        """)
        conn.commit()
        print(f"  Đã tăng lương 10% cho {cur.rowcount} nhân viên Engineering.")


# ─────────────────────────────────────────────
# 6. DELETE DỮ LIỆU
# ─────────────────────────────────────────────
def delete_data():
    print("\n── 6. DELETE DỮ LIỆU ────────────────────────")
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM employees WHERE dept = 'HR'")
        conn.commit()
        print(f"  Đã xóa {cur.rowcount} nhân viên phòng HR.")


# ─────────────────────────────────────────────
# 7. TRANSACTION (commit / rollback)
# ─────────────────────────────────────────────
def transaction_demo():
    print("\n── 7. TRANSACTION ───────────────────────────")
    with get_connection() as conn:
        cur = conn.cursor()

        # Thành công → commit
        try:
            cur.execute("INSERT INTO employees (name, dept, salary) VALUES ('Test Commit', 'IT', 999)")
            conn.commit()
            print("  INSERT 'Test Commit' → COMMIT thành công.")
        except Exception as e:
            conn.rollback()
            print(f"  Lỗi, ROLLBACK: {e}")

        # Cố tình lỗi → rollback
        try:
            cur.execute("INSERT INTO employees (name, dept, salary) VALUES ('Test Rollback', 'IT', 888)")
            cur.execute("INSERT INTO employees (id, name) VALUES (1, 'Duplicate PK')")  # lỗi PK
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"  INSERT lỗi PK → ROLLBACK. ({e})")


# ─────────────────────────────────────────────
# 8. STORED PROCEDURE
# ─────────────────────────────────────────────
def create_and_call_procedure():
    print("\n── 8. STORED PROCEDURE ──────────────────────")
    with get_connection() as conn:
        cur = conn.cursor()

        # Tạo procedure tăng lương theo phòng ban
        cur.execute("""
            CREATE OR REPLACE PROCEDURE raise_salary(
                p_dept    IN  VARCHAR2,
                p_percent IN  NUMBER,
                p_count   OUT NUMBER
            ) AS
            BEGIN
                UPDATE employees
                SET    salary = salary * (1 + p_percent / 100)
                WHERE  dept = p_dept;
                p_count := SQL%ROWCOUNT;
                COMMIT;
            END;
        """)
        print("  Đã tạo procedure 'raise_salary'.")

        # Gọi procedure
        out_count = cur.var(oracledb.NUMBER)
        cur.callproc("raise_salary", ["Marketing", 5, out_count])
        print(f"  Tăng lương 5% cho Marketing → {int(out_count.getvalue())} người được cập nhật.")


# ─────────────────────────────────────────────
# 9. QUERY TỔNG HỢP
# ─────────────────────────────────────────────
def summary_query():
    print("\n── 9. QUERY TỔNG HỢP ────────────────────────")
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT dept,
                   COUNT(*)          AS so_nv,
                   ROUND(AVG(salary),2) AS luong_tb,
                   MAX(salary)       AS luong_cao_nhat
            FROM employees
            GROUP BY dept
            ORDER BY luong_tb DESC
        """)
        print(f"  {'Phòng':<14} {'Số NV':>6} {'Lương TB':>12} {'Lương Cao Nhất':>16}")
        print(f"  {'─'*14} {'─'*6} {'─'*12} {'─'*16}")
        for row in cur:
            print(f"  {row[0]:<14} {row[1]:>6} {row[2]:>12,.2f} {row[3]:>16,.2f}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("  ORACLE DB DEMO — Python oracledb")
    print("=" * 50)

    test_connection()
    create_table()
    insert_data()
    select_data()
    update_data()
    delete_data()
    transaction_demo()
    create_and_call_procedure()
    select_data()        # Xem kết quả sau tất cả thay đổi
    summary_query()

    print("\n✓ Hoàn tất demo!\n")

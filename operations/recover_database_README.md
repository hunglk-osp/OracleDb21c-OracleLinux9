# recover_database.yml — Point-in-time Recovery Oracle DG

## Mục đích

Khôi phục Oracle Database 21c về **một thời điểm trong quá khứ** khi data bị corrupt, bị xóa nhầm, hoặc cần revert sau test.
Hỗ trợ 2 phương pháp: **Flashback Database** (nhanh) và **RMAN Restore** (khi Flashback không khả dụng).

---

## Khi nào chạy

- Data bị corrupt hoặc bị xóa nhầm bởi user/application
- Cần revert về trạng thái trước khi test (load test, schema change...)
- Disk chết hoàn toàn — chỉ còn RMAN backup (mode=rman)

**Không dùng playbook này cho:**
- DB crash thông thường → dùng `restart_crashed_db.yml`
- Standby bị lag → chạy `restart_crashed_db.yml`

---

## 3 Mode

| Mode | Lệnh | Khi nào dùng |
|---|---|---|
| `list` | `-e "mode=list"` | Xem restore points + RMAN backups hiện có |
| `flashback` | `-e "mode=flashback" -e "restore_point=<tên>"` | Flashback về restore point (~2 phút) |
| `rman` | `-e "mode=rman" -e "until_time='YYYY-MM-DD HH24:MI:SS'"` | RMAN restore đến thời điểm chỉ định (~30-60 phút) |

---

## Ví dụ sử dụng

```bash
# Bước 1 — Xem có gì để revert về
ansible-playbook operations/recover_database.yml -e "mode=list"

# Bước 2a — Flashback về restore point
ansible-playbook operations/recover_database.yml \
  -e "mode=flashback" \
  -e "restore_point=before_load_test"

# Bước 2b — RMAN restore đến thời điểm cụ thể
ansible-playbook operations/recover_database.yml \
  -e "mode=rman" \
  -e "until_time='2026-03-24 10:00:00'"
```

---

## Yêu cầu trước khi chạy

**mode=flashback:**
- Flashback Database đã được enable (đã có sẵn từ `setup_primary.yml`)
- Restore point tồn tại — kiểm tra bằng `mode=list`
- Restore point phải nằm trong khoảng `OLDEST_FLASHBACK_TIME` (hiển thị khi `mode=list`)

**mode=rman:**
- Có RMAN backup tồn tại trong `/u01/backup/rman` hoặc FRA
- Backup phải cover được thời điểm `until_time` cần restore

---

## Cách tạo restore point (làm TRƯỚC khi test)

```sql
-- Trên Primary — tạo restore point trước khi chạy load test / schema change
sqlplus sys/Oracle_4U@ORCL as sysdba
CREATE RESTORE POINT before_load_test GUARANTEE FLASHBACK DATABASE;

-- Kiểm tra
SELECT NAME, TO_CHAR(TIME,'YYYY-MM-DD HH24:MI:SS') AS TIME FROM V$RESTORE_POINT;

-- Xóa restore point khi không cần nữa (giải phóng FRA)
DROP RESTORE POINT before_load_test;
```

📌 `GUARANTEE FLASHBACK DATABASE` đảm bảo Oracle giữ flashback logs đủ lâu để revert về điểm này — không bị tự động purge dù FRA gần đầy.

---

## 5 Phase thực thi

### PHASE 1 — DETECT: Xác định vai trò từng node

Query `V$DATABASE` trên cả 2 node, set facts `is_primary` / `is_standby`.

---

### PHASE 2 — LIST: Xem restore points + backups

**Chỉ chạy khi `mode=list`** — không thay đổi gì trên DB.

Output ví dụ:

```
=== RESTORE POINTS ===
NAME                    TIME                       GUARANTEE
before_load_test        2026-03-24 09:00:00        YES

=== FLASHBACK LOG INFO ===
OLDEST_FLASHBACK_SCN    OLDEST_FLASHBACK_TIME
12345678                2026-03-23 20:00:00
```

```
=== RMAN BACKUPS ===
BS Key  Type  LV  Size       Device  Completion Time
1       Full      1.2G       DISK    24-MAR-26
```

---

### PHASE 3 — FLASHBACK DATABASE

**Thứ tự thực hiện:**

**3a — Stop MRP trên Standby**

```sql
ALTER DATABASE RECOVER MANAGED STANDBY DATABASE CANCEL;
```

Dừng apply redo trên Standby trước khi Primary flashback — tránh conflict SCN.

**3b — Flashback Primary**

```sql
SHUTDOWN IMMEDIATE;
STARTUP MOUNT;
FLASHBACK DATABASE TO RESTORE POINT before_load_test;
ALTER DATABASE OPEN RESETLOGS;
ALTER PLUGGABLE DATABASE ALL OPEN;
```

📌 `RESETLOGS` tạo ra SCN timeline mới — Standby phải flashback về cùng điểm để sync lại.

**3c — Flashback Standby về cùng restore point**

```sql
SHUTDOWN IMMEDIATE;
STARTUP MOUNT;
FLASHBACK DATABASE TO RESTORE POINT before_load_test;
ALTER DATABASE OPEN READ ONLY;
ALTER PLUGGABLE DATABASE ALL OPEN READ ONLY;
ALTER DATABASE RECOVER MANAGED STANDBY DATABASE USING CURRENT LOGFILE DISCONNECT FROM SESSION;
```

📌 Playbook tự xử lý cả Primary lẫn Standby trong 1 lần chạy — cả 2 node quay về cùng thời điểm, DG tiếp tục sync bình thường sau đó.

---

### PHASE 4 — RMAN RESTORE

**Chỉ dùng khi Flashback không khả dụng** (Flashback chưa enable, hoặc thời điểm cần restore nằm ngoài flashback window).

**4a — Stop MRP trên Standby**

**4b — RMAN restore Primary FROM SERVICE Standby**

Không cần copy file backup sang Primary. RMAN stream trực tiếp từ Standby qua network (Oracle 12c+):

```bash
rman target /
STARTUP MOUNT;
RUN {
  SET UNTIL TIME "TO_DATE('2026-03-24 10:00:00', 'YYYY-MM-DD HH24:MI:SS')";
  RESTORE DATABASE FROM SERVICE ORCL_STBY;
  RECOVER DATABASE;
}
ALTER DATABASE OPEN RESETLOGS;
```

📌 `FROM SERVICE ORCL_STBY` — Primary kết nối vào Standby qua TNS alias, RMAN tạo backup sets trên Standby rồi stream về Primary. File backup `.bkp` **không được copy** sang Primary — Primary restore trực tiếp từ datafiles của Standby.

📌 Verify sau restore: không dùng `ls /u01/backup/rman/` trên Primary (không có file gì) — dùng query SQL hoặc xem RMAN output để confirm.

⚠️ **RMAN restore + RESETLOGS làm Standby mất sync** — Standby không thể tự resync lại sau khi Primary mở với RESETLOGS mới. Cần chạy thêm:

```bash
# Thử resync Standby tự động
ansible-playbook operations/restart_crashed_db.yml

# Nếu vẫn lỗi ORA-16700 → rebuild Standby từ đầu
ansible-playbook setup/setup_standby.yml
```

**4c — Thời gian ước tính**

| DB Size | Thời gian |
|---|---|
| ~5GB | 15-20 phút |
| ~10GB | 30-45 phút |
| ~20GB | 60-90 phút |

---

### PHASE 5 — VERIFY: Trạng thái sau recover

```sql
SELECT DATABASE_ROLE, OPEN_MODE, DB_UNIQUE_NAME FROM V$DATABASE;
SELECT NAME, OPEN_MODE FROM V$PDBS WHERE NAME != 'PDB$SEED';
SELECT PROCESS, STATUS, SEQUENCE# FROM V$MANAGED_STANDBY
WHERE PROCESS IN ('MRP0','RFS') ORDER BY PROCESS;
```

Output mong đợi sau **flashback** thành công:

```
=== DATABASE ===
PRIMARY          READ WRITE        ORCL         ← Primary
PHYSICAL STANDBY READ ONLY W/APPLY ORCL_STBY    ← Standby đã sync

=== MRP/RFS ===
MRP0    APPLYING_LOG    <seq>
RFS     IDLE            <seq>
```

---

## So sánh 2 phương pháp

| | Flashback | RMAN Restore |
|---|---|---|
| Tốc độ | ~2 phút | 30-90 phút |
| Yêu cầu | Restore point + Flashback logs | RMAN backup file |
| Standby sau recover | Tự sync (flashback cùng điểm) | Cần rebuild |
| Mất data | Không (về đúng restore point) | Có thể (tùy until_time) |
| Khi nào dùng | Luôn ưu tiên dùng trước | Khi disk chết / Flashback không có |

---

## Sau khi recover — checklist

```bash
# 1. Verify DG configuration
dgmgrl sys/Oracle_4U@ORCL "SHOW CONFIGURATION;"

# 2. Kiểm tra FSFO còn enabled không
dgmgrl sys/Oracle_4U@ORCL "SHOW FAST_START FAILOVER;"

# 3. Observer còn sống không
ps aux | grep dgmgrl
tail -20 /home/ubuntu/oracle-observer/observer_dgmgrl.log

# 4. Nếu FSFO bị DISABLED hoặc Observer chết
ansible-playbook install/install_observer.yml
```

# restart_crashed_db.yml — Khôi phục Oracle DB sau sự cố

## Mục đích

Khôi phục Oracle Database 21c sau **bất kỳ sự cố nào** mà không cần can thiệp thủ công.
Playbook chạy trên cả 2 node (195 + 196), tự detect trạng thái từng node và xử lý đúng.

```bash
ansible-playbook operations/restart_crashed_db.yml
```

---

## Khi nào chạy

- Sau khi test crash (`tests/crash_primary_test.yml` hoặc `tests/crash_standby_test.yml`)
- Sau khi FSFO failover xảy ra — con Primary cũ cần reinstate thành Standby
- Khi DB bị `ORA-01109` (database not open)
- Khi DB stuck ở trạng thái `MOUNTED` nhưng không OPEN
- Sau mất điện hoặc `systemctl stop oracle-db`

---

## Các trường hợp được xử lý

| Trạng thái phát hiện | Hành động |
|---|---|
| Instance chết hoàn toàn (pmon không tồn tại) | Start listener → `STARTUP MOUNT` → đợi Observer auto-reinstate 40s → OPEN READ ONLY → Open PDB → Start MRP → Reload listener |
| DB đang `MOUNTED` nhưng chưa OPEN | `ALTER DATABASE OPEN READ ONLY` → Open PDB → Start MRP |
| DB là `PRIMARY MOUNTED` (chưa reinstate) | Manual `REINSTATE DATABASE` qua dgmgrl → đợi 20s → OPEN READ ONLY → PDB → MRP |
| DB đang chạy bình thường | Skip — không làm gì |
| Listener down | `lsnrctl start` trước khi xử lý DB |

---

## 3 Phase thực thi

### PHASE 1 — ASSESS: Đánh giá tình trạng

Playbook kiểm tra lần lượt:

1. **pmon process** — có tồn tại không (`ps -ef | grep ora_pmon`)
2. **Listener** — có đang nhận kết nối không (`lsnrctl status`)
3. **Instance status** — `V$INSTANCE.STATUS` (STARTED / MOUNTED / OPEN)
4. **DB role + open mode** — `V$DATABASE.DATABASE_ROLE` + `OPEN_MODE` + `DB_UNIQUE_NAME`

Output ví dụ:

```
primarydb (192.168.1.195): INSTANCE DOWN | listener=DOWN
standbydb (192.168.1.196): PHYSICAL STANDBY | READ ONLY WITH APPLY | listener=UP
```

---

### PHASE 2 — FIX: Khôi phục từng thành phần

**2a — Start Listener** (chỉ khi listener down)

```sql
lsnrctl start
```

**2b — Startup Mount** (chỉ khi instance chết hoàn toàn)

```sql
STARTUP MOUNT;
```

**2c — Đợi Observer auto-reinstate (40 giây)**

Observer phát hiện DB lên MOUNTED → tự gọi `REINSTATE DATABASE` qua Broker.
Đây là cơ chế tự động của FSFO — không cần can thiệp.

📌 **Tại sao đợi 40 giây?**
Observer cần thời gian kết nối lại vào DB vừa mount và gửi lệnh reinstate.
Nếu không đợi, bước check tiếp theo sẽ thấy vẫn `PRIMARY MOUNTED` và trigger manual reinstate không cần thiết.

**2d — Re-check role sau reinstate**

Sau 40s, playbook query lại `V$DATABASE` để biết kết quả:
- `PHYSICAL STANDBY | MOUNTED` → tiếp tục OPEN
- `PRIMARY | MOUNTED` → Observer chưa reinstate → chuyển sang manual reinstate (2e)

**2e — Manual Reinstate** (chỉ khi vẫn PRIMARY MOUNTED)

```bash
dgmgrl sys/Oracle_4U@ORCL  "REINSTATE DATABASE <db_unique_name>;"
# hoặc thử qua alias kia nếu không connect được
dgmgrl sys/Oracle_4U@ORCL_STBY "REINSTATE DATABASE <db_unique_name>;"
```

⚠️ Playbook tự detect `DB_UNIQUE_NAME` từ `V$DATABASE` — không hardcode tên.
Thử connect qua cả `ORCL` và `ORCL_STBY` để đảm bảo thành công dù Primary đang ở node nào.

**2f — Open Database READ ONLY** (chỉ khi Standby đang MOUNTED)

```sql
ALTER DATABASE OPEN READ ONLY;
```

**2g — Open PDB** (chỉ khi là Standby)

```sql
ALTER PLUGGABLE DATABASE ALL OPEN READ ONLY;
```

**2h — Start MRP** (chỉ khi là Standby)

```sql
ALTER DATABASE RECOVER MANAGED STANDBY DATABASE
  USING CURRENT LOGFILE DISCONNECT FROM SESSION;
```

📌 **MRP = Managed Recovery Process** — process áp dụng redo logs từ Primary lên Standby theo real-time.
Nếu MRP không chạy, Standby sẽ bị lag và FSFO sẽ bị suspend khi lag > `FastStartFailoverLagLimit`.

**2i — Reload Listener**

```bash
lsnrctl reload
```

Sau RMAN duplicate hoặc reinstate, có thể có service mới chưa được đăng ký với listener.
`reload` nhẹ hơn `stop/start` — không drop connection hiện có.

---

### PHASE 3 — VERIFY: Hiển thị trạng thái cuối

```sql
SELECT DATABASE_ROLE, OPEN_MODE, DB_UNIQUE_NAME FROM V$DATABASE;

SELECT NAME, OPEN_MODE FROM V$PDBS WHERE NAME != 'PDB$SEED';

SELECT PROCESS, STATUS, SEQUENCE# FROM V$MANAGED_STANDBY
WHERE PROCESS IN ('MRP0','RFS') ORDER BY PROCESS;
```

Output mong đợi sau khôi phục thành công:

```
=== DATABASE ===
DATABASE_ROLE         OPEN_MODE                 DB_UNIQUE_NAME
PHYSICAL STANDBY      READ ONLY WITH APPLY      ORCL_STBY

=== PDB ===
NAME        OPEN_MODE
ORCLPDB1    READ ONLY

=== MRP/RFS ===
PROCESS    STATUS     SEQUENCE#
MRP0       APPLYING      125
RFS        IDLE          126
```

---

## Lưu ý quan trọng

⚠️ **Chạy playbook này TRƯỚC khi chạy lại `install_observer.yml`**

Sau failover, thứ tự đúng:
```bash
# 1. Khôi phục con crash thành Standby
ansible-playbook operations/restart_crashed_db.yml

# 2. Re-enable FSFO và restart Observer
ansible-playbook install/install_observer.yml
```

Nếu chạy `install_observer.yml` khi DB chưa OPEN, Observer sẽ không connect được và FSFO không enable thành công.

---

## Kiểm tra nhanh sau khi chạy

```bash
# Trên máy Observer (192.168.1.18)
dgmgrl sys/Oracle_4U@ORCL 'SHOW CONFIGURATION;'

# Kết quả mong đợi
# Configuration - DG_ORCL
#   Primary database   - ORCL
#   Physical standby   - ORCL_STBY
# Fast-Start Failover: ENABLED in Potential Data Loss Mode
# Configuration Status: SUCCESS
```

```bash
# Xem Observer log
tail -30 /home/ubuntu/oracle-observer/observer_dgmgrl.log
```

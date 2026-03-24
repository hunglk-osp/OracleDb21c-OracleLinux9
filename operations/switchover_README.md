# switchover.yml — Planned Switchover Oracle Data Guard

## Mục đích

Đảo vai trò **Primary ↔ Standby** theo kế hoạch (không mất data).
Dùng cho bảo trì server, patching OS, hoặc test định kỳ để đảm bảo Standby luôn sẵn sàng.

```bash
ansible-playbook operations/switchover.yml
```

Playbook tự detect ai đang là Primary/Standby — không hardcode 195 hay 196.

---

## Khi nào chạy

- Bảo trì hoặc patching server Primary
- Test định kỳ (khuyến nghị hàng tháng) để verify Standby hoạt động đúng
- Cân bằng lại vai trò sau khi FSFO failover tự động xảy ra (đưa Primary về node cũ)

## Yêu cầu trước khi chạy

- Cả 2 DB đang `OPEN` và đồng bộ
- `SHOW CONFIGURATION` trả về `SUCCESS`
- Apply lag gần 0 (playbook sẽ hiển thị ở PHASE 1)
- Observer đang chạy, FSFO đang `ENABLED`

---

## 6 Phase thực thi

### PHASE 1 — Detect vai trò từng node

Playbook query `V$DATABASE` trên cả 2 node, set facts `is_primary` / `is_standby`, và hiển thị apply lag hiện tại:

```
primarydb (192.168.1.195): PRIMARY|READ WRITE|ORCL
standbydb (192.168.1.196): PHYSICAL STANDBY|READ ONLY WITH APPLY|ORCL_STBY
standbydb: Apply lag = +00 00:00:00
```

📌 Apply lag = 0 là điều kiện lý tưởng trước switchover — đảm bảo không mất data.

---

### PHASE 2 — Disable FSFO + Stop Observer

**2a — Disable FSFO (chỉ Primary)**

```bash
dgmgrl sys/Oracle_4U@ORCL "DISABLE FAST_START FAILOVER FORCE;"
```

📌 Phải disable FSFO trước khi switchover. Nếu không, broker sẽ từ chối lệnh SWITCHOVER khi Observer đang active.
`FORCE` cho phép disable ngay cả khi Observer đang không kết nối được.

**2b — Stop Observer qua Broker (chỉ Primary)**

```bash
dgmgrl sys/Oracle_4U@ORCL "STOP OBSERVER ALL;"
```

**2c — Stop Observer process trên máy Observer (graceful)**

Đợi 10 giây để broker `STOP OBSERVER` take effect, sau đó stop wrapper loop bằng `kill` (không phải `kill -9`) qua PID file:

```bash
kill $(cat /home/ubuntu/oracle-observer/observer.pid)
```

📌 Chỉ stop wrapper loop — dgmgrl process đã được broker stop trước đó. Graceful stop đảm bảo không có dgmgrl process zombie còn sót khi Observer restart ở PHASE 5.

---

### PHASE 3 — Thực hiện SWITCHOVER

**Kiểm tra CONFIGURATION trước**

```bash
dgmgrl sys/Oracle_4U@ORCL "SHOW CONFIGURATION;"
```

📌 Sau khi disable FSFO + stop Observer, broker có thể hiển thị `ORA-16665` hoặc `ERROR` trong vài giây — đây là bình thường. Switchover vẫn proceed được.

**Thực hiện SWITCHOVER**

Playbook tự lấy `DB_UNIQUE_NAME` của Standby từ broker config rồi chạy:

```bash
dgmgrl sys/Oracle_4U@ORCL "SWITCHOVER TO ORCL_STBY;"
```

Quá trình switchover (~90 giây):
```
Performing switchover NOW, please wait...
Operation requires a connection to database "ORCL_STBY"
Connecting ...
New primary database "ORCL_STBY" is opening...
Operation requires start up of instance "ORCL" on database "ORCL"
Starting instance "ORCL"...
Database mounted.
Database opened.
Switchover succeeded, new primary is "orcl_stby"
Switchover processing complete, broker ready.
```

Sau đó pause 30 giây để roles ổn định.

---

### PHASE 3b — Startup Standby mới

Theo Oracle docs, broker tự restart và start MRP cho Standby mới sau switchover. Tuy nhiên trên standalone (không có Oracle Clusterware), broker chỉ đưa về MOUNTED — chưa OPEN READ ONLY và chưa start MRP.

Playbook tự xử lý:
1. Detect node nào vừa là Primary → instance down hoặc MOUNTED
2. `STARTUP MOUNT` nếu instance down → đợi 15s
3. `ALTER DATABASE OPEN READ ONLY`
4. `ALTER PLUGGABLE DATABASE ALL OPEN READ ONLY`
5. Start MRP (`ALTER DATABASE RECOVER MANAGED STANDBY DATABASE...`)
6. `lsnrctl reload`

---

### PHASE 4 — Verify trạng thái sau switchover

Query cả 2 node, output mong đợi:

```
=== DATABASE ===
DATABASE_ROLE         OPEN_MODE                 DB_UNIQUE_NAME
PHYSICAL STANDBY      READ ONLY WITH APPLY      ORCL          ← node cũ là Primary
PRIMARY               READ WRITE                ORCL_STBY     ← node cũ là Standby

=== PDB ===
ORCLPDB1    READ ONLY        ← trên Standby mới
ORCLPDB1    READ WRITE       ← trên Primary mới

=== MRP/RFS ===
MRP0    APPLYING_LOG    <seq>   ← trên Standby mới
```

---

### PHASE 5 — Re-enable FSFO + Restart Observer

**5a — Enable FSFO trên Primary mới**

Detect lại Primary mới, check Flashback (enable nếu chưa ON), rồi re-enable FSFO:

```bash
dgmgrl sys/Oracle_4U@ORCL_STBY << EOF
EDIT CONFIGURATION SET PROPERTY FastStartFailoverThreshold = 30;
EDIT CONFIGURATION SET PROPERTY FastStartFailoverLagLimit = 30;
EDIT CONFIGURATION SET PROPERTY FastStartFailoverAutoReinstate = TRUE;
EDIT CONFIGURATION SET PROPERTY FastStartFailoverPmyShutdown = FALSE;
ENABLE FAST_START FAILOVER;
EOF
```

**5b — Restart Observer trên máy Observer (192.168.1.18)**

Chạy lại `start_observer.sh` — script tự detect Primary mới (ORCL_STBY trên 196) và connect vào đúng node:

```bash
/home/ubuntu/oracle-observer/start_observer.sh
```

Đợi 40 giây → verify process → hiển thị `observer_dgmgrl.log`.

**5c — Final verify**

```bash
dgmgrl sys/Oracle_4U@ORCL_STBY "SHOW CONFIGURATION;"
dgmgrl sys/Oracle_4U@ORCL_STBY "SHOW FAST_START FAILOVER;"
```

Output mong đợi:

```
Configuration - DG_ORCL
  ORCL_STBY - Primary database
    ORCL      - (*) Physical standby database

Fast-Start Failover: Enabled in Potential Data Loss Mode
Configuration Status: SUCCESS

Active Target:   ORCL
Observer:        observer_main
Auto-reinstate:  TRUE
```

---

## Kiểm tra nhanh sau khi chạy

```bash
# Trên máy Observer (192.168.1.18)
dgmgrl sys/Oracle_4U@ORCL_STBY 'SHOW CONFIGURATION;'

# Observer log
tail -20 /home/ubuntu/oracle-observer/observer_dgmgrl.log
```

---

## Sau switchover — thứ tự vai trò đã đổi

| Node | Trước | Sau |
|---|---|---|
| 192.168.1.195 (primarydb) | PRIMARY | PHYSICAL STANDBY |
| 192.168.1.196 (standbydb) | PHYSICAL STANDBY | PRIMARY |

Muốn đưa về trạng thái ban đầu (195 = Primary) thì chạy lại:
```bash
ansible-playbook operations/switchover.yml
```

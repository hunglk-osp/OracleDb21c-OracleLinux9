# Oracle 21c Data Guard + FSFO — Ansible Automation

## Kiến trúc

```
┌──────────────────────┐         ┌──────────────────────┐
│  192.168.1.195       │         │  192.168.1.196       │
│  Oracle DB (ORCL)    │◄──────►│  Oracle DB (ORCL_STBY)│
│  PRIMARY / STANDBY   │  Redo   │  PRIMARY / STANDBY   │
└──────────┬───────────┘  Logs   └──────────┬───────────┘
           │                                │
           └──────── Observer ──────────────┘
                 192.168.1.18
                 (Ubuntu — máy thứ 3)
```

**Primary/Standby KHÔNG cố định.** Sau mỗi lần failover, 2 con đổi vai cho nhau.
Tất cả playbook tự detect ai là Primary/Standby — chạy bao nhiêu lần cũng OK.

| Role     | Hostname   | IP             | DB_UNIQUE_NAME |
|----------|------------|----------------|----------------|
| DB Node  | primarydb  | 192.168.1.195  | ORCL           |
| DB Node  | standbydb  | 192.168.1.196  | ORCL_STBY      |
| Observer | observerdb | 192.168.1.18   | —              |

---

## Cấu trúc thư mục

```
OracleDb21c-OracleLinux9/
├── inventory.ini                          # Ansible inventory (3 máy)
├── ansible.cfg                            # Ansible config
├── README.md
│
├── install/                               # Cài đặt từ đầu
│   ├── install_oracle21c_primary.yml      #   Cài Oracle 21c + tạo DB trên 195
│   ├── install_oracle21c_standby.yml      #   Cài Oracle 21c (software only) trên 196
│   └── install_observer.yml               #   Cài Instant Client + Enable FSFO + Start Observer
│
├── setup/                                 # Cấu hình Data Guard
│   ├── setup_primary.yml                  #   Cấu hình DG trên Primary (archivelog, SRL, broker)
│   └── setup_standby.yml                  #   RMAN duplicate + bật MRP trên Standby
│
├── operations/                            # Vận hành
│   ├── restart_crashed_db.yml             #   Khôi phục DB sau sự cố (crash/stop/mất điện)
│   └── cleanup_oracle21c.yml              #   Xóa toàn bộ Oracle
│
└── tests/                                 # Test failover + load test
    ├── crash_primary_test.yml             #   Crash con PRIMARY (tự detect, kill -9)
    ├── crash_standby_test.yml             #   Crash con STANDBY (tự detect, kill -9)
    ├── load_test.py                       #   Load test insert + auto-failover
    ├── check_sync.py                      #   Check đồng bộ giữa 2 host
    ├── check_dg_status.py                 #   Check Data Guard status
    └── oracle_demo.py                     #   Demo CRUD cơ bản
```

---

## Cài đặt từ đầu

Chạy từ máy 18 (observer), theo thứ tự:

```bash
cd /home/ubuntu/rnd/OracleDb21c-OracleLinux9

# 1. Cài Oracle 21c trên Primary (45-60 phút)
ansible-playbook install/install_oracle21c_primary.yml

# 2. Cài Oracle 21c trên Standby (20-30 phút)
ansible-playbook install/install_oracle21c_standby.yml

# 3. Setup Data Guard — Primary
ansible-playbook setup/setup_primary.yml

# 4. Setup Data Guard — Standby (RMAN duplicate, 20-60 phút)
ansible-playbook setup/setup_standby.yml

# 5. Finish Broker trên Primary
ansible-playbook setup/setup_primary.yml --tags broker

# 6. Enable FSFO + Start Observer
ansible-playbook install/install_observer.yml
```

---

## Vận hành

### Khôi phục DB sau sự cố

Khi DB bị crash (kill -9), stop (systemctl stop), hoặc mất điện:

```bash
ansible-playbook operations/restart_crashed_db.yml
```

Playbook tự xử lý **mọi trường hợp**:

| Trạng thái | Hành động |
|---|---|
| Instance chết hoàn toàn | Start listener → STARTUP MOUNT → đợi reinstate → OPEN READ ONLY → PDB → MRP |
| DB MOUNTED nhưng chưa OPEN | ALTER DATABASE OPEN READ ONLY → PDB → MRP |
| DB chưa reinstate (vẫn PRIMARY MOUNTED) | Manual REINSTATE qua broker → OPEN → PDB → MRP |
| DB đang chạy bình thường | Skip |

### Cleanup

```bash
# Xóa cả 2 máy
ansible-playbook operations/cleanup_oracle21c.yml

# Chỉ xóa 1 máy
ansible-playbook operations/cleanup_oracle21c.yml --limit primary
ansible-playbook operations/cleanup_oracle21c.yml --limit standby
```

---

## Test Failover — 3 Kịch bản

Cần **2 terminal** trên máy 18.

### Kịch bản 1: Load test bình thường + check đồng bộ

```bash
# Terminal 1
uv run tests/load_test.py
```

Kết quả: Insert 120s, không lỗi. Cả 2 host có **cùng số row**.

---

### Kịch bản 2: Tắt Standby giữa lúc load test

```bash
# Terminal 1
uv run tests/load_test.py

# Terminal 2 (đợi ~20s)
ansible-playbook tests/crash_standby_test.yml

# Quan sát: load test KHÔNG bị ảnh hưởng

# Terminal 2 (bật lại)
ansible-playbook operations/restart_crashed_db.yml
```

Kết quả: Load test không gián đoạn. Con crash restart thành Standby, data đồng bộ.

---

### Kịch bản 3: Tắt Primary giữa lúc load test — FSFO failover

```bash
# Terminal 1
uv run tests/load_test.py

# Terminal 2 (đợi ~20s)
ansible-playbook tests/crash_primary_test.yml

# Quan sát: load test DOWN ~30-40s → tự failover sang host còn lại
# Hiển thị: Failover #1: 192.168.1.X → 192.168.1.Y

# Sau khi load test xong — Terminal 2:
ansible-playbook operations/restart_crashed_db.yml    # reinstate con crash
ansible-playbook install/install_observer.yml         # re-enable FSFO + observer
```

---

### Kịch bản 3b: Đổi qua đổi lại (round-trip)

Chạy kịch bản 3 **hai lần liên tiếp**:

```
Lần 1: 195=Primary → crash → 196 lên PRIMARY
Lần 2: 196=Primary → crash → 195 lên PRIMARY
```

Sau mỗi lần:
```bash
ansible-playbook operations/restart_crashed_db.yml
ansible-playbook install/install_observer.yml
```

Rồi lặp lại. Tất cả playbook tự detect — không cần sửa gì.

---

## FSFO Properties

| Property | Value | Giải thích |
|---|---|---|
| FastStartFailoverThreshold | 30s | Thời gian chờ trước khi trigger failover |
| FastStartFailoverLagLimit | 30s | Suspend FSFO nếu redo lag > 30s |
| FastStartFailoverAutoReinstate | TRUE | Tự reinstate old Primary thành Standby |
| FastStartFailoverPmyShutdown | FALSE | Trigger failover cả khi shutdown clean |
| CommunicationTimeout | 15s | Timeout kết nối Observer ↔ DB |

---

## Thông tin kết nối

```
Port         : 1521
CDB SID      : ORCL
PDB Service  : orclpdb1
User         : osp / Osp@123
SYS Password : Oracle_4U
```

---

## Monitoring

```bash
# Observer log (máy 18)
tail -f /home/ubuntu/oracle-observer/observer_dgmgrl.log

# Broker status (SSH vào bất kỳ con nào đang chạy)
dgmgrl sys/Oracle_4U@ORCL 'SHOW CONFIGURATION;'
dgmgrl sys/Oracle_4U@ORCL_STBY 'SHOW CONFIGURATION;'

# Alert log
tail -f /u01/app/oracle/diag/rdbms/orcl/ORCL/trace/alert_ORCL.log
tail -f /u01/app/oracle/diag/rdbms/orcl_stby/ORCL/trace/alert_ORCL.log
```

---

## Troubleshooting

**Observer không start / ORA-16814:**
```bash
ssh ubuntu@192.168.1.18 "sudo killall -9 dgmgrl start_observer.sh sleep"
dgmgrl sys/Oracle_4U@ORCL 'STOP OBSERVER ALL;'
dgmgrl sys/Oracle_4U@ORCL_STBY 'STOP OBSERVER ALL;'
ansible-playbook install/install_observer.yml
```

**FSFO suspended (ORA-16820):**
```bash
ansible-playbook install/install_observer.yml
```

**DB bị ORA-01109 (database not open):**
```bash
ansible-playbook operations/restart_crashed_db.yml
```

**Sau failover, con crash không tự reinstate:**
```bash
# Chạy restart playbook (có manual reinstate)
ansible-playbook operations/restart_crashed_db.yml

# Hoặc manual từ Primary hiện tại:
dgmgrl sys/Oracle_4U@<PRIMARY_TNS> 'REINSTATE DATABASE <DB_NAME>;'
```

# Oracle Database 21c + Data Guard on Oracle Linux 9

## Servers

| Role    | Hostname     | IP             | DB_UNIQUE_NAME |
|---------|--------------|----------------|----------------|
| Primary | primarydb    | 192.168.1.195  | ORCL           |
| Standby | standbydb    | 192.168.1.196  | ORCL_STBY      |

---

## Files

```
install_oracle21c_primary.yml  - Cài Oracle software + tạo DB trên Primary
install_oracle21c_standby.yml  - Cài Oracle software (không tạo DB) trên Standby
setup_primary.yml              - Cấu hình DG trên Primary (archivelog, SRL, broker, copy pwdfile)
setup_standby.yml              - RMAN duplicate + bật MRP trên Standby
cleanup_oracle21c.yml          - Xóa toàn bộ Oracle trên 1 hoặc 2 máy
inventory.ini                  - Ansible inventory
ansible.cfg                    - Ansible configuration
LINUX.X64_213000_db_home.zip   - Oracle 21c installer (phải có sẵn trên từng máy)
```

---

## Yêu cầu

- Oracle Linux 9 (x86_64) — Minimal Install
- RAM tối thiểu 8GB (khuyến nghị 16GB)
- Disk tối thiểu 40GB cho `/u01`
- File `LINUX.X64_213000_db_home.zip` có sẵn tại `/root/OracleDb21c-OracleLinux9/` trên **từng máy**

---

## Thứ tự chạy

### Bước 1 — Cài Primary (192.168.1.195)

```bash
ansible-playbook -i inventory.ini install_oracle21c_primary.yml --limit primary
```

Thực hiện: cài OS packages, Oracle software, tạo CDB+PDB, listener, systemd service.
Thời gian: ~45-60 phút.

---

### Bước 2 — Cài Standby (192.168.1.196)

```bash
ansible-playbook -i inventory.ini install_oracle21c_standby.yml --limit standby
```

Thực hiện: cài OS packages, Oracle software (chỉ software, **không tạo DB**), listener, init.ora tối thiểu.
Thời gian: ~20-30 phút.

---

### Bước 3 — Setup Data Guard trên Primary

```bash
ansible-playbook -i inventory.ini setup_primary.yml --limit primary \
  -e "standby_root_password=<PASSWORD_ROOT_MAY_196>"
```

Thực hiện:
- Generate SSH key trên Primary + copy sang Standby (dùng `standby_root_password` 1 lần duy nhất)
- Bật ARCHIVELOG mode + Force Logging + Flashback
- Tạo Standby Redo Logs
- Cấu hình tnsnames.ora + listener.ora
- Set DG_BROKER_CONFIG_FILE trong SPFILE
- Copy password file sang Standby qua `scp`

---

### Bước 4 — Setup Standby (RMAN Duplicate)

```bash
ansible-playbook -i inventory.ini setup_standby.yml --limit standby
```

Thực hiện:
- Cấu hình tnsnames.ora + listener.ora trên Standby
- Startup NOMOUNT
- RMAN Duplicate từ Primary (20-60 phút)
- Set DG_BROKER_CONFIG_FILE trong SPFILE của Standby
- Add Standby Redo Logs
- Bật DG_BROKER_START + MRP

---

### Bước 5 — Tạo DG Broker Configuration (trên Primary)

```bash
ansible-playbook -i inventory.ini setup_primary.yml --limit primary --tags broker
```

Thực hiện: tạo Broker config, ADD standby database, ENABLE CONFIGURATION, SHOW CONFIGURATION.

---

## Kiểm tra sau khi hoàn tất

```bash
# Trên Primary
su - oracle -c "dgmgrl sys/Oracle_4U@ORCL 'show configuration'"
su - oracle -c "dgmgrl sys/Oracle_4U@ORCL 'show database verbose ORCL'"
su - oracle -c "dgmgrl sys/Oracle_4U@ORCL 'show database verbose ORCL_STBY'"
```

Kết quả mong đợi:
```
Configuration - DG_ORCL
  Protection Mode: MaxPerformance
  Members:
  ORCL      - Primary database
  ORCL_STBY - Physical standby database

Fast-Start Failover:  Disabled
Configuration Status: SUCCESS
```

---

## Switchover

```bash
su - oracle -c "dgmgrl sys/Oracle_4U@ORCL 'validate database verbose ORCL_STBY'"
su - oracle -c "dgmgrl sys/Oracle_4U@ORCL 'switchover to ORCL_STBY'"
su - oracle -c "dgmgrl sys/Oracle_4U@ORCL 'show configuration'"
```

---

## Cleanup

```bash
# Xóa cả 2 máy
ansible-playbook -i inventory.ini cleanup_oracle21c.yml

# Chỉ xóa Primary
ansible-playbook -i inventory.ini cleanup_oracle21c.yml --limit primary

# Chỉ xóa Standby
ansible-playbook -i inventory.ini cleanup_oracle21c.yml --limit standby
```

---

## Quản lý hàng ngày

```bash
# Start/Stop database
systemctl start oracle-db
systemctl stop oracle-db
systemctl status oracle-db

# Listener
su - oracle -c "lsnrctl status"
su - oracle -c "lsnrctl start"

# Login SYSDBA
su - oracle -c "sqlplus / as sysdba"

# Kiểm tra PDB
su - oracle -c "sqlplus / as sysdba" << 'EOF'
SHOW PDBS;
EXIT;
EOF

# Test connection
su - oracle -c "sqlplus chirag/Tiger123@localhost:1521/orclpdb1"
```

---

## Thông tin kết nối (mặc định)

```
Port         : 1521
CDB SID      : ORCL
PDB Service  : orclpdb1
User         : chirag / Tiger123
SYS Password : Oracle_4U
```

### Kết nối DBeaver / SQL Client

| | Primary | Standby |
|---|---|---|
| Host | 192.168.1.195 | 192.168.1.196 |
| Port | 1521 | 1521 |
| SID (CDB) | ORCL | ORCL |
| Service (PDB) | orclpdb1 | orclpdb1 |
| User thường | chirag / Tiger123 | chirag / Tiger123 (READ ONLY) |
| SYS | sys / Oracle_4U (SYSDBA) | sys / Oracle_4U (SYSDBA) |

> **Lưu ý Standby:** Connect vào CDB (`SID=ORCL`) trước, sau đó chạy `ALTER SESSION SET CONTAINER = ORCLPDB1` để xem data của `chirag`.

---

## Kiến trúc CDB / PDB

```
CDB (Container Database) = ORCL
├── CDB$ROOT        ← SYS login vào đây (SID=ORCL)
├── PDB$SEED        ← Template tạo PDB mới
└── ORCLPDB1        ← Database thật, chứa data ứng dụng
      └── Schema CHIRAG
            └── Table: employees, ...
```

- **SYS** connect vào CDB → dùng `ALTER SESSION SET CONTAINER = ORCLPDB1` để vào PDB
- **chirag** connect thẳng vào PDB qua service `orclpdb1`
- Standby PDB luôn ở `READ ONLY`, không thể ghi

---

## Python Demo Scripts

```bash
cd /home/ubuntu/rnd/OracleDb21c-OracleLinux9

# Demo CRUD cơ bản (connect Primary, tạo table, insert/update/delete, stored procedure)
uv run oracle_demo.py

# Kiểm tra sync giữa Primary và Standby
uv run check_sync.py
```

Dependencies: `oracledb` (cài bằng `uv add oracledb`)

---

## Data Guard — Lưu ý quan trọng

### Listener trên Standby
Standby **không tự động** có service `orclpdb1` sau khi setup. Nếu cần connect trực tiếp vào PDB của Standby, chạy lại tag network:

```bash
ansible-playbook setup_standby.yml --tags network
ansible-playbook setup_standby.yml --tags broker
```

Hoặc thủ công trên Standby:
```bash
su - oracle
sqlplus / as sysdba
```
```sql
ALTER PLUGGABLE DATABASE orclpdb1 OPEN READ ONLY;
ALTER PLUGGABLE DATABASE orclpdb1 SAVE STATE;
EXIT;
```
```bash
lsnrctl reload
```

### Kiểm tra sync từ Primary
```sql
-- Xem archive log đang ship sang Standby
SELECT DEST_ID, STATUS, TARGET, DESTINATION, ERROR
FROM V$ARCHIVE_DEST
WHERE TARGET = 'STANDBY' AND STATUS != 'INACTIVE';

-- Sequence đang ở đâu
SELECT THREAD#, MAX(SEQUENCE#) FROM V$LOG GROUP BY THREAD#;
```

---

## Troubleshooting

```bash
# Ansible log
tail -f ./ansible.log

# Alert log Primary
tail -f /u01/app/oracle/diag/rdbms/orcl/ORCL/trace/alert_ORCL.log

# Alert log Standby
tail -f /u01/app/oracle/diag/rdbms/orcl_stby/ORCL/trace/alert_ORCL.log

# Kiểm tra MRP trên Standby
su - oracle -c "sqlplus / as sysdba" << 'EOF'
SELECT PROCESS, STATUS, SEQUENCE# FROM V$MANAGED_STANDBY WHERE PROCESS IN ('MRP0','RFS');
EXIT;
EOF

# Apply lag / Transport lag
su - oracle -c "dgmgrl sys/Oracle_4U@ORCL 'show database ORCL_STBY'"
```
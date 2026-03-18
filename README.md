# Oracle Database 21c Installation for Oracle Linux 9
Production-ready Ansible playbook for installing Oracle Database 21c on Oracle Linux 9.

## Files
```
install_oracle21c_FINAL.yml  - Main installation playbook
setup_primary.yml            - Data Guard: cấu hình Primary (192.168.1.195)
setup_standby.yml            - Data Guard: cấu hình Standby (192.168.1.196)
cleanup_oracle21c.yml        - Complete cleanup playbook
inventory.ini                - Ansible inventory
ansible.cfg                  - Ansible configuration
LINUX.X64_213000_db_home.zip - Oracle 21c installer (not included)
```

## Prerequisites
- Oracle Linux 9 (x86_64) - Minimal Install
- Minimum 8GB RAM (16GB recommended)
- Minimum 40GB disk space for /u01
- Oracle Database 21c ZIP file

## Quick Start - Fresh Install
```bash
# 1. Install Ansible
dnf install -y ansible-core

# 2. Place ZIP file in same directory as playbook
ls LINUX.X64_213000_db_home.zip

# 3. Run installation
ansible-playbook install_oracle21c_FINAL.yml

# Installation takes ~45-60 minutes
```

## Connection Info (Default)
```
Hostname: <auto-detected>
Port: 1521
CDB: ORCL
PDB: orclpdb1
User: chirag
Password: Tiger123
SYS Password: Oracle_4U (SYSDBA role)
```

## Customize Installation
Create a custom vars file:
```bash
cat > my_vars.yml << EOF
hostname_fqdn: mydb.example.com
oracle_sid: PROD
pdb_name: PRODPDB
db_username: appuser
db_user_password: "MySecurePass123"
sys_password: "SysSecure456"
total_memory: 4096
EOF

# Run with custom vars
ansible-playbook install_oracle21c_FINAL.yml -e @my_vars.yml
```

## Run Specific Steps
```bash
# Only OS preparation
ansible-playbook install_oracle21c_FINAL.yml --tags "os_prep"

# Only install software (skip DB creation)
ansible-playbook install_oracle21c_FINAL.yml --tags "install"

# Only create database
ansible-playbook install_oracle21c_FINAL.yml --tags "database"

# Only setup auto-start
ansible-playbook install_oracle21c_FINAL.yml --tags "autostart"
```

---

## Data Guard Setup

Cấu hình Oracle Data Guard Physical Standby giữa 2 server:

| | Primary | Standby |
|---|---|---|
| IP | 192.168.1.195 | 192.168.1.196 |
| DB_UNIQUE_NAME | ORCL | ORCL_STBY |
| DB_NAME | ORCL | ORCL |

### Yêu cầu
- `install_oracle21c_FINAL.yml` đã chạy xong trên **cả 2 server**
- Cả 2 server có thể ping nhau qua port 1521

### Bước 1 — Chạy trên Primary (192.168.1.195)
```bash
ansible-playbook setup_primary.yml
```
> Lần đầu chạy, playbook sẽ hỏi password SSH của server 196 để setup SSH key.
> Các lần sau không cần nhập gì thêm.

Playbook này thực hiện:
- Bật ARCHIVELOG mode
- Bật Force Logging + Flashback
- Tạo Fast Recovery Area
- Tạo Standby Redo Logs
- Cấu hình Data Guard init parameters
- Cấu hình tnsnames.ora + listener.ora
- Copy password file sang Standby (192.168.1.196) qua SSH

### Bước 2 — Chạy trên Standby (192.168.1.196)
```bash
ansible-playbook setup_standby.yml
```

Playbook này thực hiện:
- Verify password file đã nhận từ Primary
- Cấu hình tnsnames.ora + listener.ora
- Shutdown DB, startup NOMOUNT
- RMAN Duplicate từ Primary (20-60 phút)
- Bật DG Broker
- Tạo Broker configuration
- Validate toàn bộ cấu hình

### Kiểm tra sau khi hoàn tất
```bash
# Trên bất kỳ server nào
su - oracle -c "dgmgrl / " << EOF
SHOW CONFIGURATION;
SHOW DATABASE VERBOSE 'ORCL';
SHOW DATABASE VERBOSE 'ORCL_STBY';
EOF
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

### Switchover (chuyển đổi có kiểm soát)
```bash
su - oracle -c "dgmgrl / " << EOF
-- Kiểm tra trước khi switchover
VALIDATE DATABASE VERBOSE 'ORCL_STBY';

-- Thực hiện switchover
SWITCHOVER TO 'ORCL_STBY';

-- Kiểm tra sau switchover
SHOW CONFIGURATION;
EOF
```

### Run Specific Data Guard Steps
```bash
# Chỉ setup SSH key
ansible-playbook setup_primary.yml --tags "ssh_setup"

# Chỉ bật archivelog
ansible-playbook setup_primary.yml --tags "archivelog"

# Chỉ chạy RMAN duplicate
ansible-playbook setup_standby.yml --tags "rman"

# Chỉ validate
ansible-playbook setup_standby.yml --tags "validate"
```

---

## Complete Cleanup
```bash
# Remove everything (keeps ZIP file)
ansible-playbook cleanup_oracle21c.yml

# After cleanup, you can run fresh install again
ansible-playbook install_oracle21c_FINAL.yml
```

## Management Commands
```bash
# Start/Stop database
systemctl start oracle-db
systemctl stop oracle-db
systemctl status oracle-db

# Check listener
su - oracle -c "lsnrctl status"

# Login as SYSDBA
su - oracle -c "sqlplus / as sysdba"

# Check PDB status
su - oracle -c "sqlplus / as sysdba" << EOF
SHOW PDBS;
EXIT;
EOF

# Test connection
su - oracle -c "sqlplus chirag/Tiger123@localhost:1521/orclpdb1"
```

## SQL Developer Connection
```
Connection Type: Basic
Hostname: <your-server-ip>
Port: 1521
Service name: orclpdb1
Username: chirag
Password: Tiger123
```

## Features
✅ Fully idempotent - safe to run multiple times
✅ Automatic IP detection - portable across servers
✅ Complete error handling and validation
✅ Auto-start on system boot
✅ Production-ready configuration
✅ Silent installation (no GUI required)
✅ Data Guard Physical Standby support

## Troubleshooting
### Check installation logs
```bash
# Ansible log
tail -f /var/log/oracle_install/ansible.log

# Database alert log
tail -f /u01/app/oracle/diag/rdbms/orcl/ORCL/trace/alert_ORCL.log

# Listener log
tail -f /u01/app/oracle/diag/tnslsnr/oracledb/listener/alert/log.xml
```

### Verify database status
```bash
su - oracle -c "
sqlplus / as sysdba << EOF
SELECT status FROM v\$instance;
SELECT name, open_mode FROM v\$pdbs;
EXIT;
EOF
"
```

### Check listener services
```bash
su - oracle -c "lsnrctl status" | grep -i service
```

### Data Guard troubleshooting
```bash
# Kiểm tra apply lag
su - oracle -c "dgmgrl / " << EOF
SHOW DATABASE 'ORCL_STBY' 'ApplyLag';
SHOW DATABASE 'ORCL_STBY' 'TransportLag';
EOF

# Kiểm tra MRP process trên Standby
su - oracle -c "sqlplus / as sysdba" << EOF
SELECT PROCESS, STATUS, SEQUENCE# FROM V\$MANAGED_STANDBY WHERE PROCESS='MRP0';
EOF

# Xem alert log Data Guard
tail -100 /u01/app/oracle/diag/rdbms/orcl_stby/ORCL/trace/alert_ORCL.log
```

## Files Modified by Installation
- `/etc/oratab` - Database registry
- `/etc/systemd/system/oracle-db.service` - Auto-start service
- `/etc/hosts` - Hostname resolution
- `/root/.bash_profile` - Oracle environment
- `/home/oracle/.bash_profile` - Oracle user environment

## License
Oracle Database software is licensed by Oracle Corporation.
This Ansible playbook is provided as-is for automation purposes.

## Author
Created: March 2026
Version: 1.1 - Data Guard Support

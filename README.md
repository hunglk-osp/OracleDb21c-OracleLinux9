# Oracle Database 21c Installation for Oracle Linux 9

Production-ready Ansible playbook for installing Oracle Database 21c on Oracle Linux 9.

## Files
```
install_oracle21c_FINAL.yml  - Main installation playbook
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
Version: 1.0 - Production Ready

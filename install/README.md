# install/ — Cài đặt Oracle 21c + Observer

## Thứ tự chạy

```bash
cd /home/ubuntu/rnd/OracleDb21c-OracleLinux9

# 1. Cài Oracle 21c trên Primary (195) — 45-60 phút
ansible-playbook install/install_oracle21c_primary.yml

# 2. Cài Oracle 21c trên Standby (196) — 20-30 phút
ansible-playbook install/install_oracle21c_standby.yml

# 3. Enable FSFO + Start Observer (sau khi setup/ xong)
ansible-playbook install/install_observer.yml
```

## Chi tiết từng file

### install_oracle21c_primary.yml

Cài Oracle 21c trên máy 195 (Primary):
- Cài OS packages (oracle-database-preinstall, ...)
- Tạo user oracle, group oinstall/dba
- Cài Oracle software từ `LINUX.X64_213000_db_home.zip`
- Tạo CDB (ORCL) + PDB (ORCLPDB1) bằng DBCA
- Cấu hình listener + systemd service
- Set DG_BROKER_CONFIG_FILE

**Yêu cầu:** File `LINUX.X64_213000_db_home.zip` phải có sẵn tại `/root/OracleDb21c-OracleLinux9/` trên máy 195.

### install_oracle21c_standby.yml

Cài Oracle 21c trên máy 196 (Standby) — **chỉ software, không tạo DB**:
- Cài OS packages
- Cài Oracle software
- Cấu hình listener (có cả ORCL + ORCL_STBY entries cho FSFO)
- Tạo init.ora tối thiểu cho RMAN duplicate
- Set DG_BROKER_CONFIG_FILE

**Yêu cầu:** File `LINUX.X64_213000_db_home.zip` phải có sẵn tại `/root/OracleDb21c-OracleLinux9/` trên máy 196.

### install_observer.yml

Enable FSFO + cài Observer trên máy 18:
- **PLAY 1** (chạy trên cả 195 + 196): Detect Primary động → enable Flashback → set FSFO properties → enable FSFO → stop observer cũ
- **PLAY 2** (chạy trên 18): Cài Oracle Instant Client + dgmgrl → tạo tnsnames.ora → start Observer
- **PLAY 3** (chạy trên cả 195 + 196): Verify FSFO configuration

Chạy lại bất cứ lúc nào cần re-enable FSFO hoặc restart Observer (sau failover).

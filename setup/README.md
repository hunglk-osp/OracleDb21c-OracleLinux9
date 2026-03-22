# setup/ — Cấu hình Data Guard

## Thứ tự chạy

Chạy sau khi `install/` xong:

```bash
cd /home/ubuntu/rnd/OracleDb21c-OracleLinux9

# 1. Setup Data Guard trên Primary
ansible-playbook setup/setup_primary.yml

# 2. Setup Data Guard trên Standby (RMAN duplicate — 20-60 phút)
ansible-playbook setup/setup_standby.yml

# 3. Finish Broker (chạy lại setup_primary với tag broker)
ansible-playbook setup/setup_primary.yml --tags broker
```

## Chi tiết từng file

### setup_primary.yml

Cấu hình Data Guard trên Primary (195):
- Bật ARCHIVELOG mode + Force Logging
- Enable Flashback Database
- Cấu hình FRA (Fast Recovery Area)
- Tạo Standby Redo Logs
- Cấu hình tnsnames.ora + listener.ora (có ORCL_DGMGRL cho Broker)
- Set DG_BROKER_CONFIG_FILE trong SPFILE
- Copy password file sang Standby

**Tag `broker`** (chạy riêng sau khi Standby xong):
- Tạo DG Broker Configuration
- ADD Standby database vào Broker
- ENABLE CONFIGURATION
- SHOW CONFIGURATION

```bash
# Chạy đầy đủ
ansible-playbook setup/setup_primary.yml

# Chỉ chạy phần broker
ansible-playbook setup/setup_primary.yml --tags broker
```

### setup_standby.yml

Setup Standby (196) bằng RMAN Duplicate:
- Cấu hình tnsnames.ora
- Cấu hình listener.ora (có cả ORCL + ORCL_STBY + DGMGRL entries cho FSFO)
- Tạo FRA directory
- STARTUP NOMOUNT → RMAN DUPLICATE FROM ACTIVE DATABASE (20-60 phút)
- Set DG_BROKER_CONFIG_FILE
- Add Standby Redo Logs
- Enable DG_BROKER_START + Start MRP
- Open PDB READ ONLY

```bash
# Tags khả dụng
ansible-playbook setup/setup_standby.yml                # chạy đầy đủ
ansible-playbook setup/setup_standby.yml --tags network  # chỉ cấu hình network
ansible-playbook setup/setup_standby.yml --tags rman     # chỉ RMAN duplicate
ansible-playbook setup/setup_standby.yml --tags broker   # chỉ broker + MRP
```

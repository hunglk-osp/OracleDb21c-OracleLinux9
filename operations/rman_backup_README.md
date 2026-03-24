# rman_backup.yml — RMAN Backup từ Standby

## Mục đích

Backup Oracle Database 21c bằng RMAN **từ Standby** — offload I/O khỏi Primary, không ảnh hưởng production workload.
Tự detect node nào đang là Standby rồi chạy backup trên đó.

```bash
ansible-playbook operations/rman_backup.yml
```

---

## Khi nào chạy

- Backup định kỳ hàng ngày (cron) — offload I/O khỏi Primary
- Sau khi setup lần đầu (`--tags setup`) để configure RMAN retention policy
- Khi cần purge archive logs cũ thủ công (`--tags purge`)

**Không dùng playbook này cho:**
- Restore/recover → dùng `recover_database.yml`
- Backup khi không có Standby (Primary only) → sửa `when: is_standby` thành `is_primary`

---

## Tags

| Tag | Lệnh | Tác dụng |
|---|---|---|
| `setup` | `--tags setup` | Tạo thư mục + configure RMAN (chạy 1 lần đầu) |
| `backup` | `--tags backup` | Chạy backup + purge obsolete + verify (dùng cho cron) |
| `purge` | `--tags purge` | Chỉ purge archive logs > `archive_keep_days` ngày |
| *(không có)* | *(không có --tags)* | Chạy tất cả: setup + backup + purge + verify |

---

## Ví dụ sử dụng

```bash
# Lần đầu — configure RMAN rồi backup luôn
ansible-playbook operations/rman_backup.yml

# Chỉ backup (dùng cho cron hàng ngày)
ansible-playbook operations/rman_backup.yml --tags backup

# Chỉ purge archive logs cũ
ansible-playbook operations/rman_backup.yml --tags purge
```

---

## Tham số có thể chỉnh

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `backup_dir` | `/u01/backup/rman` | Thư mục lưu backup files |
| `retention_days` | `7` | Giữ backup trong bao nhiêu ngày (RECOVERY WINDOW) |
| `archive_keep_days` | `3` | Xóa archive logs cũ hơn bao nhiêu ngày |

Chỉnh qua `-e`:

```bash
ansible-playbook operations/rman_backup.yml \
  -e "retention_days=14" \
  -e "archive_keep_days=5" \
  --tags backup
```

---

## 5 Phase thực thi

### PHASE 1 — DETECT: Xác định Standby

Query `V$DATABASE` trên cả 2 node, chỉ node là `PHYSICAL STANDBY` mới tiếp tục.

Output ví dụ:
```
primarydb (192.168.1.195): PRIMARY|ORCL — PRIMARY → skip
standbydb (192.168.1.196): PHYSICAL STANDBY|ORCL_STBY — STANDBY → sẽ backup
```

---

### PHASE 2 — SETUP: Configure RMAN (chỉ 1 lần)

**2a — Tạo thư mục backup**

```bash
mkdir -p /u01/backup/rman
chown oracle:oinstall /u01/backup/rman
```

**2b — Configure RMAN policies**

```bash
rman target /
CONFIGURE RETENTION POLICY TO RECOVERY WINDOW OF 7 DAYS;
CONFIGURE BACKUP OPTIMIZATION ON;
CONFIGURE CONTROLFILE AUTOBACKUP ON;
CONFIGURE CONTROLFILE AUTOBACKUP FORMAT FOR DEVICE TYPE DISK TO '/u01/backup/rman/%F';
CONFIGURE ARCHIVELOG DELETION POLICY TO APPLIED ON ALL STANDBY;
```

📌 `ARCHIVELOG DELETION POLICY TO APPLIED ON ALL STANDBY` — RMAN chỉ xóa archive logs sau khi đã được apply lên tất cả Standby. An toàn cho môi trường DG.

---

### PHASE 3 — BACKUP: Chạy RMAN backup

```bash
rman target /
RUN {
  ALLOCATE CHANNEL c1 DEVICE TYPE DISK FORMAT '/u01/backup/rman/%d_%T_%s_%p.bkp';
  BACKUP AS COMPRESSED BACKUPSET DATABASE
    TAG 'ORCL_STBY_20260324'
    PLUS ARCHIVELOG DELETE INPUT;
  BACKUP CURRENT CONTROLFILE FORMAT '/u01/backup/rman/%d_%T_ctl_%s.bkp';
  RELEASE CHANNEL c1;
}
DELETE NOPROMPT OBSOLETE;
```

📌 `PLUS ARCHIVELOG DELETE INPUT` — backup archive logs đồng thời xóa chúng khỏi disk sau khi backup xong.
📌 `DELETE NOPROMPT OBSOLETE` — xóa backup files cũ ngoài retention window (7 ngày), không hỏi xác nhận.
📌 `COMPRESSED BACKUPSET` — nén backup, tiết kiệm ~50% dung lượng so với uncompressed.

**Ước tính thời gian:**

| DB Size | Thời gian |
|---|---|
| ~5GB | 5-10 phút |
| ~10GB | 15-20 phút |
| ~20GB | 30-45 phút |

---

### PHASE 4 — PURGE: Xóa archive logs cũ

```bash
rman target /
DELETE NOPROMPT ARCHIVELOG ALL
  COMPLETED BEFORE 'SYSDATE-3';
```

Xóa archive logs đã completed trước 3 ngày. Bổ sung cho `DELETE INPUT` ở phase 3 — để xử lý các archive logs còn sót (ví dụ archive logs sinh ra giữa 2 lần backup).

---

### PHASE 5 — VERIFY: Kiểm tra sau backup

**Disk usage:**

```bash
df -h /u01
du -sh /u01/backup/rman/*
```

**Danh sách backups:**

```bash
rman target /
LIST BACKUP SUMMARY;
```

Output mong đợi:

```
List of Backups
===============
Key  Type LV Size       Device Type Completion Time
---  ---- -- ---------- ----------- ---------------
1    Full    1.2G       DISK        24-MAR-26
2    Full    450M       DISK        24-MAR-26      ← controlfile autobackup
```

---

## Disk usage ước tính

| DB Size (actual data) | Backup Size (compressed) | Archive logs/ngày |
|---|---|---|
| ~5GB | ~2-3GB | ~100-500MB |
| ~10GB | ~4-6GB | ~200MB-1GB |

Với retention 7 ngày, cần ít nhất: `backup_size × 2 + archive_logs × 7` free trên `/u01`.

---

## Thiết lập cron (chạy tự động hàng ngày)

```bash
# Trên máy Ansible controller (192.168.1.18)
crontab -e

# Backup lúc 2:00 AM hàng ngày
0 2 * * * cd /home/ubuntu/rnd/OracleDb21c-OracleLinux9 && \
  ansible-playbook operations/rman_backup.yml --tags backup \
  >> /var/log/rman_backup.log 2>&1
```

---

## Kiểm tra nhanh sau khi chạy

```bash
# Xem backup list từ RMAN
export ORACLE_HOME=/u01/app/oracle/product/21c/dbhome_1 ORACLE_SID=ORCL
PATH=$ORACLE_HOME/bin:$PATH
rman target / << 'EOF'
LIST BACKUP SUMMARY;
EOF

# Kiểm tra dung lượng
df -h /u01
du -sh /u01/backup/rman/
```

---

## Flow đầy đủ: Backup → Revert

```
[TRƯỚC KHI TEST / THAY ĐỔI QUAN TRỌNG]
         ↓
(1) Tạo restore point (SQL thủ công)
    sqlplus sys/Oracle_4U@ORCL as sysdba
    CREATE RESTORE POINT before_load_test GUARANTEE FLASHBACK DATABASE;
         ↓
[SETUP LẦN ĐẦU]
         ↓
(2) ansible-playbook operations/rman_backup.yml --tags setup
    → Tạo thư mục /u01/backup/rman
    → Configure RMAN retention policy
         ↓
[CHẠY ĐỊNH KỲ — hàng ngày]
         ↓
(3) ansible-playbook operations/rman_backup.yml --tags backup
    → Backup DB + archive logs từ Standby
    → Xóa obsolete backups tự động
         ↓
[KHI CẦN REVERT]
         ↓
(4) ansible-playbook operations/recover_database.yml -e "mode=list"
    → Xem restore points + RMAN backups hiện có
         ↓
    Có restore point?
    ├── CÓ → (5a) Flashback — nhanh, ~2 phút, Standby tự sync
    │         ansible-playbook operations/recover_database.yml \
    │           -e "mode=flashback" \
    │           -e "restore_point=before_load_test"
    │
    └── KHÔNG → (5b) RMAN restore — chậm, ~30-60 phút
                # Primary tự stream từ Standby qua network (FROM SERVICE)
                # Không cần copy file .bkp sang Primary
                ansible-playbook operations/recover_database.yml \
                  -e "mode=rman" \
                  -e "until_time='2026-03-24 10:00:00'"
                      ↓
                (6) Resync Standby sau RESETLOGS
                    ansible-playbook operations/restart_crashed_db.yml
                      ↓
                    Standby vẫn lỗi ORA-16700?
                    └── (7) Rebuild Standby từ đầu
                            ansible-playbook setup/setup_standby.yml
```

**Tóm tắt:**

| Tình huống | Cách revert | Thời gian | Standby sau recover |
|---|---|---|---|
| Có restore point | `mode=flashback` | ~2 phút | Tự sync — không cần làm thêm |
| Không có restore point | `mode=rman` | 30-60 phút | Cần chạy `restart_crashed_db.yml` |
| Standby bị ORA-16700 | `setup_standby.yml` | ~20 phút | Rebuild từ Primary mới |

📌 **Luôn tạo restore point TRƯỚC khi làm bất cứ thay đổi quan trọng nào** — cho phép revert trong ~2 phút thay vì phải đợi RMAN restore 30-60 phút.

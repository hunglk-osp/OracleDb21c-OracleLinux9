# Oracle Database 21c Installation on Oracle Linux 9 — với Data Guard & Fast-Start Failover

Tài liệu này mô tả quá trình cài đặt Oracle Database 21c trên Oracle Linux 9, thiết lập Data Guard (Primary/Standby), và bật Fast-Start Failover (FSFO) tự động. Tất cả các bước đều được tự động hóa bằng Ansible.

> Tài liệu tham khảo gốc: [Oracle Database 21c Installation On Oracle Linux 8 — oracle-base.com](https://oracle-base.com/articles/21c/oracle-db-21c-installation-on-oracle-linux-8)
> Bài này đã điều chỉnh để chạy trên **Oracle Linux 9**, bổ sung cấu hình **Data Guard** và **FSFO**, và thay thế toàn bộ các bước thủ công bằng Ansible playbook.

---

## Kiến trúc hệ thống

```
┌──────────────────────┐      Redo Log Shipping      ┌──────────────────────┐
│  PRIMARY             │ ─────────────────────────── │  STANDBY             │
│  192.168.1.195       │                             │  192.168.1.196       │
│  primarydb.local     │                             │  standbydb.local     │
│  DB_UNIQUE: ORCL     │                             │  DB_UNIQUE: ORCL_STBY│
│  SID: ORCL           │                             │  SID: ORCL           │
└──────────────────────┘                             └──────────────────────┘
          │                                                     │
          └─────────────────────┬───────────────────────────────┘
                                │  Data Guard Broker
                       ┌────────▼────────┐
                       │  OBSERVER        │
                       │  192.168.1.18    │
                       │  Ubuntu 22.04    │
                       │  (máy thứ 3)     │
                       └─────────────────┘
```

**Observer** là bên thứ ba theo dõi cả Primary và Standby. Khi Primary mất liên lạc quá `FastStartFailoverThreshold` giây (mặc định 30s), Observer ra lệnh Standby tự promote lên Primary — không cần can thiệp thủ công.

---

## Yêu cầu

| Thành phần | Yêu cầu |
|---|---|
| OS | Oracle Linux 9 (Primary + Standby), Ubuntu 22/24 (Observer) |
| RAM | 4GB+ (khuyến nghị 8GB) |
| Disk | 30GB+ cho `/u01` |
| Software | `LINUX.X64_213000_db_home.zip` tại `/root/OracleDb21c-OracleLinux9/` trên mỗi server |
| Control node | `ansible`, `passlib` (`pip install passlib`), `ansible-galaxy collection install ansible.posix` |

---

## Thứ tự chạy

```
1. install/install_oracle21c_primary.yml   → Cài Oracle software + tạo DB trên Primary (195)
2. install/install_oracle21c_standby.yml   → Cài Oracle software (KHÔNG tạo DB) trên Standby (196)
3. setup/setup_primary.yml                 → Cấu hình Data Guard Primary (archivelog, SRL, tnsnames, broker)
4. setup/setup_standby.yml                 → RMAN duplicate + enable MRP trên Standby
5. setup/setup_primary.yml --tags broker   → Tạo Data Guard Broker configuration
6. install/install_observer.yml            → Cài dgmgrl trên Observer, enable FSFO, start Observer
```

---

## Bước 1 — Cài đặt Oracle Software trên Primary (`install_oracle21c_primary.yml`)

**Chạy:** `ansible-playbook -i inventory.ini install/install_oracle21c_primary.yml`

### STEP 0: Kiểm tra prerequisites

Playbook kiểm tra server có đang chạy Oracle Linux 9 không (`/etc/oracle-release`), đọc RAM/CPU/Swap và cảnh báo nếu RAM < 8GB. Nếu không phải OL9 thì dừng lại.

### STEP 1: Chuẩn bị OS

**1.1 — Update OS**

```bash
dnf update -y
```

Cập nhật toàn bộ package lên phiên bản mới nhất trước khi cài Oracle.

---

**1.2 — Cài oracle-database-preinstall-21c**

Đây là điểm khác biệt đầu tiên so với tài liệu gốc. Tài liệu gốc dùng:

```bash
dnf install -y oracle-database-preinstall-21c
```

Trên OL9, package này **không có sẵn** trong repo. Playbook tải thẳng RPM từ repo OL8 và cài thủ công:

```bash
# Tải từ OL8 appstream repo
wget https://yum.oracle.com/repo/OracleLinux/OL8/appstream/x86_64/getPackage/compat-openssl10-1.0.2o-4.el8_6.x86_64.rpm
wget https://yum.oracle.com/repo/OracleLinux/OL8/appstream/x86_64/getPackage/oracle-database-preinstall-21c-1.0-1.el8.x86_64.rpm

# Cài với CV_ASSUME_DISTID để qua kiểm tra OS version
CV_ASSUME_DISTID=OEL8.4 dnf localinstall oracle-database-preinstall-21c-1.0-1.el8.x86_64.rpm
```

`compat-openssl10` cần được cài trước vì `preinstall-21c` phụ thuộc vào nó và không tự kéo từ repo OL9.

`CV_ASSUME_DISTID=OEL8.4` báo cho Oracle installer biết đây là OEL8.4, tránh bị từ chối do check OS version. Biến này được set vĩnh viễn trong `.bash_profile` của oracle user.

Package `preinstall-21c` tự động tạo oracle user, cài kernel parameters (`/etc/sysctl.d/`), và cấu hình `limits.conf` — thay thế toàn bộ phần "Manual Setup" trong tài liệu gốc.

---

**1.2.1 — Đảm bảo groups và oracle user đúng**

Tài liệu gốc tạo groups và user bằng `groupadd`/`useradd`. Playbook dùng Ansible `group` và `user` module để đảm bảo idempotent — chạy lại không bị lỗi nếu đã tồn tại:

| Group | GID | Mục đích |
|---|---|---|
| oinstall | 54321 | Primary install group |
| dba | 54322 | SYSDBA privilege |
| oper | 54323 | SYSOPER privilege |
| backupdba | 54324 | SYSBACKUP privilege |
| dgdba | 54325 | SYSDG privilege (Data Guard) |
| kmdba | 54326 | SYSKM privilege |
| racdba | 54327 | SYSRAC privilege |

Oracle user: UID 54321, home `/home/oracle`, shell `/bin/bash`.

---

**1.2.2 — Environment variables trong `.bash_profile`**

Tài liệu gốc tạo file `setEnv.sh` riêng. Playbook inject thẳng vào `.bash_profile` bằng `blockinfile`:

```bash
export ORACLE_BASE=/u01/app/oracle
export ORACLE_HOME=/u01/app/oracle/product/21c/dbhome_1
export ORACLE_SID=ORCL
export PATH=$ORACLE_HOME/bin:$PATH
export LD_LIBRARY_PATH=$ORACLE_HOME/lib:/lib:/usr/lib
export NLS_LANG=AMERICAN_AMERICA.AL32UTF8
export CV_ASSUME_DISTID=OEL8.4
```

`CV_ASSUME_DISTID` được set ở đây để tất cả các lần chạy Oracle tools sau này đều qua được OS check.

---

**1.3 — Cài thêm packages**

Các package bổ sung mà `preinstall-21c` không tự kéo: `gcc`, `gcc-c++`, `glibc-devel`, `ksh`, `libaio-devel`, `libstdc++-devel`, `make`, `nfs-utils`, `sysstat`, `unzip`, `wget`, `vim`.

---

**1.4 — Tạo thư mục**

```
/u01/app/oracle/product/21c/dbhome_1   ← ORACLE_HOME
/u01/app/oraInventory                  ← Oracle Inventory
```

Tài liệu gốc đặt data ở `/u02/oradata`. Playbook này để DBCA tự quản lý đường dẫn data trong `/u01/app/oracle/oradata`.

---

**1.5-1.6 — Configure `/etc/hostname` và `/etc/hosts`**

```
192.168.1.195  primarydb.local  primarydb   ← entry của chính nó
192.168.1.196  standbydb.local  standbydb   ← cross-entry cho standby
```

**Lý do cần hostname riêng biệt:** Data Guard Broker xây dựng `StaticConnectIdentifier` dựa trên hostname. Nếu Primary và Standby có cùng hostname, Broker sẽ build sai địa chỉ kết nối và Observer không connect được sau failover.

---

### STEP 2: Giải nén Oracle Software

Playbook kiểm tra `runInstaller` đã tồn tại chưa (idempotent), nếu chưa thì giải nén `LINUX.X64_213000_db_home.zip` trực tiếp vào `ORACLE_HOME`:

```bash
unzip -oq /root/OracleDb21c-OracleLinux9/LINUX.X64_213000_db_home.zip \
    -d /u01/app/oracle/product/21c/dbhome_1
```

Khác tài liệu gốc: ZIP phải có sẵn trên server (không upload từ control node) để tránh timeout khi truyền file 3GB qua Ansible.

---

### STEP 3: Fix OL9 Compatibility

**Đây là phần không có trong tài liệu gốc.** Oracle 21c được thiết kế cho OL8, và một số thư viện đã thay đổi trong OL9 khiến quá trình link khi chạy `root.sh` bị lỗi.

**3.1 — Cài compat libraries**

Các thư viện cần thiết mà OL9 không cài mặc định: `libaio`, `libaio-devel`, `libstdc++`, `libstdc++-devel`, `compat-openssl10`, `libnsl2`.

**3.2 — Tạo dummy `libpthread_nonshared.a`**

OL9 bỏ file này nhưng Oracle linker vẫn tham chiếu đến nó:

```bash
ar rc /usr/lib64/libpthread_nonshared.a   # tạo archive rỗng
```

**3.3 — Fix LDFLAGS trong `env_rdbms.mk`**

Thêm `-lnsl` vào linker flags trong `$ORACLE_HOME/rdbms/lib/env_rdbms.mk`. Thiếu flag này, `root.sh` sẽ fail khi link binary với lỗi undefined symbol liên quan đến network services.

**3.4 — Inject `stat_wrap.o` vào static libraries**

OL9 dùng glibc 2.34+ trong đó hàm `stat()` đã được gộp vào `libc.so` (không còn export qua syscall trực tiếp). Oracle 21c có một số static library vẫn dùng syscall number cũ. Giải pháp: compile một wrapper nhỏ và inject vào `libnnzst.a` và `libjavavm.a`:

```c
// stat_wrap.c
int stat(const char *path, struct stat *buf) {
    return syscall(4, path, buf);   // syscall 4 = old __NR_stat
}
int fstat(int fd, struct stat *buf) {
    return syscall(5, fd, buf);
}
```

```bash
gcc -c stat_wrap.c -o stat_wrap.o
ar r $ORACLE_HOME/lib/libnnzst.a stat_wrap.o
ar r $ORACLE_HOME/lib/libjavavm.a stat_wrap.o
```

Marker file `.stat_wrap_injected` được tạo để task này không chạy lại khi chạy playbook lần 2.

> **Lưu ý:** Trên Standby playbook (`install_oracle21c_standby.yml`), bước 3.4 được bỏ qua vì Oracle 21c không dùng các static `.a` này khi chỉ cài software-only (không link binary mới).

---

### STEP 4: Cài Oracle Software (Software Only)

Tài liệu gốc chạy `runInstaller` với options trực tiếp trên command line. Playbook dùng response file để dễ đọc và tái sử dụng:

```ini
# /tmp/db_install.rsp
oracle.install.option=INSTALL_DB_SWONLY    ← Chỉ cài software, không tạo DB
oracle.install.db.InstallEdition=EE
oracle.install.db.OSDBA_GROUP=dba
oracle.install.db.rootconfig.executeRootScript=false   ← root.sh sẽ chạy riêng
```

```bash
./runInstaller -silent -responseFile /tmp/db_install.rsp \
    -ignorePrereqFailure -waitforcompletion
```

`-ignorePrereqFailure` bỏ qua các cảnh báo prereq (RAM thấp, swap thấp) để cài tiếp.

Playbook kiểm tra `inventory.xml` để bỏ qua bước này nếu đã cài (idempotent).

**4.3 — Chạy root scripts**

Sau khi installer xong, phải chạy 2 script với quyền root:

```bash
/u01/app/oraInventory/orainstRoot.sh   # Cập nhật /etc/oraInst.loc
/u01/app/oracle/product/21c/dbhome_1/root.sh   # Set permissions, compile binaries
```

**[FIX-4] — Giữ SUID bit của `oradism`**

`root.sh` tạo file `oradism` với SUID (mode `4750`, owner `root`). Nếu sau đó chạy `chown -R oracle:oinstall $ORACLE_HOME` (hoặc Ansible `file` module với `recurse: yes`), SUID bit sẽ bị xóa và database sẽ không start được (lỗi "Unable to initialize oradism").

Giải pháp: sau `root.sh`, luôn re-set lại permission:

```bash
chown root:oinstall $ORACLE_HOME/bin/oradism
chmod 4750 $ORACLE_HOME/bin/oradism
```

Và khi set permission cho thư mục `bin`, **không dùng `recurse: yes`** — chỉ set permission cho thư mục cha mà không đệ quy vào các file con.

---

### STEP 5: Listener và tạo Database

**5.1 — Tạo listener**

Playbook dùng `netca -silent` để tạo listener lần đầu, sau đó **ghi đè `listener.ora`** bằng config tùy chỉnh.

**[FIX-1] — listener.ora Primary phải có cả ORCL lẫn ORCL_STBY**

Tài liệu gốc chỉ có một entry cho SID của chính nó. Với Data Guard FSFO, sau khi failover, host 195 sẽ trở thành Standby với DB_UNIQUE_NAME là `ORCL` nhưng Observer vẫn sẽ dùng alias `ORCL_STBY` để connect vào. Nếu listener không có entry `ORCL_STBY`, Observer sẽ báo `ORA-12541: TNS:no listener`.

Listener Primary phải có **cả 4 entries**:

```
ORCL              → khi host này là Primary
ORCL_DGMGRL       → cho Broker connect khi host này là Primary
ORCL_STBY         → khi host này là Standby (sau failover)
ORCL_STBY_DGMGRL  → cho Broker connect khi host này là Standby
```

**5.4 — Tạo Database bằng DBCA**

Tài liệu gốc dùng `lsnrctl start` rồi `dbca`. Playbook tích hợp DBCA vào luồng và cấu hình như sau:

```bash
dbca -silent -createDatabase \
    -templateName General_Purpose.dbc \
    -gdbname ORCL \
    -sid ORCL \
    -createAsContainerDatabase true \   # CDB với 1 PDB
    -numberOfPDBs 1 \
    -pdbName ORCLPDB1 \
    -totalMemory 2048 \                 # Điều chỉnh theo RAM thực tế
    -characterSet AL32UTF8 \
    -sysPassword Oracle_4U
```

Playbook kiểm tra `system01.dbf` tồn tại chưa để bỏ qua nếu DB đã được tạo.

---

### STEP 6: Network — tnsnames.ora

```
ORCL      → trỏ vào 192.168.1.195 (Primary)
ORCLPDB1  → trỏ vào 192.168.1.195 (PDB)
```

Mở firewall port 1521:

```bash
firewall-cmd --permanent --add-port=1521/tcp
```

---

### STEP 7: Register services + tạo app user

**7.0-7.3 — Register services với listener**

Sau DBCA, listener cần biết về PDB service:

```sql
ALTER SYSTEM SET LOCAL_LISTENER='(ADDRESS=(PROTOCOL=TCP)(HOST=192.168.1.195)(PORT=1521))';
ALTER SYSTEM REGISTER;
```

**7.5 — Tạo application user**

```sql
ALTER SESSION SET CONTAINER = ORCLPDB1;
CREATE USER osp IDENTIFIED BY "Osp@123";
GRANT CONNECT, RESOURCE, DBA TO osp;
ALTER USER osp DEFAULT TABLESPACE users QUOTA UNLIMITED ON users;
```

---

### STEP 8: Auto-start khi khởi động lại server

Tài liệu gốc dùng `dbstart`/`dbshut` scripts. Playbook dùng **systemd service** để đáng tin cậy hơn.

**[FIX-5] — Service phải chạy as root, không as oracle**

Lý do: `oradism` cần SUID root để hoạt động. Nếu service chạy as oracle user, `oradism` sẽ không có quyền cần thiết.

Service wrapper `/usr/local/bin/oracle-start.sh`:

```bash
#!/bin/bash
# Re-fix oradism SUID mỗi khi khởi động (phòng trường hợp bị mất sau update)
chown root:oinstall $ORACLE_HOME/bin/oradism
chmod 4750 $ORACLE_HOME/bin/oradism

# Start listener
runuser -l oracle -c "lsnrctl start"

# Start DB — detect role để open đúng cách
runuser -l oracle -c "sqlplus / as sysdba <<'EOF'
STARTUP MOUNT;
DECLARE v_role VARCHAR2(30);
BEGIN
  SELECT DATABASE_ROLE INTO v_role FROM V\$DATABASE;
  IF v_role = 'PRIMARY' THEN
    EXECUTE IMMEDIATE 'ALTER DATABASE OPEN';
    EXECUTE IMMEDIATE 'ALTER PLUGGABLE DATABASE ALL OPEN';
  ELSE
    -- Sau failover, host này có thể là Standby
    EXECUTE IMMEDIATE 'ALTER DATABASE OPEN READ ONLY';
    EXECUTE IMMEDIATE 'ALTER PLUGGABLE DATABASE ALL OPEN READ ONLY';
    EXECUTE IMMEDIATE 'ALTER DATABASE RECOVER MANAGED STANDBY DATABASE USING CURRENT LOGFILE DISCONNECT FROM SESSION';
  END IF;
END;
/
EOF"
```

**Tại sao detect role khi start?** Sau FSFO failover, Primary cũ trở thành Standby. Nếu service cứng `ALTER DATABASE OPEN` (không detect role), server sẽ không start được khi đang là Standby.

`/etc/systemd/system/oracle-db.service`:

```ini
[Service]
Type=forking
ExecStart=/usr/local/bin/oracle-start.sh
ExecStop=/usr/local/bin/oracle-stop.sh
LimitMEMLOCK=infinity
TimeoutStartSec=600
```

---

### STEP 9: Chuẩn bị Data Guard

**[FIX-2] — DG_BROKER_CONFIG_FILE phải trỏ đúng path**

Oracle 21c dùng **Read-Only Oracle Home** (xem [Read-Only Oracle Homes](https://oracle-base.com/articles/18c/read-only-oracle-home-18c)). Điều này có nghĩa là các file có thể thay đổi như broker config (`dr1ORCL.dat`, `dr2ORCL.dat`) **không** nằm ở `$ORACLE_HOME/dbs/` mà ở `$ORACLE_BASE/homes/OraDB21Home1/dbs/`.

Playbook detect đúng path bằng `find`:

```bash
find $ORACLE_BASE/homes -maxdepth 3 -name "dbs" -type d | head -1
# → /u01/app/oracle/homes/OraDB21Home1/dbs
```

Rồi set vào SPFILE:

```sql
ALTER SYSTEM SET DG_BROKER_CONFIG_FILE1='/u01/app/oracle/homes/OraDB21Home1/dbs/dr1ORCL.dat' SCOPE=SPFILE;
ALTER SYSTEM SET DG_BROKER_CONFIG_FILE2='/u01/app/oracle/homes/OraDB21Home1/dbs/dr2ORCL.dat' SCOPE=SPFILE;
ALTER SYSTEM SET DB_UNIQUE_NAME='ORCL' SCOPE=SPFILE;
```

---

## Bước 2 — Cài đặt Oracle Software trên Standby (`install_oracle21c_standby.yml`)

**Chạy:** `ansible-playbook -i inventory.ini install/install_oracle21c_standby.yml`

Standby playbook thực hiện **STEP 0–3 giống Primary** (OS prep, preinstall, groups/user, extract ZIP, OL9 compat fixes), với các điểm khác biệt:

### Điểm khác biệt so với Primary

**[FIX-1] — Hostname phải khác Primary**

```
primarydb.local  ← Primary
standbydb.local  ← Standby
```

Nếu cùng hostname, Data Guard Broker build `StaticConnectIdentifier` sai và Observer không connect được.

**[FIX-5] — KHÔNG tạo Database**

Tài liệu gốc chỉ nói cài software. Standby DB sẽ được **RMAN duplicate** từ Primary — không dùng DBCA. Playbook chỉ tạo file `init.ora` tối thiểu để instance có thể `STARTUP NOMOUNT`:

```ini
# $ORACLE_HOME/dbs/initORCL.ora
DB_NAME=ORCL
DB_UNIQUE_NAME=ORCL_STBY
DG_BROKER_CONFIG_FILE1=/u01/app/oracle/homes/OraDB21Home1/dbs/dr1ORCL.dat
DG_BROKER_CONFIG_FILE2=/u01/app/oracle/homes/OraDB21Home1/dbs/dr2ORCL.dat
```

**[FIX-2] — listener.ora Standby có cả ORCL_STBY lẫn ORCL**

Tương tự Primary, Standby listener cũng phải có 4 entries để Observer và Broker kết nối được từ cả 2 hướng (khi là Standby và khi được promote thành Primary).

**[FIX-3] — DB_UNIQUE_NAME set ngay từ đầu**

```ini
DB_UNIQUE_NAME=ORCL_STBY
```

Set sẵn trong `init.ora` để RMAN duplicate không cần override lại.

**STEP 6 — tnsnames.ora Standby**

```
ORCL      → trỏ vào 192.168.1.195 (Primary, để RMAN connect)
ORCL_STBY → trỏ vào 192.168.1.196 (chính nó, để RMAN dùng làm AUXILIARY)
```

Hai entries này bắt buộc để lệnh RMAN `DUPLICATE TARGET DATABASE FOR STANDBY` hoạt động.

**STEP 8 — systemd service detect role (giống Primary)**

Cùng logic detect PRIMARY/STANDBY khi start để sau failover server có thể tự mở đúng chế độ.

---

## Bước 3 — Cấu hình Data Guard Primary (`setup/setup_primary.yml`)

**Chạy:** `ansible-playbook -i inventory.ini setup/setup_primary.yml`

### STEP 1: Enable Archive Log Mode

Data Guard yêu cầu Primary phải ở Archive Log Mode:

```sql
SHUTDOWN IMMEDIATE;
STARTUP MOUNT;
ALTER DATABASE ARCHIVELOG;
ALTER DATABASE OPEN;
```

Playbook kiểm tra mode hiện tại trước khi đổi (idempotent).

### STEP 2: Các tham số Data Guard trong SPFILE

```sql
ALTER SYSTEM SET LOG_ARCHIVE_DEST_1='LOCATION=USE_DB_RECOVERY_FILE_DEST';
ALTER SYSTEM SET LOG_ARCHIVE_DEST_2='SERVICE=ORCL_STBY ASYNC VALID_FOR=(ONLINE_LOGFILES,PRIMARY_ROLE) DB_UNIQUE_NAME=ORCL_STBY';
ALTER SYSTEM SET FAL_SERVER='ORCL_STBY';
ALTER SYSTEM SET STANDBY_FILE_MANAGEMENT='AUTO';
ALTER SYSTEM SET DB_RECOVERY_FILE_DEST='/u01/app/oracle/fast_recovery_area';
ALTER SYSTEM SET DB_RECOVERY_FILE_DEST_SIZE=20G;
```

### STEP 3: Standby Redo Logs (SRL) trên Primary

SRL cần có trên cả Primary lẫn Standby để hỗ trợ **real-time apply** và chuẩn bị cho role switch:

```sql
ALTER DATABASE ADD STANDBY LOGFILE THREAD 1 GROUP 10 SIZE 50M;
ALTER DATABASE ADD STANDBY LOGFILE THREAD 1 GROUP 11 SIZE 50M;
ALTER DATABASE ADD STANDBY LOGFILE THREAD 1 GROUP 12 SIZE 50M;
ALTER DATABASE ADD STANDBY LOGFILE THREAD 1 GROUP 13 SIZE 50M;
```

Số lượng SRL: online redo log groups + 1.

### STEP 4: Password file và tnsnames.ora

Copy password file sang Standby (SCP):

```bash
scp $ORACLE_HOME/dbs/orapwORCL oracle@192.168.1.196:$ORACLE_HOME/dbs/
```

Password file dùng để xác thực SYS khi Standby kết nối Primary qua network.

### STEP 5 (--tags broker): Tạo Data Guard Broker Configuration

Chỉ chạy sau khi `setup_standby.yml` đã hoàn thành:

```bash
dgmgrl sys/Oracle_4U@ORCL << EOF
CREATE CONFIGURATION 'DGConfig' AS
    PRIMARY DATABASE IS 'ORCL'
    CONNECT IDENTIFIER IS 'ORCL';
ADD DATABASE 'ORCL_STBY'
    AS CONNECT IDENTIFIER IS 'ORCL_STBY'
    MAINTAINED AS PHYSICAL;
ENABLE CONFIGURATION;
SHOW CONFIGURATION;
EOF
```

---

## Bước 4 — RMAN Duplicate sang Standby (`setup/setup_standby.yml`)

**Chạy:** `ansible-playbook -i inventory.ini setup/setup_standby.yml`

### STEP 3: RMAN Active Duplicate

Tạo Standby DB trực tiếp từ Primary đang chạy, không cần backup:

```bash
rman << EOF
CONNECT TARGET sys/Oracle_4U@ORCL;
CONNECT AUXILIARY sys/Oracle_4U@ORCL_STBY;

DUPLICATE TARGET DATABASE FOR STANDBY
  FROM ACTIVE DATABASE
  DORECOVER
  SPFILE
    SET DB_UNIQUE_NAME='ORCL_STBY'
    SET DB_RECOVERY_FILE_DEST='/u01/app/oracle/fast_recovery_area'
    SET FAL_SERVER='ORCL'
    SET STANDBY_FILE_MANAGEMENT='AUTO'
    SET DG_BROKER_START='FALSE'
  NOFILENAMECHECK;
EOF
```

Quá trình này mất 20–60 phút tùy kích thước DB. Ansible dùng `async: 7200` để không timeout.

**[FIX-3] — Kiểm tra trạng thái trước `STARTUP NOMOUNT`**

Nếu chạy lại playbook lần 2 (DB đã ở trạng thái MOUNTED), lệnh `STARTUP NOMOUNT` sẽ báo `ORA-01081`. Playbook kiểm tra `V$INSTANCE.STATUS` và chỉ `SHUTDOWN ABORT` + restart nếu cần.

### STEP 4: Bật MRP và DG Broker

```sql
ALTER SYSTEM SET DG_BROKER_START=TRUE SCOPE=BOTH;
ALTER DATABASE OPEN READ ONLY;
ALTER PLUGGABLE DATABASE ALL OPEN READ ONLY;
ALTER DATABASE RECOVER MANAGED STANDBY DATABASE USING CURRENT LOGFILE DISCONNECT FROM SESSION;
```

**[FIX-5] — Thêm Standby Redo Logs trên Standby**

```sql
ALTER DATABASE ADD STANDBY LOGFILE THREAD 1 GROUP 10 SIZE 50M;
-- ... GROUP 10-13
```

Thiếu SRL trên Standby gây lỗi `ORA-16789` và MRP không apply được real-time.

**[FIX-6] — Reload listener sau RMAN**

RMAN đã tạo DB với service mới (`ORCL_STBY`). Listener cần reload để đăng ký service:

```bash
lsnrctl reload
```

---

## Bước 5 — Cài Observer và Enable FSFO (`install/install_observer.yml`)

**Chạy từ máy 18 (Observer):** `ansible-playbook -i inventory.ini install/install_observer.yml`

### PLAY 1: Enable FSFO trên Primary và Standby

**Detect Primary động:** Playbook chạy trên cả 2 nodes và query `V$DATABASE.DATABASE_ROLE` để xác định ai là Primary thực sự — không hardcode IP. Điều này quan trọng vì sau failover, Primary có thể đổi sang 196.

**Enable Flashback Database**

FSFO yêu cầu Flashback Database trên cả Primary và Standby:

```sql
-- Trên Primary:
ALTER DATABASE FLASHBACK ON;

-- Trên Standby (phải cancel MRP trước):
ALTER DATABASE RECOVER MANAGED STANDBY DATABASE CANCEL;
ALTER DATABASE FLASHBACK ON;
ALTER DATABASE RECOVER MANAGED STANDBY DATABASE USING CURRENT LOGFILE DISCONNECT FROM SESSION;
```

Flashback cho phép Primary cũ (sau khi bị failover) tự reinstate thành Standby thay vì phải RMAN duplicate lại từ đầu.

**FSFO Properties:**

| Property | Giá trị | Ý nghĩa |
|---|---|---|
| `FastStartFailoverThreshold` | 30 | Giây chờ trước khi trigger failover |
| `FastStartFailoverLagLimit` | 30 | Cho phép Standby lag tối đa 30s redo data |
| `FastStartFailoverAutoReinstate` | TRUE | Primary cũ tự reinstate thành Standby |
| `FastStartFailoverPmyShutdown` | FALSE | Primary không tự shutdown sau failover |
| `CommunicationTimeout` | 15 | Broker timeout kết nối |

```bash
dgmgrl sys/Oracle_4U@ORCL << EOF
EDIT CONFIGURATION SET PROPERTY FastStartFailoverThreshold = 30;
EDIT CONFIGURATION SET PROPERTY FastStartFailoverAutoReinstate = TRUE;
ENABLE FAST_START FAILOVER;
EOF
```

### PLAY 2: Cài Oracle Instant Client + Start Observer trên Ubuntu 18

**Cài Oracle Instant Client 21.3**

Observer cần `dgmgrl` binary. Trên Ubuntu, Oracle không có native `.deb` package — cần dùng `alien` để convert RPM:

```bash
apt install alien
wget oracle-instantclient-tools-21.3.0.0.0-1.x86_64.rpm
alien --to-deb --scripts oracle-instantclient-tools-21.3.0.0.0-1.x86_64.rpm
dpkg -i oracle-instantclient-tools_21.3.0.0.0-2_amd64.deb
```

Nếu `alien` convert thất bại, playbook fallback sang copy `dgmgrl` binary thẳng từ Primary qua SCP.

**Ubuntu 24+ compatibility:** `libaio` đổi tên thành `libaio1t64`. Playbook thử cài cả 2 tên và tạo symlink:

```bash
ln -sf /usr/lib/x86_64-linux-gnu/libaio.so.1t64 /usr/lib/x86_64-linux-gnu/libaio.so.1
```

**[FIX sqlnet.ora] — `SQLNET.RECV_TIMEOUT` phải đủ dài**

Đây là nguyên nhân chính khiến FSFO thất bại ban đầu. Trong quá trình failover, Standby cần 10–30 giây để promote. Nếu timeout quá ngắn, Observer ngắt kết nối giữa chừng và báo `ORA-12609: TNS Receive timeout`:

```ini
# sqlnet.ora trên Observer
SQLNET.OUTBOUND_CONNECT_TIMEOUT = 5    # Connect timeout ngắn (server không lên thì thôi)
SQLNET.RECV_TIMEOUT = 60               # Receive timeout dài (đủ thời gian failover)
SQLNET.SEND_TIMEOUT = 30
TCP.CONNECT_TIMEOUT = 5
```

**`tnsnames.ora` trên Observer dùng `SID` (static), không dùng `SERVICE_NAME`**

```
ORCL =
  (DESCRIPTION =
    (ADDRESS = (PROTOCOL = TCP)(HOST = 192.168.1.195)(PORT = 1521))
    (CONNECT_DATA = (SID = ORCL)))    ← SID, không phải SERVICE_NAME
```

Lý do: Khi DB đang ở trạng thái MOUNTED (không phải OPEN), dynamic service chưa được đăng ký với listener, nhưng static SID entry trong `listener.ora` vẫn hoạt động.

**[FIX fsfo.dat] — Cleanup file cũ trước khi start Observer**

Nguyên nhân chính khiến Observer không start được lần 2: file `fsfo.dat` từ lần chạy trước vẫn còn và bị lock. Observer báo lỗi `fsfo.dat: could not be opened` và loop restart liên tục.

```bash
rm -f /home/ubuntu/oracle-observer/fsfo.dat
rm -f /home/ubuntu/oracle-observer/observer_dgmgrl.log
```

**Observer với auto-restart loop**

Observer start bằng script `run_observer.sh` chạy trong infinite loop:

```bash
while true; do
    # Detect PRIMARY hiện tại (có thể đổi sau failover)
    for TNS_ALIAS in ORCL ORCL_STBY; do
        ROLE=$(sqlplus ... SELECT DATABASE_ROLE FROM V$DATABASE ...)
        if echo "$ROLE" | grep -q 'PRIMARY'; then
            CONNECT_STR=$TNS_ALIAS; break
        fi
    done

    # Start Observer — block cho đến khi Observer exit
    dgmgrl sys/Oracle_4U@$CONNECT_STR \
        "START OBSERVER FILE IS 'fsfo.dat' LOGFILE IS 'observer_dgmgrl.log';"

    # Observer exit sau failover — wait rồi restart với Primary mới
    sleep 15
done
```

Loop này đảm bảo sau mỗi failover, Observer tự restart và kết nối vào Primary mới.

### PLAY 3: Verify FSFO

Kiểm tra trạng thái cuối cùng:

```bash
dgmgrl sys/Oracle_4U@ORCL << EOF
SHOW CONFIGURATION VERBOSE;
SHOW FAST_START FAILOVER;
EOF
```

Output mong đợi:
```
Fast-Start Failover:  Enabled in Potential Data Loss Mode

  Threshold:          30 seconds
  Target:             ORCL_STBY
  Observer:           192.168.1.18
  Observer State:     OK
  ...
Configuration Status: SUCCESS
```

---

## Test Failover

```bash
# Terminal 1: Chạy load test
cd tests && uv run load_test.py

# Terminal 2: Tắt Primary
ssh root@192.168.1.195 "poweroff"

# Quan sát Observer log trên máy 18
tail -f /home/ubuntu/oracle-observer/observer.log
tail -f /home/ubuntu/oracle-observer/observer_dgmgrl.log

# Sau ~30s, load_test.py tự reconnect vào 196 (Primary mới)
```

Khi Primary cũ (195) khởi động lại, `oracle-start.sh` detect role = STANDBY và tự mở READ ONLY + start MRP. Data Guard Broker reinstate nó thành Standby tự động (do `FastStartFailoverAutoReinstate=TRUE`).

---

## Troubleshooting

### Observer không start — `fsfo.dat: could not be opened`

```bash
# Chạy lại install_observer.yml — tự cleanup và restart
ansible-playbook -i inventory.ini install/install_observer.yml
```

### FSFO không trigger — `ORA-12609: TNS Receive timeout`

Kiểm tra `sqlnet.ora` trên máy Observer:

```bash
cat /usr/lib/oracle/21/client64/network/admin/sqlnet.ora
# SQLNET.RECV_TIMEOUT phải >= 60
```

### Standby không sync — kiểm tra MRP

```sql
-- Trên Standby
SELECT PROCESS, STATUS, SEQUENCE# FROM V$MANAGED_STANDBY
WHERE PROCESS IN ('MRP0','RFS') ORDER BY PROCESS;
```

### Observer mất kết nối sau failover — Primary không có ORCL_STBY trong listener

```bash
# Trên 195 (Primary cũ, giờ là Standby)
grep ORCL_STBY $ORACLE_HOME/network/admin/listener.ora
# Nếu không có → chạy lại
ansible-playbook -i inventory.ini install/install_oracle21c_primary.yml --tags listener
```

---

## Cấu trúc files

```
OracleDb21c-OracleLinux9/
├── inventory.ini                          # Ansible inventory (primary, standby, observer)
├── install/
│   ├── README.md                          # Tài liệu này
│   ├── install_oracle21c_primary.yml      # Cài Oracle + tạo DB trên Primary
│   ├── install_oracle21c_standby.yml      # Cài Oracle software-only trên Standby
│   └── install_observer.yml               # Cài dgmgrl + enable FSFO + start Observer
├── setup/
│   ├── setup_primary.yml                  # Cấu hình Data Guard Primary + Broker
│   └── setup_standby.yml                  # RMAN duplicate + enable MRP
└── tests/
    └── load_test.py                       # Test script — tự failover khi Primary down
```

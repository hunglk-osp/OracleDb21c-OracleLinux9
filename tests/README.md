# tests/ — Test Failover + Load Test

## Chuẩn bị

- FSFO đã enable: `ansible-playbook install/install_observer.yml`
- Observer đang chạy trên máy 18
- Cần **2 terminal** trên máy 18

## Các file

### crash_primary_test.yml

Crash con đang là **PRIMARY** bằng `kill -9` (simulate crash thật):

```bash
ansible-playbook tests/crash_primary_test.yml
```

- Tự detect ai là Primary (không hardcode 195 hay 196)
- Kill tất cả Oracle processes + listener
- Observer sẽ trigger FSFO sau ~30s

### crash_standby_test.yml

Crash con đang là **STANDBY**:

```bash
ansible-playbook tests/crash_standby_test.yml
```

- Tự detect ai là Standby
- Primary vẫn chạy bình thường

### load_test.py

Load test insert liên tục + auto-failover giữa 2 host:

```bash
uv run tests/load_test.py
```

- Insert vào Primary trong 120s (5 threads)
- Nếu Primary chết → tự chuyển sang host còn lại
- Cuối cùng: so sánh row count giữa 2 host
- Hiển thị: failover events, downtime, throughput

### check_sync.py

Check đồng bộ dữ liệu giữa 2 host:

```bash
uv run tests/check_sync.py
```

### check_dg_status.py

Check trạng thái Data Guard (role, sync, MRP, ...):

```bash
uv run tests/check_dg_status.py
```

### oracle_demo.py

Demo CRUD cơ bản (tạo table, insert, update, delete, stored procedure):

```bash
uv run tests/oracle_demo.py
```

## 3 Kịch bản test

### Kịch bản 1: Load test bình thường

```bash
# Terminal 1
uv run tests/load_test.py
```

Kết quả: Insert 120s không lỗi, cả 2 host cùng số row.

### Kịch bản 2: Tắt Standby giữa load test

```bash
# Terminal 1
uv run tests/load_test.py

# Terminal 2 (đợi ~20s)
ansible-playbook tests/crash_standby_test.yml

# Load test KHÔNG bị ảnh hưởng

# Terminal 2 (bật lại)
ansible-playbook operations/restart_crashed_db.yml
```

### Kịch bản 3: Tắt Primary giữa load test (FSFO)

```bash
# Terminal 1
uv run tests/load_test.py

# Terminal 2 (đợi ~20s)
ansible-playbook tests/crash_primary_test.yml

# Load test DOWN ~30-40s → tự failover

# Sau load test — Terminal 2:
ansible-playbook operations/restart_crashed_db.yml
ansible-playbook install/install_observer.yml
```

### Kịch bản 3b: Đổi qua đổi lại

Chạy kịch bản 3 **hai lần liên tiếp**. Sau mỗi lần:

```bash
ansible-playbook operations/restart_crashed_db.yml
ansible-playbook install/install_observer.yml
```

Rồi chạy lại kịch bản 3. Playbook tự detect — không cần sửa gì.

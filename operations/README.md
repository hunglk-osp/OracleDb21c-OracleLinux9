# operations/ — Vận hành Oracle Data Guard

## Các playbook

### restart_crashed_db.yml

Khôi phục Oracle DB sau **bất kỳ sự cố nào**: crash (kill -9), systemctl stop, mất điện, hoặc DB stuck ở MOUNTED.

```bash
ansible-playbook operations/restart_crashed_db.yml
```

Chạy trên cả 2 máy (195 + 196), tự detect trạng thái và xử lý:

| Trạng thái | Hành động |
|---|---|
| Instance chết hoàn toàn | Start listener → STARTUP MOUNT → đợi reinstate 40s → OPEN READ ONLY → Open PDB → Start MRP → Reload listener |
| DB MOUNTED chưa OPEN | ALTER DATABASE OPEN READ ONLY → Open PDB → Start MRP |
| PRIMARY MOUNTED (chưa reinstate) | Manual REINSTATE qua broker → đợi 20s → OPEN → PDB → MRP |
| DB đang chạy bình thường | Skip |

**3 phase:**
1. **ASSESS** — Đánh giá: instance/listener/DB role/PDB/MRP
2. **FIX** — Khôi phục từng thành phần thiếu
3. **VERIFY** — Hiển thị trạng thái cuối cùng

**Khi nào chạy:**
- Sau khi test crash (`tests/crash_primary_test.yml` hoặc `tests/crash_standby_test.yml`)
- Sau khi FSFO failover xảy ra
- Khi DB bị ORA-01109 (database not open)
- Khi DB stuck ở MOUNTED

### cleanup_oracle21c.yml

Xóa toàn bộ Oracle khỏi máy (DB + software + user + directories):

```bash
# Xóa cả 2 máy
ansible-playbook operations/cleanup_oracle21c.yml

# Chỉ xóa Primary
ansible-playbook operations/cleanup_oracle21c.yml --limit primary

# Chỉ xóa Standby
ansible-playbook operations/cleanup_oracle21c.yml --limit standby
```

**Cẩn thận:** Xóa hết data, không thể khôi phục!

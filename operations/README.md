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
| Instance chết hoàn toàn | Start listener → STARTUP MOUNT → đợi Observer auto-reinstate 40s → OPEN READ ONLY → Open PDB → Start MRP → Reload listener |
| DB MOUNTED chưa OPEN | ALTER DATABASE OPEN READ ONLY → Open PDB → Start MRP |
| PRIMARY MOUNTED (chưa reinstate) | Manual REINSTATE qua broker → đợi 20s → OPEN READ ONLY → PDB → MRP |
| DB đang chạy bình thường | Skip |

**3 phase:**
1. **ASSESS** — Đánh giá: instance/listener/DB role/PDB/MRP
2. **FIX** — Khôi phục từng thành phần thiếu
3. **VERIFY** — Hiển thị trạng thái cuối cùng

**Khi nào chạy:**
- Sau khi test crash (`tests/crash_primary_test.yml` hoặc `tests/crash_standby_test.yml`)
- Sau khi FSFO failover xảy ra — Primary cũ cần reinstate thành Standby
- Khi DB bị `ORA-01109` (database not open) hoặc stuck ở MOUNTED
- Sau mất điện hoặc `systemctl stop oracle-db`

**Không cần chạy lại `install_observer.yml` sau đây** trừ khi Observer process bị chết. Kiểm tra trước:
```bash
ps aux | grep dgmgrl
dgmgrl sys/Oracle_4U@ORCL "SHOW CONFIGURATION;"
```

---

### switchover.yml

Đảo vai trò **Primary ↔ Standby** theo kế hoạch (không mất data). Dùng cho bảo trì server, patching OS, hoặc test định kỳ.

```bash
ansible-playbook operations/switchover.yml
```

Tự detect ai đang là Primary/Standby — không hardcode 195 hay 196.

**5 phase:**
1. **DETECT** — Xác định vai trò từng node, hiển thị apply lag
2. **DISABLE FSFO + STOP OBSERVER** — Graceful stop trước khi switch
3. **SWITCHOVER** — Broker thực hiện đảo vai trò (~90 giây)
4. **VERIFY** — Hiển thị trạng thái sau switch
5. **RE-ENABLE FSFO + RESTART OBSERVER** — Khôi phục bảo vệ tự động

**Yêu cầu trước khi chạy:**
- Cả 2 DB đang OPEN và đồng bộ (`SHOW CONFIGURATION = SUCCESS`)
- Apply lag = 0
- Observer đang chạy, FSFO đang `ENABLED`

**Khi nào chạy:**
- Bảo trì hoặc patching server Primary
- Test định kỳ hàng tháng để verify Standby hoạt động đúng
- Đưa Primary về node cũ sau khi FSFO failover tự động

Chi tiết xem [switchover_README.md](switchover_README.md).

---

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

⚠️ **Cẩn thận:** Xóa hết data, không thể khôi phục!

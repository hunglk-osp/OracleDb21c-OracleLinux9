#!/bin/bash
# full_reinstall.sh — Cleanup + Cài lại Oracle DG từ đầu
# Chạy trong tmux: tmux new -s install
# Detach: Ctrl+B D   |   Reattach: tmux attach -t install

set -e
cd /home/ubuntu/rnd/OracleDb21c-OracleLinux9

LOG=/home/ubuntu/reinstall_$(date +%Y%m%d_%H%M%S).log
exec > >(tee -a "$LOG") 2>&1

echo "============================================"
echo "FULL REINSTALL — $(date)"
echo "Log: $LOG"
echo "============================================"

step() {
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "STEP $1: $2"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "Start: $(date)"
}

# ── 1. Cleanup ──────────────────────────────────
step 1 "Cleanup Oracle khỏi cả 2 máy"
ansible-playbook operations/cleanup_oracle21c.yml
echo "Done: $(date)"

# ── 2. Install Primary ──────────────────────────
step 2 "Cài Oracle 21c trên Primary (195) — 45-60 phút"
ansible-playbook install/install_oracle21c_primary.yml
echo "Done: $(date)"

# ── 3. Install Standby ──────────────────────────
step 3 "Cài Oracle 21c trên Standby (196) — 20-30 phút"
ansible-playbook install/install_oracle21c_standby.yml
echo "Done: $(date)"

# ── 4. Setup DG Primary ─────────────────────────
step 4 "Setup Data Guard trên Primary"
ansible-playbook setup/setup_primary.yml
echo "Done: $(date)"

# ── 5. Setup DG Standby (RMAN duplicate) ────────
step 5 "Setup Data Guard trên Standby — RMAN duplicate (20-60 phút)"
ansible-playbook setup/setup_standby.yml
echo "Done: $(date)"

# ── 6. Finish Broker ────────────────────────────
step 6 "Finish DG Broker trên Primary"
ansible-playbook setup/setup_primary.yml --tags broker
echo "Done: $(date)"

# ── 7. Enable FSFO + Observer ───────────────────
step 7 "Enable FSFO + Start Observer trên máy 18"
ansible-playbook install/install_observer.yml
echo "Done: $(date)"

echo ""
echo "============================================"
echo "HOÀN TẤT — $(date)"
echo "============================================"
echo ""
echo "Test ngay:"
echo "  uv run tests/check_dg_status.py"
echo "  uv run tests/load_test.py"

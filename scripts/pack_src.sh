#!/bin/bash
# Đóng gói mã nguồn V6 (không asset/work/backup) → scratch/v6_src.tgz để chép sang máy cloud chạy test.
cd "$(dirname "$0")/.."
tar czf scratch/v6_src.tgz --exclude='./.backup*' --exclude='./_to_delete' --exclude='./work' --exclude='./models' \
  --exclude='./assets' --exclude='./voices' --exclude='./voice_samples' --exclude='./cookies' --exclude='./scratch' \
  --exclude='./desktop/src-tauri/target' --exclude='*/__pycache__' --exclude='./node_modules' --exclude='./vendor' .
ls -la scratch/v6_src.tgz

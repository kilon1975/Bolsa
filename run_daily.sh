#!/bin/bash
# Corre ambos bots + regenera dashboard. Lanzado por launchd.
set -e
cd "$(dirname "$0")"
source .venv/bin/activate

STAMP=$(date -u +%Y%m%d-%H%M%S)
echo "=== $STAMP START ===" >> logs/daily.log

{
  echo "--- swing-US ---"
  python main.py || echo "[swing] exit=$?"
  echo "--- value-LATAM ---"
  python main_value.py || echo "[value] exit=$?"
  echo "--- dashboard ---"
  python dashboard.py || echo "[dashboard] exit=$?"
} >> logs/daily.log 2>&1

echo "=== $STAMP END ===" >> logs/daily.log

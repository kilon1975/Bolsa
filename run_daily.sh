#!/bin/bash
# Corre ambos bots + regenera dashboard. Lanzado por launchd.
cd "$(dirname "$0")"
source .venv/bin/activate

STAMP=$(date -u +%Y%m%d-%H%M%S)
STAMP_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "=== $STAMP START ===" >> logs/daily.log

{
  echo "--- swing-US ---"
  python main.py
} >> logs/daily.log 2>&1
SWING_EXIT=$?

{
  echo "--- value-LATAM ---"
  python main_value.py
} >> logs/daily.log 2>&1
VALUE_EXIT=$?

# Escribe estado para el dashboard
python - <<PYEOF
import json
status = {
    "ran_at": "$STAMP_ISO",
    "swing_exit": $SWING_EXIT,
    "value_exit": $VALUE_EXIT,
    "swing_ok": $SWING_EXIT == 0,
    "value_ok": $VALUE_EXIT == 0,
}
with open("logs/last_run.json", "w") as f:
    json.dump(status, f, indent=2)
PYEOF

{
  echo "--- dashboard ---"
  python dashboard.py
} >> logs/daily.log 2>&1

# Notificación de macOS si algo falló
if [ $SWING_EXIT -ne 0 ] || [ $VALUE_EXIT -ne 0 ]; then
  FAILED=""
  [ $SWING_EXIT -ne 0 ] && FAILED="Swing-US"
  [ $VALUE_EXIT -ne 0 ] && FAILED="${FAILED:+$FAILED + }Value-LATAM"
  osascript -e "display notification \"$FAILED falló. Revisa logs/daily.log (¿Gateway abierto?)\" with title \"⚠️ Bolsa Bot\" sound name \"Basso\"" 2>/dev/null
fi

echo "=== $STAMP END ===" >> logs/daily.log

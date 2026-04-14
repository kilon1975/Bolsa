"""Backtest LATAM (ADRs + Q.MX) con filtro value estilo Buffett pre-entrada."""
import backtest as bt
import value_filter as vf
import json

with open("universe_latam.json") as f:
    syms = json.load(f)["symbols"]

WHITELIST = {"Q.MX"}

print("Evaluando filtro value (una vez, snapshot actual)...")
allowed = set()
for s in syms:
    v = vf.evaluate(s)
    if v.passes or s in WHITELIST:
        allowed.add(s)
        print(f"  ✓ {s} score={v.score}")
    else:
        print(f"  ✗ {s} score={v.score} ({', '.join(v.reasons)})")

print(f"\nUniverso filtrado: {sorted(allowed)}")

bt.TRAILING_STOP = 0.20
bt.TARGET_MULT = 1.40

print("\nDescargando data histórica...")
data = bt.load_data(sorted(allowed))
print(f"Tickers con data: {list(data.keys())}")

trades, equity = bt.run_backtest(data)
m = bt.metrics(trades, equity)
print("\n=== BACKTEST LATAM (trail 20%, target 40%) ===")
for k, v in m.items():
    print(f"{k:25s} {v:>12.2f}" if isinstance(v, float) else f"{k:25s} {v:>12}")

# Trades por ticker
from collections import Counter
per_sym = Counter(t.symbol for t in trades)
print("\nTrades por ticker:")
for s, n in per_sym.most_common():
    print(f"  {s:<8} {n}")

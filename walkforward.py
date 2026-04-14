"""Walk-forward: optimiza en 2019-2022, valida en 2023-2026."""
import backtest as bt
import json
import itertools
import pandas as pd

with open("universe.json") as f:
    symbols = json.load(f)["symbols"]

print("Cargando data...")
full_data = bt.load_data(symbols)

SPLIT = pd.Timestamp("2023-01-01")


def slice_data(data, start, end):
    return {s: d.loc[start:end].copy() for s, d in data.items() if not d.loc[start:end].empty}


def sweep(data, label):
    targets = [1.15, 1.20, 1.25, 1.30, 1.40, 1.50, 1.75]
    trails = [0.08, 0.10, 0.12, 0.15, 0.20]
    results = []
    for tgt, trail in itertools.product(targets, trails):
        bt.TARGET_MULT = tgt
        bt.TRAILING_STOP = trail
        trades, equity = bt.run_backtest(data)
        m = bt.metrics(trades, equity)
        if m:
            results.append((tgt, trail, m))
    return results


in_data = slice_data(full_data, "2019-01-01", "2022-12-31")
oos_data = slice_data(full_data, "2023-01-01", "2026-04-13")

print(f"\n=== IN-SAMPLE 2019-2022 — optimizando ===")
in_res = sweep(in_data, "in")
best = max(in_res, key=lambda r: r[2]["sharpe"])
best_tgt, best_trail, best_m = best
print(f"Mejor IS: target={best_tgt} trail={best_trail} | "
      f"CAGR={best_m['cagr_pct']:.2f}% Sharpe={best_m['sharpe']:.2f} DD={best_m['max_drawdown_pct']:.2f}%")

print(f"\n=== OUT-OF-SAMPLE 2023-2026 — aplicando params óptimos ===")
bt.TARGET_MULT = best_tgt
bt.TRAILING_STOP = best_trail
oos_trades, oos_eq = bt.run_backtest(oos_data)
oos_m = bt.metrics(oos_trades, oos_eq)
print(f"Con target={best_tgt} trail={best_trail}:")
for k, v in oos_m.items():
    print(f"  {k:25s} {v:>12.2f}" if isinstance(v, float) else f"  {k:25s} {v:>12}")

# Comparar con config candidata (target=1.40, trail=0.15)
print(f"\n=== OOS con config sugerida target=1.40 trail=0.15 ===")
bt.TARGET_MULT = 1.40
bt.TRAILING_STOP = 0.15
cand_trades, cand_eq = bt.run_backtest(oos_data)
cand_m = bt.metrics(cand_trades, cand_eq)
for k, v in cand_m.items():
    print(f"  {k:25s} {v:>12.2f}" if isinstance(v, float) else f"  {k:25s} {v:>12}")

# Baseline: config original target=1.40 trail=0.20
print(f"\n=== OOS con config original target=1.40 trail=0.20 ===")
bt.TARGET_MULT = 1.40
bt.TRAILING_STOP = 0.20
base_trades, base_eq = bt.run_backtest(oos_data)
base_m = bt.metrics(base_trades, base_eq)
for k, v in base_m.items():
    print(f"  {k:25s} {v:>12.2f}" if isinstance(v, float) else f"  {k:25s} {v:>12}")

"""Sweep de target y trailing para encontrar mejor combinación."""
import backtest as bt
import json
import itertools

with open("universe.json") as f:
    symbols = json.load(f)["symbols"]

print("Cargando data una sola vez...")
data = bt.load_data(symbols)

targets = [1.15, 1.20, 1.25, 1.30, 1.40, 1.50, 1.75]
trails = [0.08, 0.10, 0.12, 0.15, 0.20]

print(f"\n{'target':>8} {'trail':>8} {'CAGR%':>8} {'Sharpe':>8} {'MaxDD%':>8} {'N':>5} {'Win%':>6} {'Payoff':>7}")
print("-" * 72)
results = []
for tgt, trail in itertools.product(targets, trails):
    bt.TARGET_MULT = tgt
    bt.TRAILING_STOP = trail
    trades, equity = bt.run_backtest(data)
    m = bt.metrics(trades, equity)
    if not m:
        continue
    results.append((tgt, trail, m))
    print(f"{tgt:>8.2f} {trail:>8.2f} {m['cagr_pct']:>8.2f} {m['sharpe']:>8.2f} "
          f"{m['max_drawdown_pct']:>8.2f} {m['n_trades']:>5} {m['win_rate_pct']:>6.1f} {m['payoff_ratio']:>7.2f}")

best_sharpe = max(results, key=lambda r: r[2]["sharpe"])
best_cagr = max(results, key=lambda r: r[2]["cagr_pct"])
print(f"\n>> Mejor Sharpe: target={best_sharpe[0]} trail={best_sharpe[1]} Sharpe={best_sharpe[2]['sharpe']:.2f}")
print(f">> Mejor CAGR:   target={best_cagr[0]} trail={best_cagr[1]} CAGR={best_cagr[2]['cagr_pct']:.2f}%")

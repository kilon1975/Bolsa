"""
Gate de transición paper → live.
Evalúa el estado acumulado (state.json o state_value.json) contra umbrales mínimos.
Si MODE=live y el sleeve no pasa, el bot se niega a operar.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os


@dataclass
class GateThresholds:
    min_trades: int = 30
    min_win_rate: float = 0.40
    min_total_pnl: float = 0.0
    max_current_drawdown: float = 0.15
    min_days_active: int = 90
    min_payoff_ratio: float = 1.5


@dataclass
class GateResult:
    passes: bool
    checks: list[tuple[str, bool, str]]
    metrics: dict


def _parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def evaluate(state_path: str, thresholds: GateThresholds = GateThresholds()) -> GateResult:
    if not os.path.exists(state_path):
        return GateResult(False, [("state_exists", False, f"{state_path} no existe")], {})

    with open(state_path) as f:
        state = json.load(f)

    history = state.get("trade_history", [])
    closed = [t for t in history if t.get("pnl") is not None or t.get("exit_price") is not None]
    n = len(closed)

    pnls = [float(t.get("pnl", 0.0)) for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    total_pnl = sum(pnls)
    win_rate = (len(wins) / n) if n else 0.0
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    payoff = (avg_win / avg_loss) if avg_loss else 0.0

    peak = float(state.get("equity_peak", 0.0) or 0.0)
    current_dd = 0.0
    net_liq = None
    last_equity = state.get("last_net_liq")
    if last_equity and peak > 0:
        current_dd = max(0.0, (peak - float(last_equity)) / peak)
        net_liq = float(last_equity)

    first_trade_dt = None
    for t in history:
        dt = _parse_dt(t.get("opened_at") or t.get("closed_at"))
        if dt:
            first_trade_dt = dt if first_trade_dt is None or dt < first_trade_dt else first_trade_dt
    days_active = 0
    if first_trade_dt:
        days_active = (datetime.now(timezone.utc) - first_trade_dt).days

    checks = [
        ("n_trades", n >= thresholds.min_trades, f"{n} >= {thresholds.min_trades}"),
        ("win_rate", win_rate >= thresholds.min_win_rate,
            f"{win_rate:.1%} >= {thresholds.min_win_rate:.0%}"),
        ("total_pnl", total_pnl >= thresholds.min_total_pnl,
            f"{total_pnl:.2f} >= {thresholds.min_total_pnl:.2f}"),
        ("current_drawdown", current_dd <= thresholds.max_current_drawdown,
            f"{current_dd:.1%} <= {thresholds.max_current_drawdown:.0%}"),
        ("days_active", days_active >= thresholds.min_days_active,
            f"{days_active} >= {thresholds.min_days_active}"),
        ("payoff_ratio", payoff >= thresholds.min_payoff_ratio,
            f"{payoff:.2f} >= {thresholds.min_payoff_ratio:.2f}"),
    ]
    passes = all(ok for _, ok, _ in checks)

    return GateResult(
        passes=passes,
        checks=checks,
        metrics={
            "n_trades": n, "win_rate": win_rate, "total_pnl": total_pnl,
            "avg_win": avg_win, "avg_loss": avg_loss, "payoff_ratio": payoff,
            "equity_peak": peak, "net_liq": net_liq, "current_drawdown": current_dd,
            "days_active": days_active,
        },
    )


def enforce(mode: str, state_path: str, sleeve: str) -> bool:
    """Llama esto al inicio de cada bot. Si MODE=live y el sleeve no pasa gate: aborta."""
    mode = (mode or "paper").strip().lower()
    if mode == "paper":
        print(f"[{sleeve}] MODO PAPER — sin gate")
        return True

    print(f"[{sleeve}] MODO LIVE — evaluando GO-LIVE gate...")
    result = evaluate(state_path)
    print(f"[{sleeve}] Gate metrics: {result.metrics}")
    for name, ok, detail in result.checks:
        mark = "✓" if ok else "✗"
        print(f"  {mark} {name:<20} {detail}")
    if not result.passes:
        print(f"[{sleeve}] ❌ GATE NO PASADO. El bot no operará en LIVE.")
        return False
    print(f"[{sleeve}] ✅ Gate pasado — confirma operación live.")
    reply = input(f"[{sleeve}] Escribe 'ACTIVAR LIVE' para continuar: ").strip()
    if reply != "ACTIVAR LIVE":
        print(f"[{sleeve}] Confirmación no recibida. Abortando.")
        return False
    return True


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "state.json"
    r = evaluate(path)
    print(f"State: {path}")
    print(f"Metrics: {json.dumps(r.metrics, indent=2, default=str)}")
    print(f"{'Check':<20} {'OK':<4} Detalle")
    for name, ok, detail in r.checks:
        mark = "✓" if ok else "✗"
        print(f"{name:<20} {mark:<4} {detail}")
    print(f"\n{'PASA' if r.passes else 'NO PASA'}")

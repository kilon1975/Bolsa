"""
Backtest de la estrategia actual (breakout 20d + trailing stop 20%) sobre el universo.
Usa yfinance (datos diarios, ajustados). Sin fees ni slippage — primera estimación.
"""
import json
import math
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf


START = "2019-01-01"
END = "2026-04-13"
INITIAL_EQUITY = 100_000.0
TRAILING_STOP = 0.20
TARGET_MULT = 1.40   # +40% take profit
RISK_FRAC = 0.02     # 2% equity en riesgo por trade (equivalente a Kelly conservador)
MAX_POSITIONS = 3
MAX_POSITION_PCT = 0.20
CASH_BUFFER = 0.20


@dataclass
class Trade:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    qty: int
    max_price: float
    stop_price: float
    target_price: float
    exit_date: pd.Timestamp | None = None
    exit_price: float | None = None
    reason: str = ""

    @property
    def pnl(self) -> float:
        if self.exit_price is None:
            return 0.0
        return (self.exit_price - self.entry_price) * self.qty

    @property
    def ret_pct(self) -> float:
        if self.exit_price is None:
            return 0.0
        return (self.exit_price / self.entry_price) - 1.0


def load_data(symbols: list[str]) -> dict[str, pd.DataFrame]:
    out = {}
    for sym in symbols:
        df = yf.download(sym, start=START, end=END, progress=False, auto_adjust=True)
        if df.empty:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.lower)
        df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
        df["high20_prev"] = df["high"].rolling(20).max().shift(1)
        df["vol20"] = df["volume"].rolling(20).mean()
        out[sym] = df
    return out


def entry_signal(row) -> bool:
    return (
        pd.notna(row["high20_prev"]) and pd.notna(row["vol20"])
        and row["close"] > row["high20_prev"]
        and row["ema20"] > row["ema50"]
        and row["close"] > row["ema20"]
        and row["volume"] > row["vol20"]
    )


def run_backtest(data: dict[str, pd.DataFrame]) -> tuple[list[Trade], pd.Series]:
    all_dates = sorted(set().union(*[df.index for df in data.values()]))
    open_trades: dict[str, Trade] = {}
    closed: list[Trade] = []
    cash = INITIAL_EQUITY
    equity_curve = []

    for dt in all_dates:
        # mark-to-market + salidas
        for sym in list(open_trades.keys()):
            df = data[sym]
            if dt not in df.index:
                continue
            row = df.loc[dt]
            close = float(row["close"])
            tr = open_trades[sym]
            if close > tr.max_price:
                tr.max_price = close
                tr.stop_price = close * (1 - TRAILING_STOP)

            reason = None
            if close <= tr.stop_price:
                reason = "trailing_stop"
            elif close >= tr.target_price:
                reason = "target"
            elif pd.notna(row["ema20"]) and close < float(row["ema20"]):
                reason = "ema20_break"

            if reason:
                tr.exit_date = dt
                tr.exit_price = close
                tr.reason = reason
                cash += close * tr.qty
                closed.append(tr)
                del open_trades[sym]

        # equity
        pos_value = sum(
            float(data[s].loc[dt, "close"]) * t.qty
            for s, t in open_trades.items() if dt in data[s].index
        )
        equity = cash + pos_value
        equity_curve.append((dt, equity))

        # entradas
        if len(open_trades) >= MAX_POSITIONS:
            continue
        for sym, df in data.items():
            if sym in open_trades or dt not in df.index:
                continue
            if len(open_trades) >= MAX_POSITIONS:
                break
            row = df.loc[dt]
            if not entry_signal(row):
                continue
            price = float(row["close"])
            stop_dist = price * TRAILING_STOP
            qty_risk = math.floor((equity * RISK_FRAC) / stop_dist)
            qty_cap = math.floor((equity * MAX_POSITION_PCT) / price)
            deployable = max(0.0, cash - equity * CASH_BUFFER)
            qty_cash = math.floor(deployable / price)
            qty = max(0, min(qty_risk, qty_cap, qty_cash))
            if qty < 1:
                continue
            open_trades[sym] = Trade(
                symbol=sym, entry_date=dt, entry_price=price, qty=qty,
                max_price=price, stop_price=price * (1 - TRAILING_STOP),
                target_price=price * TARGET_MULT,
            )
            cash -= price * qty

    # cerrar abiertas al final
    last_dt = all_dates[-1]
    for sym, tr in open_trades.items():
        if last_dt in data[sym].index:
            tr.exit_date = last_dt
            tr.exit_price = float(data[sym].loc[last_dt, "close"])
            tr.reason = "end"
            closed.append(tr)

    eq = pd.Series({d: v for d, v in equity_curve}).sort_index()
    return closed, eq


def metrics(trades: list[Trade], equity: pd.Series) -> dict:
    if not trades or equity.empty:
        return {}
    rets = equity.pct_change().dropna()
    total_ret = equity.iloc[-1] / equity.iloc[0] - 1
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (1 + total_ret) ** (1 / years) - 1 if years > 0 else 0
    sharpe = (rets.mean() / rets.std()) * math.sqrt(252) if rets.std() > 0 else 0
    roll_max = equity.cummax()
    dd = (equity - roll_max) / roll_max
    max_dd = dd.min()
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    avg_win = np.mean([t.ret_pct for t in wins]) if wins else 0
    avg_loss = np.mean([t.ret_pct for t in losses]) if losses else 0
    return {
        "initial_equity": float(equity.iloc[0]),
        "final_equity": float(equity.iloc[-1]),
        "total_return_pct": total_ret * 100,
        "cagr_pct": cagr * 100,
        "sharpe": sharpe,
        "max_drawdown_pct": max_dd * 100,
        "n_trades": len(trades),
        "win_rate_pct": (len(wins) / len(trades)) * 100 if trades else 0,
        "avg_win_pct": avg_win * 100,
        "avg_loss_pct": avg_loss * 100,
        "payoff_ratio": (avg_win / abs(avg_loss)) if avg_loss else 0,
        "years": years,
    }


def main():
    with open("universe.json") as f:
        symbols = json.load(f)["symbols"]
    print(f"Descargando data {START} a {END} para {len(symbols)} símbolos...")
    data = load_data(symbols)
    print(f"Data cargada: {list(data.keys())}")
    trades, equity = run_backtest(data)
    m = metrics(trades, equity)
    print("\n=== RESULTADOS ===")
    for k, v in m.items():
        if isinstance(v, float):
            print(f"{k:25s} {v:>12.2f}")
        else:
            print(f"{k:25s} {v:>12}")
    print("\n=== BREAKDOWN POR SALIDA ===")
    reasons = pd.Series([t.reason for t in trades]).value_counts()
    print(reasons.to_string())


if __name__ == "__main__":
    main()

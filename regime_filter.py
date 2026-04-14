"""Filtro de régimen de mercado. Usa SPY vs SMA200.
Bull: SPY > SMA200 (permite entradas swing)
Bear: SPY < SMA200 (bloquea entradas nuevas, mantiene posiciones)
"""
from dataclasses import dataclass
import yfinance as yf


@dataclass
class Regime:
    bull: bool
    spy: float
    sma200: float
    pct_vs_sma: float


def current_regime() -> Regime:
    try:
        df = yf.download("SPY", period="2y", progress=False, auto_adjust=True)
        if df.empty:
            return Regime(True, 0.0, 0.0, 0.0)
        close = df["Close"]
        if hasattr(close, "squeeze"):
            close = close.squeeze()
        spy = float(close.iloc[-1])
        sma200 = float(close.rolling(200).mean().iloc[-1])
        pct = (spy / sma200 - 1) * 100
        return Regime(bull=(spy > sma200), spy=spy, sma200=sma200, pct_vs_sma=pct)
    except Exception:
        return Regime(True, 0.0, 0.0, 0.0)  # fallback permisivo


if __name__ == "__main__":
    r = current_regime()
    label = "BULL ✅" if r.bull else "BEAR ⚠️"
    print(f"Régimen: {label} | SPY={r.spy:.2f} SMA200={r.sma200:.2f} ({r.pct_vs_sma:+.2f}%)")

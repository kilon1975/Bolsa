"""
Filtro value estilo Buffett usando yfinance (gratis).
Criterios suaves — descalifica solo si falla claramente. Data yfinance es imperfecta
para LATAM; ante missing, no penalizar.
"""
from dataclasses import dataclass
import yfinance as yf


@dataclass
class ValueVerdict:
    symbol: str
    passes: bool
    score: int
    reasons: list[str]
    metrics: dict


def evaluate(symbol: str) -> ValueVerdict:
    try:
        info = yf.Ticker(symbol).info or {}
    except Exception as e:
        return ValueVerdict(symbol, True, 0, [f"sin_data:{e}"], {})

    reasons = []
    score = 0
    failed_hard = False

    roe = info.get("returnOnEquity")
    de = info.get("debtToEquity")
    fcf = info.get("freeCashflow")
    pe = info.get("trailingPE")
    pm = info.get("profitMargins")
    current_ratio = info.get("currentRatio")

    if roe is not None:
        if roe >= 0.15:
            score += 2; reasons.append(f"ROE={roe:.1%} OK")
        elif roe >= 0.10:
            score += 1; reasons.append(f"ROE={roe:.1%} ok")
        elif roe < 0:
            failed_hard = True; reasons.append(f"ROE={roe:.1%} NEG")
        else:
            reasons.append(f"ROE={roe:.1%} bajo")

    if de is not None:
        de_ratio = de / 100 if de > 5 else de
        if de_ratio <= 1.0:
            score += 2; reasons.append(f"D/E={de_ratio:.2f} OK")
        elif de_ratio <= 2.0:
            score += 1; reasons.append(f"D/E={de_ratio:.2f} ok")
        else:
            failed_hard = True; reasons.append(f"D/E={de_ratio:.2f} ALTO")

    if fcf is not None:
        if fcf > 0:
            score += 1; reasons.append("FCF+")
        else:
            failed_hard = True; reasons.append("FCF NEG")

    if pe is not None and pe > 0:
        if pe <= 20:
            score += 1; reasons.append(f"PE={pe:.1f} OK")
        elif pe > 40:
            reasons.append(f"PE={pe:.1f} caro")

    if pm is not None:
        if pm >= 0.10:
            score += 1; reasons.append(f"margen={pm:.1%}")
        elif pm < 0:
            failed_hard = True; reasons.append(f"margen={pm:.1%} NEG")

    if current_ratio is not None and current_ratio >= 1.2:
        score += 1

    passes = (not failed_hard) and (score >= 3)
    metrics = {k: v for k, v in {
        "roe": roe, "debt_to_equity": de, "fcf": fcf,
        "pe": pe, "profit_margin": pm, "current_ratio": current_ratio
    }.items() if v is not None}

    return ValueVerdict(symbol=symbol, passes=passes, score=score,
                        reasons=reasons, metrics=metrics)


if __name__ == "__main__":
    import json
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "universe_latam.json"
    with open(path) as f:
        syms = json.load(f)["symbols"]
    print(f"{'Ticker':<8} {'Pasa':<5} {'Score':<5} Razones")
    for s in syms:
        v = evaluate(s)
        flag = "✓" if v.passes else "✗"
        print(f"{s:<8} {flag:<5} {v.score:<5} {', '.join(v.reasons)}")

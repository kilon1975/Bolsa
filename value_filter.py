"""
Filtro value estilo Buffett usando yfinance (gratis).
Criterios suaves — descalifica solo si falla claramente. Data yfinance es imperfecta
para LATAM; ante missing, no penalizar.

v2 (2026-04-20): + consistencia multi-año, + earnings yield vs bono 10Y,
                 + bonus sectores anti-IA / consumo esencial.
"""
from dataclasses import dataclass, field
import yfinance as yf


# Sectores/industrias preferidas (resistentes a IA, consumo obligatorio)
AI_RESISTANT_SECTORS = {
    "Consumer Defensive",  # Coca-Cola, FEMSA, P&G, etc.
    "Utilities",           # Agua, luz, gas regulados
    "Healthcare",          # Farma, hospitales, dispositivos
    "Basic Materials",     # Cemento, cobre, acero
}

# Sectores donde el FCF negativo por capex cíclico es aceptable (si ganancia neta+)
CAPEX_HEAVY_SECTORS = {"Utilities", "Basic Materials"}

AI_VULNERABLE_SECTORS = {
    "Technology",              # Software puro
    "Communication Services",  # Medios, telecom vulnerable parcial
    "Financial Services",      # Banca tradicional
}

# Bonos del Tesoro 10Y (fallback si no se puede fetchear)
TREASURY_10Y_FALLBACK = 0.045


@dataclass
class ValueVerdict:
    symbol: str
    passes: bool
    score: int
    reasons: list[str]
    metrics: dict
    sector: str = ""
    ai_bucket: str = "neutro"   # "resistente" | "neutro" | "vulnerable"


def _get_treasury_10y() -> float:
    try:
        tnx = yf.Ticker("^TNX").history(period="5d")
        if not tnx.empty:
            return float(tnx["Close"].iloc[-1]) / 100.0
    except Exception:
        pass
    return TREASURY_10Y_FALLBACK


def _check_consistency(ticker_obj) -> dict:
    """Revisa ventas y ganancias de los últimos 3-4 años (lo que yfinance trae)."""
    out = {}
    try:
        fin = ticker_obj.financials
        if fin is not None and not fin.empty:
            if "Total Revenue" in fin.index:
                revs = fin.loc["Total Revenue"].dropna()
                if len(revs) >= 3:
                    # yfinance da orden descendente (reciente primero)
                    revs_chron = revs.iloc[::-1].values
                    out["rev_growing"] = all(
                        revs_chron[i+1] >= revs_chron[i] * 0.95
                        for i in range(len(revs_chron)-1)
                    )
                    out["rev_years"] = len(revs_chron)
            if "Net Income" in fin.index:
                ni = fin.loc["Net Income"].dropna()
                if len(ni) >= 3:
                    ni_chron = ni.iloc[::-1].values
                    out["ni_positive"] = all(v > 0 for v in ni_chron)
                    out["ni_years"] = len(ni_chron)
    except Exception:
        pass
    return out


def _ai_bucket(sector: str, industry: str = "") -> str:
    if sector in AI_RESISTANT_SECTORS:
        return "resistente"
    # Excepción: las aseguradoras son Financial Services pero Buffett-friendly
    # (moat regulatorio, pricing power, IA mejora el negocio sin disrumpirlo).
    if sector == "Financial Services" and "Insurance" in industry:
        return "neutro"
    if sector in AI_VULNERABLE_SECTORS:
        return "vulnerable"
    return "neutro"


def evaluate(symbol: str) -> ValueVerdict:
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
    except Exception as e:
        return ValueVerdict(symbol, True, 0, [f"sin_data:{e}"], {})

    reasons = []
    score = 0
    failed_hard = False

    # === Buffett clásico (foto del momento) ===
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

    # FCF: negativo descalifica, EXCEPTO en sectores capex-intensivos (agua, mining,
    # cemento, utilities) donde es normal invertir fuerte. Ahí solo penaliza score.
    sector_preview = (info.get("sector") or "")
    if fcf is not None:
        if fcf > 0:
            score += 1; reasons.append("FCF+")
        elif sector_preview in CAPEX_HEAVY_SECTORS:
            reasons.append(f"FCF NEG (ok en {sector_preview}, capex)")
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

    # === Nuevo: Earnings yield vs bono 10Y (regla Buffett) ===
    if pe is not None and pe > 0:
        earnings_yield = 1.0 / pe
        bond_yield = _get_treasury_10y()
        ratio = earnings_yield / bond_yield if bond_yield > 0 else 0
        if ratio >= 2.0:
            score += 2; reasons.append(f"EY/bono={ratio:.1f}x OK")
        elif ratio >= 1.5:
            score += 1; reasons.append(f"EY/bono={ratio:.1f}x")
        elif ratio < 1.0:
            reasons.append(f"EY/bono={ratio:.1f}x bajo")

    # === Nuevo: consistencia multi-año ===
    consistency = _check_consistency(ticker)
    if consistency.get("rev_growing"):
        score += 1; reasons.append(f"ventas↑ {consistency.get('rev_years', 0)}y")
    if consistency.get("ni_positive"):
        score += 1; reasons.append(f"ganancia+ {consistency.get('ni_years', 0)}y")

    # === Nuevo: bonus sectorial anti-IA / consumo esencial ===
    sector = info.get("sector", "") or ""
    industry = info.get("industry", "") or ""
    bucket = _ai_bucket(sector, industry)
    if bucket == "resistente":
        score += 2; reasons.append(f"sector {sector} (anti-IA)")
    elif bucket == "vulnerable":
        score -= 1; reasons.append(f"sector {sector} (vulnerable IA)")

    passes = (not failed_hard) and (score >= 3)
    metrics = {k: v for k, v in {
        "roe": roe, "debt_to_equity": de, "fcf": fcf,
        "pe": pe, "profit_margin": pm, "current_ratio": current_ratio,
        "sector": sector, "industry": industry,
        **consistency,
    }.items() if v is not None and v != ""}

    return ValueVerdict(symbol=symbol, passes=passes, score=score,
                        reasons=reasons, metrics=metrics,
                        sector=sector, ai_bucket=bucket)


if __name__ == "__main__":
    import json
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "universe_latam.json"
    with open(path) as f:
        syms = json.load(f)["symbols"]
    print(f"{'Ticker':<8} {'Pasa':<5} {'Score':<5} {'Bucket':<12} Razones")
    for s in syms:
        v = evaluate(s)
        flag = "✓" if v.passes else "✗"
        print(f"{s:<8} {flag:<5} {v.score:<5} {v.ai_bucket:<12} {', '.join(v.reasons)}")

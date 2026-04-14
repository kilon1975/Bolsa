"""Chequeo de noticias via yfinance. Red flags por keywords en titulares recientes."""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import yfinance as yf


RED_FLAGS = [
    "fraud", "fraude", "investigation", "investigación", "sec charges", "lawsuit", "demanda",
    "bankruptcy", "quiebra", "default", "downgrade", "rebaja", "guidance cut",
    "resign", "renuncia", "ceo steps down", "scandal", "escándalo",
    "probe", "accounting", "restate", "restatement", "profit warning",
]

POSITIVE_FLAGS = [
    "beats", "supera", "upgrade", "mejora", "raises guidance",
    "record earnings", "acquisition approved",
]


@dataclass
class NewsVerdict:
    symbol: str
    n_headlines: int
    red_flags: list[str]
    positive_flags: list[str]
    recent: list[dict]


def check(symbol: str, days: int = 14, limit: int = 10) -> NewsVerdict:
    try:
        news = yf.Ticker(symbol).news or []
    except Exception:
        return NewsVerdict(symbol, 0, [], [], [])

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent, red, pos = [], [], []
    for item in news[:limit]:
        content = item.get("content") or item
        title = (content.get("title") or item.get("title") or "").strip()
        pub_raw = content.get("pubDate") or item.get("providerPublishTime")
        try:
            if isinstance(pub_raw, (int, float)):
                pub_dt = datetime.fromtimestamp(pub_raw, tz=timezone.utc)
            elif isinstance(pub_raw, str):
                pub_dt = datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
            else:
                pub_dt = datetime.now(timezone.utc)
        except Exception:
            pub_dt = datetime.now(timezone.utc)
        if pub_dt < cutoff:
            continue
        t_low = title.lower()
        hit_red = [k for k in RED_FLAGS if k in t_low]
        hit_pos = [k for k in POSITIVE_FLAGS if k in t_low]
        red += hit_red
        pos += hit_pos
        recent.append({
            "title": title,
            "published": pub_dt.isoformat(),
            "red_flags": hit_red,
            "positive_flags": hit_pos,
        })

    return NewsVerdict(symbol, len(recent), list(set(red)), list(set(pos)), recent)


if __name__ == "__main__":
    import json
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "universe_latam.json"
    with open(path) as f:
        syms = json.load(f)["symbols"]
    for s in syms:
        v = check(s)
        flag = "🚩" if v.red_flags else ("✓" if v.positive_flags else " ")
        print(f"{s:<8} {flag} headlines={v.n_headlines:<3} red={v.red_flags} pos={v.positive_flags}")
        for h in v.recent[:3]:
            print(f"    · {h['title'][:100]}")

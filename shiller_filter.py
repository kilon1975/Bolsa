"""Filtro Shiller CAPE: bloquea entradas nuevas cuando el S&P 500 está en zona de burbuja.

Cachea el valor en logs/cape.json (se refresca cada 7 días). Si no puede obtener
el valor actual, usa el cacheado o un default conservador.
"""
import json
import logging
import os
import re
from datetime import datetime, timezone
from urllib.request import Request, urlopen

CACHE_PATH = "logs/cape.json"
CACHE_TTL_DAYS = 7
DEFAULT_CAPE = 36.0  # valor fallback si no se puede obtener nada (abril 2026)
SOURCE_URL = "https://www.multpl.com/shiller-pe"


def _zone(cape: float) -> str:
    if cape < 15: return "barato"
    if cape < 20: return "normal"
    if cape < 30: return "caro"
    if cape < 40: return "muy_caro"
    return "burbuja"


def _fetch_cape_from_web() -> float | None:
    try:
        req = Request(SOURCE_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8", errors="ignore")
        m = re.search(r'id="current"[^>]*>\s*([\d.]+)', html)
        if m:
            return float(m.group(1))
        m = re.search(r'Current Shiller PE[^\d]+([\d.]+)', html, re.IGNORECASE)
        if m:
            return float(m.group(1))
    except Exception as e:
        logging.getLogger("shiller").warning(f"fetch CAPE falló: {e}")
    return None


def get_current_cape(force_refresh: bool = False) -> tuple[float, str, str]:
    """Devuelve (cape, source, fetched_at_iso). Source: 'web' | 'cache' | 'default'."""
    cached = None
    if os.path.exists(CACHE_PATH) and not force_refresh:
        try:
            with open(CACHE_PATH) as f:
                cached = json.load(f)
            dt = datetime.fromisoformat(cached["fetched_at"].replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
            if age_days < CACHE_TTL_DAYS:
                return cached["cape"], "cache", cached["fetched_at"]
        except Exception:
            cached = None

    web = _fetch_cape_from_web()
    if web is not None:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w") as f:
            json.dump({"cape": web, "fetched_at": now_iso, "source": SOURCE_URL}, f, indent=2)
        return web, "web", now_iso

    if cached:
        return cached["cape"], "cache_expired", cached["fetched_at"]

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return DEFAULT_CAPE, "default", now_iso


def allow_new_entry(threshold: float) -> tuple[bool, float, str]:
    """Devuelve (allow, cape, zone). Si cape > threshold, no permite nuevas entradas."""
    cape, _, _ = get_current_cape()
    zone = _zone(cape)
    return cape <= threshold, cape, zone


if __name__ == "__main__":
    cape, src, ts = get_current_cape(force_refresh=True)
    print(f"CAPE actual: {cape:.2f} (zona: {_zone(cape)}) — fuente: {src} @ {ts}")

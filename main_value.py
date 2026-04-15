"""
Motor value-LATAM estilo Buffett. Paralelo al swing-US.
- Corre semanal (idempotente, se puede correr a diario sin problema)
- Filtro value pre-compra, hold indefinido, vende solo si tesis rota
- Equal-weight hasta N posiciones, dentro de un allocation del NetLiq
"""
import json
import logging
import math
import os
from datetime import datetime, timezone
from dataclasses import dataclass, field

from dotenv import load_dotenv
from ib_insync import IB, Stock, MarketOrder

from value_filter import evaluate as value_evaluate
from news_check import check as news_check

load_dotenv()

MAX_CHUNK = int(os.getenv("ORDER_MAX_CHUNK", "500"))


def _place_chunked(ib, contract, action: str, qty: int, max_chunk: int = MAX_CHUNK, timeout_s: int = 15):
    """Parte la orden en chunks para evitar precautionary limits del preset IB.
    Devuelve (filled_total, avg_price, last_status)."""
    filled_total = 0.0
    notional = 0.0
    last_status = "Unknown"
    remaining = int(qty)
    while remaining > 0:
        chunk = min(max_chunk, remaining)
        order = MarketOrder(action, chunk)
        order.tif = "DAY"
        trade = ib.placeOrder(contract, order)
        for _ in range(timeout_s * 2):
            ib.sleep(0.5)
            if trade.isDone():
                break
        f = float(trade.orderStatus.filled or 0)
        p = float(trade.orderStatus.avgFillPrice or 0)
        last_status = trade.orderStatus.status
        filled_total += f
        notional += f * p
        if f < chunk:
            break
        remaining -= chunk
    avg = (notional / filled_total) if filled_total > 0 else 0.0
    return filled_total, avg, last_status


def _env(name, default=""):
    return os.getenv(name, default).strip()


def _envf(name, default):
    v = os.getenv(name)
    return float(v) if v not in (None, "") else default


def _envi(name, default):
    v = os.getenv(name)
    return int(v) if v not in (None, "") else default


@dataclass
class ValueCfg:
    ib_host: str = _env("IB_HOST", "127.0.0.1")
    ib_port: int = _envi("IB_PORT", 4002)
    ib_client_id: int = _envi("VALUE_CLIENT_ID", 27)
    ib_account: str = _env("IB_ACCOUNT", "")
    universe_file: str = _env("VALUE_UNIVERSE_FILE", "universe_latam.json")
    whitelist: list[str] = field(default_factory=list)
    state_file: str = _env("VALUE_STATE_FILE", "state_value.json")
    logs_dir: str = _env("LOGS_DIR", "logs")
    alloc_pct: float = _envf("VALUE_ALLOC_PCT", 0.40)     # % NetLiq asignado al sleeve
    max_positions: int = _envi("VALUE_MAX_POSITIONS", 6)
    rebalance_drift: float = _envf("VALUE_REBALANCE_DRIFT", 0.15)
    min_order_usd: float = _envf("VALUE_MIN_ORDER_USD", 500.0)

    def __post_init__(self):
        raw = _env("VALUE_WHITELIST", "Q.MX")
        self.whitelist = [s.strip().upper() for s in raw.split(",") if s.strip()]


def _now(): return datetime.now(timezone.utc)


def _build_contract(symbol: str):
    if symbol.endswith(".MX"):
        return Stock(symbol[:-3], "MEXI", "MXN")
    return Stock(symbol, "SMART", "USD")


def _thesis_broken(verdict) -> tuple[bool, str]:
    """Salida estilo Buffett: tesis rota, no precio.
    Sale si fundamentales muestran deterioro serio."""
    m = verdict.metrics
    reasons = []
    roe = m.get("roe")
    if roe is not None and roe < 0.05:
        reasons.append(f"ROE bajo {roe:.1%}")
    de = m.get("debt_to_equity")
    if de is not None:
        de_r = de / 100 if de > 5 else de
        if de_r > 3.0:
            reasons.append(f"D/E excesivo {de_r:.1f}")
    pm = m.get("profit_margin")
    if pm is not None and pm < 0:
        reasons.append(f"margen negativo {pm:.1%}")
    return (len(reasons) > 0), "; ".join(reasons)


class ValueBot:
    def __init__(self, cfg: ValueCfg):
        self.cfg = cfg
        self.ib = IB()
        self.logger = logging.getLogger("value_bot")
        if not self.logger.handlers:
            h = logging.StreamHandler()
            h.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
            self.logger.addHandler(h)
            self.logger.setLevel(logging.INFO)
        os.makedirs(cfg.logs_dir, exist_ok=True)
        self._log_path = os.path.join(cfg.logs_dir, f"value-{_now().strftime('%Y%m%d')}.jsonl")
        self.state = self._load_state()

    def _load_state(self):
        if os.path.exists(self.cfg.state_file):
            with open(self.cfg.state_file) as f:
                return json.load(f)
        return {"positions": {}, "trade_history": [], "last_run": None}

    def _save(self):
        with open(self.cfg.state_file, "w") as f:
            json.dump(self.state, f, indent=2)

    def jlog(self, event, **fields):
        rec = {"ts": _now().isoformat(), "event": event, **fields}
        with open(self._log_path, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")

    def connect(self):
        self.logger.info(f"Conectando IB {self.cfg.ib_host}:{self.cfg.ib_port} clientId={self.cfg.ib_client_id}")
        self.ib.connect(self.cfg.ib_host, self.cfg.ib_port, clientId=self.cfg.ib_client_id)
        self.ib.reqMarketDataType(3)  # 3=delayed, funciona sin suscripción live
        self.jlog("connected")

    def disconnect(self):
        if self.ib.isConnected():
            self.ib.disconnect()
            self.jlog("disconnected")

    def _net_liq(self) -> float:
        for v in self.ib.accountSummary():
            if self.cfg.ib_account and v.account != self.cfg.ib_account:
                continue
            if v.tag == "NetLiquidation":
                try: return float(v.value)
                except: pass
        return 0.0

    def _positions(self) -> dict:
        out = {}
        for p in self.ib.positions():
            if self.cfg.ib_account and p.account != self.cfg.ib_account:
                continue
            sym = getattr(p.contract, "symbol", None)
            if sym and float(p.position) != 0:
                out[sym] = p
        return out

    def _universe(self) -> list[str]:
        with open(self.cfg.universe_file) as f:
            return json.load(f)["symbols"]

    def _price(self, contract) -> float:
        import math as _m
        [t] = self.ib.reqTickers(contract)
        candidates = [t.marketPrice(), t.last, t.close, t.bid, t.ask,
                      getattr(t, "delayedLast", None), getattr(t, "delayedClose", None)]
        for candidate in candidates:
            try:
                v = float(candidate)
            except (TypeError, ValueError):
                continue
            if v > 0 and not _m.isnan(v):
                return v
        return 0.0

    def _sync_state_with_ib(self):
        """Reconcilia state con posiciones reales en IBKR.
        Añade ausentes (órdenes GTC que llenaron), quita las que ya no existen."""
        positions = self._positions()
        tracked = self.state.setdefault("positions", {})
        for sym, p in positions.items():
            if sym not in tracked:
                tracked[sym] = {
                    "entry_price": round(float(p.avgCost), 4) if p.avgCost else 0.0,
                    "quantity": int(abs(float(p.position))),
                    "opened_at": _now().isoformat(),
                    "score_at_entry": None,
                    "source": "ib_sync",
                }
                self.jlog("state_sync_add", symbol=sym, qty=p.position)
        for sym in list(tracked.keys()):
            if sym not in positions:
                tracked.pop(sym, None)
                self.jlog("state_sync_remove", symbol=sym)
        self._save()

    def run_once(self):
        self._sync_state_with_ib()
        net_liq = self._net_liq()
        alloc_usd = net_liq * self.cfg.alloc_pct
        per_slot_usd = alloc_usd / self.cfg.max_positions
        self.logger.info(f"NetLiq={net_liq:.0f} | value_alloc={alloc_usd:.0f} | per_slot={per_slot_usd:.0f}")
        self.jlog("start", net_liq=net_liq, alloc_usd=alloc_usd, per_slot_usd=per_slot_usd)

        universe = self._universe()
        verdicts = {s: value_evaluate(s) for s in universe}
        for s, v in verdicts.items():
            nv = news_check(s)
            if nv.red_flags:
                self.logger.warning(f"🚩 {s} red flags noticias: {nv.red_flags}")
            self.jlog("screen", symbol=s, passes=v.passes, score=v.score,
                      whitelist=s in self.cfg.whitelist, reasons=v.reasons, metrics=v.metrics,
                      news_headlines=nv.n_headlines, news_red=nv.red_flags, news_pos=nv.positive_flags,
                      news_recent=[h["title"] for h in nv.recent[:3]])

        positions = self._positions()

        # 1) SALIDAS por tesis rota
        for sym, p in positions.items():
            if sym not in verdicts:
                continue
            broken, reason = _thesis_broken(verdicts[sym])
            if not broken:
                continue
            qty = int(abs(float(p.position)))
            if qty < 1:
                continue
            self.logger.warning(f"SALIDA tesis-rota {sym} | {reason}")
            self.jlog("exit_thesis", symbol=sym, reason=reason, qty=qty)
            contract = _build_contract(sym)
            self.ib.qualifyContracts(contract)
            filled_qty, fill, status = _place_chunked(self.ib, contract, "SELL", qty)
            self.jlog("exit_chunked", symbol=sym, requested=qty, filled=filled_qty, fill=fill, status=status)
            self.state.setdefault("trade_history", []).append({
                "symbol": sym, "exit_price": fill, "quantity": filled_qty,
                "reason": reason, "closed_at": _now().isoformat()
            })
            self._save()

        # 2) ENTRADAS equal-weight para los que pasan
        positions = self._positions()
        held = set(positions.keys())
        slots_used = len(held)
        candidates = [s for s in universe
                      if (verdicts[s].passes or s in self.cfg.whitelist)
                      and s not in held]

        for sym in candidates:
            if slots_used >= self.cfg.max_positions:
                self.logger.info("Max posiciones value alcanzado")
                break
            contract = _build_contract(sym)
            try:
                self.ib.qualifyContracts(contract)
            except Exception as e:
                self.logger.warning(f"No se puede cualificar {sym}: {e}")
                self.jlog("qualify_fail", symbol=sym, error=str(e))
                continue
            price = self._price(contract)
            if price <= 0:
                self.logger.warning(f"Precio inválido {sym}")
                self.jlog("price_fail", symbol=sym)
                continue
            # MXN→USD aprox para sizing (precio ya en moneda local; alloc_usd es USD)
            # Simplificación v1: asume price y alloc en misma moneda via NetLiq consolidado
            qty = math.floor(per_slot_usd / price)
            if qty * price < self.cfg.min_order_usd:
                self.logger.info(f"Orden demasiado chica {sym}: {qty}x{price:.2f}")
                self.jlog("size_small", symbol=sym, qty=qty, price=price)
                continue
            self.logger.warning(f"ENTRADA value {sym} | qty={qty} price={price:.2f}")
            self.jlog("entry_value", symbol=sym, qty=qty, price=price,
                      score=verdicts[sym].score, metrics=verdicts[sym].metrics)
            filled_qty, fill, status = _place_chunked(self.ib, contract, "BUY", qty)
            if filled_qty < 1 or fill <= 0:
                self.logger.warning(f"Orden {sym} no ejecutada | status={status} filled={filled_qty}")
                self.jlog("entry_not_filled", symbol=sym, status=status, filled=filled_qty)
                continue
            self.state.setdefault("positions", {})[sym] = {
                "entry_price": round(fill, 4),
                "quantity": int(filled_qty),
                "opened_at": _now().isoformat(),
                "score_at_entry": verdicts[sym].score,
            }
            self._save()
            self.jlog("entry_filled", symbol=sym, fill=fill, qty=filled_qty)
            slots_used += 1

        self.state["last_run"] = _now().isoformat()
        self._save()
        self.jlog("done", positions=list(self._positions().keys()))


def main():
    cfg = ValueCfg()
    from go_live_gate import enforce as _gate_enforce
    mode = _env("MODE", "paper")
    if not _gate_enforce(mode, cfg.state_file, "value-LATAM"):
        return
    bot = ValueBot(cfg)
    try:
        bot.connect()
        bot.run_once()
    finally:
        bot.disconnect()


if __name__ == "__main__":
    main()

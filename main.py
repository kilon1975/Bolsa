import json
import logging
import math
import os
from datetime import datetime, timezone, timedelta

import pandas as pd
from ib_insync import IB, Stock, MarketOrder, util

from config import Settings


def _today_utc() -> datetime:
    return datetime.now(timezone.utc)


def _business_days_between(d1: datetime, d2: datetime) -> int:
    if d2 < d1:
        d1, d2 = d2, d1
    days = 0
    current = d1.date()
    end = d2.date()
    while current < end:
        current += timedelta(days=1)
        if current.weekday() < 5:
            days += 1
    return days


class IBSwingBot:
    def __init__(self, settings: Settings):
        self.cfg = settings
        self.ib = IB()
        self.state = self._load_state()
        self.logger = self._build_logger()
        self._jsonl_path = self._prepare_jsonl()

    def _build_logger(self):
        logger = logging.getLogger("ib_swing_bot")
        logger.setLevel(getattr(logging, self.cfg.log_level.upper(), logging.INFO))
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        return logger

    def _prepare_jsonl(self) -> str:
        os.makedirs(self.cfg.logs_dir, exist_ok=True)
        stamp = _today_utc().strftime("%Y%m%d")
        return os.path.join(self.cfg.logs_dir, f"run-{stamp}.jsonl")

    def jlog(self, event: str, **fields):
        record = {"ts": _today_utc().isoformat(), "event": event, **fields}
        with open(self._jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def _load_state(self):
        if os.path.exists(self.cfg.state_file):
            with open(self.cfg.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {}
        data.setdefault("positions", {})
        data.setdefault("trade_history", [])
        data.setdefault("equity_peak", 0.0)
        data.setdefault("paused_reason", None)
        return data

    def _save_state(self):
        with open(self.cfg.state_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def connect(self):
        self.logger.info(f"Conectando a IBKR en {self.cfg.ib_host}:{self.cfg.ib_port} clientId={self.cfg.ib_client_id}")
        self.ib.connect(self.cfg.ib_host, self.cfg.ib_port, clientId=self.cfg.ib_client_id)
        self.logger.info("Conexión establecida")
        self.jlog("connected", host=self.cfg.ib_host, port=self.cfg.ib_port,
                  universe_version=self.cfg.universe_version)

    def disconnect(self):
        if self.ib.isConnected():
            self.ib.disconnect()
            self.logger.info("Conexión cerrada")
            self.jlog("disconnected")

    def _account_values(self):
        values = self.ib.accountSummary()
        if self.cfg.ib_account:
            values = [v for v in values if v.account == self.cfg.ib_account]
        return values

    def get_account_metric(self, tag: str, default: float = 0.0) -> float:
        for item in self._account_values():
            if item.tag == tag:
                try:
                    return float(item.value)
                except Exception:
                    return default
        return default

    def get_net_liq(self) -> float:
        return self.get_account_metric("NetLiquidation", 0.0)

    def get_available_funds(self) -> float:
        value = self.get_account_metric("AvailableFunds", 0.0)
        if value <= 0:
            value = self.get_account_metric("BuyingPower", 0.0)
        return value

    def get_contract(self, symbol: str):
        if symbol.endswith(".MX"):
            base = symbol[:-3]
            contract = Stock(base, "MEXI", "MXN")
        else:
            contract = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(contract)
        return contract

    def get_history(self, symbol: str, duration: str):
        contract = self.get_contract(symbol)
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=self.cfg.bar_size,
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
        )
        df = util.df(bars)
        if df is None or df.empty:
            return contract, pd.DataFrame()
        df = df.copy()
        df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
        prev_close = df["close"].shift(1)
        tr = pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - prev_close).abs(),
                (df["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        df["atr14"] = tr.rolling(14).mean()
        df["high20_prev"] = df["high"].rolling(20).max().shift(1)
        df["vol20"] = df["volume"].rolling(20).mean()
        return contract, df

    def _is_stale(self, df: pd.DataFrame) -> bool:
        try:
            last_dt = pd.to_datetime(df.iloc[-1]["date"])
            if last_dt.tzinfo is None:
                last_dt = last_dt.tz_localize("UTC")
            gap = _business_days_between(last_dt.to_pydatetime(), _today_utc())
            return gap > self.cfg.max_stale_bars_days
        except Exception:
            return False

    def get_open_positions(self):
        positions = []
        for p in self.ib.positions():
            if self.cfg.ib_account and p.account != self.cfg.ib_account:
                continue
            if float(p.position) != 0:
                positions.append(p)
        return positions

    def get_position_map(self):
        out = {}
        for p in self.get_open_positions():
            symbol = getattr(p.contract, "symbol", None)
            if symbol:
                out[symbol] = p
        return out

    def _kelly_risk_fraction(self) -> float:
        history = self.state.get("trade_history", [])
        closed = [t for t in history if t.get("pnl") is not None]
        if len(closed) >= 20:
            wins = [t["pnl"] for t in closed if t["pnl"] > 0]
            losses = [abs(t["pnl"]) for t in closed if t["pnl"] < 0]
            if wins and losses:
                p = len(wins) / len(closed)
                R = (sum(wins) / len(wins)) / (sum(losses) / len(losses))
            else:
                p, R = self.cfg.assumed_win_rate, self.cfg.assumed_payoff
        else:
            p, R = self.cfg.assumed_win_rate, self.cfg.assumed_payoff
        edge = p - (1 - p) / max(R, 1e-6)
        kelly = max(0.0, edge) * self.cfg.kelly_fraction
        return min(kelly, 0.05)

    def calc_order_size(self, price: float, net_liq: float, available_funds: float) -> tuple[int, dict]:
        debug = {}
        if price <= 0 or net_liq <= 0:
            return 0, debug

        stop_distance = price * self.cfg.trailing_stop_pct
        if self.cfg.use_kelly:
            risk_frac = self._kelly_risk_fraction()
        else:
            risk_frac = self.cfg.risk_per_trade
        debug["risk_frac"] = risk_frac

        if risk_frac <= 0:
            return 0, debug

        risk_budget = net_liq * risk_frac
        qty_by_risk = math.floor(risk_budget / stop_distance)

        max_position_value = net_liq * self.cfg.max_position_pct
        qty_by_position_cap = math.floor(max_position_value / price)

        deployable_cash = max(0.0, available_funds * (1.0 - self.cfg.min_cash_buffer_pct))
        qty_by_cash = math.floor(deployable_cash / price)

        qty = max(0, min(qty_by_risk, qty_by_position_cap, qty_by_cash))
        debug.update(qty_by_risk=qty_by_risk, qty_by_position_cap=qty_by_position_cap,
                     qty_by_cash=qty_by_cash, stop_distance=stop_distance)
        return qty, debug

    def has_entry_signal(self, df: pd.DataFrame) -> bool:
        if len(df) < 60:
            return False
        last = df.iloc[-1]
        checks = [
            pd.notna(last["high20_prev"]),
            pd.notna(last["atr14"]),
            pd.notna(last["vol20"]),
            last["close"] > last["high20_prev"],
            last["ema20"] > last["ema50"],
            last["close"] > last["ema20"],
            last["volume"] > last["vol20"],
        ]
        return all(checks)

    def place_market_buy(self, contract, quantity: int):
        order = MarketOrder("BUY", quantity)
        trade = self.ib.placeOrder(contract, order)
        for _ in range(20):
            self.ib.sleep(0.5)
            if trade.isDone():
                break
        return trade

    def place_market_sell(self, contract, quantity: int):
        order = MarketOrder("SELL", quantity)
        trade = self.ib.placeOrder(contract, order)
        for _ in range(20):
            self.ib.sleep(0.5)
            if trade.isDone():
                break
        return trade

    def sync_state_with_positions(self):
        pos_map = self.get_position_map()
        tracked = self.state.get("positions", {})
        for symbol in list(tracked.keys()):
            if symbol not in pos_map:
                tracked.pop(symbol, None)
        self.state["positions"] = tracked
        self._save_state()

    def update_equity_and_breakers(self) -> bool:
        net_liq = self.get_net_liq()
        peak = float(self.state.get("equity_peak") or 0.0)
        if net_liq > peak:
            peak = net_liq
            self.state["equity_peak"] = peak
        drawdown = 0.0 if peak <= 0 else (peak - net_liq) / peak

        history = self.state.get("trade_history", [])
        losses_streak = 0
        for t in reversed(history):
            pnl = t.get("pnl")
            if pnl is None:
                continue
            if pnl < 0:
                losses_streak += 1
            else:
                break

        paused = None
        if drawdown >= self.cfg.max_drawdown_pct:
            paused = f"drawdown {drawdown:.1%} >= {self.cfg.max_drawdown_pct:.0%}"
        elif losses_streak >= self.cfg.max_losses_streak:
            paused = f"losses_streak {losses_streak} >= {self.cfg.max_losses_streak}"

        self.state["paused_reason"] = paused
        self._save_state()
        self.jlog("equity", net_liq=net_liq, peak=peak, drawdown=drawdown,
                  losses_streak=losses_streak, paused=paused)

        if paused:
            self.logger.warning(f"CIRCUIT BREAKER activo: {paused}. No se abrirán entradas.")
        return paused is None

    def evaluate_exits(self):
        pos_map = self.get_position_map()
        tracked = self.state.get("positions", {})

        for symbol, p in pos_map.items():
            if float(p.position) <= 0:
                continue

            contract, df = self.get_history(symbol, self.cfg.exit_duration)
            if df.empty:
                self.logger.warning(f"Sin datos para evaluar salida en {symbol}")
                continue
            if self._is_stale(df):
                self.logger.warning(f"Data stale en {symbol}, skip salida")
                self.jlog("stale_data", symbol=symbol, phase="exit")
                continue

            last = df.iloc[-1]
            close = float(last["close"])
            ema20 = float(last["ema20"]) if pd.notna(last["ema20"]) else close
            avg_cost = float(p.avgCost) if float(p.avgCost) > 0 else close

            meta = tracked.get(symbol, {})
            max_price = float(meta.get("max_price", avg_cost))
            if close > max_price:
                max_price = close
                meta["max_price"] = max_price
                tracked[symbol] = meta
                self._save_state()

            trail_stop = max_price * (1.0 - self.cfg.trailing_stop_pct)
            target_price = float(meta.get("target_price", avg_cost * (1 + self.cfg.trailing_stop_pct * 2)))

            reason = None
            if close <= trail_stop:
                reason = "trailing_stop"
            elif close >= target_price:
                reason = "target"
            elif pd.notna(last["ema20"]) and close < ema20:
                reason = "ema20_break"

            if not reason:
                self.logger.info(f"Mantener {symbol} | close={close:.2f} trail={trail_stop:.2f} max={max_price:.2f}")
                self.jlog("hold", symbol=symbol, close=close, trail_stop=trail_stop, max_price=max_price)
                continue

            qty = int(abs(p.position))
            self.logger.warning(f"SALIDA {symbol} | motivo={reason} | qty={qty}")
            self.jlog("exit_signal", symbol=symbol, reason=reason, close=close,
                      trail_stop=trail_stop, max_price=max_price)
            trade = self.place_market_sell(contract, qty)
            fill = float(trade.orderStatus.avgFillPrice) if trade.orderStatus.avgFillPrice else close
            entry = float(meta.get("entry_price", avg_cost))
            pnl = (fill - entry) * qty
            self.state.setdefault("trade_history", []).append({
                "symbol": symbol,
                "entry_price": entry,
                "exit_price": fill,
                "quantity": qty,
                "pnl": round(pnl, 2),
                "reason": reason,
                "closed_at": _today_utc().isoformat(),
            })
            self.logger.info(f"Orden de salida enviada en {symbol} | status={trade.orderStatus.status} pnl={pnl:.2f}")
            self.jlog("exit_filled", symbol=symbol, fill=fill, pnl=pnl, status=trade.orderStatus.status)
            tracked.pop(symbol, None)
            self._save_state()

    def evaluate_entries(self):
        net_liq = self.get_net_liq()
        available_funds = self.get_available_funds()
        pos_map = self.get_position_map()
        tracked = self.state.get("positions", {})

        long_positions_count = sum(1 for p in pos_map.values() if float(p.position) > 0)

        for symbol in self.cfg.symbols:
            if long_positions_count >= self.cfg.max_positions:
                self.logger.info("Máximo de posiciones alcanzado")
                break

            if symbol in pos_map and float(pos_map[symbol].position) > 0:
                continue

            contract, df = self.get_history(symbol, self.cfg.scan_duration)
            if df.empty:
                self.logger.warning(f"Sin datos para entrada en {symbol}")
                continue
            if self._is_stale(df):
                self.logger.warning(f"Data stale en {symbol}, skip entrada")
                self.jlog("stale_data", symbol=symbol, phase="entry")
                continue

            if not self.has_entry_signal(df):
                self.logger.info(f"Sin señal de entrada en {symbol}")
                self.jlog("no_signal", symbol=symbol)
                continue

            if self.cfg.use_value_filter and symbol not in self.cfg.value_whitelist:
                from value_filter import evaluate as _value_eval
                verdict = _value_eval(symbol)
                if not verdict.passes:
                    self.logger.info(f"Filtro value RECHAZA {symbol} | score={verdict.score} | {verdict.reasons}")
                    self.jlog("value_reject", symbol=symbol, score=verdict.score,
                              reasons=verdict.reasons, metrics=verdict.metrics)
                    continue
                self.jlog("value_pass", symbol=symbol, score=verdict.score, metrics=verdict.metrics)

            last = df.iloc[-1]
            price = float(last["close"])
            qty, dbg = self.calc_order_size(price, net_liq, available_funds)

            if qty < 1:
                self.logger.info(f"Tamaño insuficiente para entrar en {symbol} | {dbg}")
                self.jlog("size_zero", symbol=symbol, **dbg)
                continue

            stop_price = price * (1.0 - self.cfg.trailing_stop_pct)
            target_price = price * (1.0 + self.cfg.trailing_stop_pct * 2)

            self.logger.warning(
                f"ENTRADA {symbol} | qty={qty} close={price:.2f} stop={stop_price:.2f} target={target_price:.2f}"
            )
            self.jlog("entry_signal", symbol=symbol, qty=qty, price=price,
                      stop_price=stop_price, target_price=target_price, **dbg)
            trade = self.place_market_buy(contract, qty)

            fill_price = float(trade.orderStatus.avgFillPrice) if trade.orderStatus.avgFillPrice else price
            tracked[symbol] = {
                "entry_price": round(fill_price, 4),
                "max_price": round(fill_price, 4),
                "stop_price": round(stop_price, 4),
                "target_price": round(target_price, 4),
                "quantity": int(qty),
                "updated_at": _today_utc().isoformat(),
            }
            self.state["positions"] = tracked
            self._save_state()
            self.jlog("entry_filled", symbol=symbol, fill=fill_price, qty=qty)
            long_positions_count += 1

    def run_once(self):
        self.sync_state_with_positions()
        can_enter = self.update_equity_and_breakers()
        self.evaluate_exits()
        if can_enter:
            from regime_filter import current_regime
            r = current_regime()
            self.jlog("regime", bull=r.bull, spy=r.spy, sma200=r.sma200, pct_vs_sma=r.pct_vs_sma)
            if not r.bull:
                self.logger.warning(f"Régimen BEAR (SPY {r.pct_vs_sma:+.1f}% vs SMA200). Sin entradas nuevas.")
            else:
                self.evaluate_entries()
        else:
            self.logger.info("Entradas deshabilitadas por circuit breaker")


def main():
    settings = Settings()
    import os as _os
    from go_live_gate import enforce as _gate_enforce
    mode = _os.getenv("MODE", "paper")
    if not _gate_enforce(mode, settings.state_file, "swing-US"):
        return
    bot = IBSwingBot(settings)
    try:
        bot.connect()
        bot.run_once()
    finally:
        bot.disconnect()


if __name__ == "__main__":
    main()

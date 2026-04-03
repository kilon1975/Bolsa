import json
import logging
import math
import os
from datetime import datetime, timezone

import pandas as pd
from ib_insync import IB, Stock, MarketOrder, util

from config import Settings

class IBSwingBot:
    def __init__(self, settings: Settings):
        self.cfg = settings
        self.ib = IB()
        self.state = self._load_state()
        self.logger = self._build_logger()

    def _build_logger(self):
        logger = logging.getLogger("ib_swing_bot")
        logger.setLevel(getattr(logging, self.cfg.log_level.upper(), logging.INFO))
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        return logger

    def _load_state(self):
        if os.path.exists(self.cfg.state_file):
            with open(self.cfg.state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"positions": {}}

    def _save_state(self):
        with open(self.cfg.state_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def connect(self):
        self.logger.info(f"Conectando a IBKR en {self.cfg.ib_host}:{self.cfg.ib_port} clientId={self.cfg.ib_client_id}")
        self.ib.connect(self.cfg.ib_host, self.cfg.ib_port, clientId=self.cfg.ib_client_id)
        self.logger.info("Conexión establecida")

    def disconnect(self):
        if self.ib.isConnected():
            self.ib.disconnect()
            self.logger.info("Conexión cerrada")

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
        if df.empty:
            return contract, df
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

    def calc_order_size(self, price: float, atr: float, net_liq: float, available_funds: float) -> int:
        if price <= 0 or atr <= 0 or net_liq <= 0:
            return 0

        stop_distance = atr * self.cfg.stop_atr_mult
        if stop_distance <= 0:
            return 0

        risk_budget = net_liq * self.cfg.risk_per_trade
        qty_by_risk = math.floor(risk_budget / stop_distance)

        max_position_value = net_liq * self.cfg.max_position_pct
        qty_by_position_cap = math.floor(max_position_value / price)

        deployable_cash = max(0.0, available_funds * (1.0 - self.cfg.min_cash_buffer_pct))
        qty_by_cash = math.floor(deployable_cash / price)

        qty = min(qty_by_risk, qty_by_position_cap, qty_by_cash)
        return max(0, qty)

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

    def has_exit_signal(self, df: pd.DataFrame, stop_price: float, target_price: float) -> tuple[bool, str]:
        if len(df) < 20:
            return False, ""
        last = df.iloc[-1]
        close = float(last["close"])
        ema20 = float(last["ema20"])

        if close <= stop_price:
            return True, "stop"
        if close >= target_price:
            return True, "target"
        if close < ema20:
            return True, "ema20_break"
        return False, ""

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

            last = df.iloc[-1]
            atr = float(last["atr14"]) if pd.notna(last["atr14"]) else 0.0
            close = float(last["close"])
            avg_cost = float(p.avgCost) if float(p.avgCost) > 0 else close

            meta = tracked.get(symbol, {})
            stop_price = float(meta.get("stop_price", avg_cost - atr * self.cfg.stop_atr_mult))
            target_price = float(meta.get("target_price", avg_cost + atr * self.cfg.target_atr_mult))

            exit_now, reason = self.has_exit_signal(df, stop_price, target_price)
            if not exit_now:
                self.logger.info(f"Mantener {symbol} | close={close:.2f} stop={stop_price:.2f} target={target_price:.2f}")
                continue

            qty = int(abs(p.position))
            self.logger.warning(f"SALIDA {symbol} | motivo={reason} | qty={qty}")
            trade = self.place_market_sell(contract, qty)
            self.logger.info(f"Orden de salida enviada en {symbol} | status={trade.orderStatus.status}")
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

            if not self.has_entry_signal(df):
                self.logger.info(f"Sin señal de entrada en {symbol}")
                continue

            last = df.iloc[-1]
            price = float(last["close"])
            atr = float(last["atr14"])
            qty = self.calc_order_size(price, atr, net_liq, available_funds)

            if qty < 1:
                self.logger.info(f"Tamaño insuficiente para entrar en {symbol}")
                continue

            stop_price = price - atr * self.cfg.stop_atr_mult
            target_price = price + atr * self.cfg.target_atr_mult

            self.logger.warning(
                f"ENTRADA {symbol} | qty={qty} close={price:.2f} stop={stop_price:.2f} target={target_price:.2f}"
            )
            trade = self.place_market_buy(contract, qty)

            fill_price = float(trade.orderStatus.avgFillPrice) if trade.orderStatus.avgFillPrice else price
            tracked[symbol] = {
                "entry_price": round(fill_price, 4),
                "stop_price": round(stop_price, 4),
                "target_price": round(target_price, 4),
                "quantity": int(qty),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self.state["positions"] = tracked
            self._save_state()
            long_positions_count += 1

    def run_once(self):
        self.sync_state_with_positions()
        self.evaluate_exits()
        self.evaluate_entries()

def main():
    settings = Settings()
    bot = IBSwingBot(settings)
    try:
        bot.connect()
        bot.run_once()
    finally:
        bot.disconnect()

if __name__ == "__main__":
    main()

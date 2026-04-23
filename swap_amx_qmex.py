"""
Script de una vez: cierra posicion AMX y abre Q.MX (~$50k USD).

Uso:
    python swap_amx_qmex.py          # simulacion (dry-run, no envia ordenes)
    python swap_amx_qmex.py --live   # envia ordenes al Gateway

Requiere Gateway abierto en el puerto del .env (4002 paper).
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from ib_insync import IB, Stock, MarketOrder
import yfinance as yf

load_dotenv()

TARGET_USD = 50_000
AMX_SYMBOL = "AMX"
QMX_SYMBOL = "Q.MX"  # En IBKR: "Q" en MEXI con currency MXN
STATE_FILE = "state_value.json"

DRY_RUN = "--live" not in sys.argv


def log(msg):
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{ts}] {msg}")


def get_price_yf(symbol: str) -> float | None:
    try:
        info = yf.Ticker(symbol).info
        return info.get("regularMarketPrice") or info.get("previousClose") or info.get("currentPrice")
    except Exception as e:
        log(f"error yf {symbol}: {e}")
        return None


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"positions": {}, "trade_history": []}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def build_contract(symbol: str):
    if symbol.endswith(".MX"):
        return Stock(symbol[:-3], "MEXI", "MXN")
    return Stock(symbol, "SMART", "USD")


def main():
    host = os.getenv("IB_HOST", "127.0.0.1")
    port = int(os.getenv("IB_PORT", "4002"))
    client_id = 99  # client id distinto al bot para no chocar

    log(f"Modo: {'DRY RUN (no envia ordenes)' if DRY_RUN else 'LIVE (envia al Gateway)'}")

    # 1) Precios de referencia (yfinance)
    amx_px_usd = get_price_yf(AMX_SYMBOL)
    qmx_px_mxn = get_price_yf(QMX_SYMBOL)
    usdmxn = get_price_yf("USDMXN=X")
    log(f"Precios ref: AMX=${amx_px_usd} USDMXN={usdmxn} Q.MX={qmx_px_mxn} MXN")

    if not all([amx_px_usd, qmx_px_mxn, usdmxn]):
        log("❌ no pude obtener precios de referencia, aborto")
        return

    # Calcular qty Q.MX
    target_mxn = TARGET_USD * usdmxn
    qmx_qty = int(target_mxn / qmx_px_mxn)
    log(f"Target ${TARGET_USD} USD = {target_mxn:,.0f} MXN = {qmx_qty} acciones de Q.MX")

    if DRY_RUN:
        log("DRY RUN: conectando solo para verificar posiciones...")

    ib = IB()
    try:
        ib.connect(host, port, clientId=client_id, readonly=False)
        log(f"Conectado a {host}:{port}")
    except Exception as e:
        log(f"❌ no pude conectar al Gateway: {e}")
        return

    try:
        # 2) Revisar posicion AMX
        positions = {p.contract.symbol: p for p in ib.positions()}
        amx_pos = positions.get(AMX_SYMBOL)
        if not amx_pos or float(amx_pos.position) == 0:
            log(f"ℹ️ no hay posicion abierta de {AMX_SYMBOL}, salto cierre")
            amx_total_qty = 0
            amx_qty = 0
        else:
            amx_total_qty = int(abs(float(amx_pos.position)))
            # Vender solo $TARGET_USD de AMX (venta parcial), no todo
            amx_qty = min(int(TARGET_USD / amx_px_usd), amx_total_qty)
            log(f"Posicion AMX actual: {amx_total_qty} acciones @ avg ${amx_pos.avgCost:.2f}")
            log(f"Vendera {amx_qty}/{amx_total_qty} AMX (~${amx_qty * amx_px_usd:,.0f}) — el resto ({amx_total_qty - amx_qty} acciones) queda intacto")

        # 3) Cierre parcial de AMX
        if amx_qty > 0:
            if DRY_RUN:
                log(f"[DRY] Venderia {amx_qty} AMX a mercado (~${amx_qty * amx_px_usd:,.2f})")
            else:
                amx_contract = build_contract(AMX_SYMBOL)
                ib.qualifyContracts(amx_contract)
                order = MarketOrder("SELL", amx_qty)
                trade = ib.placeOrder(amx_contract, order)
                log(f"→ orden SELL {amx_qty} AMX enviada")
                for _ in range(30):
                    ib.sleep(1)
                    if trade.orderStatus.status in ("Filled", "ApiCancelled", "Cancelled"):
                        break
                filled_qty = int(float(trade.orderStatus.filled or 0))
                fill = float(trade.orderStatus.avgFillPrice or 0)
                status = trade.orderStatus.status
                log(f"→ AMX status={status} filled={filled_qty}/{amx_qty} fill=${fill:.2f}")

                if filled_qty <= 0 or fill <= 0 or status != "Filled":
                    log(f"❌ AMX NO llenada (status={status}, filled={filled_qty}) — state NO modificado")
                    log(f"   Probable causa: fuera de RTH o falta liquidez. Reintenta en horas de mercado.")
                    return

                state = load_state()
                entry = state.get("positions", {}).get(AMX_SYMBOL, {})
                entry_px = float(entry.get("entry_price", 0) or 0)
                pnl = (fill - entry_px) * filled_qty if entry_px else None
                state.setdefault("trade_history", []).append({
                    "symbol": AMX_SYMBOL,
                    "entry_price": entry_px or None,
                    "exit_price": fill,
                    "quantity": filled_qty,
                    "requested_qty": amx_qty,
                    "pnl": round(pnl, 2) if pnl is not None else None,
                    "reason": "manual_swap_to_qmx",
                    "status": status,
                    "closed_at": datetime.now(timezone.utc).isoformat(),
                })
                if filled_qty >= amx_qty:
                    state.get("positions", {}).pop(AMX_SYMBOL, None)
                save_state(state)
                log(f"→ state_value.json actualizado (AMX filled={filled_qty})")

        # 4) Compra Q.MX
        if QMX_SYMBOL in positions and float(positions[QMX_SYMBOL].position) > 0:
            log(f"⚠️ ya tienes posicion abierta de {QMX_SYMBOL}, no compro otra vez")
        elif qmx_qty < 1:
            log("❌ qty calculada < 1, aborto compra Q.MX")
        else:
            if DRY_RUN:
                log(f"[DRY] Compraria {qmx_qty} Q.MX a mercado (~${TARGET_USD:,} USD equiv)")
            else:
                qmx_contract = build_contract(QMX_SYMBOL)
                try:
                    ib.qualifyContracts(qmx_contract)
                except Exception as e:
                    log(f"❌ no pude qualificar contrato Q.MX ({e}). Probablemente falta suscripcion MEXI.")
                    return
                order = MarketOrder("BUY", qmx_qty)
                trade = ib.placeOrder(qmx_contract, order)
                log(f"→ orden BUY {qmx_qty} Q.MX enviada")
                for _ in range(30):
                    ib.sleep(1)
                    if trade.orderStatus.status in ("Filled", "ApiCancelled", "Cancelled"):
                        break
                filled_qty = int(float(trade.orderStatus.filled or 0))
                fill = float(trade.orderStatus.avgFillPrice or 0)
                status = trade.orderStatus.status
                log(f"→ Q.MX status={status} filled={filled_qty}/{qmx_qty} fill={fill:.2f} MXN")

                if filled_qty <= 0 or fill <= 0 or status != "Filled":
                    log(f"❌ Q.MX NO llenada (status={status}, filled={filled_qty}) — state NO modificado")
                    log(f"   Probable causa: MEXI fuera de RTH o falta suscripcion. Reintenta en horas.")
                    return

                state = load_state()
                state.setdefault("positions", {})[QMX_SYMBOL] = {
                    "quantity": filled_qty,
                    "entry_price": fill,
                    "opened_at": datetime.now(timezone.utc).isoformat(),
                    "source": "manual_swap_from_amx",
                    "status": status,
                }
                save_state(state)
                log(f"→ state_value.json actualizado (Q.MX filled={filled_qty})")

        log("✓ terminado")
    finally:
        if ib.isConnected():
            ib.disconnect()
            log("Desconectado")


if __name__ == "__main__":
    main()

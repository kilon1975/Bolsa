# IBKR Swing Bot

Bot de trading para dinero propio con Interactive Brokers usando `ib_insync`.

## Estrategia
- Universo inicial: acciones y ETFs líquidos.
- Entrada:
  - ruptura del máximo previo de 20 velas,
  - EMA20 > EMA50,
  - cierre > EMA20,
  - volumen > promedio de 20 velas.
- Salida:
  - stop loss por ATR,
  - target por ATR,
  - o pérdida de EMA20.

## Archivos
- `config.py`: configuración
- `main.py`: bot principal
- `.env.example`: variables de entorno
- `state.json`: estado persistente de posiciones

## Instalación
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

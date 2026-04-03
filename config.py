from dataclasses import dataclass
from dotenv import load_dotenv
import os

load_dotenv()

def _get_env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()

def _get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value not in (None, "") else default

def _get_env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value not in (None, "") else default

@dataclass
class Settings:
    ib_host: str = _get_env_str("IB_HOST", "127.0.0.1")
    ib_port: int = _get_env_int("IB_PORT", 7497)
    ib_client_id: int = _get_env_int("IB_CLIENT_ID", 17)
    ib_account: str = _get_env_str("IB_ACCOUNT", "")

    symbols: list[str] = None

    risk_per_trade: float = _get_env_float("RISK_PER_TRADE", 0.0075)
    max_positions: int = _get_env_int("MAX_POSITIONS", 3)
    max_position_pct: float = _get_env_float("MAX_POSITION_PCT", 0.20)
    min_cash_buffer_pct: float = _get_env_float("MIN_CASH_BUFFER_PCT", 0.20)

    stop_atr_mult: float = _get_env_float("STOP_ATR_MULT", 2.0)
    target_atr_mult: float = _get_env_float("TARGET_ATR_MULT", 4.0)

    bar_size: str = _get_env_str("BAR_SIZE", "1 day")
    scan_duration: str = _get_env_str("SCAN_DURATION", "6 M")
    exit_duration: str = _get_env_str("EXIT_DURATION", "3 M")

    state_file: str = _get_env_str("STATE_FILE", "state.json")
    log_level: str = _get_env_str("LOG_LEVEL", "INFO")

    def __post_init__(self):
        raw_symbols = _get_env_str("SYMBOLS", "AAPL,MSFT,NVDA,AMZN,META,TSLA,QQQ,SPY")
        self.symbols = [s.strip().upper() for s in raw_symbols.split(",") if s.strip()]

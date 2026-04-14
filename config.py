from dataclasses import dataclass, field
from dotenv import load_dotenv
import json
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

def _get_env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    return value.strip().lower() in ("1", "true", "yes", "y", "on")

def _load_universe(path: str, fallback_env: str) -> tuple[list[str], str]:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        syms = [s.strip().upper() for s in data.get("symbols", []) if s.strip()]
        return syms, str(data.get("version", ""))
    raw = _get_env_str(fallback_env, "AAPL,MSFT,NVDA,AMZN,META,TSLA,QQQ,SPY")
    return [s.strip().upper() for s in raw.split(",") if s.strip()], "env"

@dataclass
class Settings:
    ib_host: str = _get_env_str("IB_HOST", "127.0.0.1")
    ib_port: int = _get_env_int("IB_PORT", 7497)
    ib_client_id: int = _get_env_int("IB_CLIENT_ID", 17)
    ib_account: str = _get_env_str("IB_ACCOUNT", "")

    symbols: list[str] = field(default_factory=list)
    universe_version: str = ""

    risk_per_trade: float = _get_env_float("RISK_PER_TRADE", 0.0075)
    max_positions: int = _get_env_int("MAX_POSITIONS", 3)
    max_position_pct: float = _get_env_float("MAX_POSITION_PCT", 0.20)
    min_cash_buffer_pct: float = _get_env_float("MIN_CASH_BUFFER_PCT", 0.20)

    trailing_stop_pct: float = _get_env_float("TRAILING_STOP_PCT", 0.20)
    target_atr_mult: float = _get_env_float("TARGET_ATR_MULT", 4.0)

    use_kelly: bool = _get_env_bool("USE_KELLY", True)
    kelly_fraction: float = _get_env_float("KELLY_FRACTION", 0.25)
    assumed_win_rate: float = _get_env_float("ASSUMED_WIN_RATE", 0.45)
    assumed_payoff: float = _get_env_float("ASSUMED_PAYOFF", 2.0)

    max_drawdown_pct: float = _get_env_float("MAX_DRAWDOWN_PCT", 0.15)
    max_losses_streak: int = _get_env_int("MAX_LOSSES_STREAK", 3)
    max_stale_bars_days: int = _get_env_int("MAX_STALE_BARS_DAYS", 4)

    use_value_filter: bool = _get_env_bool("USE_VALUE_FILTER", False)
    value_whitelist: list[str] = field(default_factory=list)

    bar_size: str = _get_env_str("BAR_SIZE", "1 day")
    scan_duration: str = _get_env_str("SCAN_DURATION", "6 M")
    exit_duration: str = _get_env_str("EXIT_DURATION", "3 M")

    state_file: str = _get_env_str("STATE_FILE", "state.json")
    universe_file: str = _get_env_str("UNIVERSE_FILE", "universe.json")
    logs_dir: str = _get_env_str("LOGS_DIR", "logs")
    log_level: str = _get_env_str("LOG_LEVEL", "INFO")

    def __post_init__(self):
        self.symbols, self.universe_version = _load_universe(self.universe_file, "SYMBOLS")
        raw_wl = _get_env_str("VALUE_WHITELIST", "")
        self.value_whitelist = [s.strip().upper() for s in raw_wl.split(",") if s.strip()]

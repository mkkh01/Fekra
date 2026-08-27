from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "LTCUSDT", "XRPUSDT", "ADAUSDT",
    "SOLUSDT", "DOTUSDT", "LINKUSDT", "AVAXUSDT", "BNBUSDT",
    "TRXUSDT", "BCHUSDT", "UNIUSDT", "ETCUSDT", "ATOMUSDT",
    "XLMUSDT", "NEARUSDT", "FILUSDT", "APTUSDT", "ARBUSDT",
]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    supabase_url: str | None = "https://obljtphynhmbcbyxyrjg.supabase.co"
    supabase_key: str | None = None
    supabase_service_role_key: str | None = None
    supabase_anon_key: str | None = None
    supabase_publishable_key: str | None = None
    database_url: str | None = None
    supabase_db_url: str | None = None
    redis_url: str | None = None
    binance_rest_url: str = "https://data-api.binance.vision"
    binance_ws_url: str = "wss://data-stream.binance.vision/stream,wss://stream.binance.us:9443/stream"
    binance_ws_api_url: str = "wss://ws-api.binance.com:443/ws-api/v3"
    symbols: str = ",".join(DEFAULT_SYMBOLS)
    default_interval: str = "15m"
    cors_origins: str = ""
    confidence_threshold: int = 65
    minimum_rr: float = 2.0
    risk_per_trade: float = 0.005
    database_path: str = "weeg.db"
    vapid_private_key: str | None = None
    vapid_subject: str = "mailto:weeg-notifications@example.com"
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_webhook_url: str | None = None
    telegram_webhook_secret: str | None = None
    weeg_shadow_mode: bool = True
    weeg_safety_gates_enabled: bool = False
    weeg_mfe_shadow: bool = True
    signal_max_age_seconds: int = 900
    mtf_sync_tolerance_seconds: int = 120

    # IFVG is a separate long-only Spot paper strategy. It never calls exchange order endpoints.
    ifvg_enabled: bool = False
    ifvg_symbols: str | None = None
    ifvg_scan_seconds: int = 60
    ifvg_config_version: str = "1.2.1-baseline"
    ifvg_quote_balance: float | None = None
    ifvg_max_position_value_quote: float | None = None
    ifvg_max_global_open_positions: int | None = None
    ifvg_daily_loss_fraction: float = 0.0
    ifvg_fee_bps: float = 10.0
    ifvg_spread_bps: float = 4.0
    ifvg_entry_slippage_bps: float = 2.0
    ifvg_exit_slippage_bps: float = 2.0
    ifvg_stop_slippage_bps: float = 4.0
    ifvg_latency_bps: float = 0.0
    ifvg_orderbook_required: bool = False
    ifvg_user_id: str | None = None

    @property
    def postgres_dsn(self) -> str | None:
        if self.database_url:
            return self.database_url
        if self.supabase_db_url:
            return self.supabase_db_url
        if self.supabase_url and self.supabase_url.lower().startswith(("postgres://", "postgresql://")):
            return self.supabase_url
        return None

    @property
    def supabase_http_url(self) -> str:
        if self.supabase_url and self.supabase_url.lower().startswith(("http://", "https://")):
            return self.supabase_url
        return "https://obljtphynhmbcbyxyrjg.supabase.co"

    @property
    def supabase_public_key(self) -> str | None:
        return self.supabase_publishable_key or self.supabase_anon_key

    @property
    def supabase_auth_keys(self) -> list[str]:
        return [key for key in (self.supabase_service_role_key, self.supabase_key, self.supabase_anon_key) if key]

    @property
    def supabase_auth_key(self) -> str | None:
        return self.supabase_auth_keys[0] if self.supabase_auth_keys else None

    @property
    def symbol_list(self) -> list[str]:
        return [s.strip().upper() for s in self.symbols.split(",") if s.strip()]

    @property
    def ifvg_symbol_list(self) -> list[str]:
        raw = self.ifvg_symbols if self.ifvg_symbols is not None else self.symbols
        return [s.strip().upper() for s in raw.split(",") if s.strip()]

    @property
    def cors_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip() and x.strip() != "*"]

@lru_cache
def get_settings() -> Settings:
    return Settings()

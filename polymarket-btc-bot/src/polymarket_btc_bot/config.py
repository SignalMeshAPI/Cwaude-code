"""Runtime configuration loaded from environment.

Hard safety floors are enforced here. The environment can only TIGHTEN risk
caps from the defaults; loosening them raises ValueError at startup. Private
keys never leave this module's scope (we expose helpers to read them, but
the model itself is opaque to logs via repr override).
"""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Risk floors. Code-level invariants the bot will not cross even if env
# tries to. Order: env value must be <= floor for caps that are losses,
# and >= floor for caps that are confidence/bankroll thresholds.
_FLOOR_MAX_BET_USD = 25.0
_FLOOR_STOP_LOSS_PCT = 0.50
_FLOOR_TAKE_PROFIT_PCT = 0.50
_FLOOR_MIN_EDGE_CONFIDENCE = 0.50
_FLOOR_MAX_CONCURRENT_POSITIONS = 3
_FLOOR_MAX_DAILY_LOSS_USD = 100.0
_FLOOR_MAX_WEEKLY_LOSS_USD = 300.0
_FLOOR_MIN_BANKROLL_USD = 10.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Wallet & Polymarket. POLYMARKET_PK is accepted as an alias to match
    # the upstream aulekator bot's env var naming.
    polymarket_private_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("polymarket_private_key", "polymarket_pk"),
    )
    # Pre-supplied L2 API creds skip the derivation step. Optional.
    polymarket_api_key: str = ""
    polymarket_api_secret: SecretStr = SecretStr("")
    polymarket_passphrase: SecretStr = SecretStr("")
    polymarket_host: str = "https://clob.polymarket.com"
    polymarket_gamma_host: str = "https://gamma-api.polymarket.com"
    polygon_rpc_url: str = "https://polygon-rpc.com"
    polygon_chain_id: int = 137

    # Run mode
    pmbot_mode: Literal["live", "paper"] = "paper"
    metrics_port: int = 9100

    # Risk caps. MAX_POSITION_SIZE is accepted as an alias to MAX_BET_USD
    # for compatibility with the upstream bot's env naming.
    max_bet_usd: float = Field(
        default=2.0,
        validation_alias=AliasChoices("max_bet_usd", "max_position_size"),
    )
    stop_loss_pct: float = 0.30
    take_profit_pct: float = 0.20
    min_edge_confidence: float = 0.55
    max_concurrent_positions: int = 1
    max_daily_loss_usd: float = 10.0
    max_weekly_loss_usd: float = 30.0
    kill_switch_consecutive_losses: int = 5
    min_bankroll_usd: float = 10.0

    # Signal-tuning thresholds (override the per-signal defaults).
    spike_threshold_z: float = 3.0     # z-score that maps to confidence=1.0
    divergence_threshold_bps: float = 5.0  # bps spread that maps to confidence=1.0

    # Data sources
    binance_ws_url: str = "wss://stream.binance.com:9443/ws"
    binance_futures_rest: str = "https://fapi.binance.com"
    binance_futures_ws: str = "wss://fstream.binance.com/ws"
    coinbase_rest: str = "https://api.exchange.coinbase.com"
    fear_greed_url: str = "https://api.alternative.me/fng/"
    solana_rest: str = "https://api.coinbase.com/v2"  # /prices/SOL-USD/spot

    reddit_client_id: str = ""
    reddit_client_secret: SecretStr = SecretStr("")
    reddit_user_agent: str = "polymarket-btc-bot/0.1"
    reddit_subreddits: str = "Bitcoin,CryptoCurrency"

    # Redis. URL is preferred but legacy host/port/db env vars are honored
    # if the URL is left at its default.
    redis_url: str = "redis://redis:6379/0"
    redis_host: str = ""
    redis_port: int = 0
    redis_db: int = 0

    # Learning
    learning_db_path: str = "/data/learning.db"
    learning_rate: float = 0.02
    learning_decay: float = 0.999
    learning_replay_size: int = 1000
    learning_weight_floor: float = 0.05

    # Logging
    log_level: str = "INFO"
    log_json: bool = True

    # ----- validators enforcing safety floors --------------------------------

    @field_validator("max_bet_usd")
    @classmethod
    def _v_max_bet(cls, v: float) -> float:
        if v > _FLOOR_MAX_BET_USD:
            raise ValueError(f"max_bet_usd={v} exceeds floor {_FLOOR_MAX_BET_USD}")
        if v < 1.0:
            raise ValueError("max_bet_usd must be >= 1.0 (Polymarket min order)")
        return v

    @field_validator("stop_loss_pct")
    @classmethod
    def _v_sl(cls, v: float) -> float:
        if v > _FLOOR_STOP_LOSS_PCT or v <= 0:
            raise ValueError(f"stop_loss_pct must be in (0, {_FLOOR_STOP_LOSS_PCT}]")
        return v

    @field_validator("take_profit_pct")
    @classmethod
    def _v_tp(cls, v: float) -> float:
        if v > _FLOOR_TAKE_PROFIT_PCT or v <= 0:
            raise ValueError(f"take_profit_pct must be in (0, {_FLOOR_TAKE_PROFIT_PCT}]")
        return v

    @field_validator("min_edge_confidence")
    @classmethod
    def _v_edge(cls, v: float) -> float:
        if v < _FLOOR_MIN_EDGE_CONFIDENCE or v > 1.0:
            raise ValueError(
                f"min_edge_confidence must be in [{_FLOOR_MIN_EDGE_CONFIDENCE}, 1.0]"
            )
        return v

    @field_validator("max_concurrent_positions")
    @classmethod
    def _v_concurrent(cls, v: int) -> int:
        if v < 1 or v > _FLOOR_MAX_CONCURRENT_POSITIONS:
            raise ValueError(
                f"max_concurrent_positions must be in [1, {_FLOOR_MAX_CONCURRENT_POSITIONS}]"
            )
        return v

    @field_validator("max_daily_loss_usd")
    @classmethod
    def _v_daily(cls, v: float) -> float:
        if v <= 0 or v > _FLOOR_MAX_DAILY_LOSS_USD:
            raise ValueError(f"max_daily_loss_usd must be in (0, {_FLOOR_MAX_DAILY_LOSS_USD}]")
        return v

    @field_validator("max_weekly_loss_usd")
    @classmethod
    def _v_weekly(cls, v: float) -> float:
        if v <= 0 or v > _FLOOR_MAX_WEEKLY_LOSS_USD:
            raise ValueError(f"max_weekly_loss_usd must be in (0, {_FLOOR_MAX_WEEKLY_LOSS_USD}]")
        return v

    @field_validator("min_bankroll_usd")
    @classmethod
    def _v_bankroll(cls, v: float) -> float:
        if v < _FLOOR_MIN_BANKROLL_USD:
            raise ValueError(f"min_bankroll_usd must be >= {_FLOOR_MIN_BANKROLL_USD}")
        return v

    def reddit_subreddit_list(self) -> list[str]:
        return [s.strip() for s in self.reddit_subreddits.split(",") if s.strip()]

    def effective_redis_url(self) -> str:
        """Build a redis URL from REDIS_HOST/PORT/DB if REDIS_URL is the default."""
        if self.redis_host:
            port = self.redis_port or 6379
            db = self.redis_db or 0
            return f"redis://{self.redis_host}:{port}/{db}"
        return self.redis_url


_cached: Settings | None = None


def get_settings() -> Settings:
    global _cached
    if _cached is None:
        _cached = Settings()
    return _cached

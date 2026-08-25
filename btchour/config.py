from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CATALOG_DIR = ROOT / "catalog"
DATA_DIR = Path(os.environ.get("BTCHOUR_DATA_DIR", ROOT / "data"))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return default if raw is None or raw.strip() == "" else float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw is None or raw.strip() == "" else int(raw)


@dataclass(frozen=True)
class Settings:
    mode: str = "paper"
    target_profit: float = 0.20
    min_win_prob: float = 0.95
    min_expected_roi: float = 0.20
    annual_vol: float = 0.55
    poll_seconds: int = 10
    max_contracts: float = 10.0
    max_notional: float = 25.0
    hourly_only: bool = True
    allow_maker: bool = False
    series_ticker: str = "KXBTCD"
    kalshi_base: str = "https://external-api.kalshi.com/trade-api/v2"
    kalshi_demo: bool = False
    api_key_id: str = ""
    private_key_pem: str = ""
    user_agent: str = "BTCHOUR/0.1"

    @property
    def live(self) -> bool:
        return self.mode == "live"

    @property
    def can_sign(self) -> bool:
        return bool(self.api_key_id and self.private_key_pem)


def load_settings() -> Settings:
    demo = _env_bool("KALSHI_DEMO", False)
    base = (
        "https://external-api.demo.kalshi.co/trade-api/v2"
        if demo
        else os.environ.get("KALSHI_API_BASE", "https://external-api.kalshi.com/trade-api/v2")
    )
    pem = os.environ.get("KALSHI_PRIVATE_KEY", "").strip()
    key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "").strip()
    if not pem and key_path:
        pem = Path(key_path).read_text()
    mode = os.environ.get("BTCHOUR_MODE", "paper").strip().lower()
    if mode not in {"paper", "live"}:
        raise ValueError("BTCHOUR_MODE must be paper or live")
    return Settings(
        mode=mode,
        target_profit=_env_float("BTCHOUR_TARGET_PROFIT", 0.20),
        min_win_prob=_env_float("BTCHOUR_MIN_WIN_PROB", 0.95),
        min_expected_roi=_env_float("BTCHOUR_MIN_EXPECTED_ROI", 0.20),
        annual_vol=_env_float("BTCHOUR_ANNUAL_VOL", 0.55),
        poll_seconds=_env_int("BTCHOUR_POLL_SECONDS", 10),
        max_contracts=_env_float("BTCHOUR_MAX_CONTRACTS", 10.0),
        max_notional=_env_float("BTCHOUR_MAX_NOTIONAL", 25.0),
        hourly_only=_env_bool("BTCHOUR_HOURLY_ONLY", True),
        allow_maker=_env_bool("BTCHOUR_ALLOW_MAKER", False),
        series_ticker=os.environ.get("BTCHOUR_SERIES", "KXBTCD"),
        kalshi_base=base.rstrip("/"),
        kalshi_demo=demo,
        api_key_id=os.environ.get("KALSHI_API_KEY_ID", "").strip(),
        private_key_pem=pem,
        user_agent=os.environ.get("BTCHOUR_USER_AGENT", "BTCHOUR/0.1"),
    )

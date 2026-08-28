from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CATALOG_DIR = ROOT / "catalog"
DATA_DIR = Path(os.environ.get("BTCHOUR_DATA_DIR", ROOT / "data"))


def _load_dotenv(path: Path | None = None) -> None:
    """Load gitignored .env into os.environ without overwriting a real env var."""
    env_path = path or (ROOT / ".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


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
    poll_seconds: int = 3
    max_contracts: float = 10.0
    max_notional: float = 25.0
    hourly_only: bool = True
    allow_maker: bool = True
    playbook: str = "flex"
    min_sigma: float = 3.2
    lock_min_p: float = 0.998
    swing_min_p: float = 0.55
    swing_min_gap: float = 0.08
    swing_min_ask: float = 0.18
    swing_max_ask: float = 0.72
    swing_min_seconds: float = 180.0
    swing_target: float = 0.10
    swing_max_clip: float = 0.50
    swing_trail: float = 0.04
    swing_runner_gap: float = 0.12
    swing_max_distance: float = 600.0
    swing_fade: float = 0.12
    swing_stop: float = 0.12
    skip_after_loss: bool = True
    impulse_min: float = 100.0
    impulse_min_p: float = 0.52
    impulse_min_gap: float = 0.02
    impulse_max_ask: float = 0.52
    impulse_lookback_ms: int = 180_000
    impulse_taker: bool = False
    impulse_wait: bool = True
    impulse_rest: float = 0.25
    impulse_wait_rest_min: float = 100.0  # hang with the tape; do not park 0.25
    impulse_wait_min_ask: float = 0.32
    impulse_wait_max_ask: float = 0.70  # diagnose only; hangs stay 32–42¢
    impulse_wait_coupon_ask: float = 0.42  # only hang under a live coupon book
    impulse_wait_max_distance: float = 600.0
    impulse_wait_stop: float = 0.80
    impulse_wait_scratch_seconds: float = 480.0
    live_one: bool = True  # one live contract at a time; do not switch the loop to live
    scan_15m: bool = True
    scan_daily: bool = True
    scan_weekly: bool = True
    scalp_min_p: float = 0.60
    scalp_min_gap: float = 0.10
    scalp_max_entry: float = 0.65
    scalp_min_seconds: float = 600.0
    scalp_max_lock: float = 0.90
    invalidate_p: float = 0.40
    flatten_seconds: float = 40.0
    allow_early_exit: bool = True
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

    @property
    def min_ev(self) -> float:
        return self.min_expected_roi


def load_settings() -> Settings:
    _load_dotenv()
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
    playbook = os.environ.get("BTCHOUR_PLAYBOOK", "flex").strip().lower()
    if playbook not in {"hold", "flex", "scalp", "lock", "swing"}:
        raise ValueError("BTCHOUR_PLAYBOOK must be flex, lock, swing, hold, or scalp")
    min_win_prob = _env_float("BTCHOUR_MIN_WIN_PROB", 0.998 if playbook == "lock" else 0.95)
    allow_maker = _env_bool("BTCHOUR_ALLOW_MAKER", playbook in {"lock", "flex"})
    allow_early_exit = _env_bool("BTCHOUR_ALLOW_EARLY_EXIT", playbook in {"flex", "scalp", "swing"})
    return Settings(
        mode=mode,
        target_profit=_env_float("BTCHOUR_TARGET_PROFIT", 0.20),
        min_win_prob=min_win_prob,
        min_expected_roi=_env_float("BTCHOUR_MIN_EXPECTED_ROI", 0.20),
        annual_vol=_env_float("BTCHOUR_ANNUAL_VOL", 0.55),
        poll_seconds=_env_int("BTCHOUR_POLL_SECONDS", 3 if playbook in {"flex", "swing"} else 5),
        max_contracts=_env_float("BTCHOUR_MAX_CONTRACTS", 10.0),
        max_notional=_env_float("BTCHOUR_MAX_NOTIONAL", 25.0),
        hourly_only=_env_bool("BTCHOUR_HOURLY_ONLY", True),
        allow_maker=allow_maker,
        playbook=playbook,
        min_sigma=_env_float("BTCHOUR_MIN_SIGMA", 3.2),
        lock_min_p=_env_float("BTCHOUR_LOCK_MIN_P", 0.998),
        swing_min_p=_env_float("BTCHOUR_SWING_MIN_P", 0.55),
        swing_min_gap=_env_float("BTCHOUR_SWING_MIN_GAP", 0.08),
        swing_min_ask=_env_float("BTCHOUR_SWING_MIN_ASK", 0.18),
        swing_max_ask=_env_float("BTCHOUR_SWING_MAX_ASK", 0.72),
        swing_min_seconds=_env_float("BTCHOUR_SWING_MIN_SECONDS", 180.0),
        swing_target=_env_float("BTCHOUR_SWING_TARGET", 0.10),
        swing_max_clip=_env_float("BTCHOUR_SWING_MAX_CLIP", 0.50),
        swing_trail=_env_float("BTCHOUR_SWING_TRAIL", 0.04),
        swing_runner_gap=_env_float("BTCHOUR_SWING_RUNNER_GAP", 0.12),
        swing_max_distance=_env_float("BTCHOUR_SWING_MAX_DISTANCE", 600.0),
        swing_fade=_env_float("BTCHOUR_SWING_FADE", 0.12),
        swing_stop=_env_float("BTCHOUR_SWING_STOP", 0.12),
        skip_after_loss=_env_bool("BTCHOUR_SKIP_AFTER_LOSS", True),
        impulse_min=_env_float("BTCHOUR_IMPULSE_MIN", 100.0),
        impulse_min_p=_env_float("BTCHOUR_IMPULSE_MIN_P", 0.52),
        impulse_min_gap=_env_float("BTCHOUR_IMPULSE_MIN_GAP", 0.02),
        impulse_max_ask=_env_float("BTCHOUR_IMPULSE_MAX_ASK", 0.52),
        impulse_lookback_ms=_env_int("BTCHOUR_IMPULSE_LOOKBACK_MS", 180_000),
        impulse_taker=_env_bool("BTCHOUR_IMPULSE_TAKER", False),
        impulse_wait=_env_bool("BTCHOUR_IMPULSE_WAIT", True),
        impulse_rest=_env_float("BTCHOUR_IMPULSE_REST", 0.25),
        impulse_wait_rest_min=_env_float("BTCHOUR_IMPULSE_WAIT_REST_MIN", 100.0),
        impulse_wait_min_ask=_env_float("BTCHOUR_IMPULSE_WAIT_MIN_ASK", 0.32),
        impulse_wait_max_ask=_env_float("BTCHOUR_IMPULSE_WAIT_MAX_ASK", 0.70),
        impulse_wait_coupon_ask=_env_float("BTCHOUR_IMPULSE_WAIT_COUPON_ASK", 0.42),
        impulse_wait_max_distance=_env_float("BTCHOUR_IMPULSE_WAIT_MAX_DISTANCE", 600.0),
        impulse_wait_stop=_env_float("BTCHOUR_IMPULSE_WAIT_STOP", 0.80),
        impulse_wait_scratch_seconds=_env_float("BTCHOUR_IMPULSE_WAIT_SCRATCH_SECONDS", 480.0),
        live_one=_env_bool("BTCHOUR_LIVE_ONE", True),
        scan_15m=_env_bool("BTCHOUR_SCAN_15M", True),
        scan_daily=_env_bool("BTCHOUR_SCAN_DAILY", True),
        scan_weekly=_env_bool("BTCHOUR_SCAN_WEEKLY", True),
        scalp_min_p=_env_float("BTCHOUR_SCALP_MIN_P", 0.60),
        scalp_min_gap=_env_float("BTCHOUR_SCALP_MIN_GAP", 0.10),
        scalp_max_entry=_env_float("BTCHOUR_SCALP_MAX_ENTRY", 0.65),
        scalp_min_seconds=_env_float("BTCHOUR_SCALP_MIN_SECONDS", 600.0),
        scalp_max_lock=_env_float("BTCHOUR_SCALP_MAX_LOCK", 0.90),
        invalidate_p=_env_float("BTCHOUR_INVALIDATE_P", 0.40),
        flatten_seconds=_env_float("BTCHOUR_FLATTEN_SECONDS", 40.0),
        allow_early_exit=allow_early_exit,
        series_ticker=os.environ.get("BTCHOUR_SERIES", "KXBTCD"),
        kalshi_base=base.rstrip("/"),
        kalshi_demo=demo,
        api_key_id=os.environ.get("KALSHI_API_KEY_ID", "").strip(),
        private_key_pem=pem,
        user_agent=os.environ.get("BTCHOUR_USER_AGENT", "BTCHOUR/0.1"),
    )


def apply_playbook(
    settings: Settings,
    playbook: str | None = None,
    *,
    no_early_exit: bool = False,
    skip_after_loss: bool | None = None,
    extras: dict | None = None,
) -> Settings:
    """Copy settings into a playbook (lock / flex / swing / hold / scalp) without env mutation."""
    updates: dict = {}
    if playbook:
        updates["playbook"] = playbook
        if playbook == "lock":
            updates["allow_early_exit"] = False
            updates["allow_maker"] = True
            updates["min_win_prob"] = settings.lock_min_p
            updates["poll_seconds"] = max(settings.poll_seconds, 5)
        elif playbook in {"flex", "swing"}:
            updates["allow_early_exit"] = True
            updates["allow_maker"] = playbook == "flex"
            updates["poll_seconds"] = min(settings.poll_seconds, 3)
        elif playbook == "scalp":
            updates["allow_early_exit"] = True
            updates["allow_maker"] = False
        elif playbook == "hold":
            updates["allow_early_exit"] = False
    if no_early_exit:
        updates["allow_early_exit"] = False
    if skip_after_loss is not None:
        updates["skip_after_loss"] = skip_after_loss
    reserved = {"name", "playbook", "skip_after_loss"}
    bool_extras = {
        "impulse_wait",
        "impulse_taker",
        "live_one",
        "allow_maker",
        "allow_early_exit",
        "scan_15m",
        "scan_daily",
        "scan_weekly",
    }
    for key, value in (extras or {}).items():
        if key in reserved:
            continue
        if hasattr(settings, key):
            if key in bool_extras and not isinstance(value, bool):
                value = str(value).strip().lower() not in {"0", "false", "no", "off", ""}
            updates[key] = value
    return replace(settings, **updates) if updates else settings

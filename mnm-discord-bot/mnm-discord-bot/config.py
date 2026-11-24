"""Configuration loading and validation for the MNM Discord bot.

All secrets come from environment variables (a local ``.env`` file or the
hosting panel). Nothing in this module ever prints a secret value.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

WEBSITE_URL = "https://mnmstem.study/"
TAGLINE = "Where medicine, the brain and math meet."

PLACEHOLDER_PREFIXES = ("PASTE_", "YOUR_")

ROOT_DIR = Path(__file__).resolve().parent


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


def _clean(value: Optional[str]) -> Optional[str]:
    """Strip whitespace/quotes and treat template placeholders as 'unset'."""
    if value is None:
        return None
    value = value.strip().strip('"').strip("'").strip()
    if not value or value.upper().startswith(PLACEHOLDER_PREFIXES):
        return None
    return value


@dataclass(frozen=True)
class Config:
    discord_token: str
    guild_id: int
    admin_role_id: int
    moderator_role_id: Optional[int] = None
    welcome_channel_id: Optional[int] = None
    log_channel_id: Optional[int] = None
    ticket_category_id: Optional[int] = None
    showcase_channel_id: Optional[int] = None
    rules_channel_id: Optional[int] = None
    tracks_channel_id: Optional[int] = None
    openai_api_key: Optional[str] = None
    openai_model: Optional[str] = None
    database_path: Path = ROOT_DIR / "data" / "runtime" / "mnm.db"
    log_level: str = "INFO"
    ai_timeout_seconds: float = 45.0
    ai_max_output_tokens: int = 1200
    ai_max_input_chars: int = 1500
    ai_proof_max_chars: int = 3500
    ai_user_cooldown_seconds: int = 20
    ai_user_daily_limit: int = 20
    ai_global_daily_limit: int = 400
    warnings: tuple = field(default_factory=tuple)

    @property
    def ai_enabled(self) -> bool:
        return bool(self.openai_api_key and self.openai_model)

    def secrets(self) -> list[str]:
        """Secret values, used only to redact them from logs."""
        return [s for s in (self.discord_token, self.openai_api_key) if s]


def load_config(env: Optional[Mapping[str, str]] = None) -> Config:
    """Build a :class:`Config` from the environment, validating everything."""
    env = os.environ if env is None else env
    errors: list[str] = []
    warnings: list[str] = []

    def text(name: str) -> Optional[str]:
        return _clean(env.get(name))

    def integer(name: str, *, required: bool = False, default: Optional[int] = None,
                minimum: int = 0) -> Optional[int]:
        raw = text(name)
        if raw is None:
            if required:
                errors.append(f"{name} is missing. Set it in .env or the FadeHost environment settings.")
            return default
        try:
            value = int(raw)
        except ValueError:
            errors.append(f"{name} must be a whole number (got a non-numeric value).")
            return default
        if value < minimum:
            errors.append(f"{name} must be >= {minimum}.")
            return default
        return value

    token = text("DISCORD_TOKEN")
    if token is None:
        errors.append("DISCORD_TOKEN is missing. Paste your bot token into .env or the host's variables.")

    guild_id = integer("GUILD_ID", required=True, minimum=1)
    admin_role = integer("ADMIN_ROLE_ID", required=True, minimum=1)
    mod_role = integer("MODERATOR_ROLE_ID", minimum=1)

    optional_ids = {}
    for name in ("WELCOME_CHANNEL_ID", "LOG_CHANNEL_ID", "TICKET_CATEGORY_ID",
                 "SHOWCASE_CHANNEL_ID", "RULES_CHANNEL_ID", "TRACKS_CHANNEL_ID"):
        optional_ids[name] = integer(name, minimum=1)
        if optional_ids[name] is None and name in (
                "WELCOME_CHANNEL_ID", "LOG_CHANNEL_ID", "TICKET_CATEGORY_ID", "SHOWCASE_CHANNEL_ID"):
            warnings.append(f"{name} is not set: the related feature will be disabled.")

    api_key = text("OPENAI_API_KEY")
    model = text("OPENAI_MODEL")
    if not api_key or not model:
        warnings.append("OPENAI_API_KEY and/or OPENAI_MODEL not set: AI features are disabled.")

    db_raw = text("DATABASE_PATH")
    db_path = Path(db_raw) if db_raw else ROOT_DIR / "data" / "runtime" / "mnm.db"
    if not db_path.is_absolute():
        db_path = ROOT_DIR / db_path

    log_level = (text("LOG_LEVEL") or "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        errors.append("LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL.")
        log_level = "INFO"

    timeout = integer("AI_TIMEOUT_SECONDS", default=45, minimum=5)
    out_tokens = integer("AI_MAX_OUTPUT_TOKENS", default=1200, minimum=100)
    in_chars = integer("AI_MAX_INPUT_CHARS", default=1500, minimum=100)
    cooldown = integer("AI_USER_COOLDOWN_SECONDS", default=20, minimum=0)
    user_daily = integer("AI_USER_DAILY_LIMIT", default=20, minimum=1)
    global_daily = integer("AI_GLOBAL_DAILY_LIMIT", default=400, minimum=1)

    if errors:
        raise ConfigError("Configuration problems:\n - " + "\n - ".join(errors))

    return Config(
        discord_token=token,  # type: ignore[arg-type]  (validated above)
        guild_id=guild_id,  # type: ignore[arg-type]
        admin_role_id=admin_role,  # type: ignore[arg-type]
        moderator_role_id=mod_role,
        welcome_channel_id=optional_ids["WELCOME_CHANNEL_ID"],
        log_channel_id=optional_ids["LOG_CHANNEL_ID"],
        ticket_category_id=optional_ids["TICKET_CATEGORY_ID"],
        showcase_channel_id=optional_ids["SHOWCASE_CHANNEL_ID"],
        rules_channel_id=optional_ids["RULES_CHANNEL_ID"],
        tracks_channel_id=optional_ids["TRACKS_CHANNEL_ID"],
        openai_api_key=api_key,
        openai_model=model,
        database_path=db_path,
        log_level=log_level,
        ai_timeout_seconds=float(timeout or 45),
        ai_max_output_tokens=out_tokens or 1200,
        ai_max_input_chars=in_chars or 1500,
        ai_proof_max_chars=min(3900, max(in_chars or 1500, 3500)),
        ai_user_cooldown_seconds=cooldown if cooldown is not None else 20,
        ai_user_daily_limit=user_daily or 20,
        ai_global_daily_limit=global_daily or 400,
        warnings=tuple(warnings),
    )


def has_staff_access(role_ids: set[int], is_administrator: bool, config: Config) -> bool:
    """Pure permission rule: server administrators, the admin role or the moderator role."""
    if is_administrator:
        return True
    if config.admin_role_id in role_ids:
        return True
    return config.moderator_role_id is not None and config.moderator_role_id in role_ids


def has_admin_access(role_ids: set[int], is_administrator: bool, config: Config) -> bool:
    """Pure permission rule for admin-only actions."""
    return is_administrator or config.admin_role_id in role_ids

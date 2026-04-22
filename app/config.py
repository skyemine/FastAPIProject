from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_DATABASE_URL = "sqlite:///./messenger.db"
DEFAULT_SECRET_KEY = "change-this-secret-before-deploy"


def get_database_url(explicit_value: str | None = None) -> str:
    database_url = explicit_value or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql://") and "+psycopg" not in database_url:
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_csv(value: str | None, default: list[str]) -> list[str]:
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def get_default_allowed_hosts(app_env: str) -> list[str]:
    if app_env.lower() == "production":
        return ["127.0.0.1", "localhost"]
    return ["*"]


@dataclass(slots=True)
class Settings:
    app_env: str
    app_name: str
    database_url: str
    secret_key: str
    session_cookie_name: str
    session_max_age_seconds: int
    cookie_secure: bool
    allowed_hosts: list[str]
    allowed_origins: list[str]
    force_https: bool
    auth_rate_limit_count: int
    auth_rate_limit_window_seconds: int
    message_rate_limit_count: int
    message_rate_limit_window_seconds: int
    message_history_limit: int
    hsts_max_age_seconds: int

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    def validate(self) -> None:
        if self.is_production:
            if self.secret_key == DEFAULT_SECRET_KEY or len(self.secret_key) < 32:
                raise RuntimeError("SECRET_KEY must be changed to a long random value before production deploy.")
            if not self.cookie_secure:
                raise RuntimeError("COOKIE_SECURE=true is required in production.")
            if not self.force_https:
                raise RuntimeError("FORCE_HTTPS=true is required in production.")
            if not os.getenv("ALLOWED_HOSTS"):
                raise RuntimeError("ALLOWED_HOSTS must list your production domains or server IPs before deploy.")
            if "*" in self.allowed_hosts:
                raise RuntimeError("ALLOWED_HOSTS cannot contain '*' in production.")
        elif len(self.secret_key) < 16:
            raise RuntimeError("SECRET_KEY is too short. Use at least 16 characters locally and 32+ in production.")


def load_settings(database_url: str | None = None) -> Settings:
    app_env = os.getenv("APP_ENV", "development")
    return Settings(
        app_env=app_env,
        app_name=os.getenv("APP_NAME", "PulseChat"),
        database_url=get_database_url(database_url),
        secret_key=os.getenv("SECRET_KEY", DEFAULT_SECRET_KEY),
        session_cookie_name=os.getenv("SESSION_COOKIE_NAME", "pulsechat_session"),
        session_max_age_seconds=int(os.getenv("SESSION_MAX_AGE_SECONDS", str(60 * 60 * 24 * 7))),
        cookie_secure=parse_bool(os.getenv("COOKIE_SECURE"), default=False),
        allowed_hosts=parse_csv(os.getenv("ALLOWED_HOSTS"), default=get_default_allowed_hosts(app_env)),
        allowed_origins=parse_csv(os.getenv("ALLOWED_ORIGINS"), default=[]),
        force_https=parse_bool(os.getenv("FORCE_HTTPS"), default=False),
        auth_rate_limit_count=int(os.getenv("AUTH_RATE_LIMIT_COUNT", "8")),
        auth_rate_limit_window_seconds=int(os.getenv("AUTH_RATE_LIMIT_WINDOW_SECONDS", "60")),
        message_rate_limit_count=int(os.getenv("MESSAGE_RATE_LIMIT_COUNT", "20")),
        message_rate_limit_window_seconds=int(os.getenv("MESSAGE_RATE_LIMIT_WINDOW_SECONDS", "10")),
        message_history_limit=int(os.getenv("MESSAGE_HISTORY_LIMIT", "60")),
        hsts_max_age_seconds=int(os.getenv("HSTS_MAX_AGE_SECONDS", str(60 * 60 * 24 * 30))),
    )

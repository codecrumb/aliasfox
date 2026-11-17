import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from argon2 import extract_parameters
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ConfigError(Exception):
    """Raised when startup configuration is unsafe or incomplete."""


MIN_SECRET_KEY_LENGTH = 32


def _env(name: str) -> AliasChoices:
    # Env name only. Keyword construction works through populate_by_name; a
    # lowercase alias would let generic env vars (HOST, SECRET_KEY) leak in.
    return AliasChoices(name)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")

    aliasfox_mode: Literal["single", "multi"] = Field(
        "single", validation_alias=AliasChoices("ALIASFOX_MODE")
    )
    auth_enabled: bool = Field(False, validation_alias=_env("AUTH_ENABLED"))
    database_url: str | None = Field(None, repr=False, validation_alias=_env("DATABASE_URL"))
    data_dir: Path = Field(Path("/data"), validation_alias=_env("DATA_DIR"))
    secret_key: str | None = Field(
        None, repr=False, validation_alias=AliasChoices("ALIASFOX_SECRET_KEY")
    )
    password_hash: str | None = Field(
        None, repr=False, validation_alias=AliasChoices("ALIASFOX_PASSWORD_HASH")
    )
    icloud_cookie: str | None = Field(None, repr=False, validation_alias=_env("ICLOUD_COOKIE"))
    allow_unauthenticated_network: bool = Field(
        False,
        validation_alias=AliasChoices("ALIASFOX_ALLOW_UNAUTHENTICATED_NETWORK"),
    )
    trusted_proxy: bool = Field(False, validation_alias=_env("TRUSTED_PROXY"))
    trusted_proxy_hops: int = Field(1, ge=1, validation_alias=_env("TRUSTED_PROXY_HOPS"))
    signups_enabled: bool = Field(False, validation_alias=_env("SIGNUPS_ENABLED"))
    signup_allowlist: str = Field("", validation_alias=_env("SIGNUP_ALLOWLIST"))
    neon_auth_base_url: str | None = Field(None, validation_alias=_env("NEON_AUTH_BASE_URL"))
    neon_auth_jwks_url: str | None = Field(None, validation_alias=_env("NEON_AUTH_JWKS_URL"))
    public_url: str | None = Field(None, validation_alias=_env("PUBLIC_URL"))
    contact_email: str | None = Field(None, validation_alias=_env("CONTACT_EMAIL"))
    host: str = Field("127.0.0.1", validation_alias=_env("HOST"))
    port: int = Field(8080, validation_alias=_env("PORT"))

    @property
    def allowlist(self) -> frozenset[str]:
        return frozenset(
            e for e in (p.strip().lower() for p in self.signup_allowlist.split(",")) if e
        )

    @property
    def auth_required(self) -> bool:
        return self.auth_enabled or self.aliasfox_mode == "multi"

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        # populate_by_name (needed for keyword construction) would also make the
        # env source read generic variables named after the fields (HOST,
        # SECRET_KEY, PASSWORD_HASH). Restrict the env source to the aliases.
        env_settings.config = {
            **env_settings.config,
            "populate_by_name": False,
            "validate_by_name": False,
        }
        return (init_settings, env_settings)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _is_argon2_hash(value: str) -> bool:
    try:
        extract_parameters(value)
    except Exception:  # InvalidHashError, or anything odd: fail closed
        return False
    return True


def _validate_multi(s: Settings) -> None:
    # In multi mode AUTH_ENABLED and ALIASFOX_PASSWORD_HASH are ignored.
    for ok, name in (
        (s.neon_auth_base_url, "NEON_AUTH_BASE_URL"),
        (s.neon_auth_jwks_url, "NEON_AUTH_JWKS_URL"),
        (s.database_url, "DATABASE_URL"),
    ):
        if not ok:
            raise ConfigError(f"{name} is required when ALIASFOX_MODE=multi")
    if not s.trusted_proxy:
        raise ConfigError("TRUSTED_PROXY=true is required when ALIASFOX_MODE=multi")
    raw = os.environ.get("WEB_CONCURRENCY", "1").strip() or "1"
    try:
        workers = int(raw)
    except ValueError:
        raise ConfigError("WEB_CONCURRENCY must be an integer") from None
    if workers > 1:
        raise ConfigError(
            "ALIASFOX_MODE=multi requires a single worker process; "
            "WEB_CONCURRENCY must be 1 (in-memory caches and rate limits are per-process)"
        )


def validate_startup(s: Settings) -> None:
    if not s.secret_key:
        raise ConfigError("ALIASFOX_SECRET_KEY is required")
    if len(s.secret_key) < MIN_SECRET_KEY_LENGTH:
        raise ConfigError(
            f"ALIASFOX_SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters; "
            "generate one with `python -m aliasfox gen-secret`"
        )
    if s.public_url:
        parsed = urlparse(s.public_url)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in LOCAL_HOSTS
        ):
            raise ConfigError("PUBLIC_URL must be https (http is allowed only for localhost)")
    if s.aliasfox_mode == "multi":
        _validate_multi(s)
        return
    if s.auth_enabled and not s.password_hash:
        raise ConfigError("ALIASFOX_PASSWORD_HASH is required when AUTH_ENABLED=true")
    if s.auth_enabled and not _is_argon2_hash(s.password_hash):
        raise ConfigError(
            "ALIASFOX_PASSWORD_HASH is not a valid Argon2 hash — quote it in .env files "
            "because `$` is interpolated"
        )
    if (
        s.host not in LOCAL_HOSTS
        and not s.auth_enabled
        and not s.allow_unauthenticated_network
    ):
        raise ConfigError(
            f"Refusing to bind to {s.host} without AUTH_ENABLED=true; set "
            "ALIASFOX_ALLOW_UNAUTHENTICATED_NETWORK=true to override"
        )

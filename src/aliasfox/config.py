from functools import lru_cache
from pathlib import Path
from typing import Literal

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
    host: str = Field("127.0.0.1", validation_alias=_env("HOST"))
    port: int = Field(8080, validation_alias=_env("PORT"))

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


def validate_startup(s: Settings) -> None:
    if not s.secret_key:
        raise ConfigError("ALIASFOX_SECRET_KEY is required")
    if len(s.secret_key) < MIN_SECRET_KEY_LENGTH:
        raise ConfigError(
            f"ALIASFOX_SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters; "
            "generate one with `python -m aliasfox gen-secret`"
        )
    if s.aliasfox_mode == "multi":
        raise ConfigError("multi mode is not implemented yet")
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

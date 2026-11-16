from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ConfigError(Exception):
    """Raised when startup configuration is unsafe or incomplete."""


def _env(name: str) -> AliasChoices:
    # Accept the env name, or the Python field name when built with kwargs.
    return AliasChoices(name, name.lower())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")

    aliasfox_mode: Literal["single", "multi"] = Field(
        "single", validation_alias=AliasChoices("ALIASFOX_MODE", "aliasfox_mode")
    )
    auth_enabled: bool = Field(False, validation_alias=_env("AUTH_ENABLED"))
    database_url: str | None = Field(None, validation_alias=_env("DATABASE_URL"))
    data_dir: Path = Field(Path("/data"), validation_alias=_env("DATA_DIR"))
    secret_key: str | None = Field(
        None, repr=False, validation_alias=AliasChoices("ALIASFOX_SECRET_KEY", "secret_key")
    )
    password_hash: str | None = Field(
        None, repr=False, validation_alias=AliasChoices("ALIASFOX_PASSWORD_HASH", "password_hash")
    )
    icloud_cookie: str | None = Field(None, repr=False, validation_alias=_env("ICLOUD_COOKIE"))
    allow_unauthenticated_network: bool = Field(
        False,
        validation_alias=AliasChoices(
            "ALIASFOX_ALLOW_UNAUTHENTICATED_NETWORK", "allow_unauthenticated_network"
        ),
    )
    trusted_proxy: bool = Field(False, validation_alias=_env("TRUSTED_PROXY"))
    host: str = Field("127.0.0.1", validation_alias=_env("HOST"))
    port: int = Field(8080, validation_alias=_env("PORT"))


@lru_cache
def get_settings() -> Settings:
    return Settings()


def validate_startup(s: Settings) -> None:
    if not s.secret_key:
        raise ConfigError("ALIASFOX_SECRET_KEY is required")
    if s.auth_enabled and not s.password_hash:
        raise ConfigError("ALIASFOX_PASSWORD_HASH is required when AUTH_ENABLED=true")
    if (
        s.host not in LOCAL_HOSTS
        and not s.auth_enabled
        and not s.allow_unauthenticated_network
    ):
        raise ConfigError(
            f"Refusing to bind to {s.host} without AUTH_ENABLED=true; set "
            "ALIASFOX_ALLOW_UNAUTHENTICATED_NETWORK=true to override"
        )

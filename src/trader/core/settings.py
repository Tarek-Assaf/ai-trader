"""Validated environment settings; secret values are never included in reprs."""

from __future__ import annotations

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvironmentSettings(BaseSettings):
    """Environment-only application settings with safe trading defaults."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
    )

    role: Literal["all", "core", "worker", "backtest"] = "all"
    trading_mode: Literal["paper", "auto"] = "paper"
    broker_env: Literal["demo", "live"] = "demo"
    live_trading_enabled: bool = False
    approval_mode: bool = False
    capital_api_key: SecretStr | None = None
    capital_identifier: SecretStr | None = None
    capital_api_password: SecretStr | None = None
    llm_provider: str | None = None
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    database_url: SecretStr | None = None
    redis_url: SecretStr | None = None
    runpod_api_key: SecretStr | None = None
    runpod_network_volume_id: str | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_owner_chat_ids: str | None = None
    owner_totp_secret: SecretStr | None = None
    control_api_token: SecretStr | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: SecretStr | None = None
    smtp_password: SecretStr | None = None
    report_email_to: str | None = None
    github_token: SecretStr | None = None
    hf_token: SecretStr | None = None
    search_api_key: SecretStr | None = None
    news_api_key: SecretStr | None = None
    fred_api_key: SecretStr | None = None

    def sensitive_values(self) -> tuple[str, ...]:
        """Return explicitly provided secret values for logger redaction."""
        values: list[str] = []
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, SecretStr):
                secret = value.get_secret_value()
                if secret:
                    values.append(secret)
        return tuple(values)

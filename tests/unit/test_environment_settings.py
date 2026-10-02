"""Environment configuration retains paper-first defaults and masks secrets."""

from __future__ import annotations

from trader.core.settings import EnvironmentSettings


def test_environment_defaults_keep_live_trading_disabled() -> None:
    """Safe mode defaults are applied without environment configuration."""
    settings = EnvironmentSettings(_env_file=None)
    assert settings.trading_mode == "paper"
    assert settings.broker_env == "demo"
    assert settings.live_trading_enabled is False


def test_secrets_are_masked_from_repr_and_exposed_for_redaction() -> None:
    """SecretStr values are hidden in repr and offered to the redaction filter."""
    settings = EnvironmentSettings(_env_file=None, openai_api_key="test-secret-value")
    assert "test-secret-value" not in repr(settings)
    assert "test-secret-value" in settings.sensitive_values()

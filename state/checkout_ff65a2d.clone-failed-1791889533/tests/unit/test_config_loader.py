"""Strict loading and ceiling checks for every checked-in YAML config."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from trader.core.config import (
    ConfigLoadError,
    _check_risk_ceilings,
    load_all_configs,
    load_config_file,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
CONFIG_FILES = sorted(CONFIG_DIR.glob("*.yaml"))


@pytest.mark.parametrize("path", CONFIG_FILES, ids=lambda path: path.name)
def test_each_config_file_loads_as_frozen_model(path: Path) -> None:
    """Every repo config parses into a strict, immutable Pydantic model."""
    model = load_config_file(path)
    assert isinstance(model, BaseModel)
    assert model.model_config.get("extra") == "forbid"
    assert model.model_config.get("frozen") is True


def test_unknown_config_key_fails_with_file_and_key(tmp_path: Path) -> None:
    """Unknown keys fail closed and identify the source file and key."""
    path = tmp_path / "settings.yaml"
    path.write_text("version: 1\nnew_unreviewed_key: true\n", encoding="utf-8")
    with pytest.raises(ConfigLoadError, match=r"settings.yaml: .*new_unreviewed_key"):
        load_config_file(path)


def test_config_model_is_frozen() -> None:
    """Parsed top-level config fields cannot be reassigned."""
    model = load_config_file(CONFIG_DIR / "settings.yaml")
    with pytest.raises(ValidationError):
        model.version = 2  # type: ignore[misc]


def test_ceiling_file_is_baked_and_risk_cannot_exceed_it(tmp_path: Path) -> None:
    """Risk config overrides load from the volume but ceilings stay baked."""
    volume = tmp_path / "volume"
    volume.mkdir()
    risk_text = (CONFIG_DIR / "risk.yaml").read_text(encoding="utf-8")
    (volume / "risk.yaml").write_text(
        risk_text.replace("risk_per_trade_pct: 0.5", "risk_per_trade_pct: 2.0"),
        encoding="utf-8",
    )
    (volume / "risk_ceilings.yaml").write_text(
        "version: 1\nmax: {risk_per_trade_pct: 100}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigLoadError, match="risk_per_trade_pct=2.0 exceeds baked ceiling 1.0"):
        load_all_configs(
            CONFIG_DIR,
            volume_config_dir=volume,
            baked_ceilings_path=CONFIG_DIR / "risk_ceilings.yaml",
        )


def test_strategy_lab_floor_cannot_be_lowered(tmp_path: Path) -> None:
    """Startup rejects promotion gates looser than baked minimums."""
    volume = tmp_path / "volume"
    volume.mkdir()
    strategy_text = (CONFIG_DIR / "strategy_lab.yaml").read_text(encoding="utf-8")
    (volume / "strategy_lab.yaml").write_text(
        strategy_text.replace("min_trades: 300", "min_trades: 10", 1),
        encoding="utf-8",
    )
    with pytest.raises(ConfigLoadError, match="gate1_backtest_to_paper.min_trades=10"):
        load_all_configs(CONFIG_DIR, volume_config_dir=volume)


def test_risk_ceiling_with_unknown_risk_key_fails_clearly() -> None:
    """A ceiling typo cannot silently disable enforcement."""
    with pytest.raises(ConfigLoadError, match="missing key 'max_dayly_loss_pct'"):
        _check_risk_ceilings(
            {"risk_per_trade_pct": 0.5},
            {"max": {"max_dayly_loss_pct": 4.0}},
        )

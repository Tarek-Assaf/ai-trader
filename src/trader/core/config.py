"""Strict, immutable configuration loading and safety-ceiling validation."""

from __future__ import annotations

from pathlib import Path
from types import GenericAlias
from typing import Any, cast

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, create_model


class ConfigLoadError(ValueError):
    """Raised when a configuration file is missing or invalid."""


def _model_for_mapping(name: str, values: dict[str, Any]) -> type[BaseModel]:
    """Build a strict frozen model that mirrors one YAML mapping recursively."""
    fields = {key: (_type_for(value, f"{name}_{key}"), ...) for key, value in values.items()}
    return cast(
        type[BaseModel],
        create_model(  # type: ignore[call-overload]
            name,
            __config__=ConfigDict(extra="forbid", frozen=True),
            **fields,
        ),
    )


def _type_for(value: Any, name: str) -> Any:
    """Infer the field type from a checked-in default, preserving nested strictness."""
    if isinstance(value, dict):
        return _model_for_mapping(name.title().replace("_", ""), value)
    if isinstance(value, list):
        if not value:
            return tuple[Any, ...]
        member_types = tuple(dict.fromkeys(_type_for(item, name) for item in value))
        member_type = member_types[0] if len(member_types) == 1 else Any
        return GenericAlias(tuple, (member_type, ...))
    if isinstance(value, bool):
        return bool
    if isinstance(value, int):
        return int
    if isinstance(value, float):
        return float
    if isinstance(value, str):
        return str
    if value is None:
        return Any
    return type(value)


def _read_yaml(path: Path) -> dict[str, Any]:
    """Read one YAML mapping and identify file/key errors in plain language."""
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigLoadError(f"Could not read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigLoadError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigLoadError(f"Invalid {path}: the top level must be a YAML mapping")
    return cast(dict[str, Any], value)


def load_config_file(path: Path, *, schema_path: Path | None = None) -> BaseModel:
    """Load a YAML file using the checked-in default as its strict schema."""
    values = _read_yaml(path)
    default_schema = Path(__file__).resolve().parents[3] / "config" / path.name
    source_schema = schema_path or (default_schema if default_schema.is_file() else path)
    schema_values = _read_yaml(source_schema)
    model_type = _model_for_mapping(path.stem.title().replace("_", ""), schema_values)
    try:
        return model_type.model_validate(values)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise ConfigLoadError(f"Invalid {path}: {details}") from exc


def _check_risk_ceilings(risk: dict[str, Any], ceilings: dict[str, Any]) -> None:
    """Reject proposed risk values above baked maxima or below baked floors."""
    for key, ceiling in ceilings.get("max", {}).items():
        value = risk.get(key)
        if value is not None and value > ceiling:
            raise ConfigLoadError(
                f"Invalid risk.yaml: {key}={value} exceeds baked ceiling {ceiling}"
            )
    for key, floor in ceilings.get("min", {}).items():
        risk_key = key.removesuffix("_floor")
        value = risk.get(risk_key)
        if value is not None and value < floor:
            raise ConfigLoadError(
                f"Invalid risk.yaml: {risk_key}={value} is below baked floor {floor}"
            )


def _check_strategy_floors(strategy: dict[str, Any], ceilings: dict[str, Any]) -> None:
    """Reject Strategy Lab gates looser than the baked minimums."""
    gates = {
        "gate1_min_trades": ("gate1_backtest_to_paper", "min_trades"),
        "gate1_min_out_of_sample_trades": (
            "gate1_backtest_to_paper",
            "min_out_of_sample_trades",
        ),
        "gate1_min_walk_forward_windows": (
            "gate1_backtest_to_paper",
            "min_walk_forward_windows",
        ),
        "gate2_min_weeks": ("gate2_paper_to_micro_live", "min_weeks"),
        "gate2_min_paper_trades": ("gate2_paper_to_micro_live", "min_trades"),
        "gate3_min_weeks": ("gate3_micro_to_live", "min_weeks"),
        "gate3_min_micro_trades": ("gate3_micro_to_live", "min_trades"),
    }
    for floor_name, (gate_name, key) in gates.items():
        floor = ceilings.get("strategy_lab_floors", {}).get(floor_name)
        value = strategy.get(gate_name, {}).get(key)
        if floor is not None and value is not None and value < floor:
            raise ConfigLoadError(
                f"Invalid strategy_lab.yaml: {gate_name}.{key}={value} is below baked floor {floor}"
            )


def load_all_configs(
    default_config_dir: Path,
    *,
    volume_config_dir: Path | None = None,
    baked_ceilings_path: Path | None = None,
) -> dict[str, BaseModel]:
    """Load all YAML configs, keeping ceilings pinned to the baked image path."""
    baked_path = baked_ceilings_path or default_config_dir / "risk_ceilings.yaml"
    ceiling_schema = default_config_dir / "risk_ceilings.yaml"
    ceiling_model = load_config_file(baked_path, schema_path=ceiling_schema)
    ceiling_values = ceiling_model.model_dump()
    loaded: dict[str, BaseModel] = {"risk_ceilings": ceiling_model}
    for default_path in sorted(default_config_dir.glob("*.yaml")):
        if default_path.name == "risk_ceilings.yaml":
            continue
        volume_path = volume_config_dir / default_path.name if volume_config_dir else None
        selected_path = (
            volume_path if volume_path is not None and volume_path.is_file() else default_path
        )
        model = load_config_file(selected_path, schema_path=default_path)
        loaded[default_path.stem] = model
    if "risk" in loaded:
        _check_risk_ceilings(loaded["risk"].model_dump(), ceiling_values)
    if "strategy_lab" in loaded:
        _check_strategy_floors(loaded["strategy_lab"].model_dump(), ceiling_values)
    required = {"risk", "strategy_lab", "settings", "instruments", "scaling", "schedule", "backup"}
    missing = required.difference(loaded)
    if missing:
        raise ConfigLoadError(f"Missing configuration files: {', '.join(sorted(missing))}")
    return loaded

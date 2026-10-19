"""Allowlisted startup settings; saving never mutates the running risk controls."""
from __future__ import annotations

from datetime import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import tempfile

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.runtime_env import load_sterling_env
from app.core.risk_hierarchy import configured_hierarchy


class ProductionSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    allocation_capital_inr: float | None = Field(default=None, gt=0)
    experiment_id: str = Field(default='', max_length=120, pattern=r'^[A-Za-z0-9_.-]*$')
    dataset_start: str = Field(default='', max_length=64)
    risk_limits: str = Field(default='', max_length=20000)
    live_execution: bool = False

    @field_validator('dataset_start')
    @classmethod
    def timestamp(cls, value):
        if value and datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is None:
            raise ValueError('Dataset start must include a timezone')
        return value

    @field_validator('risk_limits')
    @classmethod
    def limits(cls, value):
        if value.strip():
            try:
                hierarchy = configured_hierarchy({'STERLING_RISK_LIMITS': value})
            except (TypeError, OverflowError) as exc:
                raise ValueError('Risk limits must contain numeric limits by scope') from exc
            if not hierarchy or not any(hierarchy.limits.values()):
                raise ValueError('Risk limits must declare at least one limit')
            if any(not math.isfinite(limit) for scopes in hierarchy.limits.values() for limit in scopes.values()):
                raise ValueError('Risk limits must be finite')
            return json.dumps(json.loads(value), separators=(',', ':'), allow_nan=False)
        return ''


KEYS = {
    'allocation_capital_inr': 'STERLING_ALLOCATION_CAPITAL_INR',
    'experiment_id': 'STERLING_EXPERIMENT_ID',
    'dataset_start': 'STERLING_DATASET_START',
    'risk_limits': 'STERLING_RISK_LIMITS',
    'live_execution': 'STERLING_LIVE_EXECUTION',
}


def settings_path() -> Path:
    return Path(__file__).resolve().parents[2] / '.env'


def read_settings(path: Path | None = None) -> ProductionSettings:
    from app.core.lane_registry import parse_live_execution
    values: dict[str, str] = {}
    load_sterling_env(path or settings_path(), environ=values)
    return ProductionSettings(
        allocation_capital_inr=values.get(KEYS['allocation_capital_inr']) or None,
        experiment_id=values.get(KEYS['experiment_id'], ''),
        dataset_start=values.get(KEYS['dataset_start'], ''),
        risk_limits=values.get(KEYS['risk_limits'], ''),
        live_execution=parse_live_execution(values.get(KEYS['live_execution'])),
    )


def save_settings(settings: ProductionSettings, path: Path | None = None) -> None:
    path = path or settings_path()
    if path.is_symlink():
        raise ValueError('Refusing a symlinked environment file')
    updates = {}
    for name, key in KEYS.items():
        value = getattr(settings, name)
        updates[key] = ('true' if value else 'false') if isinstance(value, bool) else str(value) if value is not None else ''
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name('.env.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        original = path.read_text() if path.exists() else ''
        lines = [line for line in original.splitlines()
                 if line.strip().removeprefix('export ').partition('=')[0].strip() not in updates]
        lines.extend(f'{key}={value}' for key, value in updates.items())
        descriptor, temporary = tempfile.mkstemp(prefix='.production-settings-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'w') as output:
                os.fchmod(output.fileno(), 0o600)
                output.write('\n'.join(lines) + '\n')
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

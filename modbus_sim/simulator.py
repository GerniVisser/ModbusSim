"""Time-varying value simulation — lazy, on-read generation.

Simulated values are produced as closed-form functions of a shared monotonic
clock, evaluated AT READ TIME inside :class:`~modbus_sim.register_map.RegisterMap`.
There is no background thread and no per-signal mutable state, so CPU cost scales
only with the registers actually polled (and is zero when simulation is disabled).
This is what makes the feature viable across ~123k signals.

Three stateless modes are supported:
- ``oscillate`` — sine wave between a min and max over ``period`` seconds.
- ``sawtooth``  — linear ramp min->max each period, then resets.
- ``toggle``    — square wave for bool/coil signals (on for half the period).

Each signal gets a deterministic phase from a stable hash of its name, so signals
that share a profile still look varied without storing any extra per-signal state.
"""

from __future__ import annotations

import json
import math
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic
from typing import Optional

from .signal_loader import Signal

# Process-wide clock origin. ``monotonic`` is immune to wall-clock adjustments.
_T0 = monotonic()

# Modes selectable as a project-wide default, per data category.
NUMERIC_MODES = ("oscillate", "sawtooth", "static")
BOOL_MODES = ("toggle", "static")

# Largest finite magnitude that survives float32 encoding (keeps reads finite).
_F32_MAX = 3.0e38

SIM_FILENAME = "simulation.json"


def clock() -> float:
    """Seconds since process start (shared time base for all generators)."""
    return monotonic() - _T0


# --------------------------------------------------------------------- defaults
@dataclass
class SimDefaults:
    """Project-wide simulation settings (the master switch + inherited defaults).

    Persisted as ``simulation.json`` in the project dir rather than inside the
    locked ``sim_config.yaml`` so the runtime toggle is an atomic file write and
    never has to rewrite the validated config.
    """

    enabled: bool = False
    numeric_mode: str = "oscillate"
    bool_mode: str = "toggle"
    period_seconds: float = 10.0
    amplitude_pct: float = 20.0     # +/- % around default_value when no min/max set
    amplitude_floor: float = 10.0   # minimum absolute swing (covers default_value ~ 0)

    @staticmethod
    def from_dict(d: Optional[dict]) -> "SimDefaults":
        base = SimDefaults()
        if not isinstance(d, dict):
            return base

        def _num(key, fallback):
            try:
                return float(d.get(key, fallback))
            except (TypeError, ValueError):
                return fallback

        return SimDefaults(
            enabled=bool(d.get("enabled", base.enabled)),
            numeric_mode=str(d.get("numeric_mode", base.numeric_mode)),
            bool_mode=str(d.get("bool_mode", base.bool_mode)),
            period_seconds=_num("period_seconds", base.period_seconds),
            amplitude_pct=_num("amplitude_pct", base.amplitude_pct),
            amplitude_floor=_num("amplitude_floor", base.amplitude_floor),
        )

    def to_dict(self) -> dict:
        return asdict(self)


def validate_defaults(d: SimDefaults) -> list[str]:
    errors: list[str] = []
    if d.numeric_mode not in NUMERIC_MODES:
        errors.append(f"numeric_mode must be one of {NUMERIC_MODES}")
    if d.bool_mode not in BOOL_MODES:
        errors.append(f"bool_mode must be one of {BOOL_MODES}")
    if d.period_seconds <= 0:
        errors.append("period_seconds must be > 0")
    if d.amplitude_pct < 0:
        errors.append("amplitude_pct must be >= 0")
    if d.amplitude_floor < 0:
        errors.append("amplitude_floor must be >= 0")
    return errors


def load_defaults(project_dir: Path | str) -> SimDefaults:
    """Read ``simulation.json`` from the project dir (defaults if absent/invalid)."""
    path = Path(project_dir) / SIM_FILENAME
    try:
        return SimDefaults.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return SimDefaults()


def save_defaults(project_dir: Path | str, defaults: SimDefaults) -> None:
    path = Path(project_dir) / SIM_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(defaults.to_dict(), indent=2), encoding="utf-8")


# --------------------------------------------------------------------- profiles
@dataclass
class SimProfile:
    """A signal's resolved generator. All ranges/period/phase precomputed once."""

    mode: str          # "oscillate" | "sawtooth" | "toggle"
    is_bool: bool
    lo: float
    hi: float
    period: float
    phase: float       # [0, 1)

    def value(self, t: float):
        """Engineering value at time ``t`` (bool for toggle, float otherwise)."""
        x = t / self.period + self.phase
        if self.mode == "toggle":
            return (x % 1.0) < 0.5
        if self.mode == "sawtooth":
            return self.lo + (self.hi - self.lo) * (x % 1.0)
        # oscillate (sine) between lo and hi
        mid = (self.lo + self.hi) / 2.0
        amp = (self.hi - self.lo) / 2.0
        return mid + amp * math.sin(2.0 * math.pi * x)


def _phase_for(name: str) -> float:
    """Deterministic phase in [0, 1) from a stable hash of the signal name."""
    return (zlib.crc32(name.encode("utf-8")) % 10000) / 10000.0


def _finite(v: float) -> float:
    """Clamp to a float32-safe finite range (guards against NaN/inf on encode)."""
    if v != v:  # NaN
        return 0.0
    if v > _F32_MAX:
        return _F32_MAX
    if v < -_F32_MAX:
        return -_F32_MAX
    return v


def resolve_profile(signal: Signal, defaults: SimDefaults) -> Optional[SimProfile]:
    """Resolve a signal's effective profile, or ``None`` if it should stay static.

    Precedence: an explicit ``signal.sim_mode`` overrides the project default;
    ``static`` (or simulation disabled) yields ``None``. Range comes from explicit
    ``sim_min``/``sim_max`` when both are set, else ``default_value`` +/- amplitude.
    """
    if not defaults.enabled:
        return None

    is_bool = signal.data_type == "bool"
    mode = (signal.sim_mode or "").strip()
    if mode == "static":
        return None
    if not mode:
        mode = defaults.bool_mode if is_bool else defaults.numeric_mode
    if mode == "static":
        return None

    phase = _phase_for(signal.name)
    period = signal.sim_period if (signal.sim_period and signal.sim_period > 0) else defaults.period_seconds
    if period <= 0:
        period = 1.0

    if is_bool:
        # Only a square wave makes sense for a single bit.
        return SimProfile("toggle", True, 0.0, 1.0, period, phase)

    if signal.sim_min is not None and signal.sim_max is not None:
        lo, hi = float(signal.sim_min), float(signal.sim_max)
    else:
        base = float(signal.default_value)
        amp = max(abs(base) * defaults.amplitude_pct / 100.0, defaults.amplitude_floor)
        lo, hi = base - amp, base + amp
    if hi < lo:
        lo, hi = hi, lo
    lo, hi = _finite(lo), _finite(hi)

    if mode not in ("oscillate", "sawtooth"):
        mode = "oscillate"
    return SimProfile(mode, False, lo, hi, period, phase)

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

# Motions selectable as a project-wide default, per data category. "step" is a
# per-signal-only motion (it needs an explicit step size), so it is not offered as a
# global default — see signal_loader.VALID_SIM_MODES for the full per-signal set.
NUMERIC_MODES = ("oscillate", "sawtooth", "triangle", "static")
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
    numeric_mode: str = "oscillate"   # default motion for signals that set a range
    bool_mode: str = "toggle"
    period_seconds: float = 10.0

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
    """A signal's resolved generator. All ranges/period/phase precomputed once.

    Motions: ``oscillate`` (sine), ``sawtooth`` (ramp low->high then reset),
    ``triangle`` (ramp low->high->low), ``step`` (discrete staircase that holds each
    level for ``period`` seconds before jumping by ``step``), and ``toggle`` (bool
    square wave). All are closed-form functions of the shared clock — no state.
    """

    mode: str          # "oscillate" | "sawtooth" | "triangle" | "step" | "toggle"
    is_bool: bool
    lo: float
    hi: float
    period: float      # cycle length; for "step" this is the hold interval per level
    phase: float       # [0, 1)
    step: float = 0.0  # only used by "step": engineering units per jump

    def value(self, t: float):
        """Engineering value at time ``t`` (bool for toggle, float otherwise)."""
        if self.mode == "step":
            return self._staircase(t)
        x = t / self.period + self.phase
        if self.mode == "toggle":
            return (x % 1.0) < 0.5
        if self.mode == "sawtooth":
            return self.lo + (self.hi - self.lo) * (x % 1.0)
        if self.mode == "triangle":
            f = x % 1.0
            tri = 2.0 * f if f < 0.5 else 2.0 * (1.0 - f)  # 0 -> 1 -> 0
            return self.lo + (self.hi - self.lo) * tri
        # oscillate (sine) between lo and hi
        mid = (self.lo + self.hi) / 2.0
        amp = (self.hi - self.lo) / 2.0
        return mid + amp * math.sin(2.0 * math.pi * x)

    def _staircase(self, t: float) -> float:
        """Discrete staircase: hold each level ``period`` s, jump ``step``, wrap low->high->low.

        Unlike the smooth motions, ``step`` ignores the name-derived phase so it starts
        at the low value at t=0 — matching the intuitive "low, then +step every interval".
        """
        span = self.hi - self.lo
        if self.step <= 0 or span <= 0 or self.period <= 0:
            return self.lo
        levels = max(1, round(span / self.step))  # number of jumps low -> high
        idx = int(t / self.period) % (levels + 1)
        return min(self.lo + self.step * idx, self.hi)


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

    Opt-in by range: a numeric signal fluctuates only when it has BOTH ``sim_min``
    and ``sim_max`` set (its low/high). With no range it stays static — there is no
    longer an amplitude-percentage fallback. Bools fluctuate when not ``static``.

    Motion: ``signal.sim_mode`` (oscillate/sawtooth/triangle/step) overrides the
    project default; ``static`` (or simulation disabled) yields ``None``. For ``step``,
    ``sim_period`` is the hold interval and ``sim_step`` is the jump size.
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

    # Numeric signals are opt-in: no explicit low/high => stay static.
    if signal.sim_min is None or signal.sim_max is None:
        return None
    lo, hi = float(signal.sim_min), float(signal.sim_max)
    if hi < lo:
        lo, hi = hi, lo
    lo, hi = _finite(lo), _finite(hi)

    if mode == "step":
        step = float(signal.sim_step) if signal.sim_step else 0.0
        if step <= 0:
            # "step" needs a positive jump size; without one fall back to a smooth ramp.
            mode = "oscillate"
        else:
            return SimProfile("step", False, lo, hi, period, phase, step=abs(step))

    if mode not in ("oscillate", "sawtooth", "triangle"):
        mode = "oscillate"
    return SimProfile(mode, False, lo, hi, period, phase)

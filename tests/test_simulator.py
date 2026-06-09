"""Value-simulation tests: generators, range resolution, and on-read overlay."""
import struct

import pytest

from modbus_sim import register_map as rm_mod
from modbus_sim.register_map import RegisterMap
from modbus_sim.signal_loader import parse_and_validate
from modbus_sim.simulator import SimDefaults, SimProfile, resolve_profile

HEADER = (
    "name,register_type,address,data_type,bit_index,word_order,scale,unit,"
    "section,description,default_value,writable,sim_mode,sim_min,sim_max,sim_period\n"
)


def make_signals(csv):
    signals, errors = parse_and_validate(csv)
    assert not errors, errors
    return signals


def enabled(**kw):
    base = dict(enabled=True, numeric_mode="oscillate", bool_mode="toggle",
               period_seconds=10.0, amplitude_pct=20.0, amplitude_floor=10.0)
    base.update(kw)
    return SimDefaults(**base)


@pytest.fixture
def fixed_clock(monkeypatch):
    """Pin the shared clock so reads are deterministic."""
    holder = {"t": 0.0}
    monkeypatch.setattr(rm_mod, "clock", lambda: holder["t"])
    return holder


# --------------------------------------------------------------- generators
def test_oscillate_stays_in_bounds_and_is_periodic():
    p = SimProfile("oscillate", False, lo=80, hi=120, period=10, phase=0.0)
    for i in range(200):
        t = i * 0.05
        assert 80 - 1e-9 <= p.value(t) <= 120 + 1e-9
    assert p.value(3.0) == pytest.approx(p.value(3.0 + 10))  # one full period


def test_sawtooth_ramps_then_wraps():
    p = SimProfile("sawtooth", False, lo=0, hi=100, period=10, phase=0.0)
    assert p.value(0.0) == pytest.approx(0.0)
    assert p.value(5.0) == pytest.approx(50.0)
    assert p.value(9.99) == pytest.approx(99.9, abs=0.2)
    assert p.value(10.0) == pytest.approx(0.0, abs=1e-9)  # reset


def test_toggle_alternates_each_half_period():
    p = SimProfile("toggle", True, lo=0, hi=1, period=10, phase=0.0)
    assert p.value(0.0) is True
    assert p.value(4.9) is True
    assert p.value(5.1) is False
    assert p.value(9.9) is False
    assert p.value(10.1) is True


def test_phase_varies_by_name_but_is_deterministic():
    a = resolve_profile(make_signals(HEADER + "A,holding,1,int16,,,1,,,,0,false,,,,")[0], enabled())
    b = resolve_profile(make_signals(HEADER + "B,holding,1,int16,,,1,,,,0,false,,,,")[0], enabled())
    a2 = resolve_profile(make_signals(HEADER + "A,holding,1,int16,,,1,,,,0,false,,,,")[0], enabled())
    assert a.phase != b.phase           # different names -> different phase
    assert a.phase == a2.phase          # same name -> stable across runs


# ----------------------------------------------------- range / mode resolution
def test_disabled_yields_no_profile():
    sig = make_signals(HEADER + "X,holding,1,int16,,,1,,,,50,false,,,,")[0]
    assert resolve_profile(sig, SimDefaults(enabled=False)) is None


def test_static_override_opts_out_even_when_enabled():
    sig = make_signals(HEADER + "X,holding,1,int16,,,1,,,,50,false,static,,,")[0]
    assert resolve_profile(sig, enabled()) is None


def test_range_defaults_to_value_plus_minus_amplitude():
    sig = make_signals(HEADER + "X,holding,1,int16,,,1,,,,100,false,,,,")[0]
    p = resolve_profile(sig, enabled(amplitude_pct=20, amplitude_floor=10))
    assert (p.lo, p.hi) == (80.0, 120.0)


def test_amplitude_floor_applies_near_zero():
    sig = make_signals(HEADER + "X,holding,1,int16,,,1,,,,0,false,,,,")[0]
    p = resolve_profile(sig, enabled(amplitude_pct=20, amplitude_floor=10))
    assert (p.lo, p.hi) == (-10.0, 10.0)


def test_explicit_min_max_override_default():
    sig = make_signals(HEADER + "X,holding,1,int16,,,1,,,,100,false,,5,25,")[0]
    p = resolve_profile(sig, enabled())
    assert (p.lo, p.hi) == (5.0, 25.0)


def test_per_signal_mode_overrides_default():
    sig = make_signals(HEADER + "X,holding,1,int16,,,1,,,,100,false,sawtooth,,,")[0]
    p = resolve_profile(sig, enabled(numeric_mode="oscillate"))
    assert p.mode == "sawtooth"


# ----------------------------------------------------------- on-read overlay
def test_read_block_overlays_int16(fixed_clock):
    sig = make_signals(HEADER + "X,holding,5,int16,,,1,,,,100,false,,,,")[0]
    rm = RegisterMap([sig], sim_defaults=enabled())
    p = resolve_profile(sig, enabled())
    for fixed_clock["t"] in (0.0, 2.3, 7.7):
        expected = rm._typed_to_raw(sig, p.value(fixed_clock["t"]))
        assert rm.read_block("holding", 5, 1) == [expected]


def test_read_block_overlays_int32_both_word_orders(fixed_clock):
    fixed_clock["t"] = 3.14
    sigs = make_signals(
        HEADER
        + "BE,holding,10,int32,,big_endian,1,,,,1000,false,,,,\n"
        + "LE,holding,20,int32,,little_endian,1,,,,1000,false,,,,\n"
    )
    rm = RegisterMap(sigs, sim_defaults=enabled())
    for s in sigs:
        p = resolve_profile(s, enabled())
        raw = rm._typed_to_raw(s, p.value(3.14)) & 0xFFFFFFFF
        high, low = (raw >> 16) & 0xFFFF, raw & 0xFFFF
        expect = [low, high] if s.word_order == "little_endian" else [high, low]
        assert rm.read_block("holding", s.address, 2) == expect


def test_read_block_overlays_float32(fixed_clock):
    fixed_clock["t"] = 1.0
    sig = make_signals(HEADER + "F,holding,0,float32,,big_endian,1,,,,3.5,false,,,,")[0]
    rm = RegisterMap([sig], sim_defaults=enabled())
    p = resolve_profile(sig, enabled())
    words = rm.read_block("holding", 0, 2)
    u32 = (words[0] << 16) | words[1]
    decoded = struct.unpack(">f", struct.pack(">I", u32))[0]
    assert decoded == pytest.approx(p.value(1.0), rel=1e-6)


def test_read_block_combines_bitpacked_bools(fixed_clock):
    # Two bools sharing one holding word at different bits; both toggle.
    sigs = make_signals(
        HEADER
        + "b0,holding,7,bool,0,,1,,,,0,false,,,,\n"
        + "b3,holding,7,bool,3,,1,,,,0,false,,,,\n"
    )
    rm = RegisterMap(sigs, sim_defaults=enabled())
    profs = {s.name: resolve_profile(s, enabled()) for s in sigs}
    fixed_clock["t"] = 2.0
    word = rm.read_block("holding", 7, 1)[0]
    expect = 0
    if profs["b0"].value(2.0):
        expect |= 1 << 0
    if profs["b3"].value(2.0):
        expect |= 1 << 3
    assert word == expect


def test_read_coil_overlays(fixed_clock):
    sig = make_signals(HEADER + "C,coil,2,bool,,,1,,,,0,false,,,,")[0]
    rm = RegisterMap([sig], sim_defaults=enabled())
    p = resolve_profile(sig, enabled())
    for fixed_clock["t"] in (1.0, 6.0):
        assert rm.read_coil("coil", 2) == p.value(fixed_clock["t"])


def test_wide_signal_partially_in_window_only_places_in_range_word(fixed_clock):
    fixed_clock["t"] = 4.2
    sig = make_signals(HEADER + "W,holding,10,int32,,big_endian,1,,,,1000,false,,,,")[0]
    rm = RegisterMap([sig], sim_defaults=enabled())
    p = resolve_profile(sig, enabled())
    raw = rm._typed_to_raw(sig, p.value(4.2)) & 0xFFFFFFFF
    # Read only the second word (address 11): low word for big_endian.
    assert rm.read_block("holding", 11, 1) == [raw & 0xFFFF]


def test_disabled_returns_stored_values_exactly():
    sig = make_signals(HEADER + "X,holding,5,int16,,,1,,,,0,false,,,,")[0]
    rm = RegisterMap([sig], sim_defaults=SimDefaults(enabled=False))
    rm.write_signal(sig, 1234)
    assert rm.read_block("holding", 5, 1) == [1234]
    assert rm.read_signal(sig) == 1234


def test_set_sim_defaults_toggles_overlay_live(fixed_clock):
    fixed_clock["t"] = 0.0
    sig = make_signals(HEADER + "X,holding,5,int16,,,1,,,,500,false,,,,")[0]
    rm = RegisterMap([sig], sim_defaults=SimDefaults(enabled=False))
    rm.write_signal(sig, 500)
    assert rm.read_block("holding", 5, 1) == [500]   # static
    rm.set_sim_defaults(enabled())
    # oscillate at t=0 with phase -> generally not exactly 500; just confirm it is
    # now produced by the generator rather than the stored value.
    p = resolve_profile(sig, enabled())
    assert rm.read_block("holding", 5, 1) == [rm._typed_to_raw(sig, p.value(0.0))]


def test_two_reads_at_different_times_differ(fixed_clock):
    sig = make_signals(HEADER + "X,holding,5,int16,,,1,,,,100,false,,,,")[0]
    rm = RegisterMap([sig], sim_defaults=enabled())
    fixed_clock["t"] = 1.0
    first = rm.read_block("holding", 5, 1)
    fixed_clock["t"] = 3.5
    second = rm.read_block("holding", 5, 1)
    assert first != second

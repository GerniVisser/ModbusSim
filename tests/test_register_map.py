"""RegisterMap encoding/decoding tests (REQUIREMENTS.md sections 9, 18)."""
import struct

from modbus_sim.register_map import RegisterMap
from modbus_sim.signal_loader import parse_and_validate


def make_regmap(csv):
    signals, errors = parse_and_validate(csv)
    assert not errors, errors
    return RegisterMap(signals), {s.name: s for s in signals}


HEADER = "name,register_type,address,data_type,bit_index,word_order,scale,unit,section,description,default_value,writable\n"


def test_uint16_and_int16_twos_complement():
    rm, sig = make_regmap(
        HEADER
        + "U,holding,10,uint16,,,1,,,,0,false\n"
        + "S,holding,11,int16,,,1,,,,0,false\n"
    )
    rm.write_signal(sig["U"], 65535)
    assert rm.read_signal(sig["U"]) == 65535
    rm.write_signal(sig["S"], -5)
    assert rm.read_block("holding", 11, 1)[0] == 65531  # two's complement raw
    assert rm.read_signal(sig["S"]) == -5


def test_uint32_word_order():
    rm, sig = make_regmap(
        HEADER
        + "BE,holding,20,uint32,,big_endian,1,,,,0,false\n"
        + "LE,holding,30,uint32,,little_endian,1,,,,0,false\n"
    )
    value = 0x0102ABCD
    rm.write_signal(sig["BE"], value)
    rm.write_signal(sig["LE"], value)
    # big_endian: high word at lower address; little_endian: low word at lower address
    assert rm.read_block("holding", 20, 2) == [0x0102, 0xABCD]
    assert rm.read_block("holding", 30, 2) == [0xABCD, 0x0102]
    assert rm.read_signal(sig["BE"]) == value
    assert rm.read_signal(sig["LE"]) == value


def test_int32_negative():
    rm, sig = make_regmap(HEADER + "I,holding,40,int32,,big_endian,1,,,,0,false\n")
    rm.write_signal(sig["I"], -2000000)
    assert rm.read_signal(sig["I"]) == -2000000


def test_float32_roundtrip_and_bit_pattern():
    rm, sig = make_regmap(HEADER + "F,holding,50,float32,,big_endian,1,,,,0,false\n")
    rm.write_signal(sig["F"], 600.0)
    raw = struct.unpack(">I", struct.pack(">f", 600.0))[0]
    assert rm.read_block("holding", 50, 2) == [(raw >> 16) & 0xFFFF, raw & 0xFFFF]
    assert abs(rm.read_signal(sig["F"]) - 600.0) < 1e-6


def test_two_bools_share_register_independently():
    rm, sig = make_regmap(
        HEADER
        + "A,holding,60,bool,0,,,,,,0,false\n"
        + "B,holding,60,bool,1,,,,,,0,false\n"
    )
    rm.write_signal(sig["A"], True)
    assert rm.read_signal(sig["A"]) is True
    assert rm.read_signal(sig["B"]) is False
    rm.write_signal(sig["B"], True)
    assert rm.read_signal(sig["A"]) is True  # untouched
    assert rm.read_block("holding", 60, 1)[0] == 0b11


def test_fc16_style_write_then_read():
    rm, sig = make_regmap(HEADER + "P,holding,70,int32,,big_endian,1,,,,0,false\n")
    # Simulate FC16 writing two raw words [high, low] for 100000.
    rm.write_block("holding", 70, [(100000 >> 16) & 0xFFFF, 100000 & 0xFFFF])
    assert rm.read_signal(sig["P"]) == 100000


def test_simulate_and_clear():
    rm, sig = make_regmap(
        HEADER
        + "U,holding,80,uint16,,,1,,,,4000,false\n"
        + "F,holding,82,float32,,big_endian,1,,,,600.0,false\n"
        + "Bit,holding,90,bool,3,,,,,,1,false\n"
    )
    rm.clear_all()
    assert rm.read_signal(sig["U"]) == 0
    assert rm.read_signal(sig["Bit"]) is False
    rm.set_defaults()
    assert rm.read_signal(sig["U"]) == 4000
    assert abs(rm.read_signal(sig["F"]) - 600.0) < 1e-6
    assert rm.read_signal(sig["Bit"]) is True


def test_coil_and_discrete_single_bit():
    rm, sig = make_regmap(
        HEADER
        + "Fan,coil,0,bool,,,1,,,,1,false\n"
        + "Door,discrete_input,5,bool,,,1,,,,0,false\n"
    )
    assert rm.read_signal(sig["Fan"]) is True
    assert rm.read_signal(sig["Door"]) is False
    rm.write_signal(sig["Door"], True)
    assert rm.read_signal(sig["Door"]) is True

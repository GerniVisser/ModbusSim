"""Signal CSV validation tests (REQUIREMENTS.md sections 7, 18)."""
from modbus_sim.signal_loader import parse_and_validate

HEADER = "name,register_type,address,data_type,bit_index,word_order,scale,unit,section,description,default_value,writable\n"


def errors_for(rows):
    _, errors = parse_and_validate(HEADER + rows)
    return errors


def test_all_six_data_types_load():
    csv = HEADER + "".join([
        "a,holding,0,uint16,,,1,,,,0,false\n",
        "b,holding,1,int16,,,1,,,,0,false\n",
        "c,holding,2,uint32,,big_endian,1,,,,0,false\n",
        "d,holding,4,int32,,little_endian,1,,,,0,false\n",
        "e,holding,6,float32,,big_endian,1,,,,1.5,false\n",
        "f,holding,8,bool,0,,,,,,1,false\n",
    ])
    signals, errors = parse_and_validate(csv)
    assert not errors
    assert len(signals) == 6


def test_missing_bit_index_for_bool_rejected():
    errors = errors_for("x,holding,0,bool,,,,,,,0,false\n")
    assert any(e.column == "bit_index" for e in errors)


def test_missing_word_order_for_uint32_rejected():
    errors = errors_for("x,holding,0,uint32,,,1,,,,0,false\n")
    assert any(e.column == "word_order" for e in errors)


def test_duplicate_signal_name_rejected():
    errors = errors_for(
        "dup,holding,0,uint16,,,1,,,,0,false\n"
        "dup,holding,1,uint16,,,1,,,,0,false\n"
    )
    assert any(e.column == "name" for e in errors)


def test_overlapping_32bit_register_space_rejected():
    # uint32 at 0 occupies 0 and 1; another signal at 1 overlaps.
    errors = errors_for(
        "wide,holding,0,uint32,,big_endian,1,,,,0,false\n"
        "clash,holding,1,uint16,,,1,,,,0,false\n"
    )
    assert any(e.column == "address" for e in errors)


def test_invalid_data_type_reports_row_and_column():
    errors = errors_for("x,holding,0,uint64,,,1,,,,0,false\n")
    assert errors[0].row == 2
    assert errors[0].column == "data_type"


def test_coil_bool_without_bit_index_is_valid():
    signals, errors = parse_and_validate(HEADER + "Fan,coil,0,bool,,,1,,,,1,false\n")
    assert not errors
    assert signals[0].bit_index is None


def test_duplicate_coil_address_rejected():
    errors = errors_for(
        "a,coil,3,bool,,,1,,,,0,false\n"
        "b,coil,3,bool,,,1,,,,0,false\n"
    )
    assert any(e.column == "address" for e in errors)

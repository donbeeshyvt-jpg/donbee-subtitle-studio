"""微秒範圍及重排對映契約。"""
import pytest
from pydantic import ValidationError
from app.studio.domain import TimeRange, SequenceItem, SourceMap, sequence_mappings, parse_time


@pytest.mark.parametrize("value", [-1, 1.5, True, float("nan"), 9007199254740992])
def test_microseconds_are_strict_safe_integers(value):
    with pytest.raises(ValidationError):
        TimeRange(start_us=value, end_us=9007199254740991)


def test_marker_and_invalid_clip():
    assert SequenceItem(kind="marker", start_us=3).end_us is None
    with pytest.raises(ValidationError):
        SequenceItem(start_us=3)
    with pytest.raises(ValidationError):
        TimeRange(start_us=3, end_us=3)


def test_reordered_repeated_mapping():
    items = [SequenceItem(id="a", start_us=900000000, end_us=920000000),
             SequenceItem(id="b", start_us=610000000, end_us=630000000),
             SequenceItem(id="c", start_us=610000000, end_us=630000000)]
    maps = sequence_mappings(items)
    assert maps[1].to_output(612000000) == 22000000
    assert maps[2].to_output(612000000) == 42000000
    assert maps[1].to_source(22000000) == 612000000


def test_source_map_cannot_stretch():
    with pytest.raises(ValidationError):
        SourceMap(source_start_us=10, source_end_us=30, asset_start_us=0, asset_end_us=19)


def test_parse_time_exact_microseconds():
    assert parse_time("1:50:00") == 6600000000
    assert parse_time("2:00:00.123456") == 7200123456
    assert parse_time("00:59.999999") == 59999999
    for text in ["-1", "1:60", "nan", "1.1234567"]:
        with pytest.raises(ValueError):
            parse_time(text)

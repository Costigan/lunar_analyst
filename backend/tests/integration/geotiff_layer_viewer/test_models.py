from datetime import datetime

from standalone.geotiff_layer_viewer.models import (
    TimeSeriesFrame,
    TimeSeriesLayer,
    parse_timestamp_from_name,
)


def test_parse_timestamp_from_name() -> None:
    dt = parse_timestamp_from_name("sun_image_2027-09-01T02-00-00.tif")
    assert dt == datetime(2027, 9, 1, 2, 0, 0)


def test_parse_timestamp_from_name_dash_separator() -> None:
    dt = parse_timestamp_from_name("safe_haven_2027-09-05-04-00-00.tif")
    assert dt == datetime(2027, 9, 5, 4, 0, 0)


def test_parse_timestamp_missing_returns_none() -> None:
    assert parse_timestamp_from_name("sun_image_no_date.tif") is None


def test_frame_at_or_before_floor_selection() -> None:
    layer = TimeSeriesLayer(
        series_name="sun_image_",
        frames=[
            TimeSeriesFrame(datetime(2027, 9, 1, 0, 0, 0), "/tmp/a.tif"),
            TimeSeriesFrame(datetime(2027, 9, 1, 1, 0, 0), "/tmp/b.tif"),
            TimeSeriesFrame(datetime(2027, 9, 1, 2, 0, 0), "/tmp/c.tif"),
        ],
    )

    assert layer.frame_at_or_before(datetime(2027, 9, 1, 1, 30, 0)).path == "/tmp/b.tif"
    assert layer.frame_at_or_before(datetime(2027, 9, 1, 0, 0, 0)).path == "/tmp/a.tif"
    assert layer.frame_at_or_before(datetime(2027, 8, 31, 23, 0, 0)) is None

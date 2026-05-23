from datetime import datetime

from standalone.geotiff_layer_viewer.models import SingleImageLayer, TimeSeriesFrame, TimeSeriesLayer
from standalone.geotiff_layer_viewer.persistence import AppState, load_state, save_state


def test_state_round_trip(tmp_path) -> None:
    state_path = tmp_path / "viewer_state.json"
    state = AppState(
        layers=[
            SingleImageLayer(name="base", opacity=0.5, path="/tmp/base.tif"),
            TimeSeriesLayer(
                name="sun",
                opacity=0.8,
                series_name="sun_image_",
                frames=[
                    TimeSeriesFrame(datetime(2027, 9, 1, 0, 0, 0), "/tmp/sun0.tif"),
                    TimeSeriesFrame(datetime(2027, 9, 1, 1, 0, 0), "/tmp/sun1.tif"),
                ],
            ),
        ],
        slider_time=datetime(2027, 9, 1, 1, 30, 0),
        window_geometry_hex="abcd",
    )

    save_state(state_path, state)
    loaded = load_state(state_path)

    assert len(loaded.layers) == 2
    assert isinstance(loaded.layers[0], SingleImageLayer)
    assert isinstance(loaded.layers[1], TimeSeriesLayer)
    assert loaded.layers[0].opacity == 0.5
    assert loaded.layers[1].frames[1].path == "/tmp/sun1.tif"
    assert loaded.slider_time == datetime(2027, 9, 1, 1, 30, 0)
    assert loaded.window_geometry_hex == "abcd"

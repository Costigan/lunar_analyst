from __future__ import annotations

from datetime import datetime
import math
from pathlib import Path
import sys

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .cache import LruCache
from .models import Layer, SingleImageLayer, TimeSeriesLayer, build_timeseries_from_paths
from .persistence import AppState, load_state, save_state
from .raster import cache_key_for_path, read_raster_rgba, signature_for_path

STATE_PATH = Path(__file__).resolve().parent / "geotiff_layer_viewer_state.json"


class ZoomPanGraphicsView(QGraphicsView):
    mouse_pixel_changed = Signal(object)

    def __init__(self, scene: QGraphicsScene, parent: QWidget | None = None) -> None:
        super().__init__(scene, parent)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.has_user_transform = False

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        step = event.angleDelta().y()
        if step == 0:
            return
        factor = 1.15 if step > 0 else (1.0 / 1.15)
        self.scale(factor, factor)
        self.has_user_transform = True
        event.accept()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.has_user_transform = True
        super().mousePressEvent(event)
        self._emit_pixel_at_view_pos(event.position().toPoint())

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        super().mouseMoveEvent(event)
        self._emit_pixel_at_view_pos(event.position().toPoint())

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self.mouse_pixel_changed.emit(None)
        super().leaveEvent(event)

    def fit_scene(self) -> None:
        rect = self.sceneRect()
        if rect.isNull():
            return
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        self.has_user_transform = False

    def _emit_pixel_at_view_pos(self, pos) -> None:
        rect = self.sceneRect()
        if rect.isNull():
            self.mouse_pixel_changed.emit(None)
            return

        scene_pos = self.mapToScene(pos)
        col = math.floor(scene_pos.x() - rect.left())
        row = math.floor(scene_pos.y() - rect.top())
        if 0 <= col < math.floor(rect.width()) and 0 <= row < math.floor(rect.height()):
            self.mouse_pixel_changed.emit((col, row))
        else:
            self.mouse_pixel_changed.emit(None)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("GeoTIFF Layer Viewer")
        self.layers: list[Layer] = []
        self.cache = LruCache(max_items=256)
        self.current_time: datetime | None = None
        self.graphics_items: dict[str, QGraphicsPixmapItem] = {}

        self.scene = QGraphicsScene(self)
        self.view = ZoomPanGraphicsView(self.scene)
        self.view.mouse_pixel_changed.connect(self._on_mouse_pixel_changed)

        self.layer_list = QListWidget()
        self.layer_list.currentRowChanged.connect(self._on_layer_selected)

        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)

        self.time_slider = QSlider(Qt.Orientation.Horizontal)
        self.time_slider.valueChanged.connect(self._on_time_slider_changed)
        self.time_label = QLabel("No time-series loaded")
        self.pixel_label = QLabel("Column, row: --, --")

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.addWidget(self.layer_list)

        btn_row = QHBoxLayout()
        up_btn = QPushButton("Up")
        down_btn = QPushButton("Down")
        remove_btn = QPushButton("Remove")
        up_btn.clicked.connect(self._move_selected_up)
        down_btn.clicked.connect(self._move_selected_down)
        remove_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(up_btn)
        btn_row.addWidget(down_btn)
        btn_row.addWidget(remove_btn)
        side_layout.addLayout(btn_row)

        form = QFormLayout()
        form.addRow("Opacity", self.opacity_slider)
        side_layout.addLayout(form)

        splitter = QSplitter()
        splitter.addWidget(side)
        splitter.addWidget(self.view)
        splitter.setStretchFactor(1, 1)

        bottom_time_panel = QWidget()
        bottom_time_layout = QVBoxLayout(bottom_time_panel)
        bottom_time_layout.setContentsMargins(8, 6, 8, 8)
        readout_row = QHBoxLayout()
        readout_row.addWidget(self.time_label)
        readout_row.addWidget(self.pixel_label)
        readout_row.addStretch(1)
        bottom_time_layout.addLayout(readout_row)
        bottom_time_layout.addWidget(self.time_slider)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(splitter, stretch=1)
        root_layout.addWidget(bottom_time_panel, stretch=0)
        self.setCentralWidget(root)

        self._build_menu()
        self._load_state()

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("Layers")
        add_img = QAction("Add Image Layer...", self)
        add_img.triggered.connect(self._add_image_layer)
        add_ts_files = QAction("Add Time-Series Layer (Files)...", self)
        add_ts_files.triggered.connect(self._add_timeseries_files)
        add_ts_dir = QAction("Add Time-Series Layer (Directory)...", self)
        add_ts_dir.triggered.connect(self._add_timeseries_directory)
        menu.addAction(add_img)
        menu.addAction(add_ts_files)
        menu.addAction(add_ts_dir)

    def _add_image_layer(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select GeoTIFF", "", "GeoTIFF (*.tif *.tiff)")
        if not path:
            return
        try:
            self._validate_signature(path)
        except Exception as exc:
            self._error(str(exc))
            return
        layer = SingleImageLayer(name=Path(path).name, path=str(Path(path).resolve()))
        self.layers.insert(0, layer)
        self._refresh_layer_list()
        self.layer_list.setCurrentRow(0)
        self._render()
        self._save_state()

    def _add_timeseries_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Select Time-Series GeoTIFF Files", "", "GeoTIFF (*.tif *.tiff)")
        if not paths:
            return
        self._create_timeseries_layer(paths)

    def _add_timeseries_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select Time-Series Directory")
        if not directory:
            return
        paths = [str(p) for p in sorted(Path(directory).glob("*.tif"))] + [str(p) for p in sorted(Path(directory).glob("*.tiff"))]
        if not paths:
            self._error("No .tif or .tiff files found in selected directory.")
            return
        self._create_timeseries_layer(paths)

    def _create_timeseries_layer(self, paths: list[str]) -> None:
        series_name, frames, skipped = build_timeseries_from_paths(paths)
        if not frames:
            self._error("No files with parseable timestamps were found.")
            return
        try:
            for f in frames:
                self._validate_signature(f.path)
        except Exception as exc:
            self._error(str(exc))
            return
        layer = TimeSeriesLayer(name=series_name or "time_series", series_name=series_name, frames=frames)
        self.layers.insert(0, layer)
        self._refresh_layer_list()
        self.layer_list.setCurrentRow(0)
        self._update_slider_bounds()
        self._render()
        self._save_state()
        if skipped:
            self._error(f"Skipped {len(skipped)} files without parseable timestamps.")

    def _validate_signature(self, path: str) -> None:
        sig = signature_for_path(path)
        if not self.layers:
            return
        baseline_path = self._first_layer_path(self.layers[0])
        if not baseline_path:
            return
        baseline = signature_for_path(baseline_path)
        if sig != baseline:
            raise ValueError(
                f"Raster dimensions/band count mismatch for {Path(path).name}; expected {baseline}, got {sig}."
            )

    @staticmethod
    def _first_layer_path(layer: Layer) -> str | None:
        if isinstance(layer, SingleImageLayer):
            return layer.path
        if layer.frames:
            return layer.frames[0].path
        return None

    def _refresh_layer_list(self) -> None:
        self.layer_list.clear()
        for layer in self.layers:
            self.layer_list.addItem(QListWidgetItem(layer.name or "layer"))

    def _move_selected_up(self) -> None:
        idx = self.layer_list.currentRow()
        if idx <= 0:
            return
        self.layers[idx - 1], self.layers[idx] = self.layers[idx], self.layers[idx - 1]
        self._refresh_layer_list()
        self.layer_list.setCurrentRow(idx - 1)
        self._render()
        self._save_state()

    def _move_selected_down(self) -> None:
        idx = self.layer_list.currentRow()
        if idx < 0 or idx >= len(self.layers) - 1:
            return
        self.layers[idx + 1], self.layers[idx] = self.layers[idx], self.layers[idx + 1]
        self._refresh_layer_list()
        self.layer_list.setCurrentRow(idx + 1)
        self._render()
        self._save_state()

    def _remove_selected(self) -> None:
        idx = self.layer_list.currentRow()
        if idx < 0:
            return
        del self.layers[idx]
        self._refresh_layer_list()
        self._update_slider_bounds()
        self._render()
        self._save_state()

    def _on_layer_selected(self, row: int) -> None:
        if row < 0 or row >= len(self.layers):
            return
        self.opacity_slider.blockSignals(True)
        self.opacity_slider.setValue(int(self.layers[row].opacity * 100))
        self.opacity_slider.blockSignals(False)

    def _on_opacity_changed(self, value: int) -> None:
        row = self.layer_list.currentRow()
        if row < 0 or row >= len(self.layers):
            return
        self.layers[row].opacity = value / 100.0
        self._render()
        self._save_state()

    def _update_slider_bounds(self) -> None:
        timestamps = []
        for layer in self.layers:
            if isinstance(layer, TimeSeriesLayer):
                timestamps.extend(f.timestamp for f in layer.frames)

        if not timestamps:
            self.time_slider.setEnabled(False)
            self.time_label.setText("No time-series loaded")
            self.current_time = None
            return

        min_ts = min(timestamps)
        max_ts = max(timestamps)
        self.time_slider.setEnabled(True)
        self.time_slider.blockSignals(True)
        self.time_slider.setRange(int(min_ts.timestamp()), int(max_ts.timestamp()))
        if self.current_time is None:
            self.current_time = min_ts
        self.current_time = max(min_ts, min(self.current_time, max_ts))
        self.time_slider.setValue(int(self.current_time.timestamp()))
        self.time_slider.blockSignals(False)
        self.time_label.setText(self.current_time.isoformat(sep=" "))

    def _on_time_slider_changed(self, value: int) -> None:
        self.current_time = datetime.fromtimestamp(value)
        self.time_label.setText(self.current_time.isoformat(sep=" "))
        self._render()
        self._save_state()

    def _on_mouse_pixel_changed(self, pixel: tuple[int, int] | None) -> None:
        if pixel is None:
            self.pixel_label.setText("Column, row: --, --")
            return
        col, row = pixel
        self.pixel_label.setText(f"Column, row: {col}, {row}")

    def _image_for_path(self, path: str) -> QImage:
        key = cache_key_for_path(path)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        rendered = read_raster_rgba(path)
        rgba = rendered.rgba
        h, w, _ = rgba.shape
        image = QImage(rgba.data, w, h, 4 * w, QImage.Format.Format_RGBA8888).copy()
        self.cache.put(key, image, image.sizeInBytes())
        return image

    def _render(self) -> None:
        self.scene.clear()
        self.graphics_items.clear()

        layer_count = len(self.layers)
        for z, layer in enumerate(self.layers):
            if (not layer.visible) or layer.opacity <= 0.0:
                continue
            image_path = None
            if isinstance(layer, SingleImageLayer):
                image_path = layer.path
            else:
                if self.current_time is None:
                    continue
                frame = layer.frame_at_or_before(self.current_time)
                if frame is None:
                    continue
                image_path = frame.path
            if not image_path:
                continue
            try:
                image = self._image_for_path(image_path)
            except Exception as exc:
                self._error(f"Failed to load {Path(image_path).name}: {exc}")
                continue
            pix = QPixmap.fromImage(image)
            item = self.scene.addPixmap(pix)
            item.setOpacity(layer.opacity)
            item.setZValue(float(layer_count - z))
            self.graphics_items[layer.layer_id] = item

        self.scene.setSceneRect(self.scene.itemsBoundingRect())
        if not self.view.has_user_transform:
            self.view.fit_scene()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if not self.view.has_user_transform:
            self.view.fit_scene()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._save_state()
        super().closeEvent(event)

    def _save_state(self) -> None:
        state = AppState(
            layers=self.layers,
            slider_time=self.current_time,
            window_geometry_hex=self.saveGeometry().toHex().data().decode("ascii"),
        )
        save_state(STATE_PATH, state)

    def _load_state(self) -> None:
        state = load_state(STATE_PATH)
        surviving: list[Layer] = []
        for layer in state.layers:
            if isinstance(layer, SingleImageLayer):
                if Path(layer.path).exists():
                    surviving.append(layer)
            else:
                frames = [f for f in layer.frames if Path(f.path).exists()]
                if frames:
                    layer.frames = frames
                    surviving.append(layer)
        self.layers = surviving
        self.current_time = state.slider_time
        self._refresh_layer_list()
        self._update_slider_bounds()
        self._render()
        if state.window_geometry_hex:
            try:
                self.restoreGeometry(bytes.fromhex(state.window_geometry_hex))
            except Exception:
                pass

    def _error(self, message: str) -> None:
        QMessageBox.warning(self, "GeoTIFF Layer Viewer", message)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.resize(1200, 800)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

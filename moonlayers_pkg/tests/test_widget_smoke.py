"""
Unit tests for moonlayers package.
"""

import pytest
from moonlayers import MoonMap, __version__


class TestMoonMap:
    """Tests for MoonMap widget class."""
    
    def test_version(self):
        """Test that version is defined."""
        assert __version__ == "0.1.0"
    
    def test_moonmap_instantiation(self):
        """Test basic MoonMap instantiation."""
        moon_map = MoonMap()
        assert moon_map is not None
        assert moon_map.projection == "ESRI:103878"
    
    def test_moonmap_with_custom_projection(self):
        """Test MoonMap with custom projection."""
        custom_proj = {
            "code": "CUSTOM:MOON",
            "proj4": "+proj=stere +lat_0=-90 +lon_0=0 +k=1 +x_0=0 +y_0=0 +R=1737400 +units=m +no_defs"
        }
        moon_map = MoonMap(projection=custom_proj)
        assert moon_map.projection == custom_proj
    
    def test_moonmap_with_wmts(self):
        """Test MoonMap with WMTS configuration."""
        wmts_config = {
            "get_capabilities_url": "https://trek.nasa.gov/tiles/Moon/EQ/LRO_WAC_Mosaic_Global_303ppd_v02/1.0.0/WMTSCapabilities.xml",
            "layer": "LRO_WAC_Mosaic_Global_303ppd_v02",
            "format": "image/png"
        }
        moon_map = MoonMap(wmts=wmts_config)
        assert moon_map.wmts == wmts_config
    
    def test_moonmap_controls_default(self):
        """Test default controls configuration."""
        moon_map = MoonMap()
        controls = moon_map.controls
        assert controls["zoom"] is True
        assert controls["zoom_slider"] is True
        assert controls["rotate"] is True
        assert controls["scale_line"] is True
    
    def test_moonmap_controls_custom(self):
        """Test custom controls configuration."""
        custom_controls = {
            "zoom": False,
            "zoom_slider": False,
            "rotate": True,
            "scale_line": True,
            "mouse_position": False,
            "overview_map": False,
            "fullscreen": True
        }
        moon_map = MoonMap(controls=custom_controls)
        assert moon_map.controls == custom_controls
    
    def test_moonmap_view_default(self):
        """Test default view configuration."""
        moon_map = MoonMap()
        view = moon_map.view
        assert view["center"] == [0, 0]
        assert view["zoom"] == 2
        assert view["rotation"] == 0.0
    
    def test_moonmap_view_custom(self):
        """Test custom view configuration."""
        custom_view = {
            "center": [100000, -50000],
            "zoom": 5,
            "rotation": 1.57
        }
        moon_map = MoonMap(view=custom_view)
        assert moon_map.view == custom_view
    
    def test_set_view(self):
        """Test set_view method."""
        moon_map = MoonMap()
        moon_map.set_view(center=[10000, 20000], zoom=4, rotation=0.5)
        # Note: In actual use, this would trigger a command to JS
        # Here we just verify the method exists and is callable
        assert hasattr(moon_map, '_command')
    
    def test_toggle_layer(self):
        """Test toggle_layer method."""
        moon_map = MoonMap()
        moon_map.toggle_layer("test_layer", True)
        # Verify method is callable
        assert hasattr(moon_map, 'toggle_layer')
    
    def test_set_opacity(self):
        """Test set_opacity method."""
        moon_map = MoonMap()
        moon_map.set_opacity("test_layer", 0.5)
        assert hasattr(moon_map, 'set_opacity')
    
    def test_fit_extent(self):
        """Test fit_extent method."""
        moon_map = MoonMap()
        moon_map.fit_extent()
        moon_map.fit_extent("test_layer")
        assert hasattr(moon_map, 'fit_extent')
    
    def test_export_png(self):
        """Test export_png method."""
        moon_map = MoonMap()
        moon_map.export_png(scale=1.5)
        assert hasattr(moon_map, 'export_png')
    
    def test_export_pdf(self):
        """Test export_pdf method."""
        moon_map = MoonMap()
        moon_map.export_pdf(size="A3", dpi=200)
        assert hasattr(moon_map, 'export_pdf')
    
    def test_event_callbacks(self):
        """Test event callback registration."""
        moon_map = MoonMap()
        
        clicked = []
        hovered = []
        extents = []
        exports = []
        
        def on_click(feature, coord):
            clicked.append((feature, coord))
        
        def on_hover(feature, coord):
            hovered.append((feature, coord))
        
        def on_extent(center, zoom, rotation, extent):
            extents.append((center, zoom, rotation, extent))
        
        def on_export(kind, data):
            exports.append((kind, data))
        
        moon_map.on_click_feature(on_click)
        moon_map.on_hover_feature(on_hover)
        moon_map.on_extent_changed(on_extent)
        moon_map.on_export_complete(on_export)
        
        # Verify callbacks are registered
        assert len(moon_map._click_feature_callbacks) == 1
        assert len(moon_map._hover_feature_callbacks) == 1
        assert len(moon_map._extent_changed_callbacks) == 1
        assert len(moon_map._export_complete_callbacks) == 1
    
    def test_geotiff_layers(self):
        """Test GeoTIFF layer configuration."""
        geotiffs = [
            {
                "url": "https://example.com/test.tif",
                "layer_id": "test_geotiff",
                "opacity": 0.7,
                "visible": True
            }
        ]
        moon_map = MoonMap(geotiffs=geotiffs)
        assert moon_map.geotiffs == geotiffs
    
    def test_geojson_layers(self):
        """Test GeoJSON layer configuration."""
        geojsons = [
            {
                "url": "https://example.com/test.geojson",
                "layer_id": "test_geojson",
                "style": {
                    "stroke": {"color": "#ff0000", "width": 2}
                },
                "opacity": 0.8,
                "visible": True
            }
        ]
        moon_map = MoonMap(geojsons=geojsons)
        assert moon_map.geojsons == geojsons
    
    def test_measure_config(self):
        """Test measurement configuration."""
        measure_config = {
            "enabled": True,
            "mode": "geodesic"
        }
        moon_map = MoonMap(measure=measure_config)
        assert moon_map.measure == measure_config
    
    def test_graticule(self):
        """Test graticule configuration."""
        moon_map = MoonMap(graticule=True)
        assert moon_map.graticule is True
        
        moon_map2 = MoonMap(graticule=False)
        assert moon_map2.graticule is False
    
    def test_permalink(self):
        """Test permalink configuration."""
        moon_map = MoonMap(permalink=True)
        assert moon_map.permalink is True
        
        moon_map2 = MoonMap(permalink=False)
        assert moon_map2.permalink is False
    
    def test_layer_switcher(self):
        """Test layer switcher configuration."""
        moon_map = MoonMap(layer_switcher=True)
        assert moon_map.layer_switcher is True
        
        moon_map2 = MoonMap(layer_switcher=False)
        assert moon_map2.layer_switcher is False


class TestPackageStructure:
    """Tests for package structure and imports."""
    
    def test_import_moonmap(self):
        """Test that MoonMap can be imported."""
        from moonlayers import MoonMap
        assert MoonMap is not None
    
    def test_import_version(self):
        """Test that version can be imported."""
        from moonlayers import __version__
        assert __version__ is not None
    
    def test_module_all(self):
        """Test __all__ exports."""
        import moonlayers
        assert hasattr(moonlayers, '__all__')
        assert 'MoonMap' in moonlayers.__all__
        assert '__version__' in moonlayers.__all__


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

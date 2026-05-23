"""
Test simplified MoonMap initialization and auto-fetch functionality.
"""

import pytest
from moonlayers import MoonMap


class TestSimplifiedMoonMap:
    """Tests for simplified MoonMap usage."""
    
    def test_default_initialization(self):
        """Test that MoonMap() works with no arguments."""
        moon_map = MoonMap()
        assert moon_map is not None
    
    def test_default_wmts_set(self):
        """Test that default WMTS is set when none provided."""
        moon_map = MoonMap()
        
        # Should have WMTS config
        assert moon_map.wmts is not None
        assert isinstance(moon_map.wmts, dict)
        
        # Should have required keys
        assert 'get_capabilities_url' in moon_map.wmts
        assert 'layer' in moon_map.wmts
        assert 'format' in moon_map.wmts
        
        # Should be the south polar mosaic
        assert 'SPole60_100mp' in moon_map.wmts['layer']
    
    def test_default_projection(self):
        """Test that default projection is south polar."""
        moon_map = MoonMap()
        assert moon_map.projection == "ESRI:103878"
    
    def test_custom_wmts_overrides_default(self):
        """Test that providing wmts overrides the default."""
        custom_wmts = {
            'get_capabilities_url': 'https://example.com/custom.xml',
            'layer': 'CustomLayer',
            'format': 'image/png'
        }
        
        moon_map = MoonMap(wmts=custom_wmts)
        
        assert moon_map.wmts == custom_wmts
        assert moon_map.wmts['layer'] == 'CustomLayer'
    
    def test_explicit_none_wmts_gets_default(self):
        """Test that wmts=None still gets default."""
        moon_map = MoonMap(wmts=None)
        
        assert moon_map.wmts is not None
        assert 'SPole60_100mp' in moon_map.wmts['layer']
    
    def test_default_controls(self):
        """Test that default controls are set."""
        moon_map = MoonMap()
        
        controls = moon_map.controls
        assert controls['zoom'] is True
        assert controls['zoom_slider'] is True
        assert controls['rotate'] is True
        assert controls['scale_line'] is True
    
    def test_default_layer_switcher(self):
        """Test that layer switcher is enabled by default."""
        moon_map = MoonMap()
        assert moon_map.layer_switcher is True
    
    def test_default_view(self):
        """Test that default view is south pole centered."""
        moon_map = MoonMap()
        
        view = moon_map.view
        assert view['center'] == [0, 0]
        assert view['zoom'] == 2
        assert view['rotation'] == 0.0
    
    def test_trek_layers_starts_empty(self):
        """Test that trek_layers starts empty (will auto-fetch on demand)."""
        moon_map = MoonMap()
        assert moon_map.trek_layers == []
    
    def test_manual_fetch_still_works(self):
        """Test that manual fetch_trek_layers() still works."""
        moon_map = MoonMap()
        
        # Should be able to manually fetch
        assert hasattr(moon_map, 'fetch_trek_layers')
        
        # Note: Not actually fetching in unit test to avoid network dependency
    
    def test_custom_parameters_still_work(self):
        """Test that all custom parameters still work."""
        moon_map = MoonMap(
            projection="CUSTOM:MOON",
            view={'center': [100, 200], 'zoom': 5},
            layer_switcher=False,
            graticule=True
        )
        
        assert moon_map.projection == "CUSTOM:MOON"
        assert moon_map.view['center'] == [100, 200]
        assert moon_map.view['zoom'] == 5
        assert moon_map.layer_switcher is False
        assert moon_map.graticule is True


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

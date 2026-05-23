"""
Unit tests for add_geotiff() and remove_geotiff() methods.
"""

import pytest
from moonlayers import MoonMap


class TestGeoTIFFMethods:
    """Tests for GeoTIFF layer management methods."""
    
    def test_add_geotiff_method_exists(self):
        """Test that add_geotiff method exists."""
        moon_map = MoonMap()
        assert hasattr(moon_map, 'add_geotiff')
    
    def test_remove_geotiff_method_exists(self):
        """Test that remove_geotiff method exists."""
        moon_map = MoonMap()
        assert hasattr(moon_map, 'remove_geotiff')
    
    def test_add_geotiff_from_url(self):
        """Test adding a GeoTIFF from URL."""
        moon_map = MoonMap()
        
        layer_id = moon_map.add_geotiff(
            'https://example.com/test.tif',
            layer_id='test_geotiff',
            opacity=0.7
        )
        
        assert layer_id == 'test_geotiff'
    
    def test_add_geotiff_updates_state(self):
        """Test that adding a GeoTIFF updates the geotiffs state."""
        moon_map = MoonMap()
        
        moon_map.add_geotiff(
            'https://example.com/test.tif',
            layer_id='test_geotiff',
            opacity=0.7
        )
        
        geotiffs = moon_map.geotiffs
        assert len(geotiffs) == 1
        assert geotiffs[0]['layer_id'] == 'test_geotiff'
        assert geotiffs[0]['url'] == 'https://example.com/test.tif'
        assert geotiffs[0]['opacity'] == 0.7
    
    def test_remove_geotiff(self):
        """Test removing a GeoTIFF layer."""
        moon_map = MoonMap()
        
        # Add a layer
        moon_map.add_geotiff(
            'https://example.com/test.tif',
            layer_id='test_geotiff'
        )
        assert len(moon_map.geotiffs) == 1
        
        # Remove it
        moon_map.remove_geotiff('test_geotiff')
        assert len(moon_map.geotiffs) == 0
    
    def test_add_geotiff_auto_generated_id(self):
        """Test that add_geotiff auto-generates layer IDs."""
        moon_map = MoonMap()
        
        layer_id = moon_map.add_geotiff('https://example.com/test2.tif')
        
        assert layer_id.startswith('geotiff_')
        assert len(layer_id) > len('geotiff_')
    
    def test_add_geotiff_with_extent(self):
        """Test adding a GeoTIFF with explicit extent."""
        moon_map = MoonMap()
        
        extent = [-100000, -100000, 100000, 100000]
        layer_id = moon_map.add_geotiff(
            'https://example.com/test3.tif',
            extent=extent
        )
        
        geotiff = [g for g in moon_map.geotiffs if g['layer_id'] == layer_id][0]
        assert 'extent' in geotiff
        assert geotiff['extent'] == extent
    
    def test_add_geotiff_default_params(self):
        """Test default parameters for add_geotiff."""
        moon_map = MoonMap()
        
        layer_id = moon_map.add_geotiff('https://example.com/test.tif')
        
        geotiff = [g for g in moon_map.geotiffs if g['layer_id'] == layer_id][0]
        assert geotiff['opacity'] == 1.0
        assert geotiff['visible'] is True
    
    def test_add_geotiff_custom_params(self):
        """Test custom parameters for add_geotiff."""
        moon_map = MoonMap()
        
        layer_id = moon_map.add_geotiff(
            'https://example.com/test.tif',
            layer_id='custom_id',
            opacity=0.5,
            visible=False
        )
        
        geotiff = [g for g in moon_map.geotiffs if g['layer_id'] == layer_id][0]
        assert geotiff['layer_id'] == 'custom_id'
        assert geotiff['opacity'] == 0.5
        assert geotiff['visible'] is False
    
    def test_add_multiple_geotiffs(self):
        """Test adding multiple GeoTIFF layers."""
        moon_map = MoonMap()
        
        id1 = moon_map.add_geotiff('https://example.com/test1.tif', layer_id='layer1')
        id2 = moon_map.add_geotiff('https://example.com/test2.tif', layer_id='layer2')
        id3 = moon_map.add_geotiff('https://example.com/test3.tif', layer_id='layer3')
        
        assert len(moon_map.geotiffs) == 3
        layer_ids = [g['layer_id'] for g in moon_map.geotiffs]
        assert 'layer1' in layer_ids
        assert 'layer2' in layer_ids
        assert 'layer3' in layer_ids
    
    def test_remove_specific_geotiff(self):
        """Test removing a specific GeoTIFF doesn't affect others."""
        moon_map = MoonMap()
        
        moon_map.add_geotiff('https://example.com/test1.tif', layer_id='layer1')
        moon_map.add_geotiff('https://example.com/test2.tif', layer_id='layer2')
        moon_map.add_geotiff('https://example.com/test3.tif', layer_id='layer3')
        
        # Remove middle layer
        moon_map.remove_geotiff('layer2')
        
        assert len(moon_map.geotiffs) == 2
        layer_ids = [g['layer_id'] for g in moon_map.geotiffs]
        assert 'layer1' in layer_ids
        assert 'layer2' not in layer_ids
        assert 'layer3' in layer_ids
    
    def test_add_geotiff_returns_layer_id(self):
        """Test that add_geotiff returns the layer ID."""
        moon_map = MoonMap()
        
        # With explicit ID
        result = moon_map.add_geotiff('https://example.com/test.tif', layer_id='my_layer')
        assert result == 'my_layer'
        
        # With auto-generated ID
        result2 = moon_map.add_geotiff('https://example.com/test2.tif')
        assert isinstance(result2, str)
        assert len(result2) > 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

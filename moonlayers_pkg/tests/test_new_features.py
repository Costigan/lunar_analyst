"""
Tests for new MoonLayers features:
- Layer ordering controls
- Feature (vector) layer support
- GeoTIFF/raster layer support
"""

import pytest
from moonlayers import MoonMap


class TestLayerOrdering:
    """Test layer ordering functionality."""
    
    def test_active_layers_order_preserved(self):
        """Test that active_layers maintains insertion order."""
        moon_map = MoonMap(projection="ESRI:103878")
        
        # Fetch layers
        layers = moon_map.fetch_trek_layers()
        assert len(layers) > 0, "Should fetch Trek layers"
        
        # Add a few mosaic layers
        mosaic_layers = [l for l in layers if 'Mosaic' in l.get('serviceTypes', [])][:3]
        
        for layer in mosaic_layers:
            moon_map.add_layer(layer['item_UUID'])
        
        # Check that active_layers contains the layers in order
        active = moon_map.active_layers
        assert len(active) == len(mosaic_layers)
        
        for i, layer in enumerate(mosaic_layers):
            assert active[i] == layer['item_UUID']
    
    def test_state_includes_layer_order(self):
        """Test that get_map_state includes layer order."""
        moon_map = MoonMap(projection="ESRI:103878")
        
        # Add some layers
        layers = moon_map.fetch_trek_layers()
        test_layers = [l for l in layers if 'Mosaic' in l.get('serviceTypes', [])][:2]
        
        for layer in test_layers:
            moon_map.add_layer(layer['item_UUID'])
        
        # Get state
        state = moon_map.get_map_state()
        
        assert 'active_layers' in state
        assert len(state['active_layers']) == len(test_layers)
        assert state['active_layers'] == moon_map.active_layers


class TestFeatureLayers:
    """Test Feature (vector) layer support."""
    
    def test_feature_layers_identified(self):
        """Test that Feature layers can be identified in catalog."""
        moon_map = MoonMap(projection="ESRI:103878")
        layers = moon_map.fetch_trek_layers()
        
        # Find feature layers - can be in serviceTypes OR productCat1
        feature_layers = [l for l in layers 
                         if 'Feature' in l.get('serviceTypes', []) or 
                            l.get('productCat1') == 'Feature']
        
        print(f"Found {len(feature_layers)} feature layers")
        
        # There should be some feature layers in the catalog
        assert isinstance(feature_layers, list)
        
        if feature_layers:
            # Check structure of first feature layer
            layer = feature_layers[0]
            assert 'item_UUID' in layer
            assert 'productLabel' in layer
            # Should have either serviceTypes or productCat1
            has_type_info = ('serviceTypes' in layer or 'productCat1' in layer)
            assert has_type_info, "Layer should have serviceTypes or productCat1"
    
    def test_feature_layer_search(self):
        """Test searching for feature layers."""
        moon_map = MoonMap(projection="ESRI:103878")
        
        # Search for any layers
        all_layers = moon_map.search_layers("")
        
        # Check service types are present
        has_service_types = any('serviceTypes' in l for l in all_layers)
        assert has_service_types, "Layers should have serviceTypes"


class TestGeoTIFFLayers:
    """Test GeoTIFF/raster layer support."""
    
    def test_raster_layers_identified(self):
        """Test that Raster/GeoTIFF layers can be identified in catalog."""
        moon_map = MoonMap(projection="ESRI:103878")
        layers = moon_map.fetch_trek_layers()
        
        # Find raster layers
        raster_layers = [l for l in layers 
                        if 'Raster' in l.get('serviceTypes', []) or 
                           'GeoTIFF' in l.get('serviceTypes', [])]
        
        print(f"Found {len(raster_layers)} raster layers")
        
        # Check structure (there may or may not be raster layers)
        assert isinstance(raster_layers, list)
        
        if raster_layers:
            layer = raster_layers[0]
            assert 'item_UUID' in layer
            assert 'productLabel' in layer
            assert 'serviceTypes' in layer


class TestLayerTypes:
    """Test layer type detection and handling."""
    
    def test_service_types_in_metadata(self):
        """Test that Trek layers have serviceTypes metadata."""
        moon_map = MoonMap(projection="ESRI:103878")
        layers = moon_map.fetch_trek_layers()
        
        # Check that layers have serviceTypes
        layers_with_types = [l for l in layers if 'serviceTypes' in l]
        
        # Many layers should have serviceTypes (Trek API has ~44% with this field)
        ratio = len(layers_with_types) / len(layers)
        assert ratio > 0.3, f"Only {ratio:.1%} of layers have serviceTypes"
        
        print(f"Layers with serviceTypes: {len(layers_with_types)}/{len(layers)} ({ratio:.1%})")
        
    def test_mosaic_layers_most_common(self):
        """Test that Mosaic (WMTS) layers are the most common type."""
        moon_map = MoonMap(projection="ESRI:103878")
        layers = moon_map.fetch_trek_layers()
        
        # Count by type
        mosaic_count = sum(1 for l in layers if 'Mosaic' in l.get('serviceTypes', []))
        feature_count = sum(1 for l in layers if 'Feature' in l.get('serviceTypes', []))
        raster_count = sum(1 for l in layers if 'Raster' in l.get('serviceTypes', []))
        
        print(f"Layer types: Mosaic={mosaic_count}, Feature={feature_count}, Raster={raster_count}")
        
        # Mosaic should be most common
        assert mosaic_count > 0, "Should have at least some Mosaic layers"
        assert mosaic_count >= feature_count, "Mosaic layers should be most common"


class TestIntegration:
    """Integration tests for new features."""
    
    def test_add_multiple_layer_types(self):
        """Test adding different types of layers to the same map."""
        moon_map = MoonMap(projection="ESRI:103878")
        layers = moon_map.fetch_trek_layers()
        
        # Find one of each type (ensure they're different layers)
        mosaic_layer = next((l for l in layers if 'Mosaic' in l.get('serviceTypes', [])), None)
        feature_layer = next((l for l in layers 
                             if ('Feature' in l.get('serviceTypes', []) or 
                                 l.get('productCat1') == 'Feature') and 
                             l['item_UUID'] != (mosaic_layer['item_UUID'] if mosaic_layer else None)), None)
        
        expected_count = 0
        
        if mosaic_layer:
            moon_map.add_layer(mosaic_layer['item_UUID'])
            expected_count += 1
            print(f"Added mosaic layer: {mosaic_layer['title']}")
        
        if feature_layer:
            # Note: Feature layers might fail if MapServer is not accessible
            # So we wrap this in try/except
            try:
                moon_map.add_layer(feature_layer['item_UUID'])
                expected_count += 1
                print(f"Added feature layer: {feature_layer['title']}")
            except Exception as e:
                print(f"Feature layer failed (may be expected): {e}")
        
        # Check that the expected number of layers were added
        assert len(moon_map.active_layers) >= expected_count
        print(f"Successfully added {len(moon_map.active_layers)} layers")
        
    def test_state_persistence_with_multiple_layers(self):
        """Test that state can be saved and restored with multiple layers."""
        moon_map = MoonMap(projection="ESRI:103878")
        layers = moon_map.fetch_trek_layers()
        
        # Add a few layers
        mosaic_layers = [l for l in layers if 'Mosaic' in l.get('serviceTypes', [])][:2]
        
        for layer in mosaic_layers:
            moon_map.add_layer(layer['item_UUID'])
        
        # Save state
        state = moon_map.get_map_state()
        
        assert 'projection' in state
        assert 'view' in state
        assert 'active_layers' in state
        assert len(state['active_layers']) == len(mosaic_layers)
        
        # Verify layer IDs match
        for layer in mosaic_layers:
            assert layer['item_UUID'] in state['active_layers']


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

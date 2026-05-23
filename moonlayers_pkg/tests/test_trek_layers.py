"""
Test Trek layer search and management functionality.
"""

import pytest
from moonlayers import MoonMap


class TestTrekLayers:
    """Tests for Trek layer catalog and search functionality."""
    
    def test_fetch_trek_layers(self):
        """Test fetching Trek layer catalog."""
        moon_map = MoonMap(projection="ESRI:103878")
        
        layers = moon_map.fetch_trek_layers()
        
        assert isinstance(layers, list)
        assert len(layers) > 0, "Should fetch at least one layer from Trek API"
        
        # Check layer structure
        first_layer = layers[0]
        assert 'item_UUID' in first_layer
        assert 'productLabel' in first_layer
        assert 'title' in first_layer
    
    def test_fetch_trek_layers_caching(self):
        """Test that Trek layers are cached."""
        moon_map = MoonMap(projection="ESRI:103878")
        
        # First fetch
        layers1 = moon_map.fetch_trek_layers()
        
        # Second fetch (should be cached)
        layers2 = moon_map.fetch_trek_layers()
        
        # Should be the same object (cached)
        assert layers1 is layers2
        
        # Force refresh
        layers3 = moon_map.fetch_trek_layers(force_refresh=True)
        
        # Should have same content but may be different object
        assert len(layers3) == len(layers1)
    
    def test_search_layers_simple(self):
        """Test simple layer search."""
        moon_map = MoonMap(projection="ESRI:103878")
        
        # Simple search
        results = moon_map.search_layers("Artemis")
        assert isinstance(results, list)
        assert len(results) > 0, "Should find Artemis-related layers"
        
        # Verify results contain the search term
        for layer in results[:3]:
            text = f"{layer.get('productLabel', '')} {layer.get('title', '')} {layer.get('description', '')}".lower()
            assert 'artemis' in text, f"Search result should contain 'artemis': {layer.get('title')}"
    
    def test_search_layers_and_operator(self):
        """Test AND search operator."""
        moon_map = MoonMap(projection="ESRI:103878")
        
        # AND search
        and_results = moon_map.search_layers("Artemis AND Mosaic")
        assert isinstance(and_results, list)
        
        # Each result should contain both terms
        for layer in and_results[:3]:
            text = f"{layer.get('productLabel', '')} {layer.get('title', '')} {layer.get('description', '')}".lower()
            assert 'artemis' in text and 'mosaic' in text, "AND search should require both terms"
    
    def test_search_layers_or_operator(self):
        """Test OR search operator."""
        moon_map = MoonMap(projection="ESRI:103878")
        
        # OR search
        or_results = moon_map.search_layers("Artemis OR Apollo")
        assert isinstance(or_results, list)
        assert len(or_results) > 0, "Should find layers with Artemis or Apollo"
    
    def test_search_layers_not_operator(self):
        """Test NOT search operator."""
        moon_map = MoonMap(projection="ESRI:103878")
        
        # NOT search
        not_results = moon_map.search_layers("LRO NOT crater")
        assert isinstance(not_results, list)
        
        # Results should not contain 'crater'
        for layer in not_results[:5]:
            text = f"{layer.get('productLabel', '')} {layer.get('title', '')} {layer.get('description', '')}".lower()
            assert 'crater' not in text, "NOT search should exclude the term"
    
    def test_search_layers_complex(self):
        """Test complex boolean search."""
        moon_map = MoonMap(projection="ESRI:103878")
        
        # Complex search with parentheses
        complex_results = moon_map.search_layers("(Artemis OR Apollo) AND -crater")
        assert isinstance(complex_results, list)
    
    def test_search_layers_empty_query(self):
        """Test search with empty query returns all layers."""
        moon_map = MoonMap(projection="ESRI:103878")
        
        all_layers = moon_map.fetch_trek_layers()
        empty_search = moon_map.search_layers("")
        
        assert len(empty_search) == len(all_layers), "Empty search should return all layers"
    
    def test_search_layers_no_matches(self):
        """Test search with no matches."""
        moon_map = MoonMap(projection="ESRI:103878")
        
        no_results = moon_map.search_layers("XYZ_NONEXISTENT_LAYER_12345")
        assert isinstance(no_results, list)
        assert len(no_results) == 0, "Should return empty list for no matches"


class TestTrekLayerManagement:
    """Tests for adding and removing Trek layers."""
    
    def test_add_layer_by_uuid(self):
        """Test adding layer by UUID."""
        moon_map = MoonMap(projection="ESRI:103878")
        
        # Search for a layer to add
        results = moon_map.search_layers("Artemis")
        assert len(results) > 0, "Need at least one layer to test"
        
        layer_to_add = results[0]
        layer_id = layer_to_add['item_UUID']
        
        # Initially no active layers
        assert len(moon_map.active_layers) == 0
        
        # Add layer
        moon_map.add_layer(layer_id)
        assert layer_id in moon_map.active_layers
        assert len(moon_map.active_layers) == 1
    
    def test_add_layer_by_product_label(self):
        """Test adding layer by productLabel."""
        moon_map = MoonMap(projection="ESRI:103878")
        
        results = moon_map.search_layers("Artemis")
        assert len(results) > 0, "Need at least one layer to test"
        
        layer = results[0]
        product_label = layer['productLabel']
        
        # Add by productLabel
        moon_map.add_layer(product_label)
        
        # Should be in active layers (by UUID)
        assert layer['item_UUID'] in moon_map.active_layers
    
    def test_add_layer_duplicate_prevention(self):
        """Test that adding same layer twice doesn't duplicate."""
        moon_map = MoonMap(projection="ESRI:103878")
        
        results = moon_map.search_layers("Artemis")
        assert len(results) > 0
        
        layer_id = results[0]['item_UUID']
        
        # Add layer twice
        moon_map.add_layer(layer_id)
        moon_map.add_layer(layer_id)
        
        # Should only appear once
        assert moon_map.active_layers.count(layer_id) == 1
    
    def test_add_nonexistent_layer(self):
        """Test adding non-existent layer fails gracefully."""
        moon_map = MoonMap(projection="ESRI:103878")
        
        # Should not raise exception
        moon_map.add_layer("nonexistent-uuid-12345")
        
        # Should not be in active layers
        assert "nonexistent-uuid-12345" not in moon_map.active_layers
    
    def test_remove_layer(self):
        """Test removing layer."""
        moon_map = MoonMap(projection="ESRI:103878")
        
        # Add a layer first
        results = moon_map.search_layers("Artemis")
        assert len(results) > 0
        
        layer_id = results[0]['item_UUID']
        moon_map.add_layer(layer_id)
        assert layer_id in moon_map.active_layers
        
        # Remove layer
        moon_map.remove_layer(layer_id)
        assert layer_id not in moon_map.active_layers
    
    def test_remove_nonexistent_layer(self):
        """Test removing non-existent layer fails silently."""
        moon_map = MoonMap(projection="ESRI:103878")
        
        # Should not raise exception
        moon_map.remove_layer("nonexistent-uuid-12345")
        
        # Active layers should still be empty
        assert len(moon_map.active_layers) == 0
    
    def test_active_layers_property(self):
        """Test active_layers property tracking."""
        moon_map = MoonMap(projection="ESRI:103878")
        
        # Initially empty
        assert moon_map.active_layers == []
        
        # Add layers
        results = moon_map.search_layers("Artemis")
        if len(results) >= 2:
            moon_map.add_layer(results[0]['item_UUID'])
            moon_map.add_layer(results[1]['item_UUID'])
            
            assert len(moon_map.active_layers) == 2


class TestTrekStateManagement:
    """Tests for state save and restore functionality."""
    
    def test_get_map_state(self):
        """Test getting widget state."""
        moon_map = MoonMap(
            projection="ESRI:103878",
            view={"center": [100, 200], "zoom": 5, "rotation": 0.5}
        )
        
        # Add a layer
        results = moon_map.search_layers("Artemis")
        if len(results) > 0:
            moon_map.add_layer(results[0]['item_UUID'])
        
        # Get state
        state = moon_map.get_map_state()
        
        # Verify state structure
        assert 'version' in state
        assert state['version'] == '1.0'
        assert state['projection'] == 'ESRI:103878'
        assert state['view']['center'] == [100, 200]
        assert state['view']['zoom'] == 5
        assert state['view']['rotation'] == 0.5
        assert 'active_layers' in state
    
    def test_set_map_state_view(self):
        """Test restoring view state."""
        moon_map = MoonMap(projection="ESRI:103878")
        
        state = {
            'version': '1.0',
            'projection': 'ESRI:103878',
            'view': {'center': [500, 600], 'zoom': 7, 'rotation': 1.5}
        }
        
        moon_map.set_map_state(state)
        
        assert moon_map.view['center'] == [500, 600]
        assert moon_map.view['zoom'] == 7
        assert moon_map.view['rotation'] == 1.5
    
    def test_set_map_state_controls(self):
        """Test restoring controls state."""
        moon_map = MoonMap(projection="ESRI:103878")
        
        state = {
            'version': '1.0',
            'controls': {
                'zoom': False,
                'zoom_slider': False,
                'rotate': True
            }
        }
        
        moon_map.set_map_state(state)
        
        assert moon_map.controls['zoom'] is False
        assert moon_map.controls['zoom_slider'] is False
        assert moon_map.controls['rotate'] is True
    
    def test_state_round_trip(self):
        """Test saving and restoring state preserves data."""
        moon_map = MoonMap(
            projection="ESRI:103878",
            view={"center": [100, 200], "zoom": 5, "rotation": 0.5}
        )
        
        # Add a layer
        results = moon_map.search_layers("Artemis")
        if len(results) > 0:
            layer_id = results[0]['item_UUID']
            moon_map.add_layer(layer_id)
            
            # Save state
            state = moon_map.get_map_state()
            
            # Create new widget and restore
            moon_map2 = MoonMap(projection="ESRI:103878")
            moon_map2.set_map_state(state)
            
            # Verify restoration
            assert moon_map2.view['center'] == [100, 200]
            assert moon_map2.view['zoom'] == 5

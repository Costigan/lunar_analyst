/**
 * Main widget integration - connects OpenLayers map with anywidget model.
 */

import Map from 'ol/Map';
import View from 'ol/View';
import { Graticule } from 'ol/layer';
import { Stroke, Fill } from 'ol/style';

import { setupProjection } from './projection.js';
import { createWMTSLayer } from './wmts.js';
import { createGeoTIFFLayer, createGeoJSONLayer, findLayerById, toggleLayer, setLayerOpacity, fitToLayerExtent } from './layers.js';
import { createControls, createLayerSwitcher, createZoomToExtentControl, updateControls, addLayerOrderingControls, restoreLayerOrder } from './controls.js';
import { createDefaultInteractions, createMeasurementTools, createPermalink } from './interactions.js';
import { createPopup, setupFeatureInteractions } from './overlays.js';
import { exportMapToPNG, exportMapToPDF, ensureMapRendered, downloadFile } from './export.js';
import { createTrekLayer } from './trek-layers.js';
import { createSearchControl } from './layer-catalog.js';

import 'ol/ol.css';
import 'ol-layerswitcher/dist/ol-layerswitcher.css';

/**
 * Initialize the map widget.
 * 
 * @param {Object} model - Anywidget model
 * @param {HTMLElement} el - Container element
 */
export async function render({ model, el }) {
  console.log('Initializing MoonLayers widget...');
  
  // Create map container
  const mapDiv = document.createElement('div');
  mapDiv.style.width = '100%';
  mapDiv.style.height = '600px';
  mapDiv.style.position = 'relative';
  mapDiv.style.backgroundColor = '#f0f0f0'; // Light gray background for debugging
  mapDiv.className = 'moonlayers-map';
  el.appendChild(mapDiv);
  
  console.log('Map container created:', mapDiv, 'Parent element:', el);
  
  // State management
  let map = null;
  let projection = null;
  let wmtsLayer = null;
  let controlsRef = null;
  let layerSwitcher = null;
  let measurementTools = null;
  let permalink = null;
  let popup = null;
  let graticule = null;
  let baseExtent = null;
  let mapReady = false;
  let latestGeotiffConfigs = [];
  let layerSyncPromise = Promise.resolve();
  
  // Helper to serialize layer sync operations to prevent race conditions
  function queueGeotiffSync(configs) {
    layerSyncPromise = layerSyncPromise.then(() => syncGeotiffLayers(configs)).catch(err => {
      console.error('Error in queued layer sync:', err);
    });
    return layerSyncPromise;
  }
  
  async function syncGeotiffLayers(configs) {
    if (!map || !projection) {
      return;
    }
    const geotiffs = configs || [];
    // Track existing GeoTIFF layers currently on map
    const existingLayerIds = new Set();
    map.getLayers().forEach(layer => {
      if (layer.get('layer_type') === 'geotiff') {
        existingLayerIds.add(layer.get('layer_id'));
      }
    });
    // Add layers that are not yet present
    for (const config of geotiffs) {
      const layerId = config.layer_id;
      if (!existingLayerIds.has(layerId)) {
        console.log('Adding GeoTIFF layer:', layerId);
        try {
          const { layer } = await createGeoTIFFLayer(config, projection);
          map.addLayer(layer);
          console.log('Added GeoTIFF layer to map:', layerId);
          if (layerSwitcher) {
            layerSwitcher.renderPanel();
            setTimeout(() => {
              if (controlsRef && controlsRef.layerOrderingEnhance) {
                controlsRef.layerOrderingEnhance();
              }
            }, 150);
          }
        } catch (error) {
          console.error('Failed to add GeoTIFF layer:', layerId, error);
        }
      }
    }
    // Determine which layers should be removed
    const desiredIds = new Set(geotiffs.map(g => g.layer_id));
    const layersToRemove = [];
    map.getLayers().forEach(layer => {
      if (layer.get('layer_type') === 'geotiff') {
        const layerId = layer.get('layer_id');
        if (!desiredIds.has(layerId)) {
          layersToRemove.push(layer);
        }
      }
    });
    layersToRemove.forEach(layer => {
      console.log('Removing GeoTIFF layer:', layer.get('layer_id'));
      map.removeLayer(layer);
    });
    if (geotiffs.length > existingLayerIds.size || layersToRemove.length > 0) {
      map.render();
    }
  }
  
  /**
   * Add a Trek layer to the map.
   */
  async function addTrekLayerToMap(layerMetadata) {
    try {
      const layerId = layerMetadata.item_UUID;
      
      // Check if already added
      const existing = findLayerById(map, layerId);
      if (existing) {
        console.log('Layer already exists:', layerId);
        return;
      }
      
      // Create the layer based on service types
      const layer = await createTrekLayer(layerMetadata, projection);
      
      // Debug: Check view state and layer visibility
      const view = map.getView();
      const viewExtent = view.calculateExtent(map.getSize());
      const viewResolution = view.getResolution();
      const viewZoom = view.getZoom();
      
      console.log('Current view state:', {
        extent: viewExtent,
        resolution: viewResolution,
        zoom: viewZoom
      });
      
      console.log('Layer extent:', layer.getExtent());
      console.log('Layer visible:', layer.getVisible());
      console.log('Layer min/max resolution:', layer.getMinResolution(), layer.getMaxResolution());
      
      // Add to map on top of other layers (but below controls/overlays)
      // Get all layers and add at the end so it appears on top
      const layers = map.getLayers();
      layers.push(layer);
      console.log('Trek layer added to map on top:', layerId);
      
      // Force a render
      map.render();
      
      // Update active_layers in model
      const activeLayers = model.get('active_layers') || [];
      if (!activeLayers.includes(layerId)) {
        model.set('active_layers', [...activeLayers, layerId]);
        model.save_changes();
      }
      
      // Refresh layer switcher if it exists
      if (layerSwitcher) {
        layerSwitcher.renderPanel();
        
        // Trigger layer ordering controls enhancement after a short delay
        // to allow the layer switcher to re-render
        setTimeout(() => {
          if (controlsRef && controlsRef.layerOrderingEnhance) {
            controlsRef.layerOrderingEnhance();
          }
        }, 150);
      }
      
    } catch (error) {
      console.error('Error adding Trek layer:', error);
      alert(`Failed to add layer: ${error.message}`);
    }
  }
  
  /**
   * Remove a layer from the map.
   */
  function removeLayerFromMap(layerId) {
    const layer = findLayerById(map, layerId);
    if (layer) {
      map.removeLayer(layer);
      console.log('Layer removed:', layerId);
      
      // Update active_layers in model
      const activeLayers = model.get('active_layers') || [];
      model.set('active_layers', activeLayers.filter(id => id !== layerId));
      model.save_changes();
      
      // Refresh layer switcher
      if (layerSwitcher) {
        layerSwitcher.renderPanel();
      }
    }
  }
  
  /**
   * Initialize the map.
   */
  async function initializeMap() {
    try {
      // Setup projection
      const projectionConfig = model.get('projection') || 'ESRI:103878';
      projection = setupProjection(projectionConfig);
      console.log('Projection set up:', projection.getCode());
      
      // Get initial view
      const viewConfig = model.get('view') || { center: [0, 0], zoom: 2, rotation: 0 };
      
      // Create map
      map = new Map({
        target: mapDiv,
        view: new View({
          projection: projection,
          center: viewConfig.center,
          zoom: viewConfig.zoom,
          rotation: viewConfig.rotation
        }),
        interactions: createDefaultInteractions({ dragRotateAndZoom: true })
      });
      
      console.log('Map created');
      
      // Setup controls
      const controlsConfig = model.get('controls') || {};
      const { controls, controlsArray } = createControls(controlsConfig, map);
      controlsRef = controls;
      
      // Add controls to map
      controlsArray.forEach(control => map.addControl(control));
      
      // Add layer switcher if enabled
      if (model.get('layer_switcher')) {
        layerSwitcher = createLayerSwitcher();
        map.addControl(layerSwitcher);
        
        // Add layer ordering controls and store the enhance function
        const enhanceFunc = addLayerOrderingControls(layerSwitcher, map, model);
        if (!controlsRef) {
          controlsRef = {};
        }
        controlsRef.layerOrderingEnhance = enhanceFunc;
      }
      
      // Add Trek layer search control
      const searchControl = createSearchControl(
        model, 
        (layerMetadata) => {
          console.log('Adding layer from search UI:', layerMetadata.item_UUID);
          addTrekLayerToMap(layerMetadata);
        },
        (layerMetadata) => {
          console.log('Removing layer from search UI:', layerMetadata.item_UUID);
          removeLayerFromMap(layerMetadata.item_UUID);
        }
      );
      map.addControl(searchControl);
      
      // Setup popup and interactions
      popup = createPopup(map);
      setupFeatureInteractions(
        map,
        popup,
        (feature, coord) => {
          // Emit click event to Python
          model.set('_event', {
            type: 'click_feature',
            feature: feature,
            coord: coord,
            timestamp: Date.now()
          });
          model.save_changes();
        },
        (feature, coord) => {
          // Emit hover event to Python
          model.set('_event', {
            type: 'hover_feature',
            feature: feature,
            coord: coord,
            timestamp: Date.now()
          });
          model.save_changes();
        }
      );
      
      // Setup permalink if enabled
      if (model.get('permalink')) {
        permalink = createPermalink(map);
        permalink.restoreFromPermalink();
      }
      
      // Setup measurement tools
      const measureConfig = model.get('measure') || {};
      if (measureConfig.enabled) {
        measurementTools = createMeasurementTools(map, measureConfig);
      }
      
      // Add graticule if enabled
      if (model.get('graticule')) {
        try {
          graticule = new Graticule({
            strokeStyle: new Stroke({
              color: 'rgba(255,255,255,0.4)',
              width: 1,
              lineDash: [2, 4]
            }),
            showLabels: false,  // Disable labels for custom projections to avoid errors
            wrapX: false,
            // Set target projection explicitly
            targetSize: 100
          });
          graticule.setMap(map);
          console.log('Graticule added');
        } catch (error) {
          console.warn('Failed to add graticule:', error);
        }
      }
      
      // Load WMTS layer
      const wmtsConfig = model.get('wmts');
      if (wmtsConfig) {
        console.log('Loading WMTS layer...');
        const wmtsResult = await createWMTSLayer(wmtsConfig, projection);
        wmtsLayer = wmtsResult.layer;
        baseExtent = wmtsResult.extent;
        
        console.log('WMTS layer extent:', baseExtent);
        console.log('WMTS layer source:', wmtsLayer.getSource());
        console.log('Current map view:', {
          center: map.getView().getCenter(),
          zoom: map.getView().getZoom(),
          extent: map.getView().calculateExtent(map.getSize())
        });
        
        map.addLayer(wmtsLayer);
        console.log('WMTS layer added');
        
        // Fit view to WMTS extent if no custom view was set
        if (baseExtent && viewConfig.center[0] === 0 && viewConfig.center[1] === 0) {
          map.getView().fit(baseExtent, {
            size: map.getSize(),
            padding: [20, 20, 20, 20],
            duration: 0
          });
        }
        
        // Force a render
        map.render();
        
        // Add zoom to extent control if we have an extent
        if (baseExtent) {
          const zoomToExtentControl = createZoomToExtentControl(baseExtent);
          map.addControl(zoomToExtentControl);
        }
      }
      
      // Capture current GeoTIFF configs for later processing once map is ready
      latestGeotiffConfigs = model.get('geotiffs') || [];
      
      // Load GeoJSON layers
      const geojsons = model.get('geojsons') || [];
      for (const config of geojsons) {
        console.log('Loading GeoJSON layer:', config.layer_id);
        const { layer } = await createGeoJSONLayer(config, projection);
        map.addLayer(layer);
      }
      
      // Emit initial extent
      map.on('moveend', () => {
        const view = map.getView();
        if (!view) return;
        
        const center = view.getCenter();
        const zoom = view.getZoom();
        const rotation = view.getRotation();
        const extent = view.calculateExtent(map.getSize());
        
        // Only emit if we have valid data
        if (center && zoom !== undefined && extent) {
          model.set('_event', {
            type: 'extent_changed',
            center: center,
            zoom: zoom,
            rotation: rotation,
            extent: extent,
            timestamp: Date.now()
          });
          model.save_changes();
        }
      });
      
      console.log('Map initialization complete');
      
      // Mark map ready and sync any queued GeoTIFF layers
      mapReady = true;
      await queueGeotiffSync(latestGeotiffConfigs);
      
      // Signal to Python that widget is fully ready
      model.set('_widget_ready', true);
      model.save_changes();
      
    } catch (error) {
      console.error('Error initializing map:', error);
      el.innerHTML = `<div style="color: red; padding: 20px;">
        <h3>Error initializing map:</h3>
        <p>${error.message}</p>
        <pre>${error.stack}</pre>
      </div>`;
    }
  }
  
  /**
   * Handle commands from Python.
   */
  async function handleCommand(command) {
    if (!command || !command.action) return;
    
    console.log('Handling command:', command);
    
    try {
      switch (command.action) {
        case 'set_view':
          if (map) {
            const view = map.getView();
            if (command.center) view.setCenter(command.center);
            if (command.zoom !== undefined) view.setZoom(command.zoom);
            if (command.rotation !== undefined) view.setRotation(command.rotation);
          }
          break;
          
        case 'toggle_layer':
          if (map && command.layer_id !== undefined) {
            toggleLayer(map, command.layer_id, command.visible);
          }
          break;
          
        case 'add_trek_layer':
          if (map && command.layer_metadata) {
            addTrekLayerToMap(command.layer_metadata);
          }
          break;
          
        case 'remove_layer':
          if (map && command.layer_id) {
            removeLayerFromMap(command.layer_id);
          }
          break;
          
        case 'set_opacity':
          if (map && command.layer_id !== undefined) {
            setLayerOpacity(map, command.layer_id, command.opacity);
          }
          break;
        
        case 'sync_geotiffs':
          latestGeotiffConfigs = command.configs || model.get('geotiffs') || [];
          console.log('Sync command received; geotiff count:', latestGeotiffConfigs.length);
          if (mapReady) {
            await queueGeotiffSync(latestGeotiffConfigs);
          }
          break;
          
        case 'fit_extent':
          if (map) {
            if (command.layer_id) {
              const layer = findLayerById(map, command.layer_id);
              if (layer) {
                fitToLayerExtent(map, layer);
              }
            } else if (baseExtent) {
              // Fit to base WMTS extent
              map.getView().fit(baseExtent, {
                padding: [50, 50, 50, 50],
                duration: 1000
              });
            }
          }
          break;
          
        case 'export_png':
          if (map) {
            const scale = command.scale || 1.0;
            ensureMapRendered(map).then(() => {
              return exportMapToPNG(map, scale);
            }).then((base64) => {
              model.set('_event', {
                type: 'export_complete',
                kind: 'png',
                data: base64,
                timestamp: Date.now()
              });
              model.save_changes();
            }).catch((error) => {
              console.error('PNG export failed:', error);
            });
          }
          break;
          
        case 'export_pdf':
          if (map) {
            const size = command.size || 'A4';
            const dpi = command.dpi || 150;
            ensureMapRendered(map).then(() => {
              return exportMapToPDF(map, size, dpi);
            }).then((base64) => {
              model.set('_event', {
                type: 'export_complete',
                kind: 'pdf',
                data: base64,
                timestamp: Date.now()
              });
              model.save_changes();
            }).catch((error) => {
              console.error('PDF export failed:', error);
            });
          }
          break;
      }
    } catch (error) {
      console.error('Error handling command:', error);
    }
  }
  
  // Listen for command changes
  model.on('change:_command', () => {
    const command = model.get('_command');
    handleCommand(command);
  });
  
  // Listen for control changes
  model.on('change:controls', () => {
    if (map && controlsRef) {
      const newControls = model.get('controls');
      updateControls(map, controlsRef, newControls);
    }
  });
  
  // Listen for GeoTIFF layer changes (dynamically added layers)
  model.on('change:geotiffs', async () => {
    latestGeotiffConfigs = model.get('geotiffs') || [];
    if (!mapReady) {
      return;
    }
    await queueGeotiffSync(latestGeotiffConfigs);
  });
  
  // Initialize the map (runs in background, doesn't block render completion)
  initializeMap().catch(error => {
    console.error('Map initialization failed:', error);
    el.innerHTML = `<div style="color: red; padding: 20px;">
      <h3>Error initializing map:</h3>
      <p>${error.message}</p>
      <pre>${error.stack}</pre>
    </div>`;
  });
}

// Export render function for anywidget
export default { render };

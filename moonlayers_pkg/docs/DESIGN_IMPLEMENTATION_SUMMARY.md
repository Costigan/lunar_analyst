# MoonLayers Design & Implementation Summary

## Overview
MoonLayers is an interactive lunar mapping widget for Marimo and Jupyter, built with OpenLayers and anywidget. It supports advanced lunar projections, WMTS tiles from NASA Moon Trek, dynamic GeoTIFF raster layers, and vector overlays. The project is designed for robust, zero-configuration use in notebook environments, with a focus on reliability, extensibility, and ease of use.

---

## Architectural Decisions

### 1. **Integrated HTTP Server for GeoTIFFs**
- **Problem:** Blob/data URLs for local GeoTIFFs failed to support concurrent tile streaming, causing rendering gaps and memory issues.
- **Solution:** A threaded HTTP server (`moonlayers/geotiff_server.py`) is started automatically in the background when a local file is added. It registers files, serves them via unique URLs, and supports HTTP range requests for efficient tile streaming.
- **Benefits:**
  - Zero configuration: No manual server setup required.
  - Any file location: Not limited to a single directory.
  - Reliable: No tile failures, supports large files and multiple layers.
  - Secure: Binds to localhost only, cleans up on kernel exit.
- **Tradeoffs:**
  - Adds a background thread per Python process.
  - Requires port allocation (handled automatically).

### 2. **Widget Initialization Pattern (Marimo/Jupyter)**
- **Lesson Learned:** The widget must be displayed before adding layers. In Marimo, use a 3-cell pattern: create widget → display widget → add layers. This ensures the JavaScript side is initialized before dynamic layer addition.
- **Documentation:** See `MARIMO_USAGE_PATTERN.md` for details and troubleshooting tips.

### 3. **Smart Defaults & Simplification**
- **Default Base Layer:** Automatically uses LRO WAC South Pole Mosaic (100m/pixel) if no WMTS is specified.
- **Layer Search:** Trek layer catalog is auto-fetched when the search panel is opened, improving performance and user experience.
- **Minimal Configuration:** Users can create a working map with just `MoonMap()`.

### 4. **Extensible Layer System**
- **Layer Types:** Supports WMTS, GeoTIFF (COG), GeoJSON, and is architected for future support of XYZ, WMS, and other formats.
- **Projection Handling:** Full proj4 integration for custom lunar projections. All layers are transformed to the map’s projection as needed.

### 5. **Frontend Architecture**
- **OpenLayers:** Used for map rendering, controls, and layer management.
- **ol-layerswitcher:** Provides interactive layer control and ordering.
- **geotiff.js:** Handles raster tile streaming from HTTP sources.
- **Event System:** Bidirectional communication between Python and JavaScript for state sync and event callbacks.

---

## Technical Tradeoffs
- **HTTP Server vs. Data URLs:** Chose integrated HTTP server for reliability and performance over embedding large files as data URLs.
- **Singleton Server Pattern:** Ensures only one server per process, avoids port conflicts, and simplifies resource management.
- **Localhost Binding:** Prioritizes security and simplicity; not exposed to the network.
- **Dynamic Port Allocation:** Avoids manual configuration and port conflicts.
- **Traitlets for State Sync:** Used for robust Python ↔ JavaScript communication.

---

## Implementation Highlights
- **moonlayers/geotiff_server.py:** Implements the HTTP server, file registry, and range request handling.
- **moonlayers/moon_map.py:** Main widget logic, integrates HTTP server, manages layers and controls.
- **src/**: JavaScript source for widget, layers, controls, interactions, and export.
- **examples/**: Marimo and Jupyter demo notebooks illustrating correct usage patterns.
- **tests/**: Unit and manual test scripts for backend and frontend validation.
- **Documentation:** Comprehensive guides in README.md, BUILD.md, and dedicated markdowns for limitations, implementation, and usage patterns.

---

## Lessons Learned
- **Widget Rendering Order:** Always display the widget before adding layers to avoid frontend initialization errors.
- **Range Request Handling:** Proper HTTP range support is essential for tile streaming from compressed and uncompressed GeoTIFFs.
- **Zero Configuration:** Automatic server startup and file registration greatly improve user experience and reliability.
- **Extensibility:** Modular design allows for easy addition of new layer types and features.
- **Anywidget Sync Latency in Marimo:** Trait synchronization from JavaScript to Python has inherent ~10 second latency due to marimo's batching mechanism. Do NOT rely on `model.save_changes()` for immediate sync - it only flushes when the render function completes or after certain events (like WMTS loading).
- **Automatic Queueing Over Explicit Waiting:** The widget's `change:geotiffs` listener and command queue pattern handle deferred operations automatically. Users should NOT need to call `wait_until_ready()` - just call `add_geotiff()` etc. directly.

---

## Critical Implementation Patterns for Widget Methods

### 1. **Python Method Design: Use Trait Updates, Not Commands**
When adding new widget methods that need to trigger JavaScript actions:

**✅ PREFERRED - Use Traitlets (automatically queued):**
```python
def add_layer(self, config):
    # Modify a synced trait - JS listener handles queueing
    current_layers = list(self.layers)
    current_layers.append(config)
    self.layers = current_layers  # Trait update triggers JS listener
```

**❌ AVOID - Using _send_command() requires manual queueing:**
```python
def add_layer(self, config):
    # Commands sent via _send_command() only work if widget is ready
    self._send_command({'action': 'add_layer', 'config': config})
```

**Why:** Trait updates automatically sync to JavaScript listeners, which can check `mapReady` and queue operations. Commands via `_send_command()` require the widget to be ready or manual queueing logic.

### 2. **JavaScript Listener Pattern: Check mapReady and Queue**
When handling trait changes in JavaScript that require the map to be initialized:

**✅ CORRECT Pattern:**
```javascript
model.on('change:layers', async () => {
  const layers = model.get('layers') || [];
  latestLayersConfig = layers;  // Store for later processing
  
  if (!mapReady) {
    return;  // Queue implicitly - will be processed after init
  }
  
  await syncLayers(latestLayersConfig);
});

// In initializeMap(), after mapReady = true:
mapReady = true;
await syncLayers(latestLayersConfig);  // Process queued configs
```

**❌ INCORRECT - No queueing:**
```javascript
model.on('change:layers', async () => {
  const layers = model.get('layers') || [];
  await syncLayers(layers);  // Fails if map not ready!
});
```

### 3. **Never Rely on Early Readiness Signals**
**❌ DON'T DO THIS:**
```javascript
// Attempting to signal ready before initializeMap() completes
setTimeout(() => {
  model.set('_widget_ready', true);
  model.save_changes();  // This won't sync until render completes!
}, 100);
```

**✅ DO THIS INSTEAD:**
```javascript
async function initializeMap() {
  // ... map setup including WMTS loading ...
  
  mapReady = true;
  await syncAllQueuedOperations();
  
  // Signal ready AFTER initialization completes
  model.set('_widget_ready', true);
  model.save_changes();
}
```

### 4. **Document That wait_until_ready() is Optional**
When writing documentation or examples:

**✅ RECOMMENDED Pattern:**
```python
# Create and display widget
moon_map = MoonMap()
moon_map  # Display in notebook

# Add layers immediately - they're queued automatically
moon_map.add_geotiff('data/layer.tif')
moon_map.add_trek_layer('some_uuid')
```

**⚠️ ADVANCED Pattern (rarely needed):**
```python
# Only use wait_until_ready() if you need to verify initialization
# before proceeding (e.g., in tests or error handling)
moon_map = MoonMap()
moon_map
try:
    moon_map.wait_until_ready(timeout=15)  # Account for marimo latency
    # Proceed with operations that absolutely require ready state
except TimeoutError:
    print("Widget initialization timed out")
```

### 5. **Command Queue Pattern (for non-trait operations)**
If you must use commands (e.g., for actions that don't fit trait model):

**Python side:**
```python
def _send_command(self, command: Dict) -> None:
    """Send command to JavaScript, queuing if not ready."""
    if self._widget_ready:
        self._command = command  # Send immediately
    else:
        self._command_queue.append(command)  # Queue for later

def _handle_widget_ready(self, change):
    """Flush command queue when ready."""
    if change['new']:
        self.ready = True
        self._ready_event.set()
        # Flush any queued commands
        for cmd in self._command_queue:
            self._command = cmd
        self._command_queue.clear()
```

**JavaScript side:**
```javascript
model.on('change:_command', () => {
  const command = model.get('_command');
  if (!mapReady) {
    commandQueue.push(command);  // Queue it
    return;
  }
  handleCommand(command);
});

// After mapReady = true:
commandQueue.forEach(cmd => handleCommand(cmd));
commandQueue.length = 0;
```

### 6. **Testing Widget Methods**
When testing new widget functionality:

- **Test first-load behavior:** Restart kernel and run test to catch initialization races
- **Test with marimo:** Anywidget sync behaves differently in marimo vs Jupyter
- **Don't rely on timing:** If a test needs `time.sleep()` to pass, the implementation is wrong
- **Check browser console:** JavaScript errors may not surface in Python
- **Verify queuing:** Add layers before widget is ready and confirm they appear after init

---

## GeoTIFF Startup Race Fix (2025-10-10)
- **Symptom:** The first GeoTIFF layer added in a fresh notebook session often failed to appear until the cell was rerun. Python logs showed the layer registering correctly, but the browser never created the corresponding OpenLayers layer.
- **Root Cause:** A race between widget initialization on the JavaScript side and the traitlet update from Python. The `geotiffs` list was pushed before the map finished booting, so the frontend read an empty list when it first synchronized.
- **Fix:** Added an explicit `sync_geotiffs` command issued immediately after Python updates the layer list. The command now includes the active configurations, and the frontend queues them until the map is ready, guaranteeing the first-run layer load.
- **Result:** GeoTIFF layers now appear on the first run without requiring sleep hacks or manual reruns, eliminating the race condition entirely.

---

## Future Directions
- Caching and compression for HTTP server responses.
- Support for additional layer types (WMS, XYZ, vector tiles).
- Enhanced error handling and user feedback in the UI.
- 3D lunar globe visualization and advanced measurement tools.

---

## References
- See `README.md` for API documentation and examples.
- See `GEOTIFF_BLOB_URL_LIMITATION.md` and `GEOTIFF_HTTP_SERVER_IMPLEMENTATION.md` for technical details.
- See `MARIMO_USAGE_PATTERN.md` for notebook usage guidance.

---

**Status:** All major design and implementation goals have been met. MoonLayers is production-ready, robust, and extensible for lunar mapping in notebook environments.

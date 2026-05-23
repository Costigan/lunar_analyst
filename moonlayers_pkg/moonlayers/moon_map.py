"""
MoonMap widget: Interactive lunar mapping with OpenLayers in Marimo.
"""

import pathlib
import re
import threading
from typing import Any, Callable, Optional, Union, List, Dict
import anywidget
import traitlets

try:
    import requests
except ImportError:
    requests = None

from .geotiff_server import get_server


class MoonMap(anywidget.AnyWidget):
    """
    An interactive map widget for visualizing lunar south-polar data in polar stereographic projection.
    
    This widget embeds OpenLayers to display WMTS tiles from Moon Trek, GeoTIFF rasters,
    and GeoJSON vector overlays with full interactive controls.
    
    Parameters
    ----------
    projection : dict or str, optional
        Either "ESRI:103878" (default) or a custom proj4 string dict with keys:
        - "code": projection code (e.g., "CUSTOM:MOON_SOUTH")
        - "proj4": proj4 definition string
    wmts : dict, optional
        WMTS configuration with keys:
        - "get_capabilities_url": URL to WMTS GetCapabilities XML
        - "layer": layer name (e.g., "LRO_WAC_Mosaic_SouthPole")
        - "tile_matrix_set": tile matrix set name (auto-detected if None)
        - "style": style name (auto-detected if None)
        - "format": image format (default: "image/png")
        - "attributions": attribution text
    geotiffs : list of dict, optional
        List of GeoTIFF layer configs, each with keys:
        - "url": URL to GeoTIFF (COG preferred)
        - "layer_id": unique identifier
        - "opacity": 0.0-1.0 (default: 1.0)
        - "visible": boolean (default: True)
    geojsons : list of dict, optional
        List of GeoJSON layer configs, each with keys:
        - "url": URL to GeoJSON file
        - "layer_id": unique identifier
        - "style": style dict (stroke, fill, icon configs)
        - "opacity": 0.0-1.0 (default: 1.0)
        - "visible": boolean (default: True)
    controls : dict, optional
        Control visibility settings with keys:
        - "zoom": boolean (default: True)
        - "zoom_slider": boolean (default: True)
        - "rotate": boolean (default: True)
        - "scale_line": boolean (default: True)
        - "mouse_position": dict or boolean
        - "overview_map": boolean (default: True)
        - "fullscreen": boolean (default: False)
    layer_switcher : bool, optional
        Enable layer switcher UI (default: True)
    measure : dict, optional
        Measurement tool config with keys:
        - "enabled": boolean (default: False)
        - "mode": "planar" or "geodesic" (default: "geodesic")
    graticule : bool, optional
        Enable graticule overlay (default: False)
    permalink : bool, optional
        Enable permalink/state preservation (default: True)
    view : dict, optional
        Initial view state with keys:
        - "center": [x, y] coordinates
        - "zoom": zoom level
        - "rotation": rotation in radians
    
    Methods
    -------
    set_view(center=None, zoom=None, rotation=None)
        Update the map view.
    toggle_layer(layer_id, visible)
        Toggle layer visibility.
    set_opacity(layer_id, opacity)
        Set layer opacity (0.0-1.0).
    fit_extent(layer_id=None)
        Fit map to layer extent (or WMTS extent if layer_id is None).
    export_png(scale=1.0)
        Export map to PNG. Returns base64 string when complete (via event).
    export_pdf(size="A4", dpi=150)
        Export map to PDF. Returns base64 string when complete (via event).
    
    Events
    ------
    on_click_feature(callback)
        Called when a feature is clicked. Callback receives (feature_dict, coord).
    on_hover_feature(callback)
        Called when a feature is hovered. Callback receives (feature_dict, coord).
    on_extent_changed(callback)
        Called when view changes. Callback receives (center, zoom, rotation, extent).
    on_export_complete(callback)
        Called when export completes. Callback receives (kind, data).
    """
    
    _esm = pathlib.Path(__file__).parent / "static" / "index.js"
    _css = pathlib.Path(__file__).parent / "static" / "index.css"
    
    # Add version query parameter for cache busting
    import time
    _cache_buster = str(int(time.time()))
    
    # Synced traitlets for two-way communication with JS
    projection = traitlets.Union([traitlets.Unicode(), traitlets.Dict()], default_value="ESRI:103878").tag(sync=True)
    wmts = traitlets.Dict(default_value=None, allow_none=True).tag(sync=True)
    geotiffs = traitlets.List(trait=traitlets.Dict(), default_value=[]).tag(sync=True)
    geojsons = traitlets.List(trait=traitlets.Dict(), default_value=[]).tag(sync=True)
    controls = traitlets.Dict(default_value={
        "zoom": True,
        "zoom_slider": True,
        "rotate": True,
        "scale_line": True,
        "mouse_position": {"proj": "IAU_MOON_GEOG", "precision": 4},
        "overview_map": True,
        "fullscreen": False
    }).tag(sync=True)
    layer_switcher = traitlets.Bool(default_value=True).tag(sync=True)
    measure = traitlets.Dict(default_value={"enabled": False, "mode": "geodesic"}).tag(sync=True)
    graticule = traitlets.Bool(default_value=False).tag(sync=True)
    permalink = traitlets.Bool(default_value=True).tag(sync=True)
    view = traitlets.Dict(default_value={"center": [0, 0], "zoom": 2, "rotation": 0.0}).tag(sync=True)
    
    # Trek layer catalog and active layers
    trek_layers = traitlets.List(trait=traitlets.Dict(), default_value=[]).tag(sync=True)
    active_layers = traitlets.List(trait=traitlets.Unicode(), default_value=[]).tag(sync=True)
    
    # Command channel (Python → JS)
    _command = traitlets.Dict(default_value={}).tag(sync=True)
    
    # Event channel (JS → Python)
    _event = traitlets.Dict(default_value={}).tag(sync=True)
    
    # Widget ready flag (JS → Python)
    _widget_ready = traitlets.Bool(default_value=False).tag(sync=True)
    
    def __init__(self, **kwargs):
        """Initialize the MoonMap widget with the given configuration."""
        # Set default WMTS if not provided
        if 'wmts' not in kwargs or kwargs['wmts'] is None:
            kwargs['wmts'] = {
                'get_capabilities_url': 'https://trek.nasa.gov/tiles/Moon/SP/LRO_WAC_Mosaic_SPole60_100mp/1.0.0/WMTSCapabilities.xml',
                'layer': 'LRO_WAC_Mosaic_SPole60_100mp',
                'format': 'image/png',
                'attributions': 'NASA/LROC - LRO WAC South Pole Mosaic'
            }
        
        super().__init__(**kwargs)
        
        # Event callbacks
        self._click_feature_callbacks = []
        self._hover_feature_callbacks = []
        self._extent_changed_callbacks = []
        self._export_complete_callbacks = []
        
        # Trek layer catalog cache
        self._trek_layers_cache = None
        
        # Command queue for buffering commands before widget is ready
        self._command_queue = []

        # Readiness tracking so notebooks can wait for the front-end
        self._ready_event = threading.Event()
        self.ready = False
        
        # Observe events from JS
        self.observe(self._handle_event, names=['_event'])
        
        # Observe widget ready state to flush command queue
        self.observe(self._handle_widget_ready, names=['_widget_ready'])

        # Handle the case where the widget is already flagged as ready
        if self._widget_ready:
            self.ready = True
            self._ready_event.set()
    
    def _handle_event(self, change):
        """Handle events from JavaScript."""
        event = change['new']
        if not event:
            return
        
        event_type = event.get('type')
        
        if event_type == 'click_feature':
            for callback in self._click_feature_callbacks:
                callback(event.get('feature'), event.get('coord'))
        
        elif event_type == 'hover_feature':
            for callback in self._hover_feature_callbacks:
                callback(event.get('feature'), event.get('coord'))
        
        elif event_type == 'extent_changed':
            for callback in self._extent_changed_callbacks:
                callback(
                    event.get('center'),
                    event.get('zoom'),
                    event.get('rotation'),
                    event.get('extent')
                )
        
        elif event_type == 'export_complete':
            for callback in self._export_complete_callbacks:
                callback(event.get('kind'), event.get('data'))
        
        elif event_type == 'search_panel_opened':
            # Auto-fetch Trek layers when search panel is first opened
            if not self.trek_layers:  # Only fetch if not already loaded
                try:
                    print("Layer search opened - fetching Trek catalog...")
                    self.fetch_trek_layers()
                    print(f"Loaded {len(self.trek_layers)} layers")
                except Exception as e:
                    print(f"Failed to fetch Trek layers: {e}")
    
    def _handle_widget_ready(self, change):
        """Handle widget ready event and flush command queue."""
        if not change['new']:
            self.ready = False
            self._ready_event.clear()
            return
        self.ready = True
        self._ready_event.set()
        # Widget is now ready, flush any queued commands
        if self._command_queue:
            print(f"Widget ready, flushing {len(self._command_queue)} queued commands...")
            for cmd in self._command_queue:
                self._command = cmd
            self._command_queue.clear()
        # Re-set geotiffs to trigger frontend sync and guarantee layer loading
        self.geotiffs = list(self.geotiffs)
    
    def _send_command(self, command: Dict) -> None:
        """
        Send a command to JavaScript, queuing it if widget isn't ready yet.
        
        Parameters
        ----------
        command : dict
            Command dictionary to send
        """
        if self._widget_ready:
            # Widget is ready, send immediately
            self._command = command
        else:
            # Widget not ready yet, queue the command
            self._command_queue.append(command)
            print(f"Widget not ready, queuing command: {command.get('action')}")

    def wait_until_ready(self, timeout: Optional[float] = 10) -> bool:
        """
        Block until the widget front-end signals that it is ready.
        
        Note: In marimo notebooks, anywidget trait syncing has inherent latency
        (~10 seconds on first load). For most use cases, explicit waiting is
        unnecessary as the widget automatically queues operations until ready.

        Parameters
        ----------
        timeout : float, optional
            Maximum number of seconds to wait before raising TimeoutError.
            If None, wait indefinitely. Default: 10 seconds.

        Returns
        -------
        bool
            True when the widget becomes ready.

        Raises
        ------
        TimeoutError
            If the widget does not become ready within the timeout window.
        """
        import time
        start_time = time.time()
        poll_interval = 0.1  # Poll every 100ms
        
        while True:
            # Check if already ready via event
            if self._ready_event.wait(timeout=poll_interval):
                return True
            
            # Also poll the _widget_ready trait directly in case observer didn't fire
            if self._widget_ready:
                self.ready = True
                self._ready_event.set()
                return True
            
            # Check timeout
            if timeout is not None and (time.time() - start_time) >= timeout:
                break
        
        raise TimeoutError("MoonMap widget did not become ready before timeout.")
    
    # ========== Trek Layer Catalog Methods ==========
    
    def fetch_trek_layers(self, force_refresh: bool = False) -> List[Dict]:
        """
        Fetch the Trek layer catalog for the South Pole projection.
        
        Parameters
        ----------
        force_refresh : bool, optional
            Force refetch even if cached (default: False)
        
        Returns
        -------
        list of dict
            List of layer metadata objects from Trek API
        
        Raises
        ------
        ImportError
            If requests library is not installed
        RuntimeError
            If Trek API request fails
        """
        if requests is None:
            raise ImportError("requests library is required for fetching Trek layers. Install with: pip install requests")
        
        if not force_refresh and self._trek_layers_cache is not None:
            return self._trek_layers_cache
        
        try:
            url = "https://trek.nasa.gov/moon/TrekServices/ws/index/polar/searchItems"
            params = {
                "proj": "urn:ogc:def:crs:IAU2000::30120",
                "start": 0,
                "rows": 1000
            }
            
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            layers = data.get('response', {}).get('docs', [])
            
            # Filter out tour layers
            layers = [layer for layer in layers if layer.get('productLabel') != 'tour']
            
            self._trek_layers_cache = layers
            self.trek_layers = layers  # Sync to JS
            
            return layers
        
        except Exception as e:
            raise RuntimeError(f"Failed to fetch Trek layers: {e}")
    
    def _parse_search_pattern(self, pattern: str) -> Dict:
        """
        Parse search pattern into tokens and operators.
        
        Supports:
        - AND / & : conjunction
        - OR / | : disjunction  
        - NOT / - : negation
        - () : grouping
        
        Returns AST-like structure for evaluation.
        """
        # Normalize pattern
        pattern = pattern.strip()
        if not pattern:
            return {"type": "match_all"}
        
        # Simple tokenizer - replace operators with normalized versions
        pattern = re.sub(r'\bAND\b', ' & ', pattern, flags=re.IGNORECASE)
        pattern = re.sub(r'\bOR\b', ' | ', pattern, flags=re.IGNORECASE)
        pattern = re.sub(r'\bNOT\b', ' -', pattern, flags=re.IGNORECASE)
        
        # For basic implementation, split on spaces and handle operators
        # More sophisticated: build proper parse tree
        # For now: simple left-to-right evaluation with operator precedence
        
        tokens = []
        current_token = ""
        paren_depth = 0
        
        i = 0
        while i < len(pattern):
            char = pattern[i]
            
            if char == '(':
                if current_token.strip():
                    tokens.append(current_token.strip())
                    current_token = ""
                paren_depth += 1
                # Find matching closing paren
                paren_start = i
                j = i + 1
                depth = 1
                while j < len(pattern) and depth > 0:
                    if pattern[j] == '(':
                        depth += 1
                    elif pattern[j] == ')':
                        depth -= 1
                    j += 1
                # Recursively parse content in parens
                inner = pattern[i+1:j-1]
                tokens.append(self._parse_search_pattern(inner))
                i = j
                continue
            
            elif char in ['&', '|']:
                if current_token.strip():
                    tokens.append(current_token.strip())
                    current_token = ""
                tokens.append(char)
                i += 1
                continue
            
            elif char == '-' and (i == 0 or pattern[i-1] in [' ', '(', '&', '|']):
                # This is a NOT operator
                if current_token.strip():
                    tokens.append(current_token.strip())
                    current_token = ""
                tokens.append('-')
                i += 1
                continue
            
            else:
                current_token += char
                i += 1
        
        if current_token.strip():
            tokens.append(current_token.strip())
        
        return {"type": "tokens", "tokens": tokens}
    
    def _evaluate_search(self, layer: Dict, parsed: Dict) -> bool:
        """
        Evaluate search pattern against layer metadata.
        
        Searches in: productLabel, title, description
        """
        if parsed["type"] == "match_all":
            return True
        
        if parsed["type"] != "tokens":
            # Recursive case - already parsed subexpression
            return self._evaluate_search(layer, parsed)
        
        tokens = parsed["tokens"]
        if not tokens:
            return True
        
        # Get searchable fields
        search_text = " ".join([
            str(layer.get('productLabel', '')),
            str(layer.get('title', '')),
            str(layer.get('description', ''))
        ]).lower()
        
        # Evaluate tokens left to right with operator precedence
        # NOT > AND > OR
        
        # First, handle NOT operators
        processed_tokens = []
        i = 0
        while i < len(tokens):
            if tokens[i] == '-':
                # Next token is negated
                if i + 1 < len(tokens):
                    next_token = tokens[i + 1]
                    if isinstance(next_token, dict):
                        processed_tokens.append({"op": "NOT", "operand": next_token})
                    else:
                        processed_tokens.append({"op": "NOT", "operand": next_token})
                    i += 2
                else:
                    i += 1
            else:
                processed_tokens.append(tokens[i])
                i += 1
        
        # Now evaluate AND operators
        and_groups = []
        current_group = []
        
        for token in processed_tokens:
            if token == '&':
                continue
            elif token == '|':
                if current_group:
                    and_groups.append(current_group)
                    current_group = []
                and_groups.append('|')
            else:
                current_group.append(token)
        
        if current_group:
            and_groups.append(current_group)
        
        # Evaluate each AND group, then combine with OR
        or_results = []
        
        for group in and_groups:
            if group == '|':
                continue
            
            # Evaluate AND group
            and_result = True
            for term in group:
                if isinstance(term, dict):
                    if term.get("op") == "NOT":
                        operand = term["operand"]
                        if isinstance(operand, dict):
                            match = self._evaluate_search(layer, operand)
                        else:
                            match = operand.lower() in search_text
                        and_result = and_result and (not match)
                    else:
                        # Recursive subexpression
                        and_result = and_result and self._evaluate_search(layer, term)
                else:
                    # Simple term
                    match = term.lower() in search_text
                    and_result = and_result and match
                
                if not and_result:
                    break
            
            or_results.append(and_result)
        
        # Combine with OR
        return any(or_results) if or_results else False
    
    def search_layers(self, pattern: str) -> List[Dict]:
        """
        Search Trek layers by pattern.
        
        Supports boolean operators:
        - AND / & : both terms must match
        - OR / | : either term must match
        - NOT / - : term must not match
        - () : grouping for complex queries
        
        Pattern is matched against productLabel, title, and description fields.
        
        Parameters
        ----------
        pattern : str
            Search pattern with optional boolean operators
            
        Returns
        -------
        list of dict
            Matching layer metadata objects
            
        Examples
        --------
        >>> map.search_layers("Artemis")
        >>> map.search_layers("Artemis AND Mosaic")
        >>> map.search_layers("Artemis OR Apollo")
        >>> map.search_layers("LRO NOT crater")
        >>> map.search_layers("(Artemis OR Apollo) AND -crater")
        """
        layers = self.fetch_trek_layers()
        
        if not pattern or not pattern.strip():
            return layers
        
        parsed = self._parse_search_pattern(pattern)
        matching = [layer for layer in layers if self._evaluate_search(layer, parsed)]
        
        return matching
    
    def add_layer(self, layer_id: str) -> None:
        """
        Add a Trek layer to the map by its item_UUID or productLabel.
        
        Parameters
        ----------
        layer_id : str
            Layer identifier (item_UUID or productLabel)
        """
        # Find layer in catalog
        layers = self.fetch_trek_layers()
        layer_metadata = None
        
        for layer in layers:
            if layer.get('item_UUID') == layer_id or layer.get('productLabel') == layer_id:
                layer_metadata = layer
                break
        
        if not layer_metadata:
            print(f"Warning: Layer '{layer_id}' not found in Trek catalog")
            return
        
        # Use item_UUID as canonical ID
        canonical_id = layer_metadata.get('item_UUID')
        
        # Check if already added
        if canonical_id in self.active_layers:
            print(f"Layer '{layer_id}' is already added")
            return
        
        # Send command to JS to add layer (queued if widget not ready)
        self._send_command({
            "action": "add_trek_layer",
            "layer_metadata": layer_metadata,
            "timestamp": id(self)  # Force update
        })
        
        # Update active layers list
        self.active_layers = self.active_layers + [canonical_id]
    
    def remove_layer(self, layer_id: str) -> None:
        """
        Remove a layer from the map.
        
        Parameters
        ----------
        layer_id : str
            Layer identifier (item_UUID or productLabel)
        """
        # Find canonical ID
        layers = self.fetch_trek_layers()
        canonical_id = None
        
        for layer in layers:
            if layer.get('item_UUID') == layer_id or layer.get('productLabel') == layer_id:
                canonical_id = layer.get('item_UUID')
                break
        
        # If not in catalog, try using as-is
        if not canonical_id:
            canonical_id = layer_id
        
        # Check if it's in active layers
        if canonical_id not in self.active_layers:
            # Silently ignore - as per spec
            return
        
        # Send command to JS (queued if widget not ready)
        self._send_command({
            "action": "remove_layer",
            "layer_id": canonical_id,
            "timestamp": id(self)
        })
        
        # Update active layers list
        self.active_layers = [lid for lid in self.active_layers if lid != canonical_id]
    
    def add_geotiff(
        self, 
        source: Union[str, pathlib.Path],
        layer_id: Optional[str] = None,
        name: Optional[str] = None,
        extent: Optional[List[float]] = None,
        opacity: float = 1.0,
        visible: bool = True,
        style: Optional[Dict] = None,
        **kwargs
    ) -> str:
        """
        Add a GeoTIFF raster layer to the map.
        
        This method can load GeoTIFFs from:
        - URL (http/https) - used directly
        - Local file path - served via integrated HTTP server
        
        The integrated HTTP server starts automatically in a background thread
        and serves files from anywhere on the filesystem. This avoids the
        blob URL limitations that cause tile loading failures.
        
        Parameters
        ----------
        source : str or Path
            URL or file path to the GeoTIFF file.
            For Cloud Optimized GeoTIFF (COG), use URLs for best performance.
            Local files are automatically served via the integrated HTTP server.
        layer_id : str, optional
            Unique identifier for the layer. If None, will be auto-generated.
        name : str, optional
            Display name for the layer (shown in layer switcher).
            If None, uses layer_id.
        extent : list of float, optional
            Bounding box [minX, minY, maxX, maxY] in map projection coordinates.
            If None, will be read from GeoTIFF metadata.
        opacity : float, optional
            Layer opacity from 0.0 (transparent) to 1.0 (opaque). Default: 1.0
        visible : bool, optional
            Whether layer is initially visible. Default: True
        style : dict, optional
            Custom rendering style configuration. Options:
            - 'color': Custom WebGL color expression (advanced)
            - 'min': Minimum data value for normalization (default: 0)
            - 'max': Maximum data value for normalization (default: 255)
            - 'nodata': Value to treat as transparent (default: 0)
            If None, auto-detects style based on band count:
            - 1 band: grayscale with nodata transparency
            - 3 bands: RGB
            - 4 bands: RGBA
        **kwargs : dict
            Additional layer configuration options
        
        Returns
        -------
        str
            The layer_id that can be used to reference this layer
        
        Examples
        --------
        Add from URL:
        >>> moon_map.add_geotiff('https://example.com/lunar_data.tif')
        
        Add from local file (served via integrated HTTP server):
        >>> moon_map.add_geotiff('data/my_raster.tif', layer_id='my_layer')
        
        Add from anywhere on filesystem:
        >>> moon_map.add_geotiff('C:/datasets/lunar_data.tif')
        
        Add with specific extent:
        >>> extent = [xmin, ymin, xmax, ymax]  # In map projection
        >>> moon_map.add_geotiff('data.tif', extent=extent, opacity=0.7)
        """
        import uuid
        import os
        import uuid
        import os
        
        # Generate layer ID if not provided
        if layer_id is None:
            layer_id = f'geotiff_{uuid.uuid4().hex[:8]}'
        
        # Process source - determine if it's a URL or file path
        source_str = str(source)
        band_count = None
        
        # Check if it's already a URL
        if source_str.startswith(('http://', 'https://')):
            # Use URL directly - no processing needed
            source_url = source_str
            print(f"Using GeoTIFF from URL: {source_url}")
            
        else:
            # It's a file path - use HTTP server
            file_path = pathlib.Path(source)
            if not file_path.exists():
                raise FileNotFoundError(f"GeoTIFF file not found: {file_path}")
            
            # Validate it's a GeoTIFF and extract metadata
            try:
                # Set PROJ_LIB to the virtual environment's PROJ data directory
                # This must happen BEFORE importing GDAL/OSR to avoid using system PROJ paths
                # We ALWAYS search for and use the venv PROJ, even if PROJ_LIB is already set,
                # because the global PROJ_LIB may point to an incompatible system installation
                
                # Find the osgeo package location in site-packages
                import site
                import sys
                
                # Get site-packages directories
                site_packages = site.getsitepackages()
                
                # Also check user site-packages
                user_site = site.getusersitepackages()
                if user_site:
                    site_packages.append(user_site)
                
                # Also check virtualenv site-packages (prioritize this)
                if hasattr(sys, 'prefix'):
                    venv_site = os.path.join(sys.prefix, 'Lib', 'site-packages')
                    if os.path.exists(venv_site):
                        site_packages.insert(0, venv_site)  # Prioritize venv
                
                # Look for osgeo/data/proj in each site-packages
                proj_data_dir = None
                for sp in site_packages:
                    candidate = os.path.join(sp, 'osgeo', 'data', 'proj')
                    if os.path.exists(candidate) and os.path.isdir(candidate):
                        proj_data_dir = candidate
                        break
                
                if proj_data_dir:
                    old_proj_lib = os.environ.get('PROJ_LIB')
                    os.environ['PROJ_LIB'] = proj_data_dir
                    if old_proj_lib and old_proj_lib != proj_data_dir:
                        print(f"Overrode PROJ_LIB: {old_proj_lib} -> {proj_data_dir}")
                
                # Now import GDAL with PROJ_LIB already set
                from osgeo import gdal, osr
                gdal.UseExceptions()
                osr.UseExceptions()
            except ImportError:
                raise ImportError(
                    "GDAL is required for GeoTIFF support. "
                    "Install with: pip install gdal"
                )
            
            # Open with GDAL to validate and extract metadata
            ds = gdal.Open(str(file_path))
            if ds is None:
                raise ValueError(f"Could not open GeoTIFF file: {file_path}")
            
            # Check for projection
            projection_wkt = ds.GetProjection()
            if not projection_wkt:
                raise ValueError(
                    f"GeoTIFF file has no projection information: {file_path}\n"
                    "All GeoTIFF files must include valid coordinate reference system (CRS) metadata."
                )
            
            # Extract extent from geotransform
            if extent is None:
                geotransform = ds.GetGeoTransform()
                width = ds.RasterXSize
                height = ds.RasterYSize
                
                minx = geotransform[0]
                maxy = geotransform[3]
                maxx = minx + geotransform[1] * width
                miny = maxy + geotransform[5] * height
                
                extent = [minx, miny, maxx, maxy]
                print(f"Extracted extent from GeoTIFF: {extent}")
            
            # Get band count
            band_count = ds.RasterCount
            print(f"GeoTIFF band count: {band_count}")

            # Get projection as proj4 string
            srs = osr.SpatialReference()
            srs.ImportFromWkt(projection_wkt)
            proj4_string = srs.ExportToProj4()
            
            print(f"GeoTIFF projection: {proj4_string}")
            
            # Check if projection is functionally equivalent to ESRI:103878
            def is_equivalent_south_polar(proj4_str):
                """Check if proj4 string is equivalent to ESRI:103878."""
                params = {}
                for param in proj4_str.strip().split():
                    if '=' in param:
                        key, val = param.split('=', 1)
                        params[key] = val
                
                # Check essential parameters
                if params.get('+proj') != 'stere':
                    return False
                if params.get('+lat_0') != '-90':
                    return False
                if params.get('+lon_0') != '0':
                    return False
                if params.get('+R') != '1737400':
                    return False
                if params.get('+units') != 'm':
                    return False
                
                # lat_ts=-90 and k=1 are equivalent
                has_scale = (params.get('+lat_ts') == '-90' or 
                           params.get('+k') == '1')
                if not has_scale:
                    return False
                
                return True
            
            is_equiv = is_equivalent_south_polar(proj4_string)
            
            if not is_equiv:
                print("WARNING: GeoTIFF projection is NOT equivalent to ESRI:103878")
                print("         The file may not display correctly without reprojection")
                # For now, we'll still serve it - the frontend may handle it
            
            # Close dataset - we're done with validation
            ds = None
            
            # Register the file with the HTTP server
            server = get_server()
            source_url = server.register_file(str(file_path.resolve()))
        
        # Build layer config
        layer_config = {
            'url': source_url,
            'layer_id': layer_id,
            'name': name or layer_id,
            'opacity': opacity,
            'visible': visible,
            'bands': band_count,
            **kwargs
        }
        
        if extent is not None:
            layer_config['extent'] = extent
        
        if style is not None:
            layer_config['style'] = style
        
        # Always set projection to ESRI:103878
        layer_config['projection'] = 'ESRI:103878'
        
        # Add to geotiffs list
        current_geotiffs = list(self.geotiffs)
        current_geotiffs.append(layer_config)
        self.geotiffs = current_geotiffs
        
        print(f"Added GeoTIFF layer: {layer_id}")
        # Ensure frontend synchronizes layers once widget is ready
        try:
            import time
            self._send_command({
                "action": "sync_geotiffs",
                "timestamp": time.time(),
                "configs": current_geotiffs
            })
        except Exception as sync_error:
            print(f"Warning: Failed to queue GeoTIFF sync command: {sync_error}")
        return layer_id
    
    def remove_geotiff(self, layer_id: str) -> None:
        """
        Remove a GeoTIFF layer from the map.
        
        Parameters
        ----------
        layer_id : str
            Layer identifier returned by add_geotiff()
        """
        self.geotiffs = [g for g in self.geotiffs if g.get('layer_id') != layer_id]
        print(f"Removed GeoTIFF layer: {layer_id}")
    
    def get_map_state(self) -> Dict:
        """
        Get the current map state for saving.
        
        Returns
        -------
        dict
            Serializable state dictionary including layers, view, and settings
        """
        return {
            "version": "1.0",
            "projection": self.projection,
            "view": self.view,
            "active_layers": self.active_layers,
            "controls": self.controls,
            "layer_switcher": self.layer_switcher,
            "measure": self.measure,
            "graticule": self.graticule,
            "permalink": self.permalink
        }
    
    def set_map_state(self, state: Dict) -> None:
        """
        Restore map state from a saved state dictionary.
        
        Parameters
        ----------
        state : dict
            State dictionary from get_map_state()
        """
        if state.get("version") != "1.0":
            print("Warning: State version mismatch")
        
        # Restore settings
        if "projection" in state:
            self.projection = state["projection"]
        if "view" in state:
            self.view = state["view"]
        if "controls" in state:
            self.controls = state["controls"]
        if "layer_switcher" in state:
            self.layer_switcher = state["layer_switcher"]
        if "measure" in state:
            self.measure = state["measure"]
        if "graticule" in state:
            self.graticule = state["graticule"]
        if "permalink" in state:
            self.permalink = state["permalink"]
        
        # Restore layers
        if "active_layers" in state:
            # Clear existing layers
            for layer_id in list(self.active_layers):
                self.remove_layer(layer_id)
            
            # Add layers from state
            for layer_id in state["active_layers"]:
                self.add_layer(layer_id)
    
    # ========== Existing Methods ==========
    
    def set_view(self, center: Optional[list] = None, zoom: Optional[float] = None, 
                 rotation: Optional[float] = None) -> None:
        """
        Update the map view.
        
        Parameters
        ----------
        center : list of float, optional
            [x, y] coordinates in map projection
        zoom : float, optional
            Zoom level
        rotation : float, optional
            Rotation in radians
        """
        command = {"action": "set_view"}
        if center is not None:
            command["center"] = center
        if zoom is not None:
            command["zoom"] = zoom
        if rotation is not None:
            command["rotation"] = rotation
        self._send_command(command)
    
    def toggle_layer(self, layer_id: str, visible: bool) -> None:
        """
        Toggle layer visibility.
        
        Parameters
        ----------
        layer_id : str
            Unique layer identifier
        visible : bool
            Whether the layer should be visible
        """
        self._send_command({
            "action": "toggle_layer",
            "layer_id": layer_id,
            "visible": visible
        })
    
    def set_opacity(self, layer_id: str, opacity: float) -> None:
        """
        Set layer opacity.
        
        Parameters
        ----------
        layer_id : str
            Unique layer identifier
        opacity : float
            Opacity value between 0.0 (transparent) and 1.0 (opaque)
        """
        self._send_command({
            "action": "set_opacity",
            "layer_id": layer_id,
            "opacity": max(0.0, min(1.0, opacity))
        })
    
    def fit_extent(self, layer_id: Optional[str] = None) -> None:
        """
        Fit the map view to a layer's extent.
        
        Parameters
        ----------
        layer_id : str, optional
            Layer identifier. If None, fits to WMTS or primary extent.
        """
        self._send_command({
            "action": "fit_extent",
            "layer_id": layer_id
        })
    
    def export_png(self, scale: float = 1.0) -> None:
        """
        Export the map as PNG.
        
        Parameters
        ----------
        scale : float, optional
            Scale factor for export resolution (default: 1.0)
        
        Notes
        -----
        The exported image is returned via the on_export_complete event.
        """
        self._send_command({
            "action": "export_png",
            "scale": scale
        })
    
    def export_pdf(self, size: str = "A4", dpi: int = 150) -> None:
        """
        Export the map as PDF.
        
        Parameters
        ----------
        size : str, optional
            Paper size: "A4", "A3", "letter", etc. (default: "A4")
        dpi : int, optional
            Resolution in DPI (default: 150)
        
        Notes
        -----
        The exported PDF is returned via the on_export_complete event.
        """
        self._send_command({
            "action": "export_pdf",
            "size": size,
            "dpi": dpi
        })
    
    def on_click_feature(self, callback: Callable[[dict, list], None]) -> None:
        """
        Register a callback for feature click events.
        
        Parameters
        ----------
        callback : callable
            Function that receives (feature_dict, coord)
        """
        self._click_feature_callbacks.append(callback)
    
    def on_hover_feature(self, callback: Callable[[dict, list], None]) -> None:
        """
        Register a callback for feature hover events.
        
        Parameters
        ----------
        callback : callable
            Function that receives (feature_dict, coord)
        """
        self._hover_feature_callbacks.append(callback)
    
    def on_extent_changed(self, callback: Callable[[list, float, float, list], None]) -> None:
        """
        Register a callback for extent change events.
        
        Parameters
        ----------
        callback : callable
            Function that receives (center, zoom, rotation, extent)
        """
        self._extent_changed_callbacks.append(callback)
    
    def on_export_complete(self, callback: Callable[[str, str], None]) -> None:
        """
        Register a callback for export completion events.
        
        Parameters
        ----------
        callback : callable
            Function that receives (kind, data) where kind is 'png' or 'pdf'
            and data is base64-encoded content
        """
        self._export_complete_callbacks.append(callback)

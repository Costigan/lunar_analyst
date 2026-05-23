"""
Manual test script for MoonLayers widget.

This script helps you test the MoonLayers widget manually in VSCode.

Usage:
1. Install the package in development mode:
   pip install -e .

2. Build the frontend:
   npm install
   npm run build

3. Run this script:
   python tests/manual_test.py

4. Open the URL shown in your browser
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

print("=" * 60)
print("MoonLayers Manual Test Script")
print("=" * 60)
print()

# Test 1: Import package
print("[TEST 1] Importing moonlayers package...")
try:
    from moonlayers import MoonMap, __version__
    print(f"✓ Package imported successfully (version {__version__})")
except ImportError as e:
    print(f"✗ Failed to import package: {e}")
    print("  Make sure you've installed the package: pip install -e .")
    sys.exit(1)

print()

# Test 2: Check static files
print("[TEST 2] Checking static files...")
import pathlib
static_dir = pathlib.Path(__file__).parent.parent / "moonlayers" / "static"
index_js = static_dir / "index.js"
index_css = static_dir / "index.css"

if index_js.exists():
    print(f"✓ index.js found ({index_js.stat().st_size} bytes)")
else:
    print(f"✗ index.js not found at {index_js}")
    print("  Run: npm install && npm run build")
    
if index_css.exists():
    print(f"✓ index.css found ({index_css.stat().st_size} bytes)")
else:
    print(f"✗ index.css not found at {index_css}")
    print("  Run: npm install && npm run build")

if not (index_js.exists() and index_css.exists()):
    sys.exit(1)

print()

# Test 3: Create widget instances
print("[TEST 3] Creating widget instances...")

try:
    # Basic instance
    moon_map1 = MoonMap()
    print("✓ Created basic MoonMap instance")
    
    # With WMTS
    moon_map2 = MoonMap(
        projection="ESRI:103878",
        wmts={
            "get_capabilities_url": "https://trek.nasa.gov/tiles/Moon/EQ/LRO_WAC_Mosaic_Global_303ppd_v02/1.0.0/WMTSCapabilities.xml",
            "layer": "LRO_WAC_Mosaic_Global_303ppd_v02",
            "format": "image/png",
            "attributions": "NASA/JPL/USGS Moon Trek"
        }
    )
    print("✓ Created MoonMap with WMTS configuration")
    
    # With custom controls
    moon_map3 = MoonMap(
        controls={
            "zoom": True,
            "zoom_slider": True,
            "rotate": True,
            "scale_line": True,
            "mouse_position": {"proj": "IAU_MOON_GEOG", "precision": 4},
            "overview_map": False,
            "fullscreen": True
        },
        layer_switcher=True,
        graticule=True,
        permalink=True
    )
    print("✓ Created MoonMap with custom controls")
    
except Exception as e:
    print(f"✗ Failed to create widget: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()

# Test 4: Test widget methods
print("[TEST 4] Testing widget methods...")

try:
    moon_map1.set_view(center=[0, 0], zoom=3, rotation=0)
    print("✓ set_view() works")
    
    moon_map1.toggle_layer("test_layer", True)
    print("✓ toggle_layer() works")
    
    moon_map1.set_opacity("test_layer", 0.5)
    print("✓ set_opacity() works")
    
    moon_map1.fit_extent()
    print("✓ fit_extent() works")
    
    moon_map1.export_png(scale=1.0)
    print("✓ export_png() works")
    
    moon_map1.export_pdf(size="A4", dpi=150)
    print("✓ export_pdf() works")
    
except Exception as e:
    print(f"✗ Method call failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()

# Test 5: Test event callbacks
print("[TEST 5] Testing event callbacks...")

try:
    events = []
    
    def on_click(feature, coord):
        events.append(("click", feature, coord))
    
    def on_hover(feature, coord):
        events.append(("hover", feature, coord))
    
    def on_extent(center, zoom, rotation, extent):
        events.append(("extent", center, zoom, rotation, extent))
    
    def on_export(kind, data):
        events.append(("export", kind, len(data) if data else 0))
    
    moon_map1.on_click_feature(on_click)
    moon_map1.on_hover_feature(on_hover)
    moon_map1.on_extent_changed(on_extent)
    moon_map1.on_export_complete(on_export)
    
    print("✓ Event callbacks registered")
    
except Exception as e:
    print(f"✗ Event callback registration failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()

# Test 6: Create a test notebook/app
print("[TEST 6] Creating test HTML file...")

html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>MoonLayers Manual Test</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            margin: 0;
            padding: 20px;
            background: #f5f5f5;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        h1 {{
            color: #333;
            border-bottom: 2px solid #4CAF50;
            padding-bottom: 10px;
        }}
        .test-result {{
            margin: 10px 0;
            padding: 10px;
            border-radius: 4px;
            background: #e8f5e9;
            border-left: 4px solid #4CAF50;
        }}
        .instructions {{
            background: #fff3cd;
            border-left: 4px solid #ffc107;
            padding: 15px;
            margin: 20px 0;
            border-radius: 4px;
        }}
        code {{
            background: #f5f5f5;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: monospace;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🌙 MoonLayers Manual Test</h1>
        
        <div class="test-result">
            <strong>✓ All Python-side tests passed!</strong>
            <p>Package version: {__version__}</p>
        </div>
        
        <div class="instructions">
            <h3>Next Steps for Manual Testing:</h3>
            <ol>
                <li><strong>Test in Marimo:</strong>
                    <pre><code>marimo edit examples/south_pole_demo.mo.py</code></pre>
                </li>
                <li><strong>Test in Jupyter:</strong>
                    <pre><code>jupyter notebook</code></pre>
                    Then create a new notebook and run:
                    <pre><code>from moonlayers import MoonMap
moon_map = MoonMap(
    wmts={{
        "get_capabilities_url": "https://trek.nasa.gov/tiles/Moon/EQ/LRO_WAC_Mosaic_Global_303ppd_v02/1.0.0/WMTSCapabilities.xml",
        "layer": "LRO_WAC_Mosaic_Global_303ppd_v02"
    }}
)
moon_map</code></pre>
                </li>
                <li><strong>Test Interactions:</strong>
                    <ul>
                        <li>Pan and zoom the map</li>
                        <li>Rotate using Alt+Shift+Drag</li>
                        <li>Toggle layers in the layer switcher</li>
                        <li>Click features to see popups</li>
                        <li>Check mouse position coordinates</li>
                        <li>Try exporting to PNG and PDF</li>
                    </ul>
                </li>
            </ol>
        </div>
        
        <h2>What to Check:</h2>
        <ul>
            <li>✓ Map renders correctly with Moon Trek tiles</li>
            <li>✓ Projection is correct (South Pole Stereographic)</li>
            <li>✓ All controls are working (zoom, rotate, scale line, etc.)</li>
            <li>✓ Mouse position shows correct coordinates</li>
            <li>✓ Layer switcher functions properly</li>
            <li>✓ Export to PNG/PDF works (check CORS)</li>
            <li>✓ Permalink preserves state in URL</li>
            <li>✓ Graticule displays correctly if enabled</li>
        </ul>
        
        <h2>Troubleshooting:</h2>
        <ul>
            <li><strong>Map doesn't load:</strong> Check browser console for errors, verify Moon Trek service is accessible</li>
            <li><strong>Export fails:</strong> CORS must be enabled on tile servers</li>
            <li><strong>JavaScript errors:</strong> Make sure frontend is built: <code>npm run build</code></li>
            <li><strong>Module not found:</strong> Install in dev mode: <code>pip install -e .</code></li>
        </ul>
    </div>
</body>
</html>
"""

test_html_path = pathlib.Path(__file__).parent / "manual_test.html"
with open(test_html_path, "w") as f:
    f.write(html_content)

print(f"✓ Created test HTML file: {test_html_path}")
print()

# Summary
print("=" * 60)
print("✓ ALL TESTS PASSED!")
print("=" * 60)
print()
print("Manual Testing Instructions:")
print("1. Test in Marimo:")
print("   marimo edit examples/south_pole_demo.mo.py")
print()
print("2. Test in Jupyter:")
print("   jupyter notebook")
print()
print("3. Open test report:")
print(f"   {test_html_path}")
print()
print("4. Check browser console for any JavaScript errors")
print()
print("Happy testing! 🚀🌙")

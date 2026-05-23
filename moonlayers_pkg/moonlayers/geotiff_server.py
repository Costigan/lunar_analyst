"""
Embedded HTTP server for serving GeoTIFF files from the filesystem.

This module provides a lightweight HTTP server that runs in a background thread
to serve GeoTIFF files from arbitrary filesystem locations. It's designed to
integrate seamlessly with the MoonMap widget in Jupyter/marimo notebooks.
"""

import threading
import http.server
import socketserver
import urllib.parse
import pathlib
import os
import atexit
from typing import Optional, Dict


class GeoTIFFFileHandler(http.server.SimpleHTTPRequestHandler):
    """
    Custom HTTP handler that serves files from a registry of filesystem paths.
    
    This handler maps URL paths to actual filesystem locations, allowing files
    from anywhere on the filesystem to be served under a single HTTP endpoint.
    """

    # Class variable to store the file registry
    file_registry: Dict[str, str] = {}

    def log_message(self, format, *args):
        """Suppress default logging to avoid cluttering notebook output."""
        pass

    def _send_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Expose-Headers', 'Content-Length, Content-Range')

    def do_OPTIONS(self):
        """CORS preflight support for Range requests from the browser."""
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Range, Accept, Origin, Content-Type')
        self.send_header('Access-Control-Max-Age', '86400')
        self.end_headers()

    def do_HEAD(self):
        """Handle HEAD requests to support clients probing size/range."""
        parsed_path = urllib.parse.urlparse(self.path)
        url_path = parsed_path.path
        if url_path.startswith('/'):
            url_path = url_path[1:]
        if url_path not in self.file_registry:
            self.send_error(404, f"File not registered: {url_path}")
            return
        file_path = self.file_registry[url_path]
        if not os.path.exists(file_path):
            self.send_error(404, f"File not found: {url_path}")
            return
        file_size = os.path.getsize(file_path)
        self.send_response(200)
        if file_path.endswith('.tif') or file_path.endswith('.tiff'):
            self.send_header('Content-Type', 'image/tiff')
        else:
            self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(file_size))
        self.send_header('Accept-Ranges', 'bytes')
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        """Handle GET requests by looking up paths in the file registry."""
        # Parse the URL path
        parsed_path = urllib.parse.urlparse(self.path)
        url_path = parsed_path.path

        # Remove leading slash
        if url_path.startswith('/'):
            url_path = url_path[1:]

        # Look up the file in the registry
        if url_path not in self.file_registry:
            self.send_error(404, f"File not registered: {url_path}")
            return

        file_path = self.file_registry[url_path]

        # Check if file exists
        if not os.path.exists(file_path):
            self.send_error(404, f"File not found: {url_path}")
            return

        try:
            file_size = os.path.getsize(file_path)

            # Range request support
            range_header = self.headers.get('Range')
            if range_header:
                # e.g., 'bytes=0-1023'
                if ',' in range_header:
                    self.send_error(416, "Multiple ranges not supported")
                    return
                spec = range_header.replace('bytes=', '').strip()
                start_str, end_str = (spec.split('-', 1) + [''])[:2]
                start = int(start_str) if start_str else 0
                end = int(end_str) if end_str else file_size - 1
                if end >= file_size:
                    end = file_size - 1
                if start < 0 or start > end or start >= file_size:
                    self.send_error(416, "Requested Range Not Satisfiable")
                    return

                length = end - start + 1
                self.send_response(206)
                if file_path.endswith('.tif') or file_path.endswith('.tiff'):
                    self.send_header('Content-Type', 'image/tiff')
                else:
                    self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Range', f'bytes {start}-{end}/{file_size}')
                self.send_header('Content-Length', str(length))
                self.send_header('Accept-Ranges', 'bytes')
                self._send_cors_headers()
                self.end_headers()

                with open(file_path, 'rb') as f:
                    f.seek(start)
                    self.wfile.write(f.read(length))
                return

            # Full file response
            self.send_response(200)
            if file_path.endswith('.tif') or file_path.endswith('.tiff'):
                self.send_header('Content-Type', 'image/tiff')
            else:
                self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(file_size))
            self.send_header('Accept-Ranges', 'bytes')
            self._send_cors_headers()
            self.end_headers()

            with open(file_path, 'rb') as f:
                # Stream the file in chunks to be safe for large files
                chunk = f.read(64 * 1024)
                while chunk:
                    self.wfile.write(chunk)
                    chunk = f.read(64 * 1024)
        except Exception as e:
            self.send_error(500, f"Error serving file: {str(e)}")


class GeoTIFFServer:
    """
    Singleton HTTP server for serving GeoTIFF files in background thread.
    
    This server starts automatically on first use and runs until the Python
    process exits. It provides methods to register filesystem paths and
    convert them to HTTP URLs.
    """
    
    _instance: Optional['GeoTIFFServer'] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """Ensure only one server instance exists (singleton pattern)."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    def __init__(self):
        """Initialize the server (only once due to singleton pattern)."""
        if self._initialized:
            return
        
        self._initialized = True
        self.port = None
        self.server = None
        self.server_thread = None
        self.file_registry: Dict[str, str] = {}
        self._next_file_id = 0
        
        # Register cleanup on exit
        atexit.register(self.stop)
    
    def start(self, port: int = 0) -> int:
        """
        Start the HTTP server on a background thread.
        
        Parameters
        ----------
        port : int, optional
            Port number to bind to. If 0 (default), uses any available port.
        
        Returns
        -------
        int
            The actual port number the server is listening on.
        """
        if self.server is not None:
            return self.port
        
        # Create server
        Handler = GeoTIFFFileHandler
        Handler.file_registry = self.file_registry
        
        # Try to bind to the requested port
        for attempt in range(10):
            try:
                self.server = socketserver.TCPServer(("127.0.0.1", port), Handler)
                self.port = self.server.server_address[1]
                break
            except OSError as e:
                if attempt == 9:
                    raise RuntimeError(f"Could not start HTTP server after 10 attempts: {e}")
                # Try next port if specified port was taken
                port = 0 if port != 0 else 0
        
        # Create an event to signal when the server is ready
        server_ready = threading.Event()
        
        def server_thread_func():
            """Server thread function that signals readiness."""
            # Signal that we're about to start serving
            server_ready.set()
            self.server.serve_forever()
        
        # Start server thread
        self.server_thread = threading.Thread(
            target=server_thread_func,
            daemon=True,
            name="GeoTIFFServer"
        )
        self.server_thread.start()
        
        # Wait for server thread to be ready (with timeout to prevent hanging)
        if not server_ready.wait(timeout=5.0):
            raise RuntimeError("GeoTIFF HTTP server failed to start within 5 seconds")
        
        # Give the server a brief moment to actually start accepting connections
        # The TCPServer is created and bound, but we need the event loop to start
        import time
        time.sleep(0.1)
        
        print(f"GeoTIFF HTTP server started on http://127.0.0.1:{self.port}")
        return self.port
    
    def stop(self):
        """Stop the HTTP server and clean up resources."""
        if self.server is not None:
            print("Stopping GeoTIFF HTTP server...")
            self.server.shutdown()
            self.server.server_close()
            self.server = None
            self.server_thread = None
            self.port = None
    
    def register_file(self, file_path: str) -> str:
        """
        Register a file path and return its HTTP URL.
        
        Parameters
        ----------
        file_path : str or Path
            Absolute path to the file to serve
        
        Returns
        -------
        str
            HTTP URL that can be used to access the file
        """
        # Ensure server is started
        if self.server is None:
            self.start()
        
        # Convert to absolute path
        abs_path = str(pathlib.Path(file_path).resolve())
        
        # Check if already registered
        for url_path, registered_path in self.file_registry.items():
            if registered_path == abs_path:
                return f"http://127.0.0.1:{self.port}/{url_path}"
        
        # Generate a unique URL path
        file_id = f"geotiff_{self._next_file_id}"
        self._next_file_id += 1
        
        # Register the file
        self.file_registry[file_id] = abs_path
        
        # Update the handler's registry reference
        if self.server:
            GeoTIFFFileHandler.file_registry = self.file_registry
        
        url = f"http://127.0.0.1:{self.port}/{file_id}"
        print(f"Registered GeoTIFF: {abs_path} -> {url}")
        return url
    
    def is_running(self) -> bool:
        """Check if the server is currently running."""
        return self.server is not None


# Global server instance
_server_instance: Optional[GeoTIFFServer] = None


def get_server() -> GeoTIFFServer:
    """
    Get the global GeoTIFF server instance.
    
    Returns
    -------
    GeoTIFFServer
        The singleton server instance
    """
    global _server_instance
    if _server_instance is None:
        _server_instance = GeoTIFFServer()
    return _server_instance

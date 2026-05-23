"""
Moonlayers: OpenLayers-based anywidget for visualizing lunar data in Marimo.

This package provides an interactive map widget for displaying lunar south-polar
data in polar stereographic projection using OpenLayers.
"""

from ._version import __version__
from .moon_map import MoonMap

__all__ = ["MoonMap", "__version__"]

"""Map renderers — consume a MapState and produce output (PNG or .vmap)."""
from vcmi_mapgen.renderers.png import PngRenderer
from vcmi_mapgen.renderers.vmap import VmapRenderer

__all__ = ["PngRenderer", "VmapRenderer"]

"""Shared outbound desktop transport for EagleAssets and ScreenCapture."""
import sys
from . import bridge

sys.modules["_comfy_desktop_bridge"] = bridge
bridge.register()
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
WEB_DIRECTORY = "./web"

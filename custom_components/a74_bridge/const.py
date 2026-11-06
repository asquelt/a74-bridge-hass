"""Constants for the A74 Bridge integration."""

DOMAIN = "a74_bridge"
CONF_TOKEN = "token"
DEFAULT_PORT = 8080
SCAN_INTERVAL_S = 10
PRESET_DIR = "/share/camera_presets"
LOAD_TIMEOUT_S = 150

EVENT_PRESET_APPLIED = "a74_bridge_preset_applied"
EVENT_PRESET_FAILED = "a74_bridge_preset_failed"
EVENT_CAMERA_DISCONNECTED = "a74_bridge_camera_disconnected"
EVENT_POWER_LOSS = "a74_bridge_camera_power_loss"

SERVICE_PRESET_SAVE = "preset_save"
SERVICE_PRESET_LOAD = "preset_load"
SERVICE_CLOCK_SYNC = "clock_sync"
SERVICE_SET_PROPERTIES = "set_properties"
SERVICE_RECORD = "record"

TYPE_BRIDGE = "bridge"
TYPE_CONSOLE = "console"
SERVICE_CONSOLE_RECORD = "console_record"
SERVICE_TIMECODE_JAM = "timecode_jam"
TYPE_STUDIO = "studio"
CONF_SOURCE_CAMERA = "source_camera"
DEFAULT_SOURCE_CAMERA = "a74-bridge-1"
SERVICE_PRESET_LIST = "preset_list"
SERVICE_PRESET_DELETE = "preset_delete"
SERVICE_SYNC_FROM = "sync_from"

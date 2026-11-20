"""Constants for the A74 Bridge integration."""

DOMAIN = "a74_bridge"
CONF_TOKEN = "token"
DEFAULT_PORT = 8080
SCAN_INTERVAL_S = 10
PRESET_DIR = "/share/camera_presets"
LOAD_TIMEOUT_S = 360

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
SERVICE_FOCUS_REFRESH = "focus_refresh"
TYPE_STUDIO = "studio"
CONF_SOURCE_CAMERA = "source_camera"
DEFAULT_SOURCE_CAMERA = "a74-bridge-1"
SERVICE_PRESET_LIST = "preset_list"
SERVICE_PRESET_DELETE = "preset_delete"
SERVICE_PRESET_STATUS = "preset_status"
SERVICE_SYNC_FROM = "sync_from"
CONF_GUARD_ENTITY = "guard_entity"
DEFAULT_GUARD_ENTITY = "input_boolean.tryb_nagrywania"

# recording upload (bridge >= 0.4.1)
EVENT_MEDIA_JOB_FINISHED = "a74_bridge_media_job_finished"
SERVICE_MEDIA_LIST = "media_list"
SERVICE_MEDIA_UPLOAD = "media_upload"
SERVICE_MEDIA_JOB = "media_job"
SERVICE_MEDIA_CANCEL = "media_cancel"
SERVICE_CONSOLE_LIST = "console_media_list"
SERVICE_CONSOLE_UPLOAD = "console_media_upload"
SERVICE_CONSOLE_JOB = "console_media_job"
SERVICE_CONSOLE_CANCEL = "console_media_cancel"
SERVICE_MEDIA_DELETE = "media_delete"
SERVICE_CONSOLE_DELETE = "console_media_delete"
MEDIA_RELEASE_TIMEOUT_S = 180   # record start waits this long for cameras busy with an upload (cancel takes up to ~90 s on a slow uplink)
MEDIA_RECONNECT_WAIT_S = 30   # after a card listing the camera needs about 6 s to return to Remote mode

"""Constants for the onvif component."""

import asyncio
import logging

import aiohttp
from onvif.exceptions import ONVIFError
from zeep.exceptions import Fault, TransportError

LOGGER = logging.getLogger(__package__)

DOMAIN = "thingino_onvif"

DEFAULT_PORT = 80
DEFAULT_ARGUMENTS = "-pred 1"

CONF_DEVICE_ID = "deviceid"
CONF_HARDWARE = "hardware"
CONF_SNAPSHOT_AUTH = "snapshot_auth"
CONF_ENABLE_WEBHOOKS = "enable_webhooks"
DEFAULT_ENABLE_WEBHOOKS = True
CONF_THINGINO_EXTRAS_ENABLED = "thingino_extras_enabled"
CONF_THINGINO_EXTRAS_ENDPOINT = "thingino_extras_endpoint"
CONF_THINGINO_EXEC_ENDPOINT = "thingino_exec_endpoint"
CONF_THINGINO_EXTRAS_JSON = "thingino_extras_json"
CONF_THINGINO_HTTP_USERNAME = "thingino_http_username"
CONF_THINGINO_HTTP_PASSWORD = "thingino_http_password"

DEFAULT_THINGINO_EXTRAS_ENABLED = True
DEFAULT_THINGINO_INFO_ENDPOINT = "/x/info.cgi?onvif"
DEFAULT_THINGINO_EXTRAS_ENDPOINTS = (
    "/x/info.cgi?onvif",
    "/etc/onvif.json",
    "/onvif.json",
    "/cgi-bin/onvif.json",
)
DEFAULT_THINGINO_EXEC_ENDPOINT = ""
DEFAULT_THINGINO_EXEC_CANDIDATES = (
    "/cgi-bin/thingino/exec",
    "/cgi-bin/exec",
    "/x/exec.cgi",
)

ATTR_PAN = "pan"
ATTR_TILT = "tilt"
ATTR_ZOOM = "zoom"
ATTR_DISTANCE = "distance"
ATTR_SPEED = "speed"
ATTR_MOVE_MODE = "move_mode"
ATTR_CONTINUOUS_DURATION = "continuous_duration"
ATTR_PRESET = "preset"
ATTR_PRESET_NAME = "preset_name"
ATTR_CONFIRM = "confirm"
ATTR_PAN_STEPS = "pan_steps"
ATTR_TILT_STEPS = "tilt_steps"
ATTR_ZOOM_STEPS = "zoom_steps"
ATTR_PROFILE_TOKEN = "profile_token"

CONF_PTZ_AUTO_STOP = "ptz_auto_stop"
DEFAULT_PTZ_AUTO_STOP = 0.5

DIR_UP = "UP"
DIR_DOWN = "DOWN"
DIR_LEFT = "LEFT"
DIR_RIGHT = "RIGHT"
ZOOM_OUT = "ZOOM_OUT"
ZOOM_IN = "ZOOM_IN"
PAN_FACTOR = {DIR_RIGHT: 1, DIR_LEFT: -1}
TILT_FACTOR = {DIR_UP: 1, DIR_DOWN: -1}
ZOOM_FACTOR = {ZOOM_IN: 1, ZOOM_OUT: -1}
CONTINUOUS_MOVE = "ContinuousMove"
RELATIVE_MOVE = "RelativeMove"
ABSOLUTE_MOVE = "AbsoluteMove"
GOTOPRESET_MOVE = "GotoPreset"
STOP_MOVE = "Stop"

SERVICE_PTZ = "ptz"
SERVICE_PTZ_MOVE = "ptz_move"
SERVICE_PTZ_STOP = "ptz_stop"
SERVICE_PTZ_ZOOM = "ptz_zoom"
SERVICE_PTZ_CONTINUOUS = "ptz_continuous"
SERVICE_PTZ_ABSOLUTE_STEPS = "ptz_absolute_steps"
SERVICE_GOTO_HOME = "goto_home"
SERVICE_SET_HOME = "set_home"
SERVICE_GOTO_PRESET = "goto_preset"
SERVICE_SET_PRESET = "set_preset"
SERVICE_REMOVE_PRESET = "remove_preset"


# Some cameras don't support the GetServiceCapabilities call
# and will return a 404 error which is caught by TransportError
GET_CAPABILITIES_EXCEPTIONS = (
    ONVIFError,
    Fault,
    aiohttp.ClientError,
    asyncio.TimeoutError,
    TransportError,
)

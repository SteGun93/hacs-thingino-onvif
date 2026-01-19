"""ONVIF device abstraction."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import datetime as dt
import json
import os
import time
from typing import Any
from urllib.parse import quote

import aiohttp
from aiohttp.client_exceptions import ServerDisconnectedError
import onvif
from onvif import ONVIFCamera
from onvif.exceptions import ONVIFError
from zeep.exceptions import Fault, TransportError, XMLParseError, XMLSyntaxError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import (
    ABSOLUTE_MOVE,
    CONF_ENABLE_WEBHOOKS,
    CONF_THINGINO_EXTRAS_ENABLED,
    CONF_THINGINO_EXTRAS_ENDPOINT,
    CONF_THINGINO_EXTRAS_JSON,
    CONF_THINGINO_EXEC_ENDPOINT,
    CONF_THINGINO_HTTP_PASSWORD,
    CONF_THINGINO_HTTP_USERNAME,
    CONTINUOUS_MOVE,
    DEFAULT_ENABLE_WEBHOOKS,
    DEFAULT_THINGINO_INFO_ENDPOINT,
    DEFAULT_THINGINO_EXTRAS_ENABLED,
    DEFAULT_THINGINO_EXTRAS_ENDPOINTS,
    DEFAULT_THINGINO_EXEC_ENDPOINT,
    GET_CAPABILITIES_EXCEPTIONS,
    GOTOPRESET_MOVE,
    LOGGER,
    PAN_FACTOR,
    RELATIVE_MOVE,
    STOP_MOVE,
    TILT_FACTOR,
    ZOOM_FACTOR,
)
from .event import EventManager
from .models import (
    PTZ,
    Capabilities,
    DeviceInfo,
    Profile,
    Resolution,
    ThinginoAuxCommand,
    ThinginoRelay,
    ThinginoToggle,
    PTZLimits,
    Video,
)
from .thingino_http import async_fetch_thingino_onvif_json
from .util import format_thingino_label, normalize_thingino_label, thingino_icon_for_label


class ONVIFDevice:
    """Manages an ONVIF device."""

    device: ONVIFCamera
    events: EventManager

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the device."""
        self.hass: HomeAssistant = hass
        self.config_entry: ConfigEntry = config_entry
        self._original_options = dict(config_entry.options)
        self.available: bool = True

        self.info: DeviceInfo = DeviceInfo()
        self.capabilities: Capabilities = Capabilities()
        self.onvif_capabilities: dict[str, Any] | None = None
        self.profiles: list[Profile] = []
        self.max_resolution: int = 0
        self.platforms: list[Platform] = []

        self._dt_diff_seconds: float = 0
        self._onvif_session: aiohttp.ClientSession | None = None
        self._onvif_session_supports_custom: bool | None = None
        self.onvif_retry_count: int = 0
        self.onvif_reset_count: int = 0
        self.ptz_service_available: bool = False
        self.ptz_reported: bool = False
        self.ptz_supported_runtime: bool = False
        self.ptz_fallback: bool = False
        self.thingino_ptz_mode: bool = False
        self.ptz_mapping_mode: str | None = None
        self.thingino_extras_enabled: bool = False
        self.thingino_extras_source: str | None = None
        self.thingino_extras_endpoint: str | None = None
        self.thingino_exec_endpoint: str | None = None
        self.thingino_aux_commands: list[ThinginoAuxCommand] = []
        self.thingino_aux_toggles: list[ThinginoToggle] = []
        self.thingino_relays: list[ThinginoRelay] = []

    async def _async_update_listener(
        self, hass: HomeAssistant, entry: ConfigEntry
    ) -> None:
        """Handle options update."""
        if self._original_options != entry.options:
            hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))

    @property
    def name(self) -> str:
        """Return the name of this device."""
        return self.config_entry.data[CONF_NAME]

    @property
    def host(self) -> str:
        """Return the host of this device."""
        return self.config_entry.data[CONF_HOST]

    @property
    def port(self) -> int:
        """Return the port of this device."""
        return self.config_entry.data[CONF_PORT]

    @property
    def username(self) -> str:
        """Return the username of this device."""
        return self.config_entry.data[CONF_USERNAME]

    @property
    def password(self) -> str:
        """Return the password of this device."""
        return self.config_entry.data[CONF_PASSWORD]

    async def async_setup(self) -> None:
        """Set up the device."""
        self.device = await self._async_create_onvif_client()

        # Get all device info
        await self._async_onvif_call("update_xaddrs", self.device.update_xaddrs)
        LOGGER.debug("%s: xaddrs = %s", self.name, self.device.xaddrs)

        # Get device capabilities
        self.onvif_capabilities = await self._async_onvif_call(
            "get_capabilities", self.device.get_capabilities
        )

        await self.async_check_date_and_time()

        # Create event manager
        assert self.config_entry.unique_id
        self.events = EventManager(self.hass, self.device, self.config_entry, self.name)

        # Fetch basic device info and capabilities
        self.info = await self.async_get_device_info()
        self._maybe_enable_thingino_ptz(PTZLimits())
        LOGGER.debug("%s: camera info = %s", self.name, self.info)

        #
        # We need to check capabilities before profiles, because we need the data
        # from capabilities to determine profiles correctly.
        #
        # We no longer initialize events in capabilities to avoid the problem
        # where cameras become slow to respond for a bit after starting events, and
        # instead we start events last and than update capabilities.
        #
        LOGGER.debug("%s: fetching initial capabilities", self.name)
        self.capabilities = await self.async_get_capabilities()

        LOGGER.debug("%s: fetching profiles", self.name)
        self.profiles = await self.async_get_profiles()
        LOGGER.debug("Camera %s profiles = %s", self.name, self.profiles)

        # No camera profiles to add
        if not self.profiles:
            raise ONVIFError("No camera profiles found")

        await self.async_discover_thingino_extras()

        if self.capabilities.ptz:
            LOGGER.debug("%s: creating PTZ service", self.name)
            try:
                await self.device.create_ptz_service()
                self.ptz_service_available = True
                if (
                    self.ptz_service_available
                    and not self.ptz_reported
                    and not self.ptz_fallback
                ):
                    # Thingino cameras may omit PTZ capabilities even when the PTZ service works.
                    self.ptz_fallback = True
                    LOGGER.debug(
                        "%s: PTZ service endpoint detected without reported capabilities; enabling tolerant PTZ mode",
                        self.name,
                    )
            except GET_CAPABILITIES_EXCEPTIONS as err:
                LOGGER.warning("%s: Failed to create PTZ service: %s", self.name, err)
            self.ptz_supported_runtime = await self.async_probe_ptz_support()
            if self.ptz_supported_runtime:
                LOGGER.debug(
                    "%s: PTZ runtime probe succeeded (tolerant mode=%s)",
                    self.name,
                    self.ptz_fallback,
                )
            elif self.ptz_service_available:
                LOGGER.debug(
                    "%s: PTZ runtime probe failed; keeping PTZ enabled based on service endpoint",
                    self.name,
                )
            else:
                LOGGER.debug(
                    "%s: PTZ runtime probe skipped; keeping PTZ enabled based on reported capabilities",
                    self.name,
                )

        # Determine max resolution from profiles
        self.max_resolution = max(
            profile.video.resolution.width
            for profile in self.profiles
            if profile.video.encoding == "H264"
        )

        # Start events last since some cameras become slow to respond
        # for a bit after starting events
        LOGGER.debug("%s: starting events", self.name)
        self.capabilities.events = await self.async_start_events()
        LOGGER.debug("Camera %s capabilities = %s", self.name, self.capabilities)

        # Bind the listener to the ONVIFDevice instance since
        # async_update_listener only creates a weak reference to the listener
        # and we need to make sure it doesn't get garbage collected since only
        # the ONVIFDevice instance is stored in hass.data
        self.config_entry.async_on_unload(
            self.config_entry.add_update_listener(self._async_update_listener)
        )

    async def async_stop(self, event=None):
        """Shut it all down."""
        if self.events:
            await self.events.async_stop()
        await self.device.close()
        if self._onvif_session and not self._onvif_session.closed:
            await self._onvif_session.close()

    async def async_manually_set_date_and_time(self) -> None:
        """Set Date and Time Manually using SetSystemDateAndTime command."""
        device_mgmt = await self._async_onvif_call(
            "create_devicemgmt_service", self.device.create_devicemgmt_service
        )

        # Retrieve DateTime object from camera to use as template for Set operation
        device_time = await device_mgmt.GetSystemDateAndTime()

        system_date = dt_util.utcnow()
        LOGGER.debug("System date (UTC): %s", system_date)

        dt_param = device_mgmt.create_type("SetSystemDateAndTime")
        dt_param.DateTimeType = "Manual"
        # Retrieve DST setting from system
        dt_param.DaylightSavings = bool(time.localtime().tm_isdst)
        dt_param.UTCDateTime = {
            "Date": {
                "Year": system_date.year,
                "Month": system_date.month,
                "Day": system_date.day,
            },
            "Time": {
                "Hour": system_date.hour,
                "Minute": system_date.minute,
                "Second": system_date.second,
            },
        }
        # Retrieve timezone from system
        system_timezone = str(system_date.astimezone().tzinfo)
        timezone_names: list[str | None] = [system_timezone]
        if (time_zone := device_time.TimeZone) and system_timezone != time_zone.TZ:
            timezone_names.append(time_zone.TZ)
        timezone_names.append(None)
        timezone_max_idx = len(timezone_names) - 1
        LOGGER.debug(
            "%s: SetSystemDateAndTime: timezone_names:%s", self.name, timezone_names
        )
        for idx, timezone_name in enumerate(timezone_names):
            dt_param.TimeZone = timezone_name
            LOGGER.debug("%s: SetSystemDateAndTime: %s", self.name, dt_param)
            try:
                await device_mgmt.SetSystemDateAndTime(dt_param)
                LOGGER.debug("%s: SetSystemDateAndTime: success", self.name)
            # Some cameras don't support setting the timezone and will throw an IndexError
            # if we try to set it. If we get an error, try again without the timezone.
            except (IndexError, Fault):
                if idx == timezone_max_idx:
                    raise
            else:
                return

    async def async_check_date_and_time(self) -> None:
        """Warns if device and system date not synced."""
        LOGGER.debug("%s: Setting up the ONVIF device management service", self.name)
        device_mgmt = await self.device.create_devicemgmt_service()
        system_date = dt_util.utcnow()

        LOGGER.debug("%s: Retrieving current device date/time", self.name)
        try:
            device_time = await device_mgmt.GetSystemDateAndTime()
        except (TimeoutError, aiohttp.ClientError, Fault) as err:
            LOGGER.warning(
                "Couldn't get device '%s' date/time. Error: %s", self.name, err
            )
            return

        if not device_time:
            LOGGER.debug(
                """Couldn't get device '%s' date/time.
                GetSystemDateAndTime() return null/empty""",
                self.name,
            )
            return

        LOGGER.debug("%s: Device time: %s", self.name, device_time)

        tzone = dt_util.get_default_time_zone()
        cdate = device_time.LocalDateTime
        if device_time.UTCDateTime:
            tzone = dt_util.UTC
            cdate = device_time.UTCDateTime
        elif device_time.TimeZone:
            tzone = await dt_util.async_get_time_zone(device_time.TimeZone.TZ) or tzone

        if cdate is None:
            LOGGER.warning("%s: Could not retrieve date/time on this camera", self.name)
            return

        try:
            cam_date = dt.datetime(
                cdate.Date.Year,
                cdate.Date.Month,
                cdate.Date.Day,
                cdate.Time.Hour,
                cdate.Time.Minute,
                cdate.Time.Second,
                0,
                tzone,
            )
        except ValueError as err:
            LOGGER.warning(
                "%s: Could not parse date/time from camera: %s", self.name, err
            )
            return

        cam_date_utc = cam_date.astimezone(dt_util.UTC)

        LOGGER.debug(
            "%s: Device date/time: %s | System date/time: %s",
            self.name,
            cam_date_utc,
            system_date,
        )

        dt_diff = cam_date - system_date
        self._dt_diff_seconds = dt_diff.total_seconds()

        # It could be off either direction, so we need to check the absolute value
        if abs(self._dt_diff_seconds) < 5:
            return

        if device_time.DateTimeType != "Manual":
            self._async_log_time_out_of_sync(cam_date_utc, system_date)
            return

        # Set Date and Time ourselves if Date and Time is set manually in the camera.
        try:
            await self.async_manually_set_date_and_time()
        except (TimeoutError, aiohttp.ClientError, TransportError, IndexError, Fault):
            LOGGER.warning("%s: Could not sync date/time on this camera", self.name)
            self._async_log_time_out_of_sync(cam_date_utc, system_date)

    @callback
    def _async_log_time_out_of_sync(
        self, cam_date_utc: dt.datetime, system_date: dt.datetime
    ) -> None:
        """Log a warning if the camera and system date/time are not synced."""
        LOGGER.warning(
            (
                "The date/time on %s (UTC) is '%s', "
                "which is different from the system '%s', "
                "this could lead to authentication issues"
            ),
            self.name,
            cam_date_utc,
            system_date,
        )

    async def async_get_device_info(self) -> DeviceInfo:
        """Obtain information about this device."""
        device_mgmt = await self.device.create_devicemgmt_service()
        manufacturer = None
        model = None
        firmware_version = None
        serial_number = None
        try:
            device_info = await self._async_onvif_call(
                "GetDeviceInformation", device_mgmt.GetDeviceInformation
            )
        except (XMLParseError, XMLSyntaxError, TransportError) as ex:
            # Some cameras have invalid UTF-8 in their device information (TransportError)
            # and others have completely invalid XML (XMLParseError, XMLSyntaxError)
            LOGGER.warning("%s: Failed to fetch device information: %s", self.name, ex)
        else:
            manufacturer = device_info.Manufacturer
            model = device_info.Model
            firmware_version = device_info.FirmwareVersion
            serial_number = device_info.SerialNumber

        # Grab the last MAC address for backwards compatibility
        mac = None
        try:
            network_interfaces = await self._async_onvif_call(
                "GetNetworkInterfaces", device_mgmt.GetNetworkInterfaces
            )
            for interface in network_interfaces:
                if interface.Enabled:
                    mac = interface.Info.HwAddress
        except Fault as fault:
            if "not implemented" not in fault.message:
                raise

            LOGGER.debug(
                "Couldn't get network interfaces from ONVIF device '%s'. Error: %s",
                self.name,
                fault,
            )

        return DeviceInfo(
            manufacturer,
            model,
            firmware_version,
            serial_number,
            mac,
        )

    async def async_get_capabilities(self):
        """Obtain information about the available services on the device."""
        snapshot = False
        with suppress(*GET_CAPABILITIES_EXCEPTIONS):
            media_service = await self.device.create_media_service()
            media_capabilities = await media_service.GetServiceCapabilities()
            snapshot = media_capabilities and media_capabilities.SnapshotUri

        ptz = False
        with suppress(*GET_CAPABILITIES_EXCEPTIONS):
            self.device.get_definition("ptz")
            self.ptz_reported = True
            ptz = True

        with suppress(*GET_CAPABILITIES_EXCEPTIONS):
            await self.device.create_ptz_service()
            self.ptz_service_available = True
            ptz = True

        if self.ptz_service_available and not self.ptz_reported:
            # Thingino cameras may omit PTZ capabilities even when the PTZ service works.
            self.ptz_fallback = True
            LOGGER.debug(
                "%s: PTZ service endpoint detected without reported capabilities; enabling tolerant PTZ mode",
                self.name,
            )

        imaging = False
        with suppress(*GET_CAPABILITIES_EXCEPTIONS):
            await self.device.create_imaging_service()
            imaging = True

        return Capabilities(snapshot=snapshot, ptz=ptz, imaging=imaging)

    async def _async_create_onvif_client(self) -> ONVIFCamera:
        """Create an ONVIF client with a reusable aiohttp session."""
        session = await self._async_get_onvif_session()
        return get_device(
            self.hass,
            host=self.config_entry.data[CONF_HOST],
            port=self.config_entry.data[CONF_PORT],
            username=self.config_entry.data[CONF_USERNAME],
            password=self.config_entry.data[CONF_PASSWORD],
            session=session,
        )

    async def _async_get_onvif_session(self) -> aiohttp.ClientSession | None:
        """Return or create the shared aiohttp session for ONVIF."""
        if self._onvif_session and not self._onvif_session.closed:
            return self._onvif_session
        connector = aiohttp.TCPConnector(limit=10, keepalive_timeout=30)
        timeout = aiohttp.ClientTimeout(total=10)
        self._onvif_session = aiohttp.ClientSession(
            connector=connector, timeout=timeout
        )
        return self._onvif_session

    async def _async_onvif_call(
        self, label: str, func, *args, retries: int = 2, **kwargs
    ):
        """Call an ONVIF function with retry on disconnects."""
        for attempt in range(retries + 1):
            try:
                return await func(*args, **kwargs)
            except ServerDisconnectedError as err:
                self.onvif_retry_count += 1
                LOGGER.debug(
                    "%s: ONVIF call '%s' disconnected (attempt %s/%s): %s",
                    self.name,
                    label,
                    attempt + 1,
                    retries + 1,
                    err,
                )
                if attempt >= retries:
                    raise
                await self._async_reset_onvif_client("disconnect")
                await asyncio.sleep(0.2 * (attempt + 1))

    async def _async_reset_onvif_client(self, reason: str) -> None:
        """Reset the ONVIF client/session after a disconnect."""
        self.onvif_reset_count += 1
        LOGGER.debug("%s: Resetting ONVIF client (%s)", self.name, reason)
        try:
            await self.device.close()
        except Exception as err:  # noqa: BLE001
            LOGGER.debug("%s: Error closing ONVIF client: %s", self.name, err)

        if self._onvif_session and not self._onvif_session.closed:
            await self._onvif_session.close()
        self._onvif_session = None
        self.device = await self._async_create_onvif_client()
        if self.events:
            self.events.device = self.device
        await self._async_onvif_call("update_xaddrs", self.device.update_xaddrs)

    async def async_start_events(self):
        """Start the event handler."""
        with suppress(*GET_CAPABILITIES_EXCEPTIONS, XMLParseError):
            onvif_capabilities = self.onvif_capabilities or {}
            pull_point_support = (onvif_capabilities.get("Events") or {}).get(
                "WSPullPointSupport"
            )
            LOGGER.debug("%s: WSPullPointSupport: %s", self.name, pull_point_support)
            # Even if the camera claims it does not support PullPoint, try anyway
            # since at least some AXIS and Bosch models do. The reverse is also
            # true where some cameras claim they support PullPoint but don't so
            # the only way to know is to try.
            return await self.events.async_start(
                True,
                self.config_entry.options.get(
                    CONF_ENABLE_WEBHOOKS, DEFAULT_ENABLE_WEBHOOKS
                ),
            )

        return False

    async def async_get_profiles(self) -> list[Profile]:
        """Obtain media profiles for this device."""
        media_service = await self._async_onvif_call(
            "create_media_service", self.device.create_media_service
        )
        LOGGER.debug("%s: xaddr for media_service: %s", self.name, media_service.xaddr)
        try:
            result = await self._async_onvif_call(
                "GetProfiles", media_service.GetProfiles
            )
        except GET_CAPABILITIES_EXCEPTIONS:
            LOGGER.debug(
                "%s: Could not get profiles from ONVIF device", self.name, exc_info=True
            )
            raise
        profiles: list[Profile] = []

        if not isinstance(result, list):
            return profiles

        for key, onvif_profile in enumerate(result):
            # Only add H264 profiles
            if (
                not onvif_profile.VideoEncoderConfiguration
                or onvif_profile.VideoEncoderConfiguration.Encoding != "H264"
            ):
                continue

            profile = Profile(
                key,
                onvif_profile.token,
                onvif_profile.Name,
                Video(
                    onvif_profile.VideoEncoderConfiguration.Encoding,
                    Resolution(
                        onvif_profile.VideoEncoderConfiguration.Resolution.Width,
                        onvif_profile.VideoEncoderConfiguration.Resolution.Height,
                    ),
                ),
            )

            # Configure PTZ options
            if self.capabilities.ptz:
                if onvif_profile.PTZConfiguration:
                    profile.ptz = PTZ(
                        onvif_profile.PTZConfiguration.DefaultContinuousPanTiltVelocitySpace
                        is not None,
                        onvif_profile.PTZConfiguration.DefaultRelativePanTiltTranslationSpace
                        is not None,
                        onvif_profile.PTZConfiguration.DefaultAbsolutePantTiltPositionSpace
                        is not None,
                    )
                    profile.ptz_limits = self._extract_ptz_limits(
                        onvif_profile.PTZConfiguration
                    )
                    if profile.ptz_limits:
                        self._maybe_enable_thingino_ptz(profile.ptz_limits)
                else:
                    profile.ptz = PTZ(True, True, True)
                    LOGGER.debug(
                        "%s: PTZ configuration missing for profile %s; enabling tolerant PTZ controls",
                        self.name,
                        profile.token,
                    )

                try:
                    ptz_service = await self._async_onvif_call(
                        "create_ptz_service", self.device.create_ptz_service
                    )
                    presets = await self._async_onvif_call(
                        "GetPresets", ptz_service.GetPresets, profile.token
                    )
                    profile.ptz.presets = [preset.token for preset in presets if preset]
                    LOGGER.debug(
                        "%s: PTZ presets for profile %s: %s",
                        self.name,
                        profile.token,
                        profile.ptz.presets,
                    )
                except GET_CAPABILITIES_EXCEPTIONS as err:
                    # It's OK if Presets aren't supported
                    profile.ptz.presets = None
                    LOGGER.debug(
                        "%s: Could not fetch PTZ presets for profile %s: %s",
                        self.name,
                        profile.token,
                        err,
                    )

            # Configure Imaging options
            if self.capabilities.imaging and onvif_profile.VideoSourceConfiguration:
                profile.video_source_token = (
                    onvif_profile.VideoSourceConfiguration.SourceToken
                )

            profiles.append(profile)

        return profiles

    async def async_get_stream_uri(self, profile: Profile) -> str:
        """Get the stream URI for a specified profile."""
        media_service = await self.device.create_media_service()
        req = media_service.create_type("GetStreamUri")
        req.ProfileToken = profile.token
        req.StreamSetup = {
            "Stream": "RTP-Unicast",
            "Transport": {"Protocol": "RTSP"},
        }
        result = await media_service.GetStreamUri(req)
        return result.Uri

    def _extract_ptz_limits(self, ptz_config) -> PTZLimits | None:
        """Extract PTZ limits from a PTZConfiguration."""
        limits = PTZLimits()

        pan_tilt_limits = getattr(ptz_config, "PanTiltLimits", None)
        if pan_tilt_limits:
            range_obj = getattr(pan_tilt_limits, "Range", pan_tilt_limits)
            pan_min, pan_max = self._extract_axis_range(range_obj, "X")
            tilt_min, tilt_max = self._extract_axis_range(range_obj, "Y")
            limits.pan_min = pan_min
            limits.pan_max = pan_max
            limits.tilt_min = tilt_min
            limits.tilt_max = tilt_max

        zoom_limits = getattr(ptz_config, "ZoomLimits", None)
        if zoom_limits:
            range_obj = getattr(zoom_limits, "Range", zoom_limits)
            zoom_min, zoom_max = self._extract_axis_range(range_obj, "X")
            limits.zoom_min = zoom_min
            limits.zoom_max = zoom_max

        if any(
            value is not None
            for value in (
                limits.pan_min,
                limits.pan_max,
                limits.tilt_min,
                limits.tilt_max,
                limits.zoom_min,
                limits.zoom_max,
            )
        ):
            return limits
        return None

    def _extract_axis_range(self, range_obj, axis: str) -> tuple[float | None, float | None]:
        """Extract Min/Max values for an axis range."""
        axis_range = getattr(range_obj, f"{axis}Range", None) or getattr(
            range_obj, axis, None
        )
        if not axis_range:
            return None, None
        return (
            getattr(axis_range, "Min", None),
            getattr(axis_range, "Max", None),
        )

    def _maybe_enable_thingino_ptz(self, limits: PTZLimits) -> None:
        """Enable Thingino PTZ mode based on device info or limits."""
        if self.thingino_ptz_mode:
            return
        model_hint = " ".join(
            part for part in (self.info.manufacturer, self.info.model) if part
        ).lower()
        if any(token in model_hint for token in ("thingino", "ingenic", "t31", "sc2336")):
            self.thingino_ptz_mode = True
            return
        if self.thingino_extras_source:
            self.thingino_ptz_mode = True
            return
        for min_val, max_val in (
            (limits.pan_min, limits.pan_max),
            (limits.tilt_min, limits.tilt_max),
        ):
            if min_val is None or max_val is None:
                continue
            if min_val >= 0 and max_val >= 10:
                self.thingino_ptz_mode = True
                return

    def _ptz_range_size(self, min_val: float | None, max_val: float | None) -> float | None:
        if min_val is None or max_val is None:
            return None
        return max_val - min_val

    def _ptz_max_step(self, min_val: float | None, max_val: float | None) -> float | None:
        if min_val is None or max_val is None:
            return None
        return max(abs(min_val), abs(max_val), abs(max_val - min_val))

    def _ptz_clamp(self, value: float, min_val: float | None, max_val: float | None) -> float:
        if min_val is not None:
            value = max(value, min_val)
        if max_val is not None:
            value = min(value, max_val)
        return value

    def _ptz_is_normalized(self, value: float | None) -> bool:
        if value is None:
            return False
        return -1.001 <= value <= 1.001

    def _ptz_normalize_to_unit(self, value: float, min_val: float | None) -> float:
        """Normalize value to [0..1] for Thingino mapping."""
        if min_val is not None and min_val >= 0 and 0 <= value <= 1:
            return value
        return (value + 1) / 2

    def _ptz_map_relative(self, value: float, min_val: float | None, max_val: float | None) -> float:
        """Map a relative move value to Thingino steps."""
        max_step = self._ptz_max_step(min_val, max_val)
        if max_step is None:
            return value
        if self._ptz_is_normalized(value):
            steps = round(value * max_step)
        else:
            steps = round(value)
        if steps == 0 and value != 0:
            steps = 1 if value > 0 else -1
        steps = max(-max_step, min(max_step, steps))
        return steps

    def _ptz_map_absolute(self, value: float, min_val: float | None, max_val: float | None) -> float:
        """Map an absolute move value to Thingino steps."""
        if min_val is None or max_val is None:
            return value
        if self._ptz_is_normalized(value):
            unit = self._ptz_normalize_to_unit(value, min_val)
            steps = round(min_val + unit * (max_val - min_val))
        else:
            steps = round(value)
        return self._ptz_clamp(steps, min_val, max_val)

    async def async_perform_ptz(
        self,
        profile: Profile,
        distance,
        speed,
        move_mode,
        continuous_duration,
        preset,
        pan=None,
        tilt=None,
        zoom=None,
    ):
        """Perform a PTZ action on the camera."""
        if not self.capabilities.ptz:
            LOGGER.warning("PTZ actions are not supported on device '%s'", self.name)
            return

        try:
            ptz_service = await self._async_onvif_call(
                "create_ptz_service", self.device.create_ptz_service
            )
        except GET_CAPABILITIES_EXCEPTIONS as err:
            LOGGER.warning(
                "%s: Failed to create PTZ service for action %s: %s",
                self.name,
                move_mode,
                err,
            )
            return

        limits = profile.ptz_limits
        thingino_mode = self.thingino_ptz_mode and limits is not None

        pan_val = distance * PAN_FACTOR.get(pan, 0)
        tilt_val = distance * TILT_FACTOR.get(tilt, 0)
        zoom_val = distance * ZOOM_FACTOR.get(zoom, 0)
        speed_val = speed
        preset_val = preset
        LOGGER.debug(
            (
                "Calling %s PTZ | Pan = %4.2f | Tilt = %4.2f | Zoom = %4.2f | Speed ="
                " %s | Preset = %s"
            ),
            move_mode,
            pan_val,
            tilt_val,
            zoom_val,
            speed_val,
            preset_val,
        )
        try:
            req = ptz_service.create_type(move_mode)
            req.ProfileToken = profile.token
            if move_mode == CONTINUOUS_MOVE:
                # Guard against unsupported operation
                if profile.ptz and not profile.ptz.continuous and not self.ptz_fallback:
                    LOGGER.warning(
                        "ContinuousMove not supported on device '%s'", self.name
                    )
                    return
                if profile.ptz and not profile.ptz.continuous and self.ptz_fallback:
                    LOGGER.debug(
                        "%s: ContinuousMove not advertised; attempting in tolerant PTZ mode",
                        self.name,
                    )

                velocity = {}
                if pan is not None or tilt is not None:
                    velocity["PanTilt"] = {"x": pan_val, "y": tilt_val}
                if zoom is not None:
                    velocity["Zoom"] = {"x": zoom_val}

                req.Velocity = velocity

                await self._async_onvif_call(
                    "ContinuousMove", ptz_service.ContinuousMove, req
                )
                await asyncio.sleep(continuous_duration)
                req = ptz_service.create_type("Stop")
                req.ProfileToken = profile.token
                await self._async_onvif_call(
                    "Stop",
                    ptz_service.Stop,
                    {"ProfileToken": req.ProfileToken, "PanTilt": True, "Zoom": False}
                )
            elif move_mode == RELATIVE_MOVE:
                # Guard against unsupported operation
                if profile.ptz and not profile.ptz.relative and not self.ptz_fallback:
                    LOGGER.warning(
                        "RelativeMove not supported on device '%s'", self.name
                    )
                    return
                if profile.ptz and not profile.ptz.relative and self.ptz_fallback:
                    LOGGER.debug(
                        "%s: RelativeMove not advertised; attempting in tolerant PTZ mode",
                        self.name,
                    )

                if thingino_mode:
                    original_pan = pan_val
                    original_tilt = tilt_val
                    pan_mode = (
                        "normalized" if self._ptz_is_normalized(original_pan) else "steps"
                    )
                    tilt_mode = (
                        "normalized" if self._ptz_is_normalized(original_tilt) else "steps"
                    )
                    pan_val = (
                        self._ptz_map_relative(
                            pan_val, limits.pan_min, limits.pan_max
                        )
                        if pan is not None
                        else 0
                    )
                    tilt_val = (
                        self._ptz_map_relative(
                            tilt_val, limits.tilt_min, limits.tilt_max
                        )
                        if tilt is not None
                        else 0
                    )
                    self.ptz_mapping_mode = "thingino_relative"
                    LOGGER.debug(
                        "%s: Thingino RelativeMove map pan(%s)=%s->%s tilt(%s)=%s->%s range=(%s..%s/%s..%s)",
                        self.name,
                        pan_mode,
                        original_pan,
                        pan_val,
                        tilt_mode,
                        original_tilt,
                        tilt_val,
                        limits.pan_min,
                        limits.pan_max,
                        limits.tilt_min,
                        limits.tilt_max,
                    )
                    if zoom is not None and limits.zoom_max in (0, 0.0):
                        LOGGER.debug(
                            "%s: Thingino zoom range is 0; skipping zoom for RelativeMove",
                            self.name,
                        )
                        zoom = None
                        zoom_val = 0

                translation = {"PanTilt": {"x": pan_val, "y": tilt_val}}
                if zoom is not None:
                    translation["Zoom"] = {"x": zoom_val}
                req.Translation = translation
                if speed_val is not None:
                    req.Speed = {
                        "PanTilt": {"x": speed_val, "y": speed_val},
                        "Zoom": {"x": speed_val},
                    }
                await self._async_onvif_call(
                    "RelativeMove", ptz_service.RelativeMove, req
                )
            elif move_mode == ABSOLUTE_MOVE:
                # Guard against unsupported operation
                if profile.ptz and not profile.ptz.absolute and not self.ptz_fallback:
                    LOGGER.warning(
                        "AbsoluteMove not supported on device '%s'", self.name
                    )
                    return
                if profile.ptz and not profile.ptz.absolute and self.ptz_fallback:
                    LOGGER.debug(
                        "%s: AbsoluteMove not advertised; attempting in tolerant PTZ mode",
                        self.name,
                    )

                if thingino_mode:
                    original_pan = pan_val
                    original_tilt = tilt_val
                    pan_mode = (
                        "normalized" if self._ptz_is_normalized(original_pan) else "steps"
                    )
                    tilt_mode = (
                        "normalized" if self._ptz_is_normalized(original_tilt) else "steps"
                    )
                    pan_val = self._ptz_map_absolute(
                        pan_val, limits.pan_min, limits.pan_max
                    )
                    tilt_val = self._ptz_map_absolute(
                        tilt_val, limits.tilt_min, limits.tilt_max
                    )
                    self.ptz_mapping_mode = "thingino_absolute"
                    LOGGER.debug(
                        "%s: Thingino AbsoluteMove map pan(%s)=%s->%s tilt(%s)=%s->%s range=(%s..%s/%s..%s)",
                        self.name,
                        pan_mode,
                        original_pan,
                        pan_val,
                        tilt_mode,
                        original_tilt,
                        tilt_val,
                        limits.pan_min,
                        limits.pan_max,
                        limits.tilt_min,
                        limits.tilt_max,
                    )
                    if zoom is not None and limits.zoom_max in (0, 0.0):
                        LOGGER.debug(
                            "%s: Thingino zoom range is 0; skipping zoom for AbsoluteMove",
                            self.name,
                        )
                        zoom = None
                        zoom_val = 0

                position = {"PanTilt": {"x": pan_val, "y": tilt_val}}
                if zoom is not None:
                    position["Zoom"] = {"x": zoom_val}
                req.Position = position
                if speed_val is not None:
                    req.Speed = {
                        "PanTilt": {"x": speed_val, "y": speed_val},
                        "Zoom": {"x": speed_val},
                    }
                await self._async_onvif_call(
                    "AbsoluteMove", ptz_service.AbsoluteMove, req
                )
            elif move_mode == GOTOPRESET_MOVE:
                # Guard against unsupported operation
                if profile.ptz and profile.ptz.presets is not None:
                    if preset_val not in profile.ptz.presets:
                        if not self.ptz_fallback:
                            LOGGER.warning(
                                (
                                    "PTZ preset '%s' does not exist on device '%s'. Available"
                                    " Presets: %s"
                                ),
                                preset_val,
                                self.name,
                                ", ".join(profile.ptz.presets),
                            )
                            return
                        LOGGER.debug(
                            "%s: PTZ preset '%s' not in reported list; attempting in tolerant PTZ mode",
                            self.name,
                            preset_val,
                        )

                req.PresetToken = preset_val
                if speed_val is not None:
                    req.Speed = {
                        "PanTilt": {"x": speed_val, "y": speed_val},
                        "Zoom": {"x": speed_val},
                    }
                await self._async_onvif_call("GotoPreset", ptz_service.GotoPreset, req)
            elif move_mode == STOP_MOVE:
                await self._async_onvif_call("Stop", ptz_service.Stop, req)
        except ONVIFError as err:
            reason = getattr(err, "reason", "") or str(err)
            if "Invalid position" in reason:
                LOGGER.debug(
                    "%s: PTZ request rejected with Invalid position: %s", self.name, err
                )
                return
            if "Bad Request" in reason:
                LOGGER.warning("Device '%s' doesn't support PTZ", self.name)
            else:
                LOGGER.error("Error trying to perform PTZ action: %s", err)

    async def async_probe_ptz_support(self) -> bool:
        """Probe PTZ support with lightweight service calls."""
        if not self.ptz_service_available:
            return False

        try:
            ptz_service = await self.device.create_ptz_service()
        except GET_CAPABILITIES_EXCEPTIONS:
            return False

        with suppress(*GET_CAPABILITIES_EXCEPTIONS):
            await ptz_service.GetServiceCapabilities()
            return True

        for profile in self.profiles:
            with suppress(*GET_CAPABILITIES_EXCEPTIONS):
                await ptz_service.GetPresets(profile.token)
                return True

        return False

    async def async_goto_home(self, profile: Profile) -> None:
        """Send GotoHomePosition to the camera."""
        if not self.capabilities.ptz:
            LOGGER.warning("PTZ actions are not supported on device '%s'", self.name)
            return

        try:
            ptz_service = await self.device.create_ptz_service()
        except GET_CAPABILITIES_EXCEPTIONS as err:
            LOGGER.warning(
                "%s: Failed to create PTZ service: %s",
                self.name,
                err,
            )
            return
        try:
            req = ptz_service.create_type("GotoHomePosition")
            req.ProfileToken = profile.token
            await self._async_onvif_call(
                "GotoHomePosition", ptz_service.GotoHomePosition, req
            )
        except ONVIFError as err:
            LOGGER.error("Error trying to go to Home position: %s", err)

    async def async_set_home(self, profile: Profile) -> None:
        """Send SetHomePosition to the camera."""
        if not self.capabilities.ptz:
            LOGGER.warning("PTZ actions are not supported on device '%s'", self.name)
            return

        try:
            ptz_service = await self.device.create_ptz_service()
        except GET_CAPABILITIES_EXCEPTIONS as err:
            LOGGER.warning("%s: Failed to create PTZ service: %s", self.name, err)
            return
        try:
            req = ptz_service.create_type("SetHomePosition")
            req.ProfileToken = profile.token
            await self._async_onvif_call(
                "SetHomePosition", ptz_service.SetHomePosition, req
            )
        except ONVIFError as err:
            LOGGER.error("Error trying to set Home position: %s", err)

    async def async_goto_preset(
        self, profile: Profile, preset: str, speed: float | None = None
    ) -> None:
        """Send GotoPreset to the camera."""
        if not self.capabilities.ptz:
            LOGGER.warning("PTZ actions are not supported on device '%s'", self.name)
            return

        try:
            ptz_service = await self.device.create_ptz_service()
        except GET_CAPABILITIES_EXCEPTIONS as err:
            LOGGER.warning("%s: Failed to create PTZ service: %s", self.name, err)
            return
        try:
            req = ptz_service.create_type("GotoPreset")
            req.ProfileToken = profile.token
            req.PresetToken = preset
            if speed is not None:
                req.Speed = {
                    "PanTilt": {"x": speed, "y": speed},
                    "Zoom": {"x": speed},
                }
            await self._async_onvif_call("GotoPreset", ptz_service.GotoPreset, req)
        except ONVIFError as err:
            LOGGER.error("Error trying to go to PTZ preset '%s': %s", preset, err)

    async def async_set_preset(
        self,
        profile: Profile,
        preset: str,
        name: str | None = None,
    ) -> str | None:
        """Send SetPreset to the camera and return the preset token."""
        if not self.capabilities.ptz:
            LOGGER.warning("PTZ actions are not supported on device '%s'", self.name)
            return None

        try:
            ptz_service = await self.device.create_ptz_service()
        except GET_CAPABILITIES_EXCEPTIONS as err:
            LOGGER.warning("%s: Failed to create PTZ service: %s", self.name, err)
            return None
        try:
            req = ptz_service.create_type("SetPreset")
            req.ProfileToken = profile.token
            req.PresetToken = preset
            req.PresetName = name or preset
            token = await self._async_onvif_call(
                "SetPreset", ptz_service.SetPreset, req
            )
        except ONVIFError as err:
            LOGGER.error("Error trying to set PTZ preset '%s': %s", preset, err)
            return None

        return token or preset

    async def async_remove_preset(self, profile: Profile, preset: str) -> None:
        """Send RemovePreset to the camera."""
        if not self.capabilities.ptz:
            LOGGER.warning("PTZ actions are not supported on device '%s'", self.name)
            return

        try:
            ptz_service = await self.device.create_ptz_service()
        except GET_CAPABILITIES_EXCEPTIONS as err:
            LOGGER.warning("%s: Failed to create PTZ service: %s", self.name, err)
            return
        try:
            req = ptz_service.create_type("RemovePreset")
            req.ProfileToken = profile.token
            req.PresetToken = preset
            await self._async_onvif_call(
                "RemovePreset", ptz_service.RemovePreset, req
            )
        except ONVIFError as err:
            LOGGER.error("Error trying to remove PTZ preset '%s': %s", preset, err)

    async def async_refresh_presets(self, profile: Profile) -> None:
        """Refresh cached PTZ presets for a profile."""
        if not self.capabilities.ptz:
            return

        try:
            ptz_service = await self.device.create_ptz_service()
            presets = await self._async_onvif_call(
                "GetPresets", ptz_service.GetPresets, profile.token
            )
        except GET_CAPABILITIES_EXCEPTIONS:
            LOGGER.debug(
                "%s: Failed to refresh presets for profile %s",
                self.name,
                profile.token,
            )
            if profile.ptz:
                profile.ptz.presets = None
            return

        tokens = [preset.token for preset in presets if preset]
        if profile.ptz is None:
            profile.ptz = PTZ(True, True, True, presets=tokens)
        else:
            profile.ptz.presets = tokens

    async def async_discover_thingino_extras(self) -> None:
        """Discover Thingino extras from ONVIF or HTTP endpoints."""
        options = self.config_entry.options
        self.thingino_extras_enabled = options.get(
            CONF_THINGINO_EXTRAS_ENABLED, DEFAULT_THINGINO_EXTRAS_ENABLED
        )
        if not self.thingino_extras_enabled:
            LOGGER.debug("%s: Thingino extras disabled", self.name)
            return

        onvif_relays = await self._async_discover_onvif_relays()
        if onvif_relays:
            self.thingino_relays = onvif_relays
            self.thingino_extras_source = "onvif"

        http_username = (
            options.get(CONF_THINGINO_HTTP_USERNAME)
            or self.config_entry.data.get(CONF_THINGINO_HTTP_USERNAME)
            or self.username
        )
        http_password = (
            options.get(CONF_THINGINO_HTTP_PASSWORD)
            or self.config_entry.data.get(CONF_THINGINO_HTTP_PASSWORD)
            or self.password
        )

        self.thingino_extras_endpoint = options.get(CONF_THINGINO_EXTRAS_ENDPOINT)
        if self.thingino_extras_endpoint == "":
            self.thingino_extras_endpoint = None
        self.thingino_exec_endpoint = options.get(
            CONF_THINGINO_EXEC_ENDPOINT, DEFAULT_THINGINO_EXEC_ENDPOINT
        )
        if self.thingino_exec_endpoint == "":
            self.thingino_exec_endpoint = None

        payload: dict[str, Any] | None = None
        endpoint_used: str | None = None
        if self.thingino_extras_endpoint:
            payload = await self._async_fetch_thingino_json(
                self.thingino_extras_endpoint,
                http_username,
                http_password,
            )
            endpoint_used = self.thingino_extras_endpoint if payload else None
        else:
            for candidate in DEFAULT_THINGINO_EXTRAS_ENDPOINTS:
                payload = await self._async_fetch_thingino_json(
                    candidate, http_username, http_password
                )
                if payload is not None:
                    endpoint_used = candidate
                    break

        if payload is None:
            manual_json = options.get(CONF_THINGINO_EXTRAS_JSON)
            if manual_json:
                try:
                    payload = json.loads(manual_json)
                    self.thingino_extras_source = "manual"
                    LOGGER.debug("%s: Thingino extras loaded from manual JSON", self.name)
                except json.JSONDecodeError as err:
                    LOGGER.warning(
                        "%s: Failed to parse Thingino extras JSON: %s", self.name, err
                    )

        if payload is None:
            LOGGER.debug("%s: Thingino extras not detected", self.name)
            return

        if self.thingino_extras_source is None:
            self.thingino_extras_source = "http"
        if endpoint_used:
            self.thingino_extras_endpoint = endpoint_used

        self._parse_thingino_extras(payload)
        self.thingino_ptz_mode = True

    async def _async_discover_onvif_relays(self) -> list[ThinginoRelay]:
        """Discover relay outputs via ONVIF DeviceIO."""
        try:
            deviceio = await self._async_onvif_call(
                "create_deviceio_service", self.device.create_deviceio_service
            )
            outputs = await self._async_onvif_call(
                "GetRelayOutputs", deviceio.GetRelayOutputs
            )
        except (GET_CAPABILITIES_EXCEPTIONS, XMLParseError, TypeError, ValueError):
            return []

        if not isinstance(outputs, list):
            return []

        relays: list[ThinginoRelay] = []
        for index, output in enumerate(outputs):
            token = getattr(output, "token", None) or getattr(output, "Token", None)
            name = format_thingino_label(str(token)) if token else f"Relay {index + 1}"
            idle_state = None
            properties = getattr(output, "Properties", None)
            if properties and getattr(properties, "IdleState", None):
                idle_state = str(properties.IdleState)
            relays.append(
                ThinginoRelay(
                    index=index,
                    name=name,
                    open=None,
                    close=None,
                    idle_state=idle_state,
                    icon=thingino_icon_for_label(name),
                    token=str(token) if token else None,
                    via_onvif=True,
                )
            )
        if relays:
            LOGGER.debug("%s: ONVIF relay outputs discovered: %s", self.name, relays)
        return relays

    async def _async_fetch_thingino_json(
        self, endpoint: str, username: str | None, password: str | None
    ) -> dict[str, Any] | None:
        """Fetch Thingino onvif.json payload from an endpoint."""
        payload, status = await async_fetch_thingino_onvif_json(
            self.hass,
            self.host,
            self.port,
            endpoint or DEFAULT_THINGINO_INFO_ENDPOINT,
            username,
            password,
        )
        if status == 401:
            LOGGER.debug("%s: Thingino HTTP auth required for %s", self.name, endpoint)
            return None
        if payload is None:
            LOGGER.debug(
                "%s: Thingino extras endpoint %s did not return valid JSON",
                self.name,
                endpoint,
            )
            return None
        LOGGER.debug("%s: Thingino extras discovered at %s", self.name, endpoint)
        return payload

    def _build_thingino_url(self, endpoint: str) -> str:
        """Build a full URL for a Thingino endpoint."""
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        if not endpoint.startswith("/"):
            endpoint = f"/{endpoint}"
        return f"http://{self.host}:{self.port}{endpoint}"

    def _parse_thingino_extras(self, payload: dict[str, Any]) -> None:
        """Parse Thingino extras payload."""
        aux_commands: list[ThinginoAuxCommand] = []
        relays: list[ThinginoRelay] = []

        for item in payload.get("aux") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            exec_cmd = str(item.get("exec") or "").strip()
            if not name or not exec_cmd:
                continue
            aux_commands.append(
                ThinginoAuxCommand(
                    name=format_thingino_label(name),
                    exec=exec_cmd,
                    icon=thingino_icon_for_label(name),
                )
            )

        for index, item in enumerate(payload.get("relays") or []):
            if not isinstance(item, dict):
                continue
            open_cmd = str(item.get("open") or "").strip()
            close_cmd = str(item.get("close") or "").strip()
            if not open_cmd or not close_cmd:
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                name = self._derive_thingino_relay_name(open_cmd, close_cmd, index)
            relays.append(
                ThinginoRelay(
                    index=index,
                    name=format_thingino_label(name),
                    open=open_cmd,
                    close=close_cmd,
                    idle_state=str(item.get("idle_state") or "").strip() or None,
                    icon=thingino_icon_for_label(name),
                )
            )

        toggles, remaining = self._build_thingino_aux_toggles(aux_commands)
        self.thingino_aux_toggles = toggles
        self.thingino_aux_commands = remaining
        if relays:
            self.thingino_relays = relays

        LOGGER.debug(
            "%s: Thingino extras parsed (aux=%s, toggles=%s, relays=%s, source=%s, endpoint=%s)",
            self.name,
            len(self.thingino_aux_commands),
            len(self.thingino_aux_toggles),
            len(self.thingino_relays),
            self.thingino_extras_source,
            self.thingino_extras_endpoint,
        )

    def _build_thingino_aux_toggles(
        self, commands: list[ThinginoAuxCommand]
    ) -> tuple[list[ThinginoToggle], list[ThinginoAuxCommand]]:
        """Create toggle pairs from aux commands when possible."""
        pairs: dict[str, dict[str, ThinginoAuxCommand]] = {}
        for command in commands:
            base, state = self._split_thingino_toggle_name(command.name)
            if not base or not state:
                continue
            pairs.setdefault(base, {})[state] = command

        toggles: list[ThinginoToggle] = []
        used_exec: set[str] = set()
        for base, entries in pairs.items():
            if "on" in entries and "off" in entries:
                toggles.append(
                    ThinginoToggle(
                        name=format_thingino_label(base),
                        on_exec=entries["on"].exec,
                        off_exec=entries["off"].exec,
                        icon=thingino_icon_for_label(base),
                    )
                )
                used_exec.add(entries["on"].exec)
                used_exec.add(entries["off"].exec)

        remaining = [cmd for cmd in commands if cmd.exec not in used_exec]
        return toggles, remaining

    def _split_thingino_toggle_name(self, name: str) -> tuple[str | None, str | None]:
        """Split a toggle name into base and state."""
        normalized = normalize_thingino_label(name)
        parts = [part for part in normalized.split(" ") if part]
        if len(parts) < 2:
            return None, None
        state = parts[-1]
        if state not in ("on", "off"):
            return None, None
        base = " ".join(parts[:-1]).strip()
        if not base:
            return None, None
        return base, state

    def _derive_thingino_relay_name(
        self, open_cmd: str, close_cmd: str, index: int
    ) -> str:
        """Derive a relay name from command strings."""
        for cmd in (open_cmd, close_cmd):
            cmd = cmd.strip()
            if cmd:
                return cmd.split(" ")[0]
        return f"Relay {index + 1}"

    async def async_set_relay_output_state(
        self, token: str, state: bool
    ) -> None:
        """Set ONVIF relay output state."""
        try:
            deviceio = await self._async_onvif_call(
                "create_deviceio_service", self.device.create_deviceio_service
            )
            req = deviceio.create_type("SetRelayOutputState")
            req.RelayOutputToken = token
            req.LogicalState = "active" if state else "inactive"
            await self._async_onvif_call(
                "SetRelayOutputState", deviceio.SetRelayOutputState, req
            )
        except GET_CAPABILITIES_EXCEPTIONS as err:
            LOGGER.warning(
                "%s: Failed to set relay output %s: %s", self.name, token, err
            )

    async def async_thingino_exec(self, cmd: str) -> None:
        """Execute a Thingino command via HTTP."""
        if not cmd:
            return
        if not self.thingino_exec_endpoint:
            LOGGER.warning(
                "%s: Thingino exec endpoint not configured; cannot run '%s'",
                self.name,
                cmd,
            )
            return

        endpoint = self.thingino_exec_endpoint
        if "{cmd}" in endpoint:
            url = self._build_thingino_url(endpoint.format(cmd=quote(cmd)))
            method = "get"
            json_payload = None
        else:
            url = self._build_thingino_url(endpoint)
            method = "post"
            json_payload = {"cmd": cmd, "exec": cmd}

        safe_url = url
        if "://" in url and "@" in url:
            scheme, rest = url.split("://", 1)
            safe_url = f"{scheme}://{rest.split('@', 1)[1]}"
        LOGGER.debug("%s: Thingino exec '%s' via %s", self.name, cmd, safe_url)

        session = async_get_clientsession(self.hass)
        options = self.config_entry.options
        http_username = (
            options.get(CONF_THINGINO_HTTP_USERNAME)
            or self.config_entry.data.get(CONF_THINGINO_HTTP_USERNAME)
            or self.username
        )
        http_password = (
            options.get(CONF_THINGINO_HTTP_PASSWORD)
            or self.config_entry.data.get(CONF_THINGINO_HTTP_PASSWORD)
            or self.password
        )
        auth = (
            aiohttp.BasicAuth(http_username, http_password) if http_username else None
        )
        try:
            async with session.request(
                method,
                url,
                json=json_payload,
                auth=auth,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as response:
                if response.status >= 400:
                    body = await response.text()
                    LOGGER.warning(
                        "%s: Thingino exec failed (%s) for '%s': %s",
                        self.name,
                        response.status,
                        cmd,
                        body,
                    )
                    return
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            LOGGER.warning(
                "%s: Thingino exec request failed for '%s': %s", self.name, cmd, err
            )

    async def async_run_aux_command(
        self,
        profile: Profile,
        cmd: str,
    ) -> None:
        """Execute a PTZ auxiliary command on the camera."""
        if not self.capabilities.ptz:
            LOGGER.warning("PTZ actions are not supported on device '%s'", self.name)
            return

        try:
            ptz_service = await self.device.create_ptz_service()
        except GET_CAPABILITIES_EXCEPTIONS as err:
            LOGGER.warning(
                "%s: Failed to create PTZ service for auxiliary command: %s",
                self.name,
                err,
            )
            return

        LOGGER.debug(
            "Running Aux Command | Cmd = %s",
            cmd,
        )
        try:
            req = ptz_service.create_type("SendAuxiliaryCommand")
            req.ProfileToken = profile.token
            req.AuxiliaryData = cmd
            await self._async_onvif_call(
                "SendAuxiliaryCommand", ptz_service.SendAuxiliaryCommand, req
            )
        except ONVIFError as err:
            if "Bad Request" in err.reason:
                LOGGER.warning("Device '%s' doesn't support PTZ", self.name)
            else:
                LOGGER.error("Error trying to send PTZ auxiliary command: %s", err)

    async def async_set_imaging_settings(
        self,
        profile: Profile,
        settings: dict,
    ) -> None:
        """Set an imaging setting on the ONVIF imaging service."""
        # The Imaging Service is defined by ONVIF standard
        # https://www.onvif.org/specs/srv/img/ONVIF-Imaging-Service-Spec-v210.pdf
        if not self.capabilities.imaging:
            LOGGER.warning(
                "The imaging service is not supported on device '%s'", self.name
            )
            return

        imaging_service = await self.device.create_imaging_service()

        LOGGER.debug("Setting Imaging Setting | Settings = %s", settings)
        try:
            req = imaging_service.create_type("SetImagingSettings")
            req.VideoSourceToken = profile.video_source_token
            req.ImagingSettings = settings
            await imaging_service.SetImagingSettings(req)
        except ONVIFError as err:
            if "Bad Request" in err.reason:
                LOGGER.warning(
                    "Device '%s' doesn't support the Imaging Service", self.name
                )
            else:
                LOGGER.error("Error trying to set Imaging settings: %s", err)


def get_device(
    hass: HomeAssistant,
    host: str,
    port: int,
    username: str | None,
    password: str | None,
    session: aiohttp.ClientSession | None = None,
) -> ONVIFCamera:
    """Get ONVIFCamera instance."""
    wsdl_path = f"{os.path.dirname(onvif.__file__)}/wsdl/"
    if session is not None:
        try:
            return ONVIFCamera(
                host,
                port,
                username,
                password,
                wsdl_path,
                no_cache=True,
                session=session,
            )
        except TypeError:
            pass
    return ONVIFCamera(
        host,
        port,
        username,
        password,
        wsdl_path,
        no_cache=True,
    )

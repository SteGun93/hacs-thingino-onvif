"""Support for ONVIF Cameras with FFmpeg as decoder."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from haffmpeg.camera import CameraMjpeg
from onvif.exceptions import ONVIFError
import voluptuous as vol
from yarl import URL

from homeassistant.components import ffmpeg
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.ffmpeg import CONF_EXTRA_ARGUMENTS, get_ffmpeg_manager
from homeassistant.components.stream import (
    CONF_RTSP_TRANSPORT,
    CONF_USE_WALLCLOCK_AS_TIMESTAMPS,
    RTSP_TRANSPORTS,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import HTTP_BASIC_AUTHENTICATION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.aiohttp_client import async_aiohttp_proxy_stream
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    ABSOLUTE_MOVE,
    ATTR_CONTINUOUS_DURATION,
    ATTR_DISTANCE,
    ATTR_MOVE_MODE,
    ATTR_PAN,
    ATTR_PRESET,
    ATTR_PRESET_NAME,
    ATTR_PAN_STEPS,
    ATTR_TILT_STEPS,
    ATTR_ZOOM_STEPS,
    ATTR_PROFILE_TOKEN,
    ATTR_SPEED,
    ATTR_TILT,
    ATTR_ZOOM,
    CONF_SNAPSHOT_AUTH,
    CONF_PTZ_AUTO_STOP,
    CONTINUOUS_MOVE,
    DEFAULT_PTZ_AUTO_STOP,
    DIR_DOWN,
    DIR_LEFT,
    DIR_RIGHT,
    DIR_UP,
    DOMAIN,
    GOTOPRESET_MOVE,
    LOGGER,
    RELATIVE_MOVE,
    SERVICE_GOTO_HOME,
    SERVICE_GOTO_PRESET,
    SERVICE_PTZ_ABSOLUTE_STEPS,
    SERVICE_PTZ_CONTINUOUS,
    SERVICE_PTZ_MOVE,
    SERVICE_PTZ,
    SERVICE_PTZ_STOP,
    SERVICE_PTZ_ZOOM,
    SERVICE_REMOVE_PRESET,
    SERVICE_SET_HOME,
    SERVICE_SET_PRESET,
    STOP_MOVE,
    ZOOM_IN,
    ZOOM_OUT,
)
from .device import ONVIFDevice
from .entity import ONVIFBaseEntity
from .models import Profile


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the ONVIF camera video stream."""
    platform = entity_platform.async_get_current_platform()

    # Create PTZ service
    platform.async_register_entity_service(
        SERVICE_PTZ,
        {
            vol.Optional(ATTR_PAN): vol.In([DIR_LEFT, DIR_RIGHT]),
            vol.Optional(ATTR_TILT): vol.In([DIR_UP, DIR_DOWN]),
            vol.Optional(ATTR_ZOOM): vol.In([ZOOM_OUT, ZOOM_IN]),
            vol.Optional(ATTR_DISTANCE, default=0.1): cv.small_float,
            vol.Optional(ATTR_SPEED): cv.small_float,
            vol.Optional(ATTR_MOVE_MODE, default=RELATIVE_MOVE): vol.In(
                [
                    CONTINUOUS_MOVE,
                    RELATIVE_MOVE,
                    ABSOLUTE_MOVE,
                    GOTOPRESET_MOVE,
                    STOP_MOVE,
                ]
            ),
            vol.Optional(ATTR_CONTINUOUS_DURATION, default=0.5): cv.small_float,
            vol.Optional(ATTR_PRESET, default="0"): cv.string,
        },
        "async_perform_ptz",
    )

    platform.async_register_entity_service(
        SERVICE_PTZ_MOVE,
        {
            vol.Optional(ATTR_PAN): vol.In([DIR_LEFT, DIR_RIGHT]),
            vol.Optional(ATTR_TILT): vol.In([DIR_UP, DIR_DOWN]),
            vol.Optional(ATTR_DISTANCE, default=0.1): cv.small_float,
            vol.Optional(ATTR_SPEED): cv.small_float,
        },
        "async_ptz_move",
    )

    platform.async_register_entity_service(
        SERVICE_PTZ_ZOOM,
        {
            vol.Required(ATTR_ZOOM): vol.In([ZOOM_OUT, ZOOM_IN]),
            vol.Optional(ATTR_DISTANCE, default=0.1): cv.small_float,
            vol.Optional(ATTR_SPEED): cv.small_float,
        },
        "async_ptz_zoom",
    )

    platform.async_register_entity_service(
        SERVICE_PTZ_STOP,
        {},
        "async_ptz_stop",
    )

    platform.async_register_entity_service(
        SERVICE_PTZ_CONTINUOUS,
        {
            vol.Optional(ATTR_PAN): vol.In([DIR_LEFT, DIR_RIGHT]),
            vol.Optional(ATTR_TILT): vol.In([DIR_UP, DIR_DOWN]),
            vol.Optional(ATTR_ZOOM): vol.In([ZOOM_OUT, ZOOM_IN]),
            vol.Optional(ATTR_SPEED): cv.small_float,
            vol.Optional(ATTR_CONTINUOUS_DURATION): cv.small_float,
            vol.Optional(ATTR_PROFILE_TOKEN): cv.string,
        },
        "async_ptz_continuous",
    )

    platform.async_register_entity_service(
        SERVICE_PTZ_ABSOLUTE_STEPS,
        {
            vol.Required(ATTR_PAN_STEPS): vol.Coerce(float),
            vol.Required(ATTR_TILT_STEPS): vol.Coerce(float),
            vol.Optional(ATTR_ZOOM_STEPS): vol.Coerce(float),
            vol.Optional(ATTR_SPEED): cv.small_float,
            vol.Optional(ATTR_PROFILE_TOKEN): cv.string,
        },
        "async_ptz_absolute_steps",
    )

    platform.async_register_entity_service(
        SERVICE_GOTO_HOME,
        {},
        "async_goto_home",
    )

    platform.async_register_entity_service(
        SERVICE_SET_HOME,
        {},
        "async_set_home",
    )

    platform.async_register_entity_service(
        SERVICE_GOTO_PRESET,
        {
            vol.Required(ATTR_PRESET): cv.string,
            vol.Optional(ATTR_SPEED): cv.small_float,
        },
        "async_goto_preset",
    )

    platform.async_register_entity_service(
        SERVICE_SET_PRESET,
        {
            vol.Required(ATTR_PRESET): cv.string,
            vol.Optional(ATTR_PRESET_NAME): cv.string,
        },
        "async_set_preset",
    )

    platform.async_register_entity_service(
        SERVICE_REMOVE_PRESET,
        {vol.Required(ATTR_PRESET): cv.string},
        "async_remove_preset",
    )

    device = hass.data[DOMAIN][config_entry.unique_id]
    async_add_entities(
        [ONVIFCameraEntity(device, profile) for profile in device.profiles]
    )


class ONVIFCameraEntity(ONVIFBaseEntity, Camera):
    """Representation of an ONVIF camera."""

    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, device: ONVIFDevice, profile: Profile) -> None:
        """Initialize ONVIF camera entity."""
        ONVIFBaseEntity.__init__(self, device)
        Camera.__init__(self)
        self.profile = profile
        self.stream_options[CONF_RTSP_TRANSPORT] = device.config_entry.options.get(
            CONF_RTSP_TRANSPORT, next(iter(RTSP_TRANSPORTS))
        )
        self.stream_options[CONF_USE_WALLCLOCK_AS_TIMESTAMPS] = (
            device.config_entry.options.get(CONF_USE_WALLCLOCK_AS_TIMESTAMPS, False)
        )
        self._basic_auth = (
            device.config_entry.data.get(CONF_SNAPSHOT_AUTH)
            == HTTP_BASIC_AUTHENTICATION
        )
        self._stream_uri: str | None = None
        self._stream_uri_future: asyncio.Future[str] | None = None
        self._attr_entity_registry_enabled_default = (
            device.max_resolution == profile.video.resolution.width
        )
        self._attr_unique_id = f"{self.mac_or_serial}#{profile.token}"
        self._attr_name = f"{device.name} {profile.name}"

    @property
    def use_stream_for_stills(self) -> bool:
        """Whether or not to use stream to generate stills."""
        return bool(self.stream and self.stream.dynamic_stream_settings.preload_stream)

    async def stream_source(self):
        """Return the stream source."""
        return await self._async_get_stream_uri()

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image response from the camera."""

        if self.device.capabilities.snapshot:
            try:
                if image := await self.device.device.get_snapshot(
                    self.profile.token, self._basic_auth
                ):
                    return image
            except ONVIFError as err:
                LOGGER.error(
                    "Fetch snapshot image failed from %s, falling back to FFmpeg; %s",
                    self.device.name,
                    err,
                )
            else:
                LOGGER.error(
                    "Fetch snapshot image failed from %s, falling back to FFmpeg",
                    self.device.name,
                )

        stream_uri = await self._async_get_stream_uri()
        return await ffmpeg.async_get_image(
            self.hass,
            stream_uri,
            extra_cmd=self.device.config_entry.options.get(CONF_EXTRA_ARGUMENTS),
            width=width,
            height=height,
        )

    async def handle_async_mjpeg_stream(self, request):
        """Generate an HTTP MJPEG stream from the camera."""
        LOGGER.debug("Handling mjpeg stream from camera '%s'", self.device.name)

        ffmpeg_manager = get_ffmpeg_manager(self.hass)
        stream = CameraMjpeg(ffmpeg_manager.binary)
        stream_uri = await self._async_get_stream_uri()

        await stream.open_camera(
            stream_uri,
            extra_cmd=self.device.config_entry.options.get(CONF_EXTRA_ARGUMENTS),
        )

        try:
            stream_reader = await stream.get_reader()
            return await async_aiohttp_proxy_stream(
                self.hass,
                request,
                stream_reader,
                ffmpeg_manager.ffmpeg_stream_content_type,
            )
        finally:
            await stream.close()

    async def _async_get_stream_uri(self) -> str:
        """Return the stream URI."""
        if self._stream_uri:
            return self._stream_uri
        if self._stream_uri_future:
            return await self._stream_uri_future
        loop = asyncio.get_running_loop()
        self._stream_uri_future = loop.create_future()
        try:
            uri_no_auth = await self.device.async_get_stream_uri(self.profile)
        except (TimeoutError, Exception) as err:
            LOGGER.error("Failed to get stream uri: %s", err)
            if self._stream_uri_future:
                self._stream_uri_future.set_exception(err)
            raise
        url = URL(uri_no_auth)
        url = url.with_user(self.device.username)
        url = url.with_password(self.device.password)
        self._stream_uri = str(url)
        self._stream_uri_future.set_result(self._stream_uri)
        return self._stream_uri

    async def async_perform_ptz(
        self,
        distance,
        move_mode,
        continuous_duration,
        preset,
        speed=None,
        pan=None,
        tilt=None,
        zoom=None,
    ) -> None:
        """Perform a PTZ action on the camera."""
        await self.device.async_perform_ptz(
            self.profile,
            distance,
            speed,
            move_mode,
            continuous_duration,
            preset,
            pan,
            tilt,
            zoom,
        )

    async def async_ptz_move(
        self,
        distance,
        speed=None,
        pan=None,
        tilt=None,
    ) -> None:
        """Perform a PTZ pan/tilt move."""
        if pan is None and tilt is None:
            LOGGER.warning(
                "%s: PTZ move called without pan/tilt directions", self.device.name
            )
            return
        await self.device.async_perform_ptz(
            self.profile,
            distance,
            speed,
            RELATIVE_MOVE,
            0,
            None,
            pan,
            tilt,
            None,
        )

    async def async_ptz_zoom(
        self,
        zoom,
        distance,
        speed=None,
    ) -> None:
        """Perform a PTZ zoom move."""
        await self.device.async_perform_ptz(
            self.profile,
            distance,
            speed,
            RELATIVE_MOVE,
            0,
            None,
            None,
            None,
            zoom,
        )

    async def async_ptz_stop(self) -> None:
        """Stop PTZ motion."""
        await self.device.async_perform_ptz(
            self.profile,
            0,
            None,
            STOP_MOVE,
            0,
            None,
            None,
            None,
            None,
        )

    async def async_ptz_continuous(
        self,
        pan=None,
        tilt=None,
        zoom=None,
        speed=None,
        continuous_duration=None,
        profile_token=None,
    ) -> None:
        """Perform a PTZ continuous move with optional auto-stop."""
        if pan is None and tilt is None and zoom is None:
            LOGGER.warning(
                "%s: PTZ continuous called without directions", self.device.name
            )
            return
        profile = self._resolve_profile(profile_token)
        duration = (
            continuous_duration
            if continuous_duration is not None
            else self.device.config_entry.options.get(
                CONF_PTZ_AUTO_STOP, DEFAULT_PTZ_AUTO_STOP
            )
        )
        await self.device.async_perform_ptz(
            profile,
            1,
            speed,
            CONTINUOUS_MOVE,
            duration,
            None,
            pan,
            tilt,
            zoom,
        )

    async def async_ptz_absolute_steps(
        self,
        pan_steps,
        tilt_steps,
        zoom_steps=None,
        speed=None,
        profile_token=None,
    ) -> None:
        """Perform an absolute move using step values."""
        profile = self._resolve_profile(profile_token)
        await self.device.async_absolute_move_steps(
            profile,
            pan_steps,
            tilt_steps,
            speed,
            zoom_steps,
        )

    async def async_goto_home(self) -> None:
        """Go to the configured Home position."""
        await self.device.async_goto_home(self.profile)

    async def async_set_home(self) -> None:
        """Set the current position as Home."""
        await self.device.async_set_home(self.profile)

    async def async_goto_preset(self, preset, speed=None) -> None:
        """Go to a PTZ preset."""
        await self.device.async_goto_preset(self.profile, preset, speed)

    async def async_set_preset(self, preset, preset_name=None) -> None:
        """Create or update a PTZ preset."""
        if not preset:
            LOGGER.warning("%s: Preset name is required", self.device.name)
            return
        token = await self.device.async_set_preset(self.profile, preset, preset_name)
        await self.device.async_refresh_presets(self.profile)
        if token and self.profile.ptz:
            if self.profile.ptz.presets is None:
                self.profile.ptz.presets = [token]
            elif token not in self.profile.ptz.presets:
                self.profile.ptz.presets.append(token)

    async def async_remove_preset(self, preset) -> None:
        """Remove a PTZ preset."""
        await self.device.async_remove_preset(self.profile, preset)
        await self.device.async_refresh_presets(self.profile)
        if self.profile.ptz and self.profile.ptz.presets:
            with suppress(ValueError):
                self.profile.ptz.presets.remove(preset)

    def _resolve_profile(self, profile_token: str | None) -> Profile:
        """Resolve profile by token, falling back to this entity's profile."""
        if profile_token:
            for profile in self.device.profiles:
                if profile.token == profile_token:
                    return profile
        return self.profile

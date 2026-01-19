"""Config flow for ONVIF."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
import logging
from pprint import pformat
from typing import Any
from urllib.parse import urlparse

from onvif.util import is_auth_error, stringify_onvif_error
import voluptuous as vol
from wsdiscovery.discovery import ThreadedWSDiscovery as WSDiscovery
from wsdiscovery.qname import QName
from wsdiscovery.scope import Scope
from wsdiscovery.service import Service
from zeep.exceptions import Fault

from homeassistant.components.ffmpeg import CONF_EXTRA_ARGUMENTS
from homeassistant.components.stream import (
    CONF_RTSP_TRANSPORT,
    CONF_USE_WALLCLOCK_AS_TIMESTAMPS,
    RTSP_TRANSPORTS,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .const import (
    CONF_DEVICE_ID,
    CONF_ENABLE_WEBHOOKS,
    CONF_HARDWARE,
    CONF_THINGINO_EXTRAS_ENABLED,
    CONF_THINGINO_EXTRAS_ENDPOINT,
    CONF_THINGINO_EXTRAS_JSON,
    CONF_THINGINO_EXEC_ENDPOINT,
    CONF_THINGINO_HTTP_PASSWORD,
    CONF_THINGINO_HTTP_USERNAME,
    DEFAULT_ARGUMENTS,
    DEFAULT_ENABLE_WEBHOOKS,
    DEFAULT_PORT,
    DEFAULT_THINGINO_EXTRAS_ENABLED,
    DEFAULT_THINGINO_INFO_ENDPOINT,
    DEFAULT_THINGINO_EXEC_ENDPOINT,
    DOMAIN,
    GET_CAPABILITIES_EXCEPTIONS,
    LOGGER,
)
from .device import get_device
from .thingino_http import async_fetch_thingino_onvif_json

CONF_MANUAL_INPUT = "Manually configure ONVIF device"
CONF_SELECTED_DEVICES = "devices"


def wsdiscovery() -> list[Service]:
    """Get ONVIF Profile S devices from network."""
    discovery = WSDiscovery(ttl=4, relates_to=True)
    try:
        discovery.start()
        return discovery.searchServices(
            types=[
                QName(
                    "http://www.onvif.org/ver10/network/wsdl",
                    "NetworkVideoTransmitter",
                    "dp0",
                )
            ],
            timeout=10,
        )
    finally:
        discovery.stop()


async def async_discovery(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Return if there are devices that can be discovered."""
    LOGGER.debug("Starting ONVIF discovery")
    services = await hass.async_add_executor_job(wsdiscovery)

    devices = []
    for service in services:
        url = urlparse(service.getXAddrs()[0])
        device = {
            CONF_DEVICE_ID: None,
            CONF_NAME: service.getEPR(),
            CONF_HOST: url.hostname,
            CONF_PORT: url.port or 80,
            CONF_HARDWARE: None,
        }
        for scope in service.getScopes():
            scope_str = scope.getValue()
            if scope_str.lower().startswith("onvif://www.onvif.org/name"):
                device[CONF_NAME] = scope_str.split("/")[-1]
            if scope_str.lower().startswith("onvif://www.onvif.org/hardware"):
                device[CONF_HARDWARE] = scope_str.split("/")[-1]
            if scope_str.lower().startswith("onvif://www.onvif.org/mac"):
                device[CONF_DEVICE_ID] = scope_str.split("/")[-1]
        devices.append(device)

    return devices


class OnvifFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle a ONVIF config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OnvifOptionsFlowHandler:
        """Get the options flow for this handler."""
        return OnvifOptionsFlowHandler(config_entry)

    def __init__(self) -> None:
        """Initialize the ONVIF config flow."""
        self.device_id = None
        self.devices: list[dict[str, Any]] = []
        self.onvif_config: dict[str, Any] = {}
        self.thingino_http_username: str | None = None
        self.thingino_http_password: str | None = None
        self._selected_devices: list[dict[str, Any]] = []
        self._discovered_devices: dict[str, dict[str, Any]] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle user flow."""
        if user_input:
            if user_input["auto"]:
                return await self.async_step_device()
            return await self.async_step_configure()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("auto", default=True): bool}),
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication of an existing config entry."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauth."""
        errors: dict[str, str] | None = {}
        reauth_entry = self._get_reauth_entry()
        description_placeholders: dict[str, str] | None = None
        if user_input is not None:
            self.onvif_config = reauth_entry.data | user_input
            errors, description_placeholders = await self.async_setup_profiles(
                configure_unique_id=False
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    reauth_entry, data=self.onvif_config
                )

        username = (user_input or {}).get(CONF_USERNAME) or reauth_entry.data[
            CONF_USERNAME
        ]
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=username): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> ConfigFlowResult:
        """Handle dhcp discovery."""
        hass = self.hass
        mac = discovery_info.macaddress
        registry = dr.async_get(self.hass)
        if not (
            device := registry.async_get_device(
                connections={(dr.CONNECTION_NETWORK_MAC, mac)}
            )
        ):
            return self.async_abort(reason="no_devices_found")
        for entry_id in device.config_entries:
            if (
                not (entry := hass.config_entries.async_get_entry(entry_id))
                or entry.domain != DOMAIN
                or entry.state is ConfigEntryState.LOADED
            ):
                continue
            if hass.config_entries.async_update_entry(
                entry, data=entry.data | {CONF_HOST: discovery_info.ip}
            ):
                hass.async_create_task(self.hass.config_entries.async_reload(entry_id))
        return self.async_abort(reason="already_configured")

    async def async_step_device(
        self, user_input: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Handle WS-Discovery.

        Let user choose between discovered devices and manual configuration.
        If no device is found allow user to manually input configuration.
        """
        errors: dict[str, str] = {}
        if user_input:
            selection = user_input.get(CONF_SELECTED_DEVICES) or []
            if not selection:
                errors["base"] = "no_selection"
            else:
                self._selected_devices = [
                    self._discovered_devices[key]
                    for key in selection
                    if key in self._discovered_devices
                ]
                LOGGER.debug(
                    "Thingino bulk selection: %s",
                    [
                        f"{device[CONF_NAME]} ({device[CONF_HOST]})"
                        for device in self._selected_devices
                    ],
                )
                return await self.async_step_thingino_http_auth_bulk()

        discovery = await async_discovery(self.hass)
        self._discovered_devices = {}
        for device in discovery:
            device_id = device.get(CONF_DEVICE_ID)
            key = device_id or f"{device[CONF_HOST]}:{device[CONF_PORT]}"
            configured = any(
                entry.unique_id == device_id
                for entry in self._async_current_entries()
                if device_id
            )
            if configured:
                continue
            self._discovered_devices[key] = device

        if LOGGER.isEnabledFor(logging.DEBUG):
            LOGGER.debug(
                "Discovered Thingino/ONVIF devices (%s): %s",
                len(self._discovered_devices),
                pformat(list(self._discovered_devices.values())),
            )

        if self._discovered_devices:
            devices = {}
            for key, device in self._discovered_devices.items():
                description = f"{device[CONF_NAME]} ({device[CONF_HOST]})"
                if hardware := device[CONF_HARDWARE]:
                    description += f" [{hardware}]"
                devices[key] = description

            return self.async_show_form(
                step_id="device",
                data_schema=vol.Schema(
                    {vol.Required(CONF_SELECTED_DEVICES): vol.MultiSelect(devices)}
                ),
                errors=errors,
            )

        LOGGER.debug("WS-Discovery found no ONVIF devices; using manual host if provided")
        return await self.async_step_configure()

    async def async_step_configure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Device configuration."""
        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {}
        if user_input:
            self.onvif_config = user_input
            if not self.devices:
                LOGGER.debug(
                    "Discovery skipped/empty; configuring ONVIF device manually at %s:%s",
                    self.onvif_config[CONF_HOST],
                    self.onvif_config[CONF_PORT],
                )
            errors, description_placeholders = await self.async_setup_profiles()
            if not errors:
                thingino_status = await self._async_check_thingino_http(
                    self.onvif_config.get(CONF_USERNAME),
                    self.onvif_config.get(CONF_PASSWORD),
                )
                if thingino_status == 401:
                    return await self.async_step_thingino_http_auth()
                title = f"{self.onvif_config[CONF_NAME]} - {self.device_id}"
                return self.async_create_entry(title=title, data=self.onvif_config)

        def conf(name, default=None):
            return self.onvif_config.get(name, default)

        # Username and Password are optional and default empty
        # due to some cameras not allowing you to change ONVIF user settings.
        # See https://github.com/home-assistant/core/issues/39182
        # and https://github.com/home-assistant/core/issues/35904
        return self.async_show_form(
            step_id="configure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=conf(CONF_NAME)): str,
                    vol.Required(CONF_HOST, default=conf(CONF_HOST)): str,
                    vol.Required(CONF_PORT, default=conf(CONF_PORT, DEFAULT_PORT)): int,
                    vol.Optional(CONF_USERNAME, default=conf(CONF_USERNAME, "")): str,
                    vol.Optional(CONF_PASSWORD, default=conf(CONF_PASSWORD, "")): str,
                }
            ),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def async_step_thingino_http_auth_bulk(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle bulk Thingino HTTP authentication."""
        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] | None = None
        if user_input:
            self.thingino_http_username = user_input[CONF_THINGINO_HTTP_USERNAME]
            self.thingino_http_password = user_input[CONF_THINGINO_HTTP_PASSWORD]
            primary = self._selected_devices[0]
            payload, status = await async_fetch_thingino_onvif_json(
                self.hass,
                primary[CONF_HOST],
                primary[CONF_PORT],
                DEFAULT_THINGINO_INFO_ENDPOINT,
                self.thingino_http_username,
                self.thingino_http_password,
                retries=1,
            )
            LOGGER.debug(
                "Thingino bulk HTTP probe status=%s for %s",
                status,
                primary[CONF_HOST],
            )
            if status == 401:
                errors["base"] = "auth_failed"
                description_placeholders = {"error": "Thingino HTTP"}
            else:
                if status and status != 200:
                    LOGGER.debug(
                        "Thingino bulk HTTP probe non-200 status=%s; continuing",
                        status,
                    )
                if status == 200 and payload is not None:
                    LOGGER.debug(
                        "Thingino bulk HTTP probe succeeded for %s",
                        primary[CONF_HOST],
                    )
                options = {
                    CONF_THINGINO_HTTP_USERNAME: self.thingino_http_username,
                    CONF_THINGINO_HTTP_PASSWORD: self.thingino_http_password,
                }
                await self._async_create_bulk_entries(options)
                first = self._selected_devices[0]
                self.device_id = first.get(CONF_DEVICE_ID) or f"{first[CONF_HOST]}:{first[CONF_PORT]}"
                await self.async_set_unique_id(self.device_id, raise_on_progress=False)
                self._abort_if_unique_id_configured(
                    updates={
                        CONF_HOST: first[CONF_HOST],
                        CONF_PORT: first[CONF_PORT],
                        CONF_NAME: first[CONF_NAME],
                        CONF_USERNAME: "",
                        CONF_PASSWORD: "",
                    }
                )
                title = f"{first[CONF_NAME]} - {self.device_id}"
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_NAME: first[CONF_NAME],
                        CONF_HOST: first[CONF_HOST],
                        CONF_PORT: first[CONF_PORT],
                        CONF_USERNAME: "",
                        CONF_PASSWORD: "",
                    },
                    options=options,
                )

        return self.async_show_form(
            step_id="thingino_http_auth_bulk",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_THINGINO_HTTP_USERNAME, default="root"): str,
                    vol.Required(CONF_THINGINO_HTTP_PASSWORD): str,
                }
            ),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def _async_create_bulk_entries(self, options: dict[str, Any]) -> None:
        """Start flows for remaining selected devices."""
        if len(self._selected_devices) < 2:
            return
        # HA allows only a single entry per flow, so we spawn additional flows here.
        for device in self._selected_devices[1:]:
            device_id = device.get(CONF_DEVICE_ID) or f"{device[CONF_HOST]}:{device[CONF_PORT]}"
            data = {
                CONF_DEVICE_ID: device_id,
                CONF_NAME: device[CONF_NAME],
                CONF_HOST: device[CONF_HOST],
                CONF_PORT: device[CONF_PORT],
                CONF_USERNAME: "",
                CONF_PASSWORD: "",
                "options": options,
            }
            self.hass.async_create_task(
                self.hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": "import"}, data=data
                )
            )

    async def async_step_import(
        self, user_input: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle import flow for bulk setup."""
        options = user_input.pop("options", {})
        device_id = user_input.pop(CONF_DEVICE_ID, None)
        if device_id:
            await self.async_set_unique_id(device_id, raise_on_progress=False)
            self._abort_if_unique_id_configured(
                updates={
                    CONF_HOST: user_input[CONF_HOST],
                    CONF_PORT: user_input[CONF_PORT],
                    CONF_NAME: user_input[CONF_NAME],
                    CONF_USERNAME: user_input.get(CONF_USERNAME, ""),
                    CONF_PASSWORD: user_input.get(CONF_PASSWORD, ""),
                }
            )
        title = f"{user_input[CONF_NAME]} - {device_id or user_input[CONF_HOST]}"
        return self.async_create_entry(title=title, data=user_input, options=options)

    async def async_step_thingino_http_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle Thingino HTTP authentication."""
        errors: dict[str, str] = {}
        if user_input:
            self.thingino_http_username = user_input[CONF_THINGINO_HTTP_USERNAME]
            self.thingino_http_password = user_input[CONF_THINGINO_HTTP_PASSWORD]
            status = await self._async_check_thingino_http(
                self.thingino_http_username,
                self.thingino_http_password,
            )
            if status == 401:
                errors["base"] = "auth_failed"
            else:
                self.onvif_config[CONF_THINGINO_HTTP_USERNAME] = (
                    self.thingino_http_username
                )
                self.onvif_config[CONF_THINGINO_HTTP_PASSWORD] = (
                    self.thingino_http_password
                )
                title = f"{self.onvif_config[CONF_NAME]} - {self.device_id}"
                return self.async_create_entry(title=title, data=self.onvif_config)

        return self.async_show_form(
            step_id="thingino_http_auth",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_THINGINO_HTTP_USERNAME, default=""): str,
                    vol.Required(CONF_THINGINO_HTTP_PASSWORD, default=""): str,
                }
            ),
            errors=errors,
        )

    async def _async_check_thingino_http(
        self, username: str | None, password: str | None
    ) -> int | None:
        """Probe Thingino HTTP endpoint for onvif.json access."""
        payload, status = await async_fetch_thingino_onvif_json(
            self.hass,
            self.onvif_config[CONF_HOST],
            self.onvif_config[CONF_PORT],
            DEFAULT_THINGINO_INFO_ENDPOINT,
            username,
            password,
            retries=1,
        )
        if status == 200 and payload is not None:
            LOGGER.debug(
                "Thingino HTTP probe succeeded for %s:%s",
                self.onvif_config[CONF_HOST],
                self.onvif_config[CONF_PORT],
            )
            return 200
        if status == 401:
            LOGGER.debug(
                "Thingino HTTP probe requires auth for %s:%s",
                self.onvif_config[CONF_HOST],
                self.onvif_config[CONF_PORT],
            )
            return 401
        if status:
            LOGGER.debug(
                "Thingino HTTP probe returned status %s for %s:%s",
                status,
                self.onvif_config[CONF_HOST],
                self.onvif_config[CONF_PORT],
            )
        return status

    async def async_setup_profiles(
        self, configure_unique_id: bool = True
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Fetch ONVIF device profiles."""
        if LOGGER.isEnabledFor(logging.DEBUG):
            LOGGER.debug(
                "Fetching profiles from ONVIF device %s", pformat(self.onvif_config)
            )

        device = get_device(
            self.hass,
            self.onvif_config[CONF_HOST],
            self.onvif_config[CONF_PORT],
            self.onvif_config[CONF_USERNAME],
            self.onvif_config[CONF_PASSWORD],
        )

        try:
            await device.update_xaddrs()
            device_mgmt = await device.create_devicemgmt_service()
            LOGGER.debug(
                "%s: Discovery xaddrs=%s",
                self.onvif_config[CONF_NAME],
                device.xaddrs,
            )
            with suppress(GET_CAPABILITIES_EXCEPTIONS, Fault):
                services = await device_mgmt.GetServices(False)
                if isinstance(services, list):
                    LOGGER.debug(
                        "%s: Discovery services=%s",
                        self.onvif_config[CONF_NAME],
                        [service.Namespace for service in services if service],
                    )
            with suppress(GET_CAPABILITIES_EXCEPTIONS, Fault):
                device_info_probe = await device_mgmt.GetDeviceInformation()
                LOGGER.debug(
                    "%s: Discovery device info manufacturer=%s model=%s hardware=%s",
                    self.onvif_config[CONF_NAME],
                    device_info_probe.Manufacturer,
                    device_info_probe.Model,
                    getattr(device_info_probe, "HardwareId", None),
                )
            # Get the MAC address to use as the unique ID for the config flow
            if not self.device_id:
                try:
                    network_interfaces = await device_mgmt.GetNetworkInterfaces()
                    interface = next(
                        filter(lambda interface: interface.Enabled, network_interfaces),
                        None,
                    )
                    if interface:
                        self.device_id = interface.Info.HwAddress
                except Fault as fault:
                    if "not implemented" not in fault.message:
                        raise
                    LOGGER.debug(
                        "%s: Could not get network interfaces: %s",
                        self.onvif_config[CONF_NAME],
                        stringify_onvif_error(fault),
                    )
            # If no network interfaces are exposed, fallback to serial number
            if not self.device_id:
                device_info = await device_mgmt.GetDeviceInformation()
                self.device_id = device_info.SerialNumber

            if not self.device_id:
                raise AbortFlow(reason="no_mac")

            if configure_unique_id:
                await self.async_set_unique_id(self.device_id, raise_on_progress=False)
                self._abort_if_unique_id_configured(
                    updates={
                        CONF_HOST: self.onvif_config[CONF_HOST],
                        CONF_PORT: self.onvif_config[CONF_PORT],
                        CONF_NAME: self.onvif_config[CONF_NAME],
                        CONF_USERNAME: self.onvif_config[CONF_USERNAME],
                        CONF_PASSWORD: self.onvif_config[CONF_PASSWORD],
                    }
                )
            # Verify there is an H264 profile
            media_service = await device.create_media_service()
            profiles = await media_service.GetProfiles()
        except AttributeError:  # Likely an empty document or 404 from the wrong port
            LOGGER.debug(
                "%s: No ONVIF service found at %s:%s",
                self.onvif_config[CONF_NAME],
                self.onvif_config[CONF_HOST],
                self.onvif_config[CONF_PORT],
                exc_info=True,
            )
            return {CONF_PORT: "no_onvif_service"}, {}
        except Fault as err:
            stringified_error = stringify_onvif_error(err)
            description_placeholders = {"error": stringified_error}
            if is_auth_error(err):
                LOGGER.debug(
                    "%s: Could not authenticate with camera: %s",
                    self.onvif_config[CONF_NAME],
                    stringified_error,
                )
                return {CONF_PASSWORD: "auth_failed"}, description_placeholders
            LOGGER.debug(
                "%s: Could not determine camera capabilities: %s",
                self.onvif_config[CONF_NAME],
                stringified_error,
                exc_info=True,
            )
            return {"base": "onvif_error"}, description_placeholders
        except GET_CAPABILITIES_EXCEPTIONS as err:
            LOGGER.debug(
                "%s: Could not determine camera capabilities: %s",
                self.onvif_config[CONF_NAME],
                stringify_onvif_error(err),
                exc_info=True,
            )
            return {"base": "onvif_error"}, {"error": stringify_onvif_error(err)}
        else:
            if not any(
                profile.VideoEncoderConfiguration
                and profile.VideoEncoderConfiguration.Encoding == "H264"
                for profile in profiles
            ):
                raise AbortFlow(reason="no_h264")
            return {}, {}
        finally:
            await device.close()


class OnvifOptionsFlowHandler(OptionsFlow):
    """Handle ONVIF options."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize ONVIF options flow."""
        self.options = dict(config_entry.options)

    async def async_step_init(self, user_input: None = None) -> ConfigFlowResult:
        """Manage the ONVIF options."""
        return await self.async_step_onvif_devices()

    async def async_step_onvif_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the ONVIF devices options."""
        if user_input is not None:
            self.options[CONF_EXTRA_ARGUMENTS] = user_input[CONF_EXTRA_ARGUMENTS]
            self.options[CONF_RTSP_TRANSPORT] = user_input[CONF_RTSP_TRANSPORT]
            self.options[CONF_USE_WALLCLOCK_AS_TIMESTAMPS] = user_input.get(
                CONF_USE_WALLCLOCK_AS_TIMESTAMPS,
                self.config_entry.options.get(CONF_USE_WALLCLOCK_AS_TIMESTAMPS, False),
            )
            self.options[CONF_ENABLE_WEBHOOKS] = user_input.get(
                CONF_ENABLE_WEBHOOKS,
                self.config_entry.options.get(
                    CONF_ENABLE_WEBHOOKS, DEFAULT_ENABLE_WEBHOOKS
                ),
            )
            self.options[CONF_THINGINO_EXTRAS_ENABLED] = user_input.get(
                CONF_THINGINO_EXTRAS_ENABLED,
                self.config_entry.options.get(
                    CONF_THINGINO_EXTRAS_ENABLED, DEFAULT_THINGINO_EXTRAS_ENABLED
                ),
            )
            self.options[CONF_THINGINO_EXTRAS_ENDPOINT] = user_input.get(
                CONF_THINGINO_EXTRAS_ENDPOINT,
                self.config_entry.options.get(CONF_THINGINO_EXTRAS_ENDPOINT, ""),
            )
            self.options[CONF_THINGINO_EXEC_ENDPOINT] = user_input.get(
                CONF_THINGINO_EXEC_ENDPOINT,
                self.config_entry.options.get(
                    CONF_THINGINO_EXEC_ENDPOINT, DEFAULT_THINGINO_EXEC_ENDPOINT
                ),
            )
            self.options[CONF_THINGINO_EXTRAS_JSON] = user_input.get(
                CONF_THINGINO_EXTRAS_JSON,
                self.config_entry.options.get(CONF_THINGINO_EXTRAS_JSON, ""),
            )
            self.options[CONF_THINGINO_HTTP_USERNAME] = user_input.get(
                CONF_THINGINO_HTTP_USERNAME,
                self.config_entry.options.get(CONF_THINGINO_HTTP_USERNAME, ""),
            )
            self.options[CONF_THINGINO_HTTP_PASSWORD] = user_input.get(
                CONF_THINGINO_HTTP_PASSWORD,
                self.config_entry.options.get(CONF_THINGINO_HTTP_PASSWORD, ""),
            )
            return self.async_create_entry(title="", data=self.options)

        advanced_options = {}
        if self.show_advanced_options:
            advanced_options[
                vol.Optional(
                    CONF_USE_WALLCLOCK_AS_TIMESTAMPS,
                    default=self.config_entry.options.get(
                        CONF_USE_WALLCLOCK_AS_TIMESTAMPS, False
                    ),
                )
            ] = bool
        return self.async_show_form(
            step_id="onvif_devices",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_EXTRA_ARGUMENTS,
                        default=self.config_entry.options.get(
                            CONF_EXTRA_ARGUMENTS, DEFAULT_ARGUMENTS
                        ),
                    ): str,
                    vol.Optional(
                        CONF_RTSP_TRANSPORT,
                        default=self.config_entry.options.get(
                            CONF_RTSP_TRANSPORT, next(iter(RTSP_TRANSPORTS))
                        ),
                    ): vol.In(RTSP_TRANSPORTS),
                    vol.Optional(
                        CONF_ENABLE_WEBHOOKS,
                        default=self.config_entry.options.get(
                            CONF_ENABLE_WEBHOOKS, DEFAULT_ENABLE_WEBHOOKS
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_THINGINO_EXTRAS_ENABLED,
                        default=self.config_entry.options.get(
                            CONF_THINGINO_EXTRAS_ENABLED,
                            DEFAULT_THINGINO_EXTRAS_ENABLED,
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_THINGINO_EXTRAS_ENDPOINT,
                        default=self.config_entry.options.get(
                            CONF_THINGINO_EXTRAS_ENDPOINT, ""
                        ),
                    ): str,
                    vol.Optional(
                        CONF_THINGINO_EXEC_ENDPOINT,
                        default=self.config_entry.options.get(
                            CONF_THINGINO_EXEC_ENDPOINT,
                            DEFAULT_THINGINO_EXEC_ENDPOINT,
                        ),
                    ): str,
                    vol.Optional(
                        CONF_THINGINO_EXTRAS_JSON,
                        default=self.config_entry.options.get(
                            CONF_THINGINO_EXTRAS_JSON, ""
                        ),
                    ): str,
                    vol.Optional(
                        CONF_THINGINO_HTTP_USERNAME,
                        default=self.config_entry.options.get(
                            CONF_THINGINO_HTTP_USERNAME, ""
                        ),
                    ): str,
                    vol.Optional(
                        CONF_THINGINO_HTTP_PASSWORD,
                        default=self.config_entry.options.get(
                            CONF_THINGINO_HTTP_PASSWORD, ""
                        ),
                    ): str,
                    **advanced_options,
                }
            ),
        )

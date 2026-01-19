"""Diagnostics support for ONVIF."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .device import ONVIFDevice

REDACT_CONFIG = {CONF_HOST, CONF_PASSWORD, CONF_USERNAME}


def _redact_url(value: str | None) -> str | None:
    if not value or "://" not in value:
        return value
    parts = urlsplit(value)
    if not parts.username and not parts.password:
        return value
    hostname = parts.hostname or ""
    if parts.port:
        hostname = f"{hostname}:{parts.port}"
    return urlunsplit((parts.scheme, hostname, parts.path, parts.query, parts.fragment))


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    device: ONVIFDevice = hass.data[DOMAIN][entry.unique_id]
    data: dict[str, Any] = {}

    data["config"] = async_redact_data(entry.as_dict(), REDACT_CONFIG)
    data["device"] = {
        "info": asdict(device.info),
        "capabilities": asdict(device.capabilities),
        "ptz": {
            "reported": device.ptz_reported,
            "service_endpoint": device.ptz_service_available,
            "runtime_probe": device.ptz_supported_runtime,
            "tolerant_mode": device.ptz_fallback,
            "thingino_mode": device.thingino_ptz_mode,
            "mapping_mode": device.ptz_mapping_mode,
            "retry_count": device.onvif_retry_count,
            "reset_count": device.onvif_reset_count,
        },
        "thingino_extras": {
            "enabled": device.thingino_extras_enabled,
            "source": device.thingino_extras_source,
            "endpoint": _redact_url(device.thingino_extras_endpoint),
            "exec_endpoint": _redact_url(device.thingino_exec_endpoint),
            "aux": [command.name for command in device.thingino_aux_commands],
            "aux_toggles": [toggle.name for toggle in device.thingino_aux_toggles],
            "relays": [relay.name for relay in device.thingino_relays],
        },
        "profiles": [asdict(profile) for profile in device.profiles],
        "services": {
            str(key): service.url for key, service in device.device.services.items()
        },
        "xaddrs": device.device.xaddrs,
    }
    data["events"] = {
        "webhook_manager_state": device.events.webhook_manager.state,
        "pullpoint_manager_state": device.events.pullpoint_manager.state,
    }

    return data

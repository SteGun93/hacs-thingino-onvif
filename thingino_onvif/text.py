"""ONVIF text entities."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .device import ONVIFDevice
from .entity import ONVIFBaseEntity
from .models import Profile


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ONVIF text entities."""
    device = hass.data[DOMAIN][config_entry.unique_id]
    entities: list[TextEntity] = []

    if device.capabilities.ptz:
        for profile in device.profiles:
            entities.append(ONVIFPresetNameText(device, profile))

    async_add_entities(entities)


class ONVIFPresetNameText(ONVIFBaseEntity, TextEntity):
    """Text entity to provide a preset name."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:tag-text"
    _attr_native_max = 64
    _attr_native_min = 1

    def __init__(self, device: ONVIFDevice, profile: Profile) -> None:
        """Initialize the preset name text entity."""
        super().__init__(device)
        self.profile = profile
        profile_suffix = (
            f" ({profile.name})" if len(device.profiles) > 1 and profile.name else ""
        )
        self._attr_name = f"{self.device.name} Preset Name{profile_suffix}"
        self._attr_unique_id = f"{self.mac_or_serial}#{profile.token}_preset_name"
        stored = self.device.get_preset_name_value(profile) or ""
        self._attr_native_value = stored

    async def async_set_value(self, value: str) -> None:
        """Update the preset name."""
        value = value.strip()
        self._attr_native_value = value
        self.device.set_preset_name_value(self.profile, value)
        self.async_write_ha_state()

"""ONVIF select entities."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
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
    """Set up ONVIF select entities."""
    device = hass.data[DOMAIN][config_entry.unique_id]
    entities: list[SelectEntity] = []

    for profile in device.profiles:
        if profile.ptz and profile.ptz.presets:
            entities.append(ONVIFPresetSelect(device, profile))

    async_add_entities(entities)


class ONVIFPresetSelect(ONVIFBaseEntity, SelectEntity):
    """Select entity to trigger PTZ presets."""

    _attr_has_entity_name = True
    _attr_should_poll = True
    _attr_translation_key = "ptz_preset"

    def __init__(self, device: ONVIFDevice, profile: Profile) -> None:
        """Initialize the preset select."""
        super().__init__(device)
        self.profile = profile
        self._attr_unique_id = f"{self.mac_or_serial}#{profile.token}_preset"
        self._attr_options = list(profile.ptz.presets or [])

    async def async_select_option(self, option: str) -> None:
        """Select a preset."""
        await self.device.async_goto_preset(self.profile, option)
        self._attr_current_option = option
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Refresh presets."""
        await self.device.async_refresh_presets(self.profile)
        presets = list(self.profile.ptz.presets or []) if self.profile.ptz else []
        self._attr_options = presets
        if self._attr_current_option not in presets:
            self._attr_current_option = None

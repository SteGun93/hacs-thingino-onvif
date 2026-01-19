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

NEW_PRESET_OPTION = "New preset..."


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ONVIF select entities."""
    device = hass.data[DOMAIN][config_entry.unique_id]
    entities: list[SelectEntity] = []

    for profile in device.profiles:
        if profile.ptz:
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
        self._option_to_token: dict[str, str] = {}
        self._token_to_option: dict[str, str] = {}
        self._sync_options()

    async def async_select_option(self, option: str) -> None:
        """Select a preset."""
        if option == NEW_PRESET_OPTION:
            self.device.set_selected_preset(self.profile, None)
            self._attr_current_option = option
            self.async_write_ha_state()
            return
        token = self._option_to_token.get(option, option)
        self.device.set_selected_preset(self.profile, token)
        self._attr_current_option = self._token_to_option.get(token, option)
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Refresh presets."""
        await self.device.async_refresh_presets(self.profile)
        selected = self.device.get_selected_preset(self.profile)
        if not selected and self._attr_current_option:
            selected = self._option_to_token.get(self._attr_current_option)
        self._sync_options()
        if selected and selected in self._token_to_option:
            self._attr_current_option = self._token_to_option[selected]
        elif self._attr_current_option not in self._attr_options:
            self._attr_current_option = None

    def _sync_options(self) -> None:
        """Build options and token mappings from the preset cache."""
        tokens = list(self.profile.ptz.presets or []) if self.profile.ptz else []
        options: list[str] = []
        name_counts: dict[str, int] = {}
        option_to_token: dict[str, str] = {}
        token_to_option: dict[str, str] = {}
        for token in tokens:
            name = self.device.get_preset_name(self.profile, token) or token
            name_counts[name] = name_counts.get(name, 0) + 1
        for token in tokens:
            name = self.device.get_preset_name(self.profile, token) or token
            option = name
            if name_counts.get(name, 0) > 1 and name != token:
                option = f"{name} ({token})"
            options.append(option)
            option_to_token[option] = token
            token_to_option[token] = option
        options.insert(0, NEW_PRESET_OPTION)
        self._option_to_token = option_to_token
        self._token_to_option = token_to_option
        self._attr_options = options

"""ONVIF switches for controlling cameras."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import slugify

from .const import DOMAIN
from .device import ONVIFDevice
from .entity import ONVIFBaseEntity
from .models import Profile, ThinginoRelay, ThinginoToggle


@dataclass(frozen=True, kw_only=True)
class ONVIFSwitchEntityDescription(SwitchEntityDescription):
    """Describes ONVIF switch entity."""

    turn_on_fn: Callable[
        [ONVIFDevice], Callable[[Profile, Any], Coroutine[Any, Any, None]]
    ]
    turn_off_fn: Callable[
        [ONVIFDevice], Callable[[Profile, Any], Coroutine[Any, Any, None]]
    ]
    turn_on_data: Any
    turn_off_data: Any
    supported_fn: Callable[[ONVIFDevice], bool]


SWITCHES: tuple[ONVIFSwitchEntityDescription, ...] = (
    ONVIFSwitchEntityDescription(
        key="autofocus",
        translation_key="autofocus",
        turn_on_data={"Focus": {"AutoFocusMode": "AUTO"}},
        turn_off_data={"Focus": {"AutoFocusMode": "MANUAL"}},
        turn_on_fn=lambda device: device.async_set_imaging_settings,
        turn_off_fn=lambda device: device.async_set_imaging_settings,
        supported_fn=lambda device: device.capabilities.imaging,
    ),
    ONVIFSwitchEntityDescription(
        key="ir_lamp",
        translation_key="ir_lamp",
        turn_on_data={"IrCutFilter": "OFF"},
        turn_off_data={"IrCutFilter": "ON"},
        turn_on_fn=lambda device: device.async_set_imaging_settings,
        turn_off_fn=lambda device: device.async_set_imaging_settings,
        supported_fn=lambda device: device.capabilities.imaging,
    ),
    ONVIFSwitchEntityDescription(
        key="wiper",
        translation_key="wiper",
        turn_on_data="tt:Wiper|On",
        turn_off_data="tt:Wiper|Off",
        turn_on_fn=lambda device: device.async_run_aux_command,
        turn_off_fn=lambda device: device.async_run_aux_command,
        supported_fn=lambda device: device.capabilities.ptz,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a ONVIF switch platform."""
    device = hass.data[DOMAIN][config_entry.unique_id]
    entities: list[SwitchEntity] = [
        ONVIFSwitch(device, description)
        for description in SWITCHES
        if description.supported_fn(device)
    ]
    entities += [
        ThinginoRelaySwitch(device, relay) for relay in device.thingino_relays
    ]
    entities += [
        ThinginoAuxToggleSwitch(device, toggle) for toggle in device.thingino_aux_toggles
    ]
    async_add_entities(entities)


class ONVIFSwitch(ONVIFBaseEntity, SwitchEntity):
    """An ONVIF switch."""

    entity_description: ONVIFSwitchEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self, device: ONVIFDevice, description: ONVIFSwitchEntityDescription
    ) -> None:
        """Initialize the switch."""
        super().__init__(device)
        self._attr_unique_id = f"{self.mac_or_serial}_{description.key}"
        self.entity_description = description

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on switch."""
        self._attr_is_on = True
        profile = self.device.profiles[0]
        await self.entity_description.turn_on_fn(self.device)(
            profile, self.entity_description.turn_on_data
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off switch."""
        self._attr_is_on = False
        profile = self.device.profiles[0]
        await self.entity_description.turn_off_fn(self.device)(
            profile, self.entity_description.turn_off_data
        )


class ThinginoRelaySwitch(ONVIFBaseEntity, SwitchEntity):
    """Switch for a Thingino relay."""

    _attr_has_entity_name = True

    def __init__(self, device: ONVIFDevice, relay: ThinginoRelay) -> None:
        """Initialize the relay switch."""
        super().__init__(device)
        self.relay = relay
        self._attr_unique_id = f"{self.mac_or_serial}_thingino_relay_{relay.index}"
        self._attr_name = relay.name
        self._attr_icon = relay.icon
        self._attr_is_on = self._idle_state_to_bool(relay.idle_state)

    @staticmethod
    def _idle_state_to_bool(state: str | None) -> bool | None:
        if not state:
            return None
        normalized = state.strip().lower()
        if normalized in ("open", "opened", "on", "true", "1"):
            return True
        if normalized in ("close", "closed", "off", "false", "0"):
            return False
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Open the relay."""
        if self.relay.via_onvif and self.relay.token:
            await self.device.async_set_relay_output_state(self.relay.token, True)
        elif self.relay.open:
            await self.device.async_thingino_exec(self.relay.open)
        self._attr_is_on = True

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Close the relay."""
        if self.relay.via_onvif and self.relay.token:
            await self.device.async_set_relay_output_state(self.relay.token, False)
        elif self.relay.close:
            await self.device.async_thingino_exec(self.relay.close)
        self._attr_is_on = False


class ThinginoAuxToggleSwitch(ONVIFBaseEntity, SwitchEntity):
    """Switch for a Thingino auxiliary toggle."""

    _attr_has_entity_name = True

    def __init__(self, device: ONVIFDevice, toggle: ThinginoToggle) -> None:
        """Initialize the aux toggle switch."""
        super().__init__(device)
        self.toggle = toggle
        slug = slugify(toggle.name)
        self._attr_unique_id = f"{self.mac_or_serial}_thingino_aux_{slug}"
        self._attr_name = toggle.name
        self._attr_icon = toggle.icon

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Execute the ON command."""
        await self.device.async_thingino_exec(self.toggle.on_exec)
        self._attr_is_on = True

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Execute the OFF command."""
        await self.device.async_thingino_exec(self.toggle.off_exec)
        self._attr_is_on = False

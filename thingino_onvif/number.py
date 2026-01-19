"""ONVIF number entities."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .device import ONVIFDevice
from .entity import ONVIFBaseEntity
from .models import Profile


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ONVIF number entities."""
    device = hass.data[DOMAIN][config_entry.unique_id]
    entities: list[NumberEntity] = []

    if device.capabilities.ptz:
        for profile in device.profiles:
            entities.append(ONVIFRelativeDistanceNumber(device, profile))
            entities.append(ONVIFRelativeSpeedNumber(device, profile))
            entities.append(ONVIFAbsolutePanNumber(device, profile))
            entities.append(ONVIFAbsoluteTiltNumber(device, profile))
            entities.append(ONVIFAbsoluteSpeedNumber(device, profile))

    async_add_entities(entities)


class ONVIFNumberBase(ONVIFBaseEntity, NumberEntity, RestoreEntity):
    """Base class for ONVIF numbers."""

    _attr_has_entity_name = True

    def __init__(self, device: ONVIFDevice, profile: Profile) -> None:
        """Initialize the number entity."""
        super().__init__(device)
        self.profile = profile
        self._profile_suffix = (
            f" ({profile.name})" if len(device.profiles) > 1 and profile.name else ""
        )

    async def async_added_to_hass(self) -> None:
        """Restore previous value."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state and state.state not in ("unknown", "unavailable"):
            try:
                value = float(state.state)
            except ValueError:
                return
            self._attr_native_value = value
            self._apply_restored_value(value)

    def _apply_restored_value(self, value: float) -> None:
        """Apply a restored value to the device cache."""
        return


class ONVIFRelativeDistanceNumber(ONVIFNumberBase):
    """Number entity for relative move distance."""

    _attr_icon = "mdi:arrow-expand"
    _attr_native_min_value = 0.01
    _attr_native_max_value = 1.0
    _attr_native_step = 0.01

    def __init__(self, device: ONVIFDevice, profile: Profile) -> None:
        """Initialize the relative distance number."""
        super().__init__(device, profile)
        self._attr_name = f"{self.device.name} PTZ Move Distance{self._profile_suffix}"
        self._attr_unique_id = f"{self.mac_or_serial}#{profile.token}_ptz_distance"
        self._attr_native_value = self.device.get_relative_distance(profile)

    async def async_set_native_value(self, value: float) -> None:
        """Update the relative distance value."""
        self._attr_native_value = value
        self.device.set_relative_distance(self.profile, value)

    def _apply_restored_value(self, value: float) -> None:
        """Apply restored relative distance."""
        self.device.set_relative_distance(self.profile, value)


class ONVIFRelativeSpeedNumber(ONVIFNumberBase):
    """Number entity for relative move speed."""

    _attr_icon = "mdi:speedometer"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 1.0
    _attr_native_step = 0.01

    def __init__(self, device: ONVIFDevice, profile: Profile) -> None:
        """Initialize the relative speed number."""
        super().__init__(device, profile)
        self._attr_name = f"{self.device.name} PTZ Move Speed{self._profile_suffix}"
        self._attr_unique_id = f"{self.mac_or_serial}#{profile.token}_ptz_speed"
        self._attr_native_value = self.device.get_relative_speed_value(profile)

    async def async_set_native_value(self, value: float) -> None:
        """Update the relative speed value."""
        self._attr_native_value = value
        self.device.set_relative_speed(self.profile, value)

    def _apply_restored_value(self, value: float) -> None:
        """Apply restored relative speed."""
        self.device.set_relative_speed(self.profile, value)


class ONVIFAbsolutePanNumber(ONVIFNumberBase):
    """Number entity for absolute pan steps."""

    _attr_icon = "mdi:axis-x-rotate-clockwise"
    _attr_native_step = 1.0

    def __init__(self, device: ONVIFDevice, profile: Profile) -> None:
        """Initialize the absolute pan number."""
        super().__init__(device, profile)
        limits = profile.ptz_limits
        self._attr_name = f"{self.device.name} PTZ Pan Steps{self._profile_suffix}"
        self._attr_unique_id = f"{self.mac_or_serial}#{profile.token}_ptz_pan_steps"
        self._attr_native_min_value = (
            float(limits.pan_min) if limits and limits.pan_min is not None else 0.0
        )
        self._attr_native_max_value = (
            float(limits.pan_max) if limits and limits.pan_max is not None else 1.0
        )
        self._attr_native_value = self.device.get_absolute_pan(profile)

    async def async_set_native_value(self, value: float) -> None:
        """Update the pan steps."""
        self._attr_native_value = value
        self.device.set_absolute_pan(self.profile, value)

    def _apply_restored_value(self, value: float) -> None:
        """Apply restored pan steps."""
        self.device.set_absolute_pan(self.profile, value)


class ONVIFAbsoluteTiltNumber(ONVIFNumberBase):
    """Number entity for absolute tilt steps."""

    _attr_icon = "mdi:axis-z-rotate-clockwise"
    _attr_native_step = 1.0

    def __init__(self, device: ONVIFDevice, profile: Profile) -> None:
        """Initialize the absolute tilt number."""
        super().__init__(device, profile)
        limits = profile.ptz_limits
        self._attr_name = f"{self.device.name} PTZ Tilt Steps{self._profile_suffix}"
        self._attr_unique_id = f"{self.mac_or_serial}#{profile.token}_ptz_tilt_steps"
        self._attr_native_min_value = (
            float(limits.tilt_min) if limits and limits.tilt_min is not None else 0.0
        )
        self._attr_native_max_value = (
            float(limits.tilt_max) if limits and limits.tilt_max is not None else 1.0
        )
        self._attr_native_value = self.device.get_absolute_tilt(profile)

    async def async_set_native_value(self, value: float) -> None:
        """Update the tilt steps."""
        self._attr_native_value = value
        self.device.set_absolute_tilt(self.profile, value)

    def _apply_restored_value(self, value: float) -> None:
        """Apply restored tilt steps."""
        self.device.set_absolute_tilt(self.profile, value)


class ONVIFAbsoluteSpeedNumber(ONVIFNumberBase):
    """Number entity for absolute move speed."""

    _attr_icon = "mdi:speedometer"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 1.0
    _attr_native_step = 0.01

    def __init__(self, device: ONVIFDevice, profile: Profile) -> None:
        """Initialize the absolute speed number."""
        super().__init__(device, profile)
        self._attr_name = f"{self.device.name} PTZ Absolute Speed{self._profile_suffix}"
        self._attr_unique_id = f"{self.mac_or_serial}#{profile.token}_ptz_abs_speed"
        self._attr_native_value = self.device.get_absolute_speed_value(profile)

    async def async_set_native_value(self, value: float) -> None:
        """Update the absolute speed value."""
        self._attr_native_value = value
        self.device.set_absolute_speed(self.profile, value)

    def _apply_restored_value(self, value: float) -> None:
        """Apply restored absolute speed."""
        self.device.set_absolute_speed(self.profile, value)

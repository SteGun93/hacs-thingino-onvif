"""ONVIF Buttons."""

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from .const import DOMAIN, RELATIVE_MOVE, STOP_MOVE
from .device import ONVIFDevice
from .entity import ONVIFBaseEntity
from .models import Profile, ThinginoAuxCommand


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ONVIF button based on a config entry."""
    device = hass.data[DOMAIN][config_entry.unique_id]
    entities: list[ButtonEntity] = [
        RebootButton(device),
        SetSystemDateAndTimeButton(device),
    ]
    if device.capabilities.ptz:
        entities += [
            GotoHomeButton(device),
            SetHomeButton(device),
        ]
        for profile in device.profiles:
            entities.append(ONVIFPresetGotoSelectedButton(device, profile))
            entities.append(ONVIFAbsoluteMoveButton(device, profile))
            entities.append(ONVIFRelativeMoveButton(device, profile, pan="LEFT"))
            entities.append(ONVIFRelativeMoveButton(device, profile, pan="RIGHT"))
            entities.append(ONVIFRelativeMoveButton(device, profile, tilt="UP"))
            entities.append(ONVIFRelativeMoveButton(device, profile, tilt="DOWN"))
            entities.append(ONVIFRelativeMoveButton(device, profile, zoom="ZOOM_IN"))
            entities.append(ONVIFRelativeMoveButton(device, profile, zoom="ZOOM_OUT"))
            entities.append(ONVIFStopMoveButton(device, profile))
    entities += [
        ThinginoAuxButton(device, command) for command in device.thingino_aux_commands
    ]
    async_add_entities(entities)


class RebootButton(ONVIFBaseEntity, ButtonEntity):
    """Defines a ONVIF reboot button."""

    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, device: ONVIFDevice) -> None:
        """Initialize the button entity."""
        super().__init__(device)
        self._attr_name = f"{self.device.name} Reboot"
        self._attr_unique_id = f"{self.mac_or_serial}_reboot"

    async def async_press(self) -> None:
        """Send out a SystemReboot command."""
        device_mgmt = await self.device.device.create_devicemgmt_service()
        await device_mgmt.SystemReboot()


class SetSystemDateAndTimeButton(ONVIFBaseEntity, ButtonEntity):
    """Defines a ONVIF SetSystemDateAndTime button."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, device: ONVIFDevice) -> None:
        """Initialize the button entity."""
        super().__init__(device)
        self._attr_name = f"{self.device.name} Set System Date and Time"
        self._attr_unique_id = f"{self.mac_or_serial}_setsystemdatetime"

    async def async_press(self) -> None:
        """Send out a SetSystemDateAndTime command."""
        await self.device.async_manually_set_date_and_time()


class GotoHomeButton(ONVIFBaseEntity, ButtonEntity):
    """Defines a ONVIF GotoHomePosition button."""

    def __init__(self, device: ONVIFDevice) -> None:
        """Initialize the button entity."""
        super().__init__(device)
        self._attr_name = f"{self.device.name} Home"
        self._attr_unique_id = f"{self.mac_or_serial}_ptz_home"

    async def async_press(self) -> None:
        """Send out a GotoHomePosition command."""
        await self.device.async_goto_home(self.device.profiles[0])


class SetHomeButton(ONVIFBaseEntity, ButtonEntity):
    """Defines a ONVIF SetHomePosition button."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, device: ONVIFDevice) -> None:
        """Initialize the button entity."""
        super().__init__(device)
        self._attr_name = f"{self.device.name} Set Home"
        self._attr_unique_id = f"{self.mac_or_serial}_ptz_set_home"

    async def async_press(self) -> None:
        """Send out a SetHomePosition command."""
        await self.device.async_set_home(self.device.profiles[0])


class ThinginoAuxButton(ONVIFBaseEntity, ButtonEntity):
    """Defines a Thingino auxiliary command button."""

    def __init__(self, device: ONVIFDevice, command: ThinginoAuxCommand) -> None:
        """Initialize the button entity."""
        super().__init__(device)
        self.command = command
        slug = slugify(command.name)
        self._attr_name = command.name
        self._attr_unique_id = f"{self.mac_or_serial}_thingino_aux_{slug}"
        self._attr_icon = command.icon

    async def async_press(self) -> None:
        """Execute the command."""
        await self.device.async_thingino_exec(self.command.exec)


class ONVIFPresetActionBase(ONVIFBaseEntity, ButtonEntity):
    """Base class for preset action buttons."""

    def __init__(self, device: ONVIFDevice, profile: Profile) -> None:
        """Initialize the preset action."""
        super().__init__(device)
        self.profile = profile
        profile_suffix = (
            f" ({profile.name})" if len(device.profiles) > 1 and profile.name else ""
        )
        self._profile_suffix = profile_suffix

    def _selected_token(self) -> str | None:
        """Return the selected preset token."""
        return self.device.get_selected_preset(self.profile)

    def _selected_name(self, token: str | None) -> str | None:
        """Return a friendly name for the selected token."""
        if not token:
            return None
        return self.device.get_preset_name(self.profile, token) or token


class ONVIFPresetGotoSelectedButton(ONVIFPresetActionBase):
    """Button to go to the selected preset."""

    _attr_icon = "mdi:map-marker"

    def __init__(self, device: ONVIFDevice, profile: Profile) -> None:
        """Initialize the goto preset button."""
        super().__init__(device, profile)
        self._attr_name = f"{self.device.name} Go to Preset{self._profile_suffix}"
        self._attr_unique_id = f"{self.mac_or_serial}#{profile.token}_preset_goto"

    async def async_press(self) -> None:
        """Go to the selected preset."""
        token = self._selected_token()
        if not token:
            return
        await self.device.async_goto_preset(self.profile, token)


class ONVIFAbsoluteMoveButton(ONVIFPresetActionBase):
    """Button to run an absolute move using stored steps."""

    _attr_icon = "mdi:axis-arrow"

    def __init__(self, device: ONVIFDevice, profile: Profile) -> None:
        """Initialize the absolute move button."""
        super().__init__(device, profile)
        self._attr_name = (
            f"{self.device.name} Absolute Move{self._profile_suffix}"
        )
        self._attr_unique_id = f"{self.mac_or_serial}#{profile.token}_ptz_abs_move"

    async def async_press(self) -> None:
        """Run an absolute move using stored steps."""
        pan = self.device.get_absolute_pan(self.profile)
        tilt = self.device.get_absolute_tilt(self.profile)
        speed = self.device.get_absolute_speed(self.profile)
        await self.device.async_absolute_move_steps(self.profile, pan, tilt, speed, None)


class ONVIFRelativeMoveButton(ONVIFPresetActionBase):
    """Button for a relative move direction."""

    _attr_icon = "mdi:arrow-up"

    def __init__(
        self,
        device: ONVIFDevice,
        profile: Profile,
        *,
        pan: str | None = None,
        tilt: str | None = None,
        zoom: str | None = None,
    ) -> None:
        """Initialize the relative move button."""
        super().__init__(device, profile)
        self._pan = pan
        self._tilt = tilt
        self._zoom = zoom
        label = pan or tilt or zoom or "Move"
        label = label.replace("ZOOM_", "Zoom ").title()
        self._attr_name = f"{self.device.name} PTZ {label}{self._profile_suffix}"
        self._attr_unique_id = (
            f"{self.mac_or_serial}#{profile.token}_ptz_rel_{label.replace(' ', '_').lower()}"
        )
        if pan == "LEFT":
            self._attr_icon = "mdi:arrow-left"
        elif pan == "RIGHT":
            self._attr_icon = "mdi:arrow-right"
        elif tilt == "UP":
            self._attr_icon = "mdi:arrow-up"
        elif tilt == "DOWN":
            self._attr_icon = "mdi:arrow-down"
        elif zoom == "ZOOM_IN":
            self._attr_icon = "mdi:magnify-plus"
        elif zoom == "ZOOM_OUT":
            self._attr_icon = "mdi:magnify-minus"

    async def async_press(self) -> None:
        """Perform a relative move."""
        distance = self.device.get_relative_distance(self.profile)
        speed = self.device.get_relative_speed(self.profile)
        await self.device.async_perform_ptz(
            self.profile,
            distance,
            speed,
            RELATIVE_MOVE,
            0,
            None,
            self._pan,
            self._tilt,
            self._zoom,
        )


class ONVIFStopMoveButton(ONVIFPresetActionBase):
    """Button to stop PTZ motion."""

    _attr_icon = "mdi:stop-circle"

    def __init__(self, device: ONVIFDevice, profile: Profile) -> None:
        """Initialize the stop button."""
        super().__init__(device, profile)
        self._attr_name = f"{self.device.name} PTZ Stop{self._profile_suffix}"
        self._attr_unique_id = f"{self.mac_or_serial}#{profile.token}_ptz_stop"

    async def async_press(self) -> None:
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

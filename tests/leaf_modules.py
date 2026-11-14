"""Load helper modules only; these shims do not emulate a Home Assistant runtime."""

from contextlib import contextmanager
from enum import StrEnum
import importlib
from pathlib import Path
import sys
from types import ModuleType
from unittest.mock import Mock, patch


@contextmanager
def helper_modules():
    package = ModuleType("thingino_onvif")
    package.__path__ = [str(Path(__file__).resolve().parents[1] / "thingino_onvif")]
    ha = ModuleType("homeassistant")
    ha.__path__ = []
    core = ModuleType("homeassistant.core")
    core.HomeAssistant = object
    const = ModuleType("homeassistant.const")
    const.EntityCategory = StrEnum("EntityCategory", {"CONFIG": "config", "DIAGNOSTIC": "diagnostic"})
    helpers = ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    client = ModuleType("homeassistant.helpers.aiohttp_client")
    client.async_get_clientsession = Mock(side_effect=AssertionError("HTTP session must be mocked"))
    modules = {
        "thingino_onvif": package,
        "homeassistant": ha,
        "homeassistant.core": core,
        "homeassistant.const": const,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.aiohttp_client": client,
    }
    with patch.dict(sys.modules, modules):
        yield (
            importlib.import_module("thingino_onvif.thingino_http"),
            importlib.import_module("thingino_onvif.util"),
            importlib.import_module("thingino_onvif.models"),
        )

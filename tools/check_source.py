"""Check source and integration metadata without importing Home Assistant."""

import ast
import json
from importlib.metadata import version
from pathlib import Path

import yaml

root = Path(__file__).resolve().parents[1]
integration = root / "thingino_onvif"
for path in sorted(integration.rglob("*.py")):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
for path in sorted(integration.rglob("*.json")):
    json.loads(path.read_text(encoding="utf-8"))
manifest = json.loads((integration / "manifest.json").read_text(encoding="utf-8"))
assert manifest["domain"] == integration.name
assert manifest["version"] and manifest["config_flow"] is True
for requirement in manifest["requirements"]:
    name, expected_version = requirement.split("==")
    assert version(name) == expected_version, requirement
services = yaml.safe_load((integration / "services.yaml").read_text(encoding="utf-8"))
assert isinstance(services, dict) and services
for name, service in services.items():
    assert isinstance(name, str) and isinstance(service, dict), name
    assert service["target"]["entity"]["integration"] == manifest["domain"], name
print("Python syntax, JSON metadata, service YAML and domain: OK")

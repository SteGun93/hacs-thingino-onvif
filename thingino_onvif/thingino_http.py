"""Thingino HTTP helpers."""

from __future__ import annotations

import asyncio
import html
import json
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import LOGGER

_PRE_BLOCK_RE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.IGNORECASE | re.DOTALL)


def build_thingino_url(host: str, port: int, endpoint: str) -> str:
    """Build a Thingino URL from host/port and an endpoint."""
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint
    if not endpoint.startswith("/"):
        endpoint = f"/{endpoint}"
    return f"http://{host}:{port}{endpoint}"


def redact_url(value: str) -> str:
    """Remove credentials from a URL for logging."""
    parts = urlsplit(value)
    if not parts.username and not parts.password:
        return value
    hostname = parts.hostname or ""
    if parts.port:
        hostname = f"{hostname}:{parts.port}"
    return urlunsplit((parts.scheme, hostname, parts.path, parts.query, parts.fragment))


def parse_thingino_onvif_html(text: str) -> dict[str, Any] | None:
    """Parse Thingino onvif.json data from HTML."""
    match = _PRE_BLOCK_RE.search(text)
    if not match:
        return None
    payload = html.unescape(match.group(1)).strip()
    if not payload:
        return None
    if not payload.lstrip().startswith("{"):
        payload = "{" + payload.strip().strip(",") + "}"
    return json.loads(payload)


def parse_thingino_onvif_payload(text: str) -> dict[str, Any] | None:
    """Parse Thingino onvif.json data from response payload."""
    payload = text.strip()
    if payload.startswith("{") or payload.startswith("["):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            data = None
        else:
            return data if isinstance(data, dict) else None
    return parse_thingino_onvif_html(text)


async def async_fetch_thingino_onvif_json(
    hass: HomeAssistant,
    host: str,
    port: int,
    endpoint: str,
    username: str | None,
    password: str | None,
    retries: int = 1,
) -> tuple[dict[str, Any] | None, int | None]:
    """Fetch and parse Thingino onvif.json from HTTP."""
    url = build_thingino_url(host, port, endpoint)
    safe_url = redact_url(url)
    session = async_get_clientsession(hass)
    auth = aiohttp.BasicAuth(username, password) if username else None

    last_status: int | None = None
    for attempt in range(retries + 1):
        try:
            async with session.get(
                url,
                auth=auth,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as response:
                last_status = response.status
                content_type = response.headers.get("Content-Type", "")
                body = await response.text()
                length = response.content_length or len(body)
                LOGGER.debug(
                    "Thingino HTTP %s status=%s content-type=%s length=%s",
                    safe_url,
                    response.status,
                    content_type,
                    length,
                )
                if response.status == 401:
                    return None, 401
                if response.status != 200:
                    return None, response.status
                try:
                    data = parse_thingino_onvif_payload(body)
                except json.JSONDecodeError as err:
                    LOGGER.debug(
                        "Thingino HTTP parse failed from %s: %s", safe_url, err
                    )
                    return None, response.status
                if not isinstance(data, dict):
                    LOGGER.debug(
                        "Thingino HTTP payload from %s is not a JSON object", safe_url
                    )
                    return None, response.status
                LOGGER.debug("Thingino HTTP parsed onvif.json from %s", safe_url)
                return data, response.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            LOGGER.debug(
                "Thingino HTTP request failed (%s/%s) for %s: %s",
                attempt + 1,
                retries + 1,
                safe_url,
                err,
            )
            if attempt >= retries:
                break
            await asyncio.sleep(0.2 * (attempt + 1))

    return None, last_status

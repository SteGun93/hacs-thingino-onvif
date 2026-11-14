import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from zeep.exceptions import Fault

from leaf_modules import helper_modules


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.http, self.util, self.models = self.enterContext(helper_modules())
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("No network in tests")))

    def test_json_and_escaped_html_payloads(self):
        expected = {"switch": {"name": "IR LED", "enabled": True}}
        self.assertEqual(self.http.parse_thingino_onvif_payload(json.dumps(expected)), expected)
        html = '<pre>&quot;switch&quot;: {&quot;name&quot;: &quot;IR LED&quot;, &quot;enabled&quot;: true},</pre>'
        self.assertEqual(self.http.parse_thingino_onvif_payload(html), expected)

    def test_non_object_and_empty_payloads(self):
        for body in ("[]", "", "<html>no config</html>"):
            with self.subTest(body=body):
                self.assertIsNone(self.http.parse_thingino_onvif_payload(body))

    def test_url_construction_and_credential_redaction(self):
        self.assertEqual(self.http.build_thingino_url("camera.test", 8080, "onvif.json"),
                         "http://camera.test:8080/onvif.json")
        self.assertEqual(self.http.redact_url("http://fixture:dummy@camera.test:8080/onvif.json"),
                         "http://camera.test:8080/onvif.json")

    def test_auth_fault_and_label_helpers(self):
        self.assertTrue(self.util.is_auth_error(Fault("Denied", subcodes=["NotAuthorized"])))
        self.assertFalse(self.util.is_auth_error(ValueError("authentication problem")))
        self.assertEqual(self.util.format_thingino_label("ir_led"), "IR LED")
        self.assertEqual(self.util.thingino_icon_for_label("IR-Cut"), "mdi:camera-iris")
        self.assertIsNone(self.util.thingino_icon_for_label("unmapped"))

    def test_event_names_are_stable_across_discovery_order(self):
        events = [SimpleNamespace(uid="b", name="Motion"), SimpleNamespace(uid="a", name="Motion")]
        expected = {"a": "Motion 1", "b": "Motion 2"}
        self.assertEqual(self.util.build_event_entity_names(events), expected)
        self.assertEqual(self.util.build_event_entity_names(list(reversed(events))), expected)


class HttpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.http, _, _ = self.enterContext(helper_modules())
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("No network in tests")))

    def session(self, status, body):
        response = MagicMock(status=status, headers={"Content-Type": "application/json"}, content_length=None)
        response.text = AsyncMock(return_value=body)
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.get.return_value = context
        self.enterContext(patch.object(self.http, "async_get_clientsession", return_value=session))
        return session

    async def test_success_uses_basic_auth_and_five_second_timeout(self):
        session = self.session(200, '{"enabled":true}')
        self.assertEqual(await self.http.async_fetch_thingino_onvif_json(
            object(), "camera.test", 80, "/onvif.json", "fixture", "dummy"), ({"enabled": True}, 200))
        self.assertEqual(session.get.call_args.kwargs["auth"], aiohttp.BasicAuth("fixture", "dummy"))
        self.assertEqual(session.get.call_args.kwargs["timeout"].total, 5)

    async def test_unauthorized_response_is_not_retried(self):
        session = self.session(401, "")
        self.assertEqual(await self.http.async_fetch_thingino_onvif_json(
            object(), "camera.test", 80, "/", None, None, retries=3), (None, 401))
        session.get.assert_called_once()

    async def test_invalid_json_returns_no_data(self):
        self.session(200, "<pre>{invalid}</pre>")
        self.assertEqual(await self.http.async_fetch_thingino_onvif_json(
            object(), "camera.test", 80, "/", None, None), (None, 200))

    async def test_connection_errors_have_a_bounded_retry(self):
        session = self.session(200, "{}")
        session.get.side_effect = aiohttp.ClientConnectionError("fixture failure")
        sleep = self.enterContext(patch.object(self.http.asyncio, "sleep", new_callable=AsyncMock))
        self.assertEqual(await self.http.async_fetch_thingino_onvif_json(
            object(), "camera.test", 80, "/", None, None, retries=1), (None, None))
        self.assertEqual(session.get.call_count, 2)
        sleep.assert_awaited_once_with(0.2)

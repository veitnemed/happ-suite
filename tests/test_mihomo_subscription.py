import logging
from pathlib import Path
import tempfile
import unittest

import requests

from src.mihomo_subscription import SubscriptionClient, load_or_create_hwid
from src.vpn_backend import ProviderFormatError, SubscriptionError

URL = "https://sub.example.test/private-key"
YAML = bytes.fromhex("70726f786965733a0a20202d206e616d653a206e6f64652d6f6e650a20202020747970653a20736f636b73350a202020207365727665723a203132372e302e302e310a20202020706f72743a20313038300a")

class Response:
    def __init__(self, body=b"", status=200, headers=None, url=URL):
        self.status_code = status
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.url, self.body = url, body
    def iter_content(self, size): yield self.body
    def close(self): pass

class Session:
    def __init__(self, *responses): self.responses, self.calls = list(responses), []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException): raise response
        return response

class SubscriptionClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()
    def client(self, session): return SubscriptionClient(self.directory, session=session)

    def test_mihomo_ua_hwid_and_yaml_nodes(self):
        session = Session(Response(YAML, headers={
            "content-type": "application/x-yaml", "x-hwid-active": "true",
            "profile-update-interval": "6",
        }))
        result = self.client(session).fetch(URL)
        headers = session.calls[0][1]["headers"]
        self.assertEqual(result.nodes[0]["name"], "node-one")
        self.assertTrue(result.hwid_active)
        self.assertEqual(result.update_interval_hours, 6)
        self.assertTrue(headers["User-Agent"].startswith("mihomo/HappSuite-"))
        self.assertEqual(headers["Accept"], "application/yaml, text/yaml, */*")
        self.assertNotIn("text/html", headers["Accept"])
        self.assertEqual(headers["x-device-os"], "Windows")
        self.assertRegex(headers["x-hwid"], r"^[A-Za-z0-9=-]{10,64}$")

    def test_browser_html_then_explicit_mihomo_endpoint(self):
        session = Session(Response(b"<html>page</html>", headers={"content-type": "text/html"}),
                          Response(YAML, headers={"content-type": "application/x-yaml"}))
        result = self.client(session).fetch(URL)
        self.assertTrue(result.used_mihomo_suffix)
        self.assertEqual(session.calls[1][0], URL + "/mihomo")

    def test_html_from_endpoint_is_controlled_error(self):
        session = Session(Response(b"<html/>", headers={"content-type": "text/html"}),
                          Response(b"<html/>", headers={"content-type": "text/html"}))
        with self.assertRaisesRegex(ProviderFormatError, "веб-страницу"):
            self.client(session).fetch(URL)

    def test_hwid_required_and_device_limit_headers(self):
        session = Session(Response(status=404, headers={"x-hwid-not-supported": "true"}))
        with self.assertRaisesRegex(SubscriptionError, "идентификатор устройства"):
            self.client(session).fetch(URL)
        self.assertIn("x-hwid", session.calls[0][1]["headers"])
        session = Session(Response(status=403, headers={"x-hwid-max-devices-reached": "true"}))
        with self.assertRaisesRegex(SubscriptionError, "лимит устройств"):
            self.client(session).fetch(URL)
        session = Session(Response(status=403, headers={"x-hwid-limit": "true"}))
        with self.assertRaisesRegex(SubscriptionError, "лимит устройств"):
            self.client(session).fetch(URL)

    def test_hwid_is_random_persistent_and_reused(self):
        first = load_or_create_hwid(self.directory)
        self.assertEqual(first, load_or_create_hwid(self.directory))
        self.assertRegex(first, r"^[a-f0-9]{40}$")

    def test_url_never_appears_in_exceptions_or_logs(self):
        logger = logging.getLogger("subscription-test")
        with self.assertLogs(logger, level="INFO") as logs:
            with self.assertRaises(SubscriptionError) as error:
                self.client(Session(requests.Timeout(URL))).fetch(URL)
            logger.info("subscription request failed")
        self.assertNotIn(URL, str(error.exception))
        self.assertNotIn(URL, "\n".join(logs.output))

    def test_invalid_yaml_is_rejected(self):
        with self.assertRaisesRegex(ProviderFormatError, "Некорректная конфигурация Mihomo"):
            self.client(Session(Response(b"proxies: [broken"))).fetch(URL)

    def test_https_redirect_is_followed(self):
        session = Session(Response(status=302, headers={"location": "/v2/config"}),
                          Response(YAML, url="https://sub.example.test/v2/config"))
        self.assertEqual(len(self.client(session).fetch(URL).nodes), 1)
        self.assertEqual(session.calls[1][0], "https://sub.example.test/v2/config")

    def test_timeout_is_sanitized(self):
        with self.assertRaises(SubscriptionError) as error:
            self.client(Session(requests.ReadTimeout(URL))).fetch(URL)
        self.assertNotIn(URL, str(error.exception))

    def test_403_404_and_expired_userinfo(self):
        for status in (403, 404):
            with self.subTest(status=status):
                with self.assertRaisesRegex(SubscriptionError, "недоступна или истекла"):
                    self.client(Session(Response(status=status))).fetch(URL)
        response = Response(YAML, headers={"subscription-userinfo": "upload=0; expire=1"})
        with self.assertRaisesRegex(SubscriptionError, "Подписка истекла"):
            self.client(Session(response)).fetch(URL)

if __name__ == "__main__": unittest.main()

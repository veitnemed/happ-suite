import unittest
from unittest.mock import Mock

import requests

from src.gemini_availability import (
    GeminiAvailabilityClient, RegionSupport, classify_region, load_region_snapshot,
)


class _Response:
    def __init__(self, status=200, text=""):
        self.status_code = status
        self.text = text


class _Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.trust_env = True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, _url, timeout):
        self.timeout = timeout
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def _session_factory(responses):
    pending = iter(responses)
    return lambda: _Session([next(pending)])


class GeminiAvailabilityTests(unittest.TestCase):
    def test_http_200_means_website_reachable_and_account_unknown(self):
        client = GeminiAvailabilityClient(
            session_factory=_session_factory([
                _Response(204), _Response(200), _Response(200, "fl=1\nloc=FI\nip=203.0.113.4\n"),
            ]), dns_status=lambda: {"managed": True},
        )
        result = client.check()
        self.assertTrue(result.network_available)
        self.assertTrue(result.website_reachable)
        self.assertEqual(result.website_http_status, 200)
        self.assertTrue(result.dns_managed)
        self.assertEqual(result.exit_country, "FI")
        self.assertEqual(result.external_ip, "203.0.113.4")
        self.assertEqual(result.region_supported, RegionSupport.SUPPORTED)
        self.assertEqual(result.account_status, "UNKNOWN")

    def test_http_error_response_is_still_website_reachability_not_account_access(self):
        client = GeminiAvailabilityClient(session_factory=_session_factory([
            _Response(204), _Response(403), _Response(200, "loc=RU\n"),
        ]))
        result = client.check()
        self.assertTrue(result.website_reachable)
        self.assertEqual(result.website_http_status, 403)
        self.assertEqual(result.region_supported, RegionSupport.UNSUPPORTED)
        self.assertEqual(result.account_status, "UNKNOWN")

    def test_timeout_marks_network_and_website_unavailable(self):
        client = GeminiAvailabilityClient(session_factory=_session_factory([
            requests.Timeout(), requests.Timeout(), requests.Timeout(),
        ]))
        result = client.check()
        self.assertFalse(result.network_available)
        self.assertFalse(result.website_reachable)
        self.assertIsNone(result.website_http_status)
        self.assertIsNone(result.exit_country)
        self.assertEqual(result.account_status, "UNKNOWN")

    def test_dns_failure_does_not_claim_website_reachable(self):
        failure = requests.ConnectionError("DNS lookup failed")
        client = GeminiAvailabilityClient(session_factory=_session_factory([
            failure, failure, failure,
        ]), dns_status=lambda: {"managed": False})
        result = client.check()
        self.assertFalse(result.network_available)
        self.assertFalse(result.website_reachable)
        self.assertFalse(result.dns_managed)

    def test_region_status_has_supported_unsupported_and_unknown_states(self):
        snapshot = {
            "source": "official", "checked_at": "2026-09-29",
            "supported_country_codes": ["FI"], "known_country_codes": ["FI", "RU"],
        }
        self.assertEqual(classify_region("fi", snapshot), RegionSupport.SUPPORTED)
        self.assertEqual(classify_region("RU", snapshot), RegionSupport.UNSUPPORTED)
        self.assertEqual(classify_region("ZZ", snapshot), RegionSupport.UNKNOWN)
        self.assertEqual(classify_region(None, snapshot), RegionSupport.UNKNOWN)

    def test_region_snapshot_is_versioned_and_includes_common_supported_and_unsupported(self):
        snapshot = load_region_snapshot()
        self.assertIn("support.google.com/gemini/answer/13575153", snapshot["source"])
        self.assertRegex(snapshot["checked_at"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertIn("FI", snapshot["supported_country_codes"])
        self.assertNotIn("RU", snapshot["supported_country_codes"])
        self.assertIn("RU", snapshot["known_country_codes"])

    def test_dns_status_error_is_unknown_and_does_not_break_other_diagnostics(self):
        client = GeminiAvailabilityClient(
            session_factory=_session_factory([
                _Response(204), _Response(200), _Response(200, "loc=FI\n"),
            ]), dns_status=Mock(side_effect=OSError("unavailable")),
        )
        result = client.check()
        self.assertTrue(result.website_reachable)
        self.assertIsNone(result.dns_managed)
        self.assertEqual(result.account_status, "UNKNOWN")

    def test_invalid_exit_ip_is_not_rendered(self):
        client = GeminiAvailabilityClient(session_factory=_session_factory([
            _Response(204), _Response(200), _Response(200, "loc=FI\nip=not-an-ip\n"),
        ]))
        result = client.check()
        self.assertEqual(result.exit_country, "FI")
        self.assertIsNone(result.external_ip)


if __name__ == "__main__":
    unittest.main()

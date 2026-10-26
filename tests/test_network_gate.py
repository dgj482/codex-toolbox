"""Offline verification of startup decisions and the caller deadline."""
import threading
import time
import unittest
from unittest.mock import patch
import network_gate as gate


class NetworkGateCase(unittest.TestCase):
    def setUp(self):
        gate.reset_cache()

    def inspect(self, enabled=True, reachable=True, tun=False, ip="203.0.113.1"):
        with patch.object(gate, "_system_proxy", return_value=(enabled, "127.0.0.1:7890")), \
                patch.object(gate, "_tcp_ok", return_value=reachable), \
                patch.object(gate, "_tun_active", return_value=tun), \
                patch.object(gate, "_probe_ip", return_value={"exit_ip": ip} if ip else {}) as probe:
            return gate.check(force=True), probe

    def test_enabled_and_working_is_silent(self):
        result, probe = self.inspect()
        self.assertTrue(result["ok"])
        self.assertEqual(gate.warning_text(result), "")
        probe.assert_called_once_with("http://127.0.0.1:7890")

    def test_disabled_warns_without_external_request(self):
        result, probe = self.inspect(enabled=False)
        self.assertEqual(result["issues"][0]["code"], "clash_off")
        probe.assert_not_called()

    def test_unreachable_warns(self):
        result, _ = self.inspect(reachable=False)
        self.assertEqual(result["issues"][0]["code"], "proxy_unreachable")

    def test_tun_counts_as_enabled_without_system_proxy(self):
        result, probe = self.inspect(enabled=False, tun=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "tun")
        probe.assert_called_once_with("")

    def test_network_failure_is_not_reported_as_disabled(self):
        result, _ = self.inspect(ip="")
        self.assertEqual(result["issues"][0]["code"], "network_unknown")

    def test_normalizes_country_and_timezone_for_both_services(self):
        primary = gate._normalize_exit({"ip":"203.0.113.1", "country":"Japan", "country_code":"JP", "timezone":{"id":"Asia/Tokyo", "utc":"+09:00"}})
        self.assertEqual(primary["country"], "JP")
        self.assertEqual(primary["ip_timezone"], "Asia/Tokyo")
        fallback = gate._normalize_exit({"ip":"203.0.113.2", "country":"US", "timezone":"America/New_York"})
        self.assertEqual(fallback["ip_timezone"], "America/New_York")

    def test_location_is_informational_and_never_warns(self):
        location = {"exit_ip":"203.0.113.1", "country":"JP", "ip_timezone":"Asia/Tokyo"}
        with patch.object(gate,"_system_proxy",return_value=(True,"127.0.0.1:7890")), patch.object(gate,"_tcp_ok",return_value=True), patch.object(gate,"_probe_ip",return_value=location):
            result = gate.check(force=True)
        self.assertTrue(result["ok"])
        self.assertEqual(gate.warning_text(result), "")
        self.assertEqual(result["country"], "JP")

    def test_selects_https_proxy_regardless_of_order(self):
        self.assertEqual(gate._parse_endpoint("https=127.0.0.1:7890;http=127.0.0.1:8080")[:2], ("127.0.0.1", 7890))
        self.assertIsNone(gate._parse_endpoint(""))

    def test_exception_returns_unknown_warning(self):
        with patch.object(gate, "_inspect", side_effect=RuntimeError("test")):
            result = gate.check(force=True)
        self.assertFalse(result["ok"])
        self.assertTrue(gate.warning_text(result))

    def test_cache_avoids_repeated_probes(self):
        with patch.object(gate, "_inspect", return_value={"ok": True, "issues": []}) as inspect:
            gate.check()
            self.assertTrue(gate.check()["cached"])
        self.assertEqual(inspect.call_count, 1)

    def test_slow_worker_respects_caller_budget_and_is_shared(self):
        release = threading.Event()
        def slow():
            release.wait(1)
            return {"ok": True, "issues": []}
        try:
            with patch.object(gate, "_inspect", side_effect=slow) as inspect, patch.object(gate, "TOTAL_BUDGET", 0.03):
                started = time.monotonic()
                first = gate.check(force=True)
                second = gate.check(force=True)
                self.assertLess(time.monotonic() - started, 0.2)
                self.assertEqual(first["issues"][0]["code"], "check_timeout")
                self.assertFalse(second["ok"])
                self.assertEqual(inspect.call_count, 1)
        finally:
            release.set()
            gate._flight[0].wait(1)


if __name__ == "__main__":
    unittest.main()

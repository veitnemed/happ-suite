import unittest

from src.core import ComponentState
from src.gemini_availability import GeminiNetworkStatus, RegionSupport
from src.ui_model import DASHBOARD_PAGES, gemini_access_summary, vpn_presentation


class DashboardModelRegressionTests(unittest.TestCase):
    def test_expected_dashboard_pages_are_declared(self):
        self.assertEqual(set(DASHBOARD_PAGES), {"vpn", "google", "ag", "settings"})

    def test_vpn_running_maps_to_disconnect(self):
        view = vpn_presentation(ComponentState.RUNNING)
        self.assertEqual(view.label, "Подключено")
        self.assertEqual(view.action, "Отключить")

    def test_vpn_stopped_maps_to_connect(self):
        view = vpn_presentation(ComponentState.STOPPED)
        self.assertEqual(view.label, "Выключено")
        self.assertEqual(view.action, "Подключить")

    def test_vpn_error_keeps_error_detail_and_retry_action(self):
        view = vpn_presentation(ComponentState.ERROR, "Маршрут не подтверждён")
        self.assertEqual(view.label, "Маршрут не подтверждён")
        self.assertEqual(view.action, "Повторить")
        self.assertEqual(view.tone, "error")

    def test_reachable_website_never_implies_available_google_account(self):
        status = GeminiNetworkStatus(
            network_available=True, website_reachable=True,
            region_supported=RegionSupport.SUPPORTED, account_status="UNKNOWN",
        )
        website, account = gemini_access_summary(status)
        self.assertEqual(website, "Сайт Google отвечает")
        self.assertEqual(account, "Google Account: ? не проверен")
        self.assertNotIn("доступен", account.casefold())

    def test_pending_vpn_operations_disable_toggle_in_view(self):
        for state in (ComponentState.STARTING, ComponentState.STOPPING, ComponentState.RECOVERING):
            with self.subTest(state=state):
                view = vpn_presentation(state)
                self.assertEqual(view.tone, "busy")
                self.assertEqual(view.action, "Подождите…")


if __name__ == "__main__":
    unittest.main()

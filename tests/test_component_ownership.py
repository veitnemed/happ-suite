"""Mock-only state and ownership checks; these tests never change the network."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.core import (
    AGUnlockerComponent,
    Component,
    ComponentOwnership,
    ComponentState,
    DesiredState,
    Orchestrator,
    ZapretComponent,
)
from src.health import HealthMonitor


class RuntimeStateTests(unittest.TestCase):
    def test_transitions_do_not_replace_observed_state(self):
        component = Component("test")
        component.observe(ComponentState.RUNNING)

        component.set_state(ComponentState.STOPPING)

        self.assertEqual(component.observed_state, ComponentState.RUNNING)
        self.assertEqual(component.state, ComponentState.STOPPING)

    def test_health_observation_does_not_change_vpn_intent(self):
        vpn = Component("VPN")
        vpn.read_status = Mock(return_value=SimpleNamespace(
            route=SimpleNamespace(through_happ=True), connected=True
        ))
        ag = Component("AG")
        ag.is_running = Mock(return_value=False)
        orchestrator = SimpleNamespace(vpn=vpn, ag_unlocker=ag)
        monitor = HealthMonitor(orchestrator, SimpleNamespace())

        monitor._check_once()

        self.assertEqual(vpn.desired_state, DesiredState.OFF)
        self.assertEqual(vpn.observed_state, ComponentState.RUNNING)
        self.assertEqual(vpn.ownership, ComponentOwnership.EXTERNAL)

    def test_vpn_route_without_https_is_degraded_not_stopped(self):
        vpn = Component("VPN")
        vpn.read_status = Mock(return_value=SimpleNamespace(
            route=SimpleNamespace(through_happ=True), connected=False
        ))
        ag = Component("AG")
        ag.is_running = Mock(return_value=False)
        monitor = HealthMonitor(
            SimpleNamespace(vpn=vpn, ag_unlocker=ag),
            SimpleNamespace(),
        )

        monitor._check_once()

        self.assertEqual(vpn.desired_state, DesiredState.OFF)
        self.assertEqual(vpn.observed_state, ComponentState.DEGRADED)
        self.assertEqual(vpn.ownership, ComponentOwnership.EXTERNAL)

    def test_refresh_status_does_not_change_any_component_intent(self):
        vpn = Component("VPN")
        vpn.read_status = Mock(return_value=SimpleNamespace(
            route=SimpleNamespace(through_happ=True), connected=True
        ))
        ag = Component("AG")
        ag.is_running = Mock(return_value=True)
        zapret = ZapretComponent(SimpleNamespace())
        zapret._winws_processes = Mock(return_value={321: (10.0, r"c:\zapret\winws.exe")})
        orchestrator = SimpleNamespace(happ=vpn, ag_unlocker=ag, zapret=zapret)

        Orchestrator.refresh_status(orchestrator)

        self.assertEqual(vpn.desired_state, DesiredState.OFF)
        self.assertEqual(ag.desired_state, DesiredState.OFF)
        self.assertEqual(zapret.desired_state, DesiredState.OFF)
        self.assertEqual(vpn.ownership, ComponentOwnership.EXTERNAL)
        self.assertEqual(ag.ownership, ComponentOwnership.EXTERNAL)
        self.assertEqual(zapret.ownership, ComponentOwnership.EXTERNAL)


class AGOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.component = AGUnlockerComponent(SimpleNamespace(ag_unlocker_port=53129))

    def test_external_relay_is_not_stopped(self):
        self.component.ownership = ComponentOwnership.EXTERNAL
        self.component.started_task_this_session = False
        with patch("src.core.stop_dns_relay") as stop_relay:
            self.assertTrue(self.component.stop())

        stop_relay.assert_not_called()
        self.assertEqual(self.component.desired_state, DesiredState.OFF)

    def test_idempotent_start_does_not_claim_preexisting_relay(self):
        with patch("src.core.ensure_dns_relay", return_value=SimpleNamespace(
            ready=True, started_by_suite=False, reason="already running"
        )):
            self.assertTrue(self.component.start())

        self.assertEqual(self.component.ownership, ComponentOwnership.EXTERNAL)
        self.assertFalse(self.component.started_task_this_session)

    def test_suite_started_relay_can_be_stopped(self):
        self.component.ownership = ComponentOwnership.SUITE
        self.component.started_task_this_session = True
        with patch("src.core.stop_dns_relay", return_value=SimpleNamespace(ready=True)) as stop_relay:
            self.assertTrue(self.component.stop())

        stop_relay.assert_called_once()
        self.assertEqual(self.component.ownership, ComponentOwnership.UNKNOWN)
        self.assertFalse(self.component.started_task_this_session)


class ZapretOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.component = ZapretComponent(SimpleNamespace())

    def test_external_and_unknown_processes_are_never_terminated(self):
        import psutil

        for ownership in (ComponentOwnership.EXTERNAL, ComponentOwnership.UNKNOWN):
            with self.subTest(ownership=ownership):
                self.component.ownership = ownership
                self.component._owned_winws = {}
                with patch.object(psutil, "Process") as process:
                    self.assertTrue(self.component.stop())
                process.assert_not_called()

    def test_only_recorded_suite_owned_identity_is_terminated(self):
        import psutil

        self.component.ownership = ComponentOwnership.SUITE
        self.component._owned_winws = {321: (10.5, r"c:\zapret\winws.exe")}
        proc = Mock()
        proc.create_time.return_value = 10.5
        proc.exe.return_value = r"C:\Zapret\winws.exe"
        with patch.object(self.component, "_winws_processes", side_effect=[
            {
                321: (10.5, r"c:\zapret\winws.exe"),
                654: (11.0, r"c:\other\winws.exe"),
            },
            {654: (11.0, r"c:\other\winws.exe")},
        ]), patch.object(psutil, "Process", return_value=proc) as process:
            self.assertTrue(self.component.stop())

        process.assert_called_once_with(321)
        proc.terminate.assert_called_once()
        self.assertEqual(self.component.ownership, ComponentOwnership.EXTERNAL)
        self.assertEqual(self.component.observed_state, ComponentState.RUNNING)


if __name__ == "__main__":
    unittest.main()

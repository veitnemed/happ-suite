"""Qt UI and command regressions without network, UAC or Windows settings writes."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QLineEdit

from src.ui_qt.controller import Controller
from src.ui_qt.main_window import MainWindow
from src.ui_qt.preview import PreviewController
from src.ui_qt.theme import apply_dark_theme, COLORS
from src.core import ComponentState, ComponentOwnership


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    apply_dark_theme(application)
    yield application


@pytest.fixture
def window(app):
    controller = PreviewController()
    widget = MainWindow(controller, preview=True)
    widget.render(controller.snapshot)
    widget.show()
    app.processEvents()
    yield widget, controller
    widget.close()
    widget.deleteLater()
    app.processEvents()


@pytest.fixture
def controller(app):
    vpn = Mock(state=ComponentState.STOPPED, ownership=ComponentOwnership.UNKNOWN,
               preferred_node=None, last_error=None)
    tray = Mock(orchestrator=SimpleNamespace(vpn=vpn, ag_unlocker=Mock(state=ComponentState.STOPPED)))
    tray._hotkey_choices = {}
    tray._action_lock = threading.Lock()
    with patch("src.ui_qt.controller.is_enabled", return_value=False), \
         patch("src.ui_qt.controller.MihomoComponent"), \
         patch("src.ui_qt.controller.AgUnlockerComponent"), \
         patch("src.ui_qt.controller.VSCodeComponent"), \
         patch("src.ui_qt.controller.GoogleAntigravityComponent"):
        result = Controller(tray, Mock())
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


def drain(app, predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert predicate()


def test_dark_palette_and_four_pages(window, app):
    widget, _ = window
    assert app.palette().color(QPalette.ColorRole.Window).name().upper() == COLORS["background"]
    assert set(widget.pages) == {"vpn", "google", "ag", "settings"}
    assert widget.subscription.echoMode() == QLineEdit.EchoMode.Password


def test_selecting_server_does_not_apply_route(window):
    widget, controller = window
    controller.apply_node = Mock()
    widget.servers.setCurrentIndex(widget.proxy.index(1, 0))
    controller.apply_node.assert_not_called()
    widget.apply.click()
    controller.apply_node.assert_called_once_with("Germany · Frankfurt")


def test_search_filters_without_runtime_command(window):
    widget, _ = window
    widget.search.setText("frankfurt")
    assert widget.proxy.rowCount() == 1
    widget.search.setText("nonexistent")
    assert widget.proxy.rowCount() == 0
    assert not widget.apply.isEnabled()


def test_loading_disables_mutating_actions(window):
    widget, controller = window
    snapshot = dict(controller.snapshot, busy={"subscription"})
    widget.render(snapshot)
    assert not widget.save.isEnabled()
    assert not widget.apply.isEnabled()
    assert not widget.vpn_button.isEnabled()


def test_disabled_primary_button_looks_disabled(window, app):
    widget, _ = window
    widget.apply.setEnabled(False)
    app.processEvents()
    image = widget.apply.grab().toImage()
    assert image.pixelColor(5, image.height() // 2).name().upper() == "#111820"


def test_empty_subscription_keeps_editor_and_no_fake_server(window):
    widget, controller = window
    widget.render(dict(controller.snapshot, nodes=[]))
    widget.subscription_editor.show()
    assert widget.empty.isVisible()
    assert not widget.servers.isVisible()
    assert not widget.best.isEnabled()


def test_node_change_invalidates_gemini_diagnostics(controller):
    controller._gemini = object()
    controller._exit_country = "US"
    controller._active_node = "previous"
    controller._complete("apply", True, "")
    assert controller._gemini is None
    assert controller._exit_country is None
    assert controller._active_node is None


def test_selected_preference_is_not_reported_as_active(window):
    widget, controller = window
    widget.render(dict(controller.snapshot, vpn_state=ComponentState.RUNNING,
                       preferred_node="Germany", active_node="Netherlands"))
    assert widget.current_node.text() == "Активный сервер: Netherlands"


def test_window_close_hides_without_stopping_runtime(window, app):
    widget, _ = window
    widget.preview = False
    widget.close()
    app.processEvents()
    assert not widget.isVisible()
    widget.show_window()
    assert widget.isVisible()
    widget.preview = True


def test_duplicate_command_rejected_and_completion_on_gui_thread(controller, app):
    gate = threading.Event()
    threads = []
    controller.changed.connect(lambda value: threads.append(QThread.currentThread()))
    assert controller._run("test", lambda: gate.wait(1))
    assert not controller._run("test", lambda: pytest.fail("Duplicate executed"))
    gate.set()
    drain(app, lambda: "test" not in controller._active)
    assert threads and all(thread == app.thread() for thread in threads)


def test_subscription_success_excludes_credentials_from_snapshot(controller, app):
    controller.vpn.set_subscription_url.return_value = SimpleNamespace(
        nodes=[{"name": "Demo", "type": "vless", "password": "private-value"}], device_limit_warning=False)
    controller.save_subscription("https://example.test/private-subscription")
    drain(app, lambda: "subscription" not in controller._active)
    assert controller._nodes == [{"name": "Demo", "type": "vless"}]
    assert not controller.tray._action_lock.locked()


def test_exception_does_not_leak_subscription_url(controller, app, caplog):
    secret = "https://example.test/private-subscription"
    messages = []
    controller.message.connect(messages.append)
    controller.vpn.set_subscription_url.side_effect = RuntimeError(secret)
    controller.save_subscription(secret)
    drain(app, lambda: "subscription" not in controller._active)
    assert secret not in caplog.text
    assert all(secret not in message for message in messages)
    assert any("RuntimeError" in message for message in messages)


def test_shared_tray_lock_blocks_node_switch(controller, app):
    controller.tray._action_lock.acquire()
    try:
        controller.apply_node("Demo")
        drain(app, lambda: "apply" not in controller._active)
        controller.vpn.select_node.assert_not_called()
    finally:
        controller.tray._action_lock.release()


def test_closed_controller_discards_late_completion(controller, app):
    gate = threading.Event()
    messages = []
    controller.message.connect(messages.append)
    controller._run("apply", lambda: gate.wait(1))
    controller.close()
    gate.set()
    app.processEvents()
    assert not messages


def test_source_repeat_launch_never_terminates_previous_process():
    import start
    with patch.object(start, "_stop_old_suite") as stop, patch("src.start.main") as launch:
        start.main()
    stop.assert_not_called()
    launch.assert_called_once()


def test_packaged_autostart_accepts_actual_executable_name():
    from src import autostart
    with patch("sys.frozen", True, create=True), patch("sys.executable", "C:/Apps/RelayStudio.exe"):
        assert autostart._command().endswith('RelayStudio.exe" --background')

"""Dark Qt Widgets dashboard. Geometry uses Qt's device-independent pixels."""
from pathlib import Path
import ctypes
import os
import sys
import webbrowser

from PySide6.QtCore import Qt, QSortFilterProxyModel, QSize, Slot
from PySide6.QtGui import QIcon, QPainter, QColor, QPen
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QStackedWidget, QScrollArea, QLineEdit, QListView,
    QCheckBox, QAbstractItemView, QStyledItemDelegate, QStyle, QComboBox)

from ..core import ComponentState
from .. import __version__
from ..ui_model import vpn_presentation, gemini_access_summary
from ..components import component_presentation
from .server_list import ServerListModel
from .theme import COLORS


def label(text, style="", wrap=False):
    widget = QLabel(text)
    widget.setProperty("class", style)
    widget.setWordWrap(wrap)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def button(text, callback, primary=False):
    widget = QPushButton(text)
    if primary:
        widget.setProperty("class", "primaryButton")
    widget.clicked.connect(callback)
    return widget


def card():
    frame = QFrame()
    frame.setObjectName("surface")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 14, 18, 14)
    layout.setSpacing(9)
    return frame, layout


class Logo(QWidget):
    """Small vector route mark; remains crisp at fractional display scaling."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 40)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(COLORS["border"]), 1))
        painter.setBrush(QColor(COLORS["surface"]))
        painter.drawRoundedRect(1, 1, 38, 38, 11, 11)
        painter.setPen(QPen(QColor(COLORS["accent"]), 3, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(12, 28, 12, 12)
        painter.drawLine(12, 12, 25, 12)
        painter.drawLine(25, 12, 28, 19)
        painter.drawLine(28, 19, 12, 19)
        painter.drawLine(21, 19, 28, 28)
        painter.end()


class ServerDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(200, 66)

    def paint(self, painter, option, index):
        painter.save()
        rect = option.rect.adjusted(0, 1, 0, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hover:
            painter.fillRect(rect, QColor("#20323C" if selected else COLORS["surface_alt"]))
        if selected:
            painter.fillRect(rect.x(), rect.y() + 8, 3, rect.height() - 16, QColor(COLORS["accent"]))
        font = option.font
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(COLORS["text"]))
        title = painter.fontMetrics().elidedText(index.data(), Qt.TextElideMode.ElideRight,
                                                max(10, rect.width() - 36))
        painter.drawText(rect.adjusted(16, 8, -16, -30), Qt.AlignmentFlag.AlignVCenter, title)
        font.setBold(False)
        font.setPointSizeF(9)
        painter.setFont(font)
        painter.setPen(QColor(COLORS["muted"]))
        protocol = index.data(ServerListModel.ProtocolRole) or "VPN"
        delay = index.data(ServerListModel.DelayRole)
        detail = f"{protocol.upper()}  ·  " + (f"{delay:.0f} мс" if delay is not None else "задержка не измерена")
        painter.drawText(rect.adjusted(16, 31, -16, -7), Qt.AlignmentFlag.AlignVCenter, detail)
        painter.setPen(QColor(COLORS["border"]))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.restore()


class MainWindow(QMainWindow):
    def __init__(self, controller, *, preview=False):
        super().__init__()
        self.controller = controller
        self.preview = preview
        self._snapshot = {}
        self._last_nodes = None
        self.setWindowTitle("Relay Studio" + (" · Preview" if preview else ""))
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
        self.setWindowIcon(QIcon(str(base / "assets" / "relay-studio.ico")))
        self.setMinimumSize(860, 560)
        screen = self.screen().availableGeometry()
        self.resize(min(1160, screen.width()), min(800, screen.height()))
        root = QWidget()
        root.setObjectName("appRoot")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(208)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(18, 25, 18, 20)
        nav.setSpacing(8)
        brand = QHBoxLayout()
        brand.addWidget(Logo())
        brand.addWidget(label("Relay\nStudio", "cardTitle"))
        nav.addLayout(brand)
        nav.addSpacing(28)
        nav.addWidget(label("РАБОЧЕЕ ПРОСТРАНСТВО", "eyebrow"))
        self.nav = {}
        self.stack = QStackedWidget()
        self.pages = {}
        for key, title in (("vpn", "VPN / Серверы"), ("google", "Gemini Web"),
                           ("ag", "Antigravity"), ("settings", "Настройки")):
            b = button(title, lambda checked=False, page=key: self.show_page(page))
            b.setProperty("class", "navButton")
            self.nav[key] = b
            nav.addWidget(b)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(28, 25, 28, 24)
            layout.setSpacing(14)
            scroll.setWidget(page)
            self.stack.addWidget(scroll)
            self.pages[key] = layout
        nav.addStretch()
        nav.addWidget(label("SECURE ACCESS", "eyebrow"))
        nav.addWidget(label("Mihomo · System control", "subtle", True))
        nav.addWidget(button("Свернуть в трей", self.hide))
        shell.addWidget(sidebar)
        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.addWidget(self.stack)
        self.message_label = label("Предпросмотр: системные команды отключены" if preview else "Готово к работе", "muted", True)
        self.message_label.setContentsMargins(28, 10, 28, 15)
        content.addWidget(self.message_label)
        shell.addLayout(content, 1)
        self._build_vpn()
        self._build_google()
        self._build_ag()
        self._build_settings()
        self.show_page("vpn")
        controller.message.connect(self.message_label.setText)
        controller.changed.connect(self.render)
        controller.busy_changed.connect(lambda key, busy: self._render_busy())
        controller.subscription_loaded.connect(self._load_subscription)

    def heading(self, key, title, description):
        layout = self.pages[key]
        heading = QVBoxLayout()
        heading.setSpacing(4)
        heading.addWidget(label(title, "pageTitle"))
        heading.addWidget(label(description, "muted", True))
        layout.addLayout(heading)
        return layout

    def _build_vpn(self):
        layout = self.heading("vpn", "VPN / Серверы", "Подписка, выбор сервера и проверенное VPN-соединение.")
        frame, box = card()
        row = QHBoxLayout()
        row.addWidget(label("MIHOMO VPN", "eyebrow"))
        self.vpn_status = label("Выключено", "cardTitle", True)
        row.addWidget(self.vpn_status)
        row.addStretch()
        self.vpn_button = button("Подключить", self.controller.toggle_vpn, True)
        row.addWidget(self.vpn_button)
        box.addLayout(row)
        self.current_node = label("Сервер не выбран", "muted", True)
        self.exit_country = label("Страна выхода · не проверена", "subtle", True)
        self.ownership = label("", "subtle", True)
        box.addWidget(self.current_node)
        box.addWidget(self.exit_country)
        box.addWidget(self.ownership)
        layout.addWidget(frame)

        frame, box = card()
        row = QHBoxLayout()
        row.addWidget(label("Подписка", "cardTitle"))
        row.addStretch()
        self.subscription_toggle = button("Изменить", self._toggle_subscription)
        row.addWidget(self.subscription_toggle)
        box.addLayout(row)
        box.addWidget(label("Ссылка хранится локально в зашифрованном виде.", "muted", True))
        self.subscription_editor = QWidget()
        editor = QHBoxLayout(self.subscription_editor)
        editor.setContentsMargins(0, 0, 0, 0)
        self.subscription = QLineEdit()
        self.subscription.setEchoMode(QLineEdit.EchoMode.Password)
        self.subscription.setPlaceholderText("HTTPS-ссылка подписки")
        self.subscription.setClearButtonEnabled(True)
        self.subscription.returnPressed.connect(self._save_subscription)
        editor.addWidget(self.subscription, 1)
        self.reveal = button("Показать", self._reveal)
        editor.addWidget(self.reveal)
        self.save = button("Сохранить", self._save_subscription, True)
        editor.addWidget(self.save)
        box.addWidget(self.subscription_editor)
        layout.addWidget(frame)

        frame, box = card()
        row = QHBoxLayout()
        self.server_count = label("Серверы", "cardTitle")
        row.addWidget(self.server_count)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск сервера")
        self.search.setClearButtonEnabled(True)
        row.addWidget(self.search, 1)
        self.refresh_button = button("Обновить", self.controller.refresh_nodes)
        row.addWidget(self.refresh_button)
        box.addLayout(row)
        self.model = ServerListModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        self.servers = QListView()
        self.servers.setModel(self.proxy)
        self.servers.setItemDelegate(ServerDelegate(self.servers))
        self.servers.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.servers.setMinimumHeight(132)
        self.servers.setMaximumHeight(220)
        self.servers.setMouseTracking(True)
        self.servers.selectionModel().selectionChanged.connect(self._selection_changed)
        box.addWidget(self.servers)
        self.empty = label("Добавьте подписку, чтобы загрузить серверы.", "muted", True)
        box.addWidget(self.empty)
        row = QHBoxLayout()
        self.best = button("Лучший зарубежный", self.controller.choose_best)
        self.best.setToolTip("Подключает VPN и переключает серверы во время проверки доступности.")
        row.addWidget(self.best)
        row.addStretch()
        self.apply = button("Применить сервер", self._apply_node, True)
        self.apply.setEnabled(False)
        row.addWidget(self.apply)
        box.addLayout(row)
        box.addWidget(label("Выделение строки не меняет маршрут. «Применить» переключает сервер.", "subtle", True))
        layout.addWidget(frame)
        layout.addStretch()

    def _build_google(self):
        layout = self.heading("google", "Gemini Web", "DNS текущей сети и доступность Gemini проверяются отдельно.")
        frame, box = card()
        row = QHBoxLayout()
        row.addWidget(label("Gemini Web DNS", "cardTitle"))
        row.addStretch()
        self.dns_button = button("Включить DNS", self.controller.toggle_dns, True)
        row.addWidget(self.dns_button)
        box.addLayout(row)
        self.dns_status = label("Проверяю DNS текущей сети…", "muted", True)
        box.addWidget(self.dns_status)
        box.addWidget(label("При отключении Suite восстанавливает сохранённые настройки DNS.", "subtle", True))
        layout.addWidget(frame)
        frame, box = card()
        box.addWidget(label("Диагностика доступа", "cardTitle"))
        self.gemini_status = label("Сайт и регион ещё не проверены.\nGoogle Account: ? не проверен", "muted", True)
        box.addWidget(self.gemini_status)
        row = QHBoxLayout()
        self.check = button("Проверить доступ", self.controller.check_gemini)
        row.addWidget(self.check)
        row.addWidget(button("Открыть Gemini", lambda: webbrowser.open("https://gemini.google.com/app") if not self.preview else None))
        row.addStretch()
        box.addLayout(row)
        layout.addWidget(frame)
        layout.addStretch()

    def _build_ag(self):
        layout = self.heading("ag", "Antigravity", "Локальный relay AG Unlocker для Google Antigravity.")
        frame, box = card()
        row = QHBoxLayout()
        row.addWidget(label("AG Unlocker", "cardTitle"))
        row.addStretch()
        self.ag_button = button("Включить", self.controller.toggle_ag, True)
        row.addWidget(self.ag_button)
        box.addLayout(row)
        self.ag_status = label("Relay выключен", "muted", True)
        box.addWidget(self.ag_status)
        box.addWidget(label("Запущенный процесс и подтверждённый ответ модели отображаются как разные состояния.", "subtle", True))
        layout.addWidget(frame)
        layout.addWidget(button("Установка и горячие клавиши", lambda: self.show_page("settings")))
        layout.addStretch()

    def _build_settings(self):
        layout = self.heading("settings", "Настройки", "Компоненты, горячие клавиши и запуск вместе с Windows.")
        frame, box = card()
        box.addWidget(label("Компоненты", "cardTitle"))
        self.component_widgets = {}
        for key, name in (("mihomo", "Mihomo"), ("google_antigravity", "Google Antigravity"),
                          ("vscode", "Visual Studio Code"), ("ag_unlocker", "AG Unlocker")):
            row = QHBoxLayout()
            texts = QVBoxLayout()
            texts.addWidget(label(name))
            status = label("Проверяю установку…", "subtle", True)
            texts.addWidget(status)
            row.addLayout(texts, 1)
            action = button("Проверка…", lambda checked=False, name=key: self.controller.component_action(name))
            action.setEnabled(False)
            row.addWidget(action)
            box.addLayout(row)
            self.component_widgets[key] = status, action
        layout.addWidget(frame)
        frame, box = card()
        box.addWidget(label("Глобальные горячие клавиши", "cardTitle"))
        self.hotkeys = {}
        for key, name in (("happ", "VPN"), ("dns", "Gemini DNS"), ("gemini", "Antigravity relay")):
            row = QHBoxLayout()
            row.addWidget(label(name), 1)
            letter = {"happ": "H", "dns": "D", "gemini": "G"}[key]
            combo = QComboBox()
            combo.addItems([f"Ctrl+Alt+{letter}", f"Ctrl+Shift+{letter}",
                            {"happ": "F8", "dns": "F10", "gemini": "F9"}[key]])
            combo.activated.connect(lambda index, name=key, editor=combo: self.controller.set_hotkey(name, editor.itemText(index)))
            row.addWidget(combo)
            row.addWidget(button("Назначить…", lambda checked=False, name=key: self.controller.capture_hotkey(name)))
            box.addLayout(row)
            self.hotkeys[key] = combo
        box.addWidget(label("Конфликт сочетаний не заменяет действующую клавишу. Esc отменяет запись.", "subtle", True))
        layout.addWidget(frame)
        frame, box = card()
        self.autostart = QCheckBox("Запускать вместе с Windows в трее")
        self.autostart.clicked.connect(self.controller.set_autostart)
        box.addWidget(self.autostart)
        box.addWidget(label(f"Relay Studio {__version__} · тёмная тема · Qt Widgets\nАвтоматический масштаб экрана", "subtle", True))
        box.addWidget(button("Открыть журнал", self.controller.open_log))
        layout.addWidget(frame)
        layout.addStretch()

    def show_page(self, name):
        index = list(self.pages).index(name)
        self.stack.setCurrentIndex(index)
        for key, widget in self.nav.items():
            widget.setProperty("active", key == name)
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def _toggle_subscription(self):
        visible = not self.subscription_editor.isVisible()
        self.subscription_editor.setVisible(visible)
        self.subscription_toggle.setText("Скрыть" if visible else "Изменить")
        if visible:
            self.controller.load_subscription()

    @Slot(str)
    def _load_subscription(self, value):
        if self.subscription_editor.isVisible() and not self.subscription.isModified():
            self.subscription.setText(value)

    def _reveal(self):
        hidden = self.subscription.echoMode() == QLineEdit.EchoMode.Password
        self.subscription.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
        self.reveal.setText("Скрыть" if hidden else "Показать")

    def _save_subscription(self):
        self.controller.save_subscription(self.subscription.text())

    def _selection_changed(self, *args):
        self._render_busy()

    def _apply_node(self):
        index = self.servers.currentIndex()
        if index.isValid():
            self.controller.apply_node(index.data(ServerListModel.NameRole))

    def _render_busy(self):
        snapshot = self._snapshot
        busy = snapshot.get("busy", set())
        network = snapshot.get("network_busy", False) or bool(busy & {"toggle", "toggle_ag", "apply", "best", "elevated"})
        node_busy = bool(busy & {"subscription", "refresh_nodes", "nodes"})
        self.apply.setEnabled(self.servers.currentIndex().isValid() and not network and not node_busy)
        self.best.setEnabled(bool(self.model.rowCount()) and not network and not node_busy)
        self.refresh_button.setEnabled(not node_busy and not network)
        self.save.setEnabled(not node_busy and not network)
        self.check.setEnabled("gemini" not in busy)
        self.ag_button.setEnabled(not network)
        self.autostart.setEnabled("autostart" not in busy)
        state = snapshot.get("vpn_state", ComponentState.STOPPED)
        self.vpn_button.setEnabled(not network and not node_busy and state not in (
            ComponentState.STARTING, ComponentState.STOPPING, ComponentState.RECOVERING))
        self.dns_button.setEnabled(bool(snapshot.get("dns")) and not snapshot.get("dns_error")
                                   and not (busy & {"dns", "inspect"}))

    @Slot(object)
    def render(self, snapshot):
        self._snapshot = snapshot
        p = vpn_presentation(snapshot["vpn_state"], snapshot.get("vpn_error"))
        self.vpn_status.setText(p.label)
        self.vpn_button.setText(p.action)
        self.vpn_status.setStyleSheet(f"color: {COLORS.get(p.tone, COLORS['muted'])};")
        preferred = snapshot.get("preferred_node")
        active = snapshot.get("active_node")
        connected = snapshot["vpn_state"] in (ComponentState.RUNNING, ComponentState.DEGRADED)
        self.current_node.setText(("Сервер для подключения: " + (preferred or "автоматический")) if not connected else
                                 "Активный сервер: " + (active or "ещё не подтверждён контроллером"))
        self.exit_country.setText("Страна выхода · " + (snapshot.get("exit_country") or "не проверена"))
        self.exit_country.setVisible(connected)
        owned = snapshot.get("ownership", "unknown").lower()
        self.ownership.setText("Управляется Suite" if owned == "suite" else
                               "Внешнее соединение: Suite не останавливает его" if owned == "external" else
                               "Владение не подтверждено: остановка чужого процесса запрещена")
        self.ownership.setVisible(connected or owned in ("suite", "external"))
        nodes = snapshot.get("nodes", [])
        if nodes != self._last_nodes:
            selected = self.servers.currentIndex().data(ServerListModel.NameRole)
            self.model.set_items(nodes)
            self._last_nodes = list(nodes)
            target = selected or preferred
            for row in range(self.proxy.rowCount()):
                index = self.proxy.index(row, 0)
                if index.data(ServerListModel.NameRole) == target:
                    self.servers.setCurrentIndex(index)
                    break
            self.server_count.setText(f"Серверы · {self.model.rowCount()}")
            self.empty.setVisible(not bool(nodes))
            self.servers.setVisible(bool(nodes))
            if nodes and not self.subscription.hasFocus():
                self.subscription.clear()
                self.subscription_editor.hide()
                self.subscription_toggle.setText("Изменить")
        dns = snapshot.get("dns")
        self.dns_status.setText(snapshot.get("dns_error") or (dns["message"] if dns else "Проверяю DNS текущей сети…"))
        self.dns_button.setText("Вернуть DNS" if dns and dns["managed"] else "Включить DNS")
        status = snapshot.get("gemini")
        if status:
            site, account = gemini_access_summary(status)
            self.gemini_status.setText(f"{site}\nСтрана выхода: {status.exit_country or 'не определена'} · Регион: {status.region_supported.value}\n{account}")
        else:
            self.gemini_status.setText("Сайт и регион ещё не проверены.\nGoogle Account: ? не проверен")
        state = snapshot["ag_state"]
        self.ag_status.setText({ComponentState.STOPPED: "Relay выключен", ComponentState.RUNNING: "Relay работает; ответ подтверждён",
                               ComponentState.DEGRADED: "Relay запущен; ответ модели ещё не подтверждён",
                               ComponentState.ERROR: "Ошибка relay"}.get(state, "Выполняется операция…"))
        self.ag_button.setText("Выключить" if state in (ComponentState.RUNNING, ComponentState.DEGRADED) else "Включить")
        busy = snapshot.get("busy", set())
        for name, installation in snapshot.get("installations", {}).items():
            text, action = self.component_widgets[name]
            presentation = component_presentation(installation,
                open_label="Проверить" if name == "mihomo" else "Переключить" if name == "ag_unlocker" else "Открыть")
            installing = f"install:{name}" in busy or name in snapshot.get("pending", set())
            text.setText("Установка…" if installing else presentation.status_text)
            action.setText("Подождите…" if installing else presentation.action_text)
            action.setEnabled(presentation.action_enabled and not installing)
        for name, value in snapshot.get("hotkeys", {}).items():
            editor = self.hotkeys[name]
            if editor.findText(value) < 0:
                editor.addItem(value)
            editor.setCurrentText(value)
        self.autostart.setChecked(snapshot.get("autostart", False))
        self._render_busy()

    @Slot()
    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        if self.preview:
            event.accept()
        else:
            event.ignore()
            self.hide()

    def showEvent(self, event):
        super().showEvent(event)
        if os.name == "nt":
            enabled = ctypes.c_int(1)
            try:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    ctypes.c_void_p(int(self.winId())), 20, ctypes.byref(enabled), ctypes.sizeof(enabled))
            except (OSError, AttributeError):
                # Older Windows versions can still render the client area dark.
                return

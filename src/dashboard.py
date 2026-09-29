"""Compact first-run dashboard; tray and hotkeys keep running when hidden."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import ttk

import requests
from PIL import Image, ImageDraw, ImageFont, ImageTk

try:
    from .components import (
        AgUnlockerComponent, ComponentOperationError, ComponentStatus, GoogleAntigravityComponent,
        MihomoComponent, VSCodeComponent, component_presentation,
    )
    from .core import ComponentState
    from .gemini_availability import GeminiAvailabilityClient, GeminiNetworkStatus, RegionSupport
    from .ui_model import DASHBOARD_PAGES, gemini_access_summary, vpn_presentation
    from .hotkey import HotkeyChoice, MOD_ALT, MOD_CONTROL, MOD_SHIFT, VK_F8, VK_F9
    from .node_region import country_hint
    from .windows_elevation import is_admin
    from .autostart import is_enabled as autostart_enabled, set_enabled as set_autostart
    from .gemini_dns import DnsManager, apply_action, XBOX_DNS
    from .vpn_backend import (
        NetworkUnavailableError, ProviderFormatError, SubscriptionError,
    )
except ImportError:
    from components import (
        AgUnlockerComponent, ComponentOperationError, ComponentStatus, GoogleAntigravityComponent,
        MihomoComponent, VSCodeComponent, component_presentation,
    )
    from core import ComponentState
    from gemini_availability import GeminiAvailabilityClient, GeminiNetworkStatus, RegionSupport
    from ui_model import DASHBOARD_PAGES, gemini_access_summary, vpn_presentation
    from hotkey import HotkeyChoice, MOD_ALT, MOD_CONTROL, MOD_SHIFT, VK_F8, VK_F9
    from node_region import country_hint
    from windows_elevation import is_admin
    from autostart import is_enabled as autostart_enabled, set_enabled as set_autostart
    from gemini_dns import DnsManager, apply_action, XBOX_DNS
    from vpn_backend import (
        NetworkUnavailableError, ProviderFormatError, SubscriptionError,
    )


BG = "#F7F8F9"
SIDEBAR = "#F0F2F4"
CARD = "#FFFFFF"
FIELD = "#FFFFFF"
TEXT = "#20272D"
MUTED = "#68747E"
BLUE = "#345F7D"
GREEN = "#32805C"
YELLOW = "#A87628"
RED = "#B94F4F"
LINE = "#DCE2E6"
SELECTED = "#E2EAF0"
FONT = "Segoe UI"
logger = logging.getLogger("happ_suite.dashboard")


def package_variant() -> str:
    base = Path(__file__).resolve().parent.parent
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    try:
        return json.loads((base / "variant.json").read_text(encoding="utf-8")).get("variant", "setup")
    except (OSError, ValueError):
        return "setup"


class Dashboard:
    def __init__(self, root: tk.Tk, tray, config):
        self.root = root
        self.tray = tray
        self.config = config
        self.variant = package_variant()
        self.mihomo_component = MihomoComponent()
        self.ag_unlocker_component = AgUnlockerComponent()
        self.vscode_component = VSCodeComponent()
        self.google_antigravity_component = GoogleAntigravityComponent()
        self.gemini_site_status = "Сайт ещё не проверен"
        self.gemini_availability = None
        self._gemini_probe_running = False
        self._dns_busy = False
        self._dns_refreshing = False
        self._dns_status = None
        self._dns_error = ""
        self._next_dns_refresh = 0.0
        self._best_busy = False
        try:
            self.ui_scale = max(1.0, min(float(root.winfo_fpixels("1i")) / 96.0, 2.0))
        except tk.TclError:
            self.ui_scale = 1.0
        self._nav_buttons = {}
        self._component_installing = set()
        self._component_install_pending = set()
        self._component_install_deadlines = {}
        self._build()
        self.gemini_availability_client = GeminiAvailabilityClient(
            dns_status=lambda: self._dns_status if self._dns_status is not None else DnsManager().status(),
        )
        if self.config.vpn_backend == "mihomo":
            try:
                saved_url = self.tray.orchestrator.vpn.saved_subscription_url()
                if saved_url:
                    self.subscription.insert(0, saved_url)
                nodes = self.tray.orchestrator.vpn.provider_nodes()
                names = [node["name"] for node in nodes]
                self.vpn_node.configure(values=names)
                selected = getattr(self.tray.orchestrator.vpn, "_last_good_node", None)
                if selected in names:
                    self.vpn_node.set(selected)
            except Exception:
                pass
        self._refresh()

    def _px(self, value: int | float) -> int:
        return max(1, round(value * self.ui_scale))

    def _label(self, parent, text, size=10, color=TEXT, weight="normal", **options):
        return tk.Label(parent, text=text, fg=color, bg=options.pop("bg", BG),
                        font=(FONT, size, weight), **options)

    def _button(self, parent, text, command, *, filled=False, width=15):
        return tk.Button(parent, text=text, command=command, width=width,
                         font=(FONT, 9, "normal"), relief="flat", bd=0,
                         bg=BLUE if filled else CARD, fg="#FFFFFF" if filled else TEXT,
                         activebackground="#284D67" if filled else SELECTED,
                         activeforeground="#FFFFFF" if filled else TEXT, cursor="hand2",
                         padx=self._px(11), pady=self._px(7),
                         highlightthickness=1,
                         highlightbackground=BLUE if filled else LINE,
                         highlightcolor=BLUE, takefocus=True)

    def _component_tile(self, parent, title, action, command, row, column):
        tile = tk.Frame(parent, bg="#FBFCFD", highlightthickness=1,
                        highlightbackground=LINE, padx=self._px(12), pady=self._px(10))
        tile.grid(row=row, column=column, sticky="nsew",
                  padx=(0, self._px(6)) if column == 0 else (self._px(6), 0),
                  pady=(0, self._px(8)))
        heading = tk.Frame(tile, bg="#FBFCFD")
        heading.pack(fill="x")
        self._label(heading, title, size=10, weight="bold", bg="#FBFCFD").pack(side="left", anchor="w")
        button = self._button(heading, action, command, width=13)
        button.pack(side="right")
        status = self._label(tile, "Проверка…", size=9, color=MUTED, bg="#FBFCFD", anchor="w")
        status.pack(fill="x", pady=(self._px(7), 0))
        return status, button

    def _render_component(self, installation, status_label, button, *, install_label, open_label):
        presentation = component_presentation(
            installation, install_label=install_label, open_label=open_label,
        )
        if (installation.id in self._component_installing
                or installation.id in self._component_install_pending):
            status_label.configure(text="Установка…", fg=YELLOW)
            button.configure(text="Подождите…", state="disabled")
            return presentation
        color = {"success": GREEN, "error": RED, "warning": YELLOW,
                 "busy": YELLOW, "muted": MUTED}.get(presentation.tone, MUTED)
        status_label.configure(text=presentation.status_text, fg=color)
        button.configure(text=presentation.action_text,
                          state="normal" if presentation.action_enabled else "disabled")
        return presentation

    def _begin_component_install(self, component_id, button, status_label) -> bool:
        if (component_id in self._component_installing
                or component_id in self._component_install_pending):
            return False
        self._component_installing.add(component_id)
        status_label.configure(text="Установка…", fg=YELLOW)
        button.configure(text="Подождите…", state="disabled")
        return True

    def _finish_component_install(self, component_id, refresh):
        self._component_installing.discard(component_id)
        self.root.after(0, refresh)

    def _build(self):
        root = self.root
        root.title("Relay Studio")
        root.configure(bg=BG)
        icon = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(icon)
        draw.rounded_rectangle((2, 2, 62, 62), radius=10, fill="#34414B")
        try:
            font = ImageFont.truetype(r"C:\Windows\Fonts\segoeuib.ttf", 22)
        except OSError:
            font = ImageFont.load_default()
        draw.text((32, 32), "GPT", fill="#FFFFFF", anchor="mm", font=font)
        self._window_icon = ImageTk.PhotoImage(icon, master=root)
        root.iconphoto(True, self._window_icon)
        screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
        width = min(self._px(1180), screen_w - self._px(48))
        height = min(self._px(740), screen_h - self._px(64))
        root.geometry(f"{width}x{height}")
        root.minsize(min(self._px(860), width), min(self._px(620), height))
        root.protocol("WM_DELETE_WINDOW", root.withdraw)
        style = ttk.Style(root)
        # Clam permits consistent field and list colors on Windows.
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Suite.TCombobox", fieldbackground=FIELD, background=FIELD,
                        foreground=TEXT, arrowcolor=MUTED, bordercolor=LINE,
                        lightcolor=LINE, darkcolor=LINE, padding=self._px(6),
                        font=(FONT, 9))
        style.map("Suite.TCombobox", fieldbackground=[("readonly", FIELD)],
                  foreground=[("readonly", TEXT)],
                  background=[("readonly", FIELD)],
                  arrowcolor=[("readonly", BLUE)])
        root.option_add("*TCombobox*Listbox.background", FIELD)
        root.option_add("*TCombobox*Listbox.foreground", TEXT)
        root.option_add("*TCombobox*Listbox.selectBackground", SELECTED)
        root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
        root.option_add("*TCombobox*Listbox.font", (FONT, 10))

        content = tk.Frame(root, bg=BG, padx=self._px(22), pady=self._px(18))
        content.pack(fill="both", expand=True)
        header = tk.Frame(content, bg=BG)
        header.pack(fill="x")
        brand = tk.Frame(header, bg=BG)
        brand.pack(side="left", fill="x", expand=True)
        mark = tk.Canvas(brand, width=self._px(36), height=self._px(36), bg=BG,
                         bd=0, highlightthickness=0)
        mark.pack(side="left", padx=(0, self._px(10)))
        self._draw_logo(mark)
        title_group = tk.Frame(brand, bg=BG)
        title_group.pack(side="left", anchor="center")
        self._label(title_group, "Relay Studio", size=16, weight="bold").pack(anchor="w")
        self._label(title_group, "Управление подключениями", size=9,
                    color=MUTED).pack(anchor="w", pady=(1, 0))
        self._button(header, "Свернуть в трей", root.withdraw, width=16).pack(side="right", anchor="center")

        body = tk.Frame(content, bg=BG)
        body.pack(fill="both", expand=True, pady=(self._px(17), 0))
        sidebar = tk.Frame(body, bg=SIDEBAR, width=self._px(166), padx=self._px(8), pady=self._px(15),
                           highlightthickness=1, highlightbackground=LINE)
        sidebar.pack(side="left", fill="y", padx=(0, self._px(14)))
        sidebar.pack_propagate(False)
        self._label(sidebar, "Разделы", size=9, color=MUTED, bg=SIDEBAR).pack(
            anchor="w", padx=self._px(11), pady=(0, self._px(11)))
        self._nav_buttons = {}
        self.mode_status = {}
        self._nav_button(sidebar, "vpn", "VPN", "Mihomo")
        self._nav_button(sidebar, "google", "Gemini", "Сеть и DNS")
        self._nav_button(sidebar, "ag", "Antigravity", "AG Unlocker / Relay")
        tk.Frame(sidebar, bg=LINE, height=1).pack(fill="x", pady=(self._px(13), self._px(10)))
        self._nav_button(sidebar, "settings", "Настройки", "Клавиши и автозапуск")
        self._label(sidebar, "Состояние каждого режима\nпоказано отдельно.", size=8, color=MUTED,
                    bg=SIDEBAR, justify="left").pack(side="bottom", anchor="w", padx=self._px(11))

        workspace = tk.Frame(body, bg=BG)
        status_panel = tk.Frame(body, bg=SIDEBAR, width=self._px(238), padx=self._px(13), pady=self._px(15),
                                highlightthickness=1, highlightbackground=LINE)
        status_panel.pack(side="right", fill="y", padx=(self._px(14), 0))
        status_panel.pack_propagate(False)
        workspace.pack(side="left", fill="both", expand=True)
        self.pages = {name: tk.Frame(workspace, bg=BG) for name in DASHBOARD_PAGES}
        vpn, google, ag_page, settings = (
            self.pages[name] for name in ("vpn", "google", "ag", "settings")
        )
        self.cards = {}

        self._page_heading(vpn, "VPN", "Безопасное подключение через Mihomo")

        subscription = tk.Frame(vpn, bg=CARD, highlightthickness=1,
                                highlightbackground=LINE, padx=self._px(18), pady=self._px(15))
        subscription.pack(fill="x")
        heading = tk.Frame(subscription, bg=CARD)
        heading.pack(fill="x", pady=(0, self._px(8)))
        self._label(heading, "Подписка", size=11, weight="bold", bg=CARD).pack(side="left")
        self._label(subscription, "Ссылка сохраняется на этом компьютере в зашифрованном виде.",
                    size=9, color=MUTED, bg=CARD).pack(anchor="w", pady=(0, self._px(11)))
        row = tk.Frame(subscription, bg=CARD)
        row.pack(fill="x")
        self.subscription = tk.Entry(row, bg=FIELD, fg=TEXT, insertbackground=TEXT,
                                     relief="flat", font=(FONT, 10), show="",
                                     highlightthickness=1, highlightbackground=LINE,
                                     highlightcolor=BLUE, bd=0)
        self.subscription.pack(side="left", fill="x", expand=True, ipady=self._px(7))
        self.subscription_visibility = self._button(row, "Скрыть", self._reveal_subscription, width=9)
        self.subscription_visibility.pack(side="left", padx=(self._px(8), 0))
        self._button(row, "Сохранить", self._import_subscription, filled=True, width=11).pack(side="left", padx=(self._px(8), 0))
        self._label(subscription, "Узел", size=9, color=MUTED, bg=CARD).pack(
            anchor="w", pady=(self._px(15), self._px(7)))
        node_row = tk.Frame(subscription, bg=CARD)
        node_row.pack(fill="x")
        self.vpn_node = ttk.Combobox(node_row, state="readonly", style="Suite.TCombobox", width=34)
        self.vpn_node.pack(side="left", fill="x", expand=True)
        self.vpn_node.bind("<<ComboboxSelected>>", lambda _event: self._select_vpn_node())
        self._button(node_row, "Обновить узлы", self._refresh_vpn_nodes, width=14).pack(side="left", padx=(self._px(8), 0))
        self.best_button = self._button(vpn, "Выбрать лучший зарубежный узел", self._choose_best_vpn_node,
                                        filled=True, width=29)
        self.best_button.pack(side="bottom", anchor="e", pady=(self._px(15), 0))

        self._page_heading(google, "Gemini Web", "Доступность сети, сайта и региона — отдельно от Google Account")
        self._card(google, "gemini", "Gemini Web", "Проверяю текущие соединения…",
                   self._toggle_gemini_dns)
        actions = tk.Frame(google, bg=BG)
        actions.pack(fill="x", pady=(self._px(10), self._px(17)))
        self._button(actions, "Проверить сайт", self._check_gemini_now, width=16).pack(side="left")
        self._button(actions, "Открыть Gemini", self._open_gemini, width=17).pack(side="left", padx=(self._px(8), 0))
        self._page_heading(ag_page, "Antigravity", "AG Unlocker / Relay управляется отдельно от Gemini Web")
        self._card(ag_page, "ag", "AG Unlocker / Relay", "Работает независимо от VPN и DNS.",
                   self.tray._schedule_toggle_ag)
        self._page_heading(settings, "Настройки", "Горячие клавиши, автозапуск и компоненты")

        self._label(status_panel, "СОСТОЯНИЕ", size=8, color=MUTED, weight="bold", bg=SIDEBAR).pack(anchor="w")
        self._card(status_panel, "vpn", "VPN", "Подключение через Mihomo.",
                   self.tray._schedule_toggle_happ)
        self._label(status_panel, "ТЕКУЩИЙ УЗЕЛ", size=8, color=MUTED, weight="bold", bg=SIDEBAR).pack(
            anchor="w", pady=(self._px(14), self._px(4)))
        self.vpn_status_node = self._label(status_panel, "Не выбран", size=10, weight="bold",
                                           bg=SIDEBAR, wraplength=self._px(205), justify="left")
        self.vpn_status_node.pack(anchor="w")
        self.vpn_status_ping = self._label(status_panel, "Задержка · не измерена", size=9,
                                           color=MUTED, bg=SIDEBAR)
        self.vpn_status_ping.pack(anchor="w", pady=(self._px(8), 0))
        self.vpn_status_country = self._label(status_panel, "Страна выхода · не проверена", size=9,
                                              color=MUTED, bg=SIDEBAR)
        self.vpn_status_country.pack(anchor="w", pady=(self._px(5), 0))
        self.vpn_status_ip = self._label(status_panel, "Внешний IP · не проверен", size=9,
                                         color=MUTED, bg=SIDEBAR)
        self.vpn_status_ip.pack(anchor="w", pady=(self._px(5), 0))
        tk.Frame(status_panel, bg=LINE, height=1).pack(fill="x", pady=(self._px(15), self._px(12)))
        self._label(status_panel, "Relay", size=9, weight="bold", bg=SIDEBAR).pack(anchor="w")
        self._label(status_panel, "Состояние и управление на странице Antigravity.",
                    size=8, color=MUTED, bg=SIDEBAR, wraplength=self._px(205),
                    justify="left").pack(anchor="w", pady=(self._px(4), 0))

        keys = tk.Frame(settings, bg=CARD, highlightthickness=1,
                        highlightbackground=LINE, padx=self._px(18), pady=self._px(15))
        keys.pack(fill="x", pady=(0, self._px(10)))
        self._label(keys, "Горячие клавиши", size=11, weight="bold", bg=CARD).pack(anchor="w")
        self._label(keys, "Отдельная клавиша для каждого режима.", size=9,
                    color=MUTED, bg=CARD).pack(anchor="w", pady=(2, self._px(5)))
        self.key_labels = {}
        for component, title in (("happ", "VPN"), ("dns", "Gemini Web"), ("gemini", "Antigravity")):
            row = tk.Frame(keys, bg=CARD)
            row.pack(fill="x", pady=(self._px(7), 0))
            self._label(row, title, bg=CARD, width=15, anchor="w").pack(side="left")
            current = self._label(row, "", color=TEXT, bg=CARD, width=16, anchor="w")
            current.pack(side="left")
            self.key_labels[component] = current
            presets = {"happ": ("Ctrl+Alt+H", "Ctrl+Shift+H", "F8"),
                       "dns": ("Ctrl+Alt+D", "Ctrl+Shift+D", "F10"),
                       "gemini": ("Ctrl+Alt+G", "Ctrl+Shift+G", "F9")}[component]
            choice = ttk.Combobox(row, values=presets, state="readonly", style="Suite.TCombobox", width=17)
            choice.set("Выбрать сочетание")
            choice.pack(side="left", padx=(4, 7))
            choice.bind("<<ComboboxSelected>>", lambda event, name=component, widget=choice:
                        self._choose_preset(name, widget.get()))
            self._button(row, "Своя клавиша", lambda name=component:
                         self._capture_key(name), width=13).pack(side="left")

        install = tk.Frame(settings, bg=CARD, highlightthickness=1,
                           highlightbackground=LINE, padx=self._px(18), pady=self._px(14))
        install.pack(fill="x")
        self._label(install, "Компоненты", size=11, weight="bold", bg=CARD).pack(anchor="w")
        self._label(install, "Установка и состояние каждого приложения проверяются отдельно.", size=9,
                    color=MUTED, bg=CARD).pack(anchor="w", pady=(2, self._px(10)))
        component_grid = tk.Frame(install, bg=CARD)
        component_grid.pack(fill="x")
        component_grid.grid_columnconfigure(0, weight=1, uniform="component")
        component_grid.grid_columnconfigure(1, weight=1, uniform="component")
        self.mihomo_installation_label, self.mihomo_install_button = self._component_tile(
            component_grid, "Mihomo", "Установить", self._install_mihomo, 0, 0,
        )
        self.vscode_status_label, self.vscode_install_button = self._component_tile(
            component_grid, "Visual Studio Code", "Установить", self._install_or_open_vscode, 0, 1,
        )
        self.google_antigravity_status_label, self.google_antigravity_install_button = self._component_tile(
            component_grid, "Google Antigravity", "Установить", self._install_or_open_google_antigravity, 1, 0,
        )
        self.ag_unlocker_status_label, self.ag_unlocker_install_button = self._component_tile(
            component_grid, "AG Unlocker / Relay", "Установить", self._install_ag_unlocker, 1, 1,
        )
        self.autostart = tk.BooleanVar(value=autostart_enabled())
        tk.Checkbutton(settings, text="Запускать с Windows после входа в систему",
                       variable=self.autostart, command=self._set_autostart,
                       bg=BG, fg=TEXT, selectcolor=CARD, activebackground=BG,
                       activeforeground=TEXT, font=(FONT, 9), bd=0,
                       highlightthickness=0).pack(anchor="w", pady=(self._px(12), 0))
        self._button(settings, "Открыть журнал", self._open_log, width=17).pack(
            anchor="w", pady=(self._px(8), 0))
        self._label(settings, "DNS: " + " / ".join(XBOX_DNS["ipv4"]) +
                    ". Вернуть прежние настройки можно на странице Gemini Web.",
                    size=9, color=MUTED, justify="left", wraplength=self._px(690)).pack(anchor="w", pady=(self._px(12), 0))
        footer = tk.Frame(content, bg=BG)
        footer.pack(side="bottom", fill="x", pady=(self._px(8), 0))
        tk.Frame(footer, bg=LINE, height=1).pack(fill="x", pady=(0, self._px(8)))
        self.message = self._label(footer, "Готово", size=9, color=MUTED,
                                   anchor="w", justify="left", wraplength=self._px(700))
        self.message.pack(side="left", fill="x", expand=True)
        self._show_page("vpn")
        self._refresh_mihomo_installation()
        self._refresh_ag_unlocker_installation()
        self._refresh_vscode_installation()
        self._refresh_google_antigravity_installation()

    def _refresh_mihomo_installation(self):
        installation = self.mihomo_component.detect()
        self._render_component(
            installation, self.mihomo_installation_label, self.mihomo_install_button,
            install_label="Установить Mihomo", open_label="Проверить Mihomo",
        )
        return installation

    def _refresh_ag_unlocker_installation(self):
        installation = self.ag_unlocker_component.detect()
        self._render_component(
            installation, self.ag_unlocker_status_label, self.ag_unlocker_install_button,
            install_label="Установить AG Unlocker", open_label="Переключить Relay",
        )
        return installation

    def _refresh_vscode_installation(self):
        installation = self.vscode_component.detect()
        self._render_component(
            installation, self.vscode_status_label, self.vscode_install_button,
            install_label="Установить VS Code", open_label="Открыть VS Code",
        )
        return installation

    def _refresh_google_antigravity_installation(self):
        installation = self.google_antigravity_component.detect()
        self._render_component(
            installation, self.google_antigravity_status_label, self.google_antigravity_install_button,
            install_label="Установить Antigravity", open_label="Открыть Antigravity",
        )
        return installation

    def _install_or_open_google_antigravity(self):
        if self.google_antigravity_component.detect().installed:
            try:
                if self.google_antigravity_component.open():
                    self.set_message("Google Antigravity открыт.")
                else:
                    self.set_message("Не удалось найти установленный Google Antigravity.")
            except OSError as exc:
                self.set_message(f"Не удалось открыть Google Antigravity: {type(exc).__name__}")
            return
        if not self._begin_component_install(
            "google_antigravity", self.google_antigravity_install_button,
            self.google_antigravity_status_label,
        ):
            return

        def worker():
            try:
                installation = self.google_antigravity_component.install()
                self.root.after(0, self._refresh_google_antigravity_installation)
                self.set_message(f"Google Antigravity {installation.version} установлен и обнаружен.")
            except ComponentOperationError as exc:
                self.set_message(str(exc))
            finally:
                self._finish_component_install(
                    "google_antigravity", self._refresh_google_antigravity_installation,
                )

        threading.Thread(target=worker, name="InstallGoogleAntigravity", daemon=True).start()

    def _install_or_open_vscode(self):
        if self.vscode_component.detect().installed:
            try:
                if self.vscode_component.open():
                    self.set_message("Visual Studio Code открыт.")
                else:
                    self.set_message("Не удалось найти установленный Visual Studio Code.")
            except OSError as exc:
                self.set_message(f"Не удалось открыть Visual Studio Code: {type(exc).__name__}")
            return
        if not self._begin_component_install("vscode", self.vscode_install_button, self.vscode_status_label):
            return

        def worker():
            try:
                installation = self.vscode_component.install()
                self.root.after(0, self._refresh_vscode_installation)
                self.set_message(f"Visual Studio Code {installation.version} установлен и проверен.")
            except ComponentOperationError as exc:
                self.set_message(str(exc))
            finally:
                self._finish_component_install("vscode", self._refresh_vscode_installation)

        threading.Thread(target=worker, name="InstallVSCode", daemon=True).start()

    def _draw_logo(self, canvas):
        s = self._px
        canvas.create_rectangle(s(1), s(1), s(35), s(35), fill="#34414B", outline="")
        canvas.create_text(s(18), s(18), text="GPT", fill="#FFFFFF", font=(FONT, 9, "bold"))

    def _nav_button(self, parent, page, title, subtitle):
        frame = tk.Frame(parent, bg=SIDEBAR, padx=self._px(11), pady=self._px(9),
                         cursor="hand2")
        frame.pack(fill="x", pady=(0, self._px(3)))
        heading = self._label(frame, title, size=10, weight="bold", bg=SIDEBAR, cursor="hand2")
        heading.pack(anchor="w")
        detail = self._label(frame, subtitle, size=8, color=MUTED, bg=SIDEBAR,
                             cursor="hand2", wraplength=self._px(180), justify="left")
        detail.pack(anchor="w", pady=(self._px(2), 0))
        for widget in (frame, heading, detail):
            widget.bind("<Button-1>", lambda _event, name=page: self._show_page(name))
        self._nav_buttons[page] = (frame, heading, detail)
        if page != "settings":
            self.mode_status[page] = detail

    def _show_page(self, name: str):
        for page in self.pages.values():
            page.pack_forget()
        self.pages[name].pack(fill="both", expand=True)
        for page, widgets in self._nav_buttons.items():
            color = SELECTED if page == name else SIDEBAR
            for widget in widgets:
                widget.configure(bg=color)

    def _page_heading(self, parent, title: str, description: str):
        self._label(parent, title, size=19, weight="bold").pack(anchor="w")
        self._label(parent, description, size=9, color=MUTED, wraplength=self._px(690),
                    justify="left").pack(anchor="w", pady=(self._px(4), self._px(17)))

    def _card(self, parent, key, title, description, action):
        frame = tk.Frame(parent, bg=CARD,
                         highlightthickness=1, highlightbackground=LINE,
                         padx=self._px(18), pady=self._px(15))
        frame.pack(fill="x", pady=(0, self._px(11)))
        title_row = tk.Frame(frame, bg=CARD)
        title_row.pack(fill="x")
        dot = tk.Canvas(title_row, width=self._px(12), height=self._px(12),
                        bg=CARD, highlightthickness=0)
        dot.pack(side="left", padx=(0, self._px(8)))
        dot.create_oval(self._px(2), self._px(2), self._px(10), self._px(10), fill=MUTED, outline="")
        self._label(title_row, title, size=11, weight="bold", bg=CARD).pack(side="left")
        button = self._button(title_row, "Включить", action, width=13)
        button.pack(side="right")
        detail = self._label(frame, description, size=9, color=MUTED,
                             bg=CARD, justify="left",
                             wraplength=self._px(650))
        detail.pack(anchor="w", padx=(self._px(20), 0), pady=(self._px(5), 0))
        self.cards[key] = (dot, detail, button)

    def set_message(self, message: str):
        self.root.after(0, lambda: self.message.configure(text=message))

    def _reveal_subscription(self):
        hidden = bool(self.subscription.cget("show"))
        self.subscription.configure(show="" if hidden else "•")
        self.subscription_visibility.configure(text="Скрыть" if hidden else "Показать")

    def _import_subscription(self):
        url = self.subscription.get().strip()
        if not url:
            self.set_message("Вставьте ссылку подписки")
            return

        self.set_message("Проверяю подписку… ссылка останется в поле")
        logger.info("Mihomo subscription save requested")

        def worker():
            try:
                result = self.tray.orchestrator.vpn.set_subscription_url(url)
                names = [node["name"] for node in result.nodes]
                logger.info("Subscription saved; nodes=%d; provider_limit_warning=%s; fallback=%s",
                            len(names), result.device_limit_warning, result.used_mihomo_suffix)
                self.root.after(0, lambda: self.vpn_node.configure(values=names))
                warning = (" Провайдер сообщил о лимите устройств; подключение ещё нужно проверить."
                           if result.device_limit_warning else " Нажмите «Включить».")
                self.root.after(0, lambda: self._subscription_saved(
                    f"Подписка проверена: загружено узлов {len(names)}.{warning}"
                ))
            except ProviderFormatError as exc:
                logger.warning("Subscription format rejected: %s", exc)
                self.set_message(str(exc))
            except SubscriptionError as exc:
                logger.warning("Subscription rejected: %s", exc)
                self.set_message(str(exc))
            except ValueError:
                logger.warning("Subscription URL failed validation")
                self.set_message("Нужна корректная HTTPS-ссылка подписки")
            except Exception as exc:
                logger.error("Subscription save failed: %s", type(exc).__name__)
                self.set_message("Не удалось сохранить ссылку. Откройте журнал в настройках.")
        threading.Thread(target=worker, name="ImportMihomoSubscription", daemon=True).start()

    def _subscription_saved(self, message: str):
        self.set_message(message)

    def _open_log(self):
        log_path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "HappSuite" / "logs" / "happ_suite.log"
        try:
            os.startfile(log_path)
        except OSError:
            self.set_message(f"Не удалось открыть журнал: {log_path}")

    def _install_mihomo(self):
        if not self._begin_component_install(
            "mihomo", self.mihomo_install_button, self.mihomo_installation_label,
        ):
            return

        def worker():
            try:
                installation = self.mihomo_component.install()
                message = f"Mihomo {installation.version} установлен и проверен."
                self.root.after(0, self._refresh_mihomo_installation)
                self.set_message(message)
            except ComponentOperationError as exc:
                self.set_message(str(exc))
            except Exception as exc:
                self.set_message(f"Не удалось установить Mihomo: {type(exc).__name__}")
            finally:
                self._finish_component_install("mihomo", self._refresh_mihomo_installation)
        threading.Thread(target=worker, name="InstallMihomo", daemon=True).start()

    def _choose_preset(self, component: str, label: str):
        key = ord({"happ": "H", "dns": "D", "gemini": "G"}[component])
        if label.startswith("Ctrl+Alt+"):
            choice = HotkeyChoice(key, MOD_CONTROL | MOD_ALT)
        elif label.startswith("Ctrl+Shift+"):
            choice = HotkeyChoice(key, MOD_CONTROL | MOD_SHIFT)
        else:
            choice = HotkeyChoice({"happ": VK_F8, "dns": 0x79, "gemini": VK_F9}[component])
        self.tray._set_shortcut(component, choice)
        self.set_message(f"Назначена клавиша: {choice.label}")

    def _refresh_vpn_nodes(self):
        def worker():
            try:
                nodes = self.tray.orchestrator.vpn.update_nodes()
                names = [node["name"] for node in nodes]
                self.root.after(0, lambda: self.vpn_node.configure(values=names))
                self.set_message(f"Подписка обновлена, узлов: {len(names)}.")
            except Exception as exc:
                self.set_message(str(exc) if isinstance(exc, (ProviderFormatError, SubscriptionError))
                                 else f"Не удалось обновить узлы: {type(exc).__name__}")
        threading.Thread(target=worker, name="UpdateMihomoNodes", daemon=True).start()

    def _select_vpn_node(self):
        name = self.vpn_node.get().strip()
        if not name:
            self.set_message("Сначала выберите узел")
            return
        def worker():
            try:
                self.tray.orchestrator.vpn.select_node(name)
                self.set_message(f"Выбран узел: {name}")
            except Exception as exc:
                self.set_message(f"Не удалось выбрать узел: {type(exc).__name__}")
        threading.Thread(target=worker, name="SelectMihomoNode", daemon=True).start()

    def _choose_best_vpn_node(self):
        if self._best_busy:
            return
        self._best_busy = True
        self.best_button.configure(state="disabled", text="Проверяю узлы…")
        def worker():
            try:
                vpn = self.tray.orchestrator.vpn
                if not vpn.read_status(with_external_probe=False).connected:
                    nodes = vpn.provider_nodes()
                    first = next((node["name"] for node in nodes
                                  if country_hint(node["name"]) not in (None, "RU")), None)
                    if not first:
                        raise NetworkUnavailableError("В подписке нет узлов с указанной зарубежной страной")
                    vpn.select_node(first)
                    vpn.last_error = None
                    self.set_message("Подключаю зарубежный узел; затем сравню доступные узлы…")
                    self.root.after(0, lambda: self.tray._schedule_toggle_happ(best_foreign=True))
                    if not is_admin():
                        self.set_message("Подтвердите UAC: выбор узла продолжится после запуска Relay Studio.")
                        return
                    deadline = time.monotonic() + 150
                    while time.monotonic() < deadline:
                        if vpn.read_status(with_external_probe=False).connected:
                            break
                        if vpn.state == ComponentState.ERROR or vpn.last_error:
                            raise NetworkUnavailableError(vpn.last_error or "VPN не подключился")
                        time.sleep(1)
                    else:
                        raise NetworkUnavailableError("VPN не подключился за отведённое время")
                name = vpn.choose_best_foreign_node(progress=self.set_message)
                self.root.after(0, lambda: self.vpn_node.set(name))
                self.set_message(f"Выбран зарубежный узел: {name}. Выход: {vpn.last_exit_country}.")
            except Exception as exc:
                self.set_message(str(exc) if isinstance(exc, (NetworkUnavailableError, ProviderFormatError, SubscriptionError))
                                 else f"Не удалось проверить узлы: {type(exc).__name__}")
            finally:
                self._best_busy = False
                self.root.after(0, lambda: self.best_button.configure(
                    state="normal", text="Выбрать лучший зарубежный узел"))
        threading.Thread(target=worker, name="ChooseBestMihomoNode", daemon=True).start()
    def _capture_key(self, component: str):
        self.tray._capture_shortcut(component)
        self.set_message("Нажмите клавишу в течение 10 секунд; Esc — отмена")

    def _install_ag_unlocker(self):
        if self.ag_unlocker_component.detect().installed:
            self.tray._schedule_toggle_ag()
            return
        if not self._begin_component_install(
            "ag_unlocker", self.ag_unlocker_install_button, self.ag_unlocker_status_label,
        ):
            return

        def worker():
            try:
                installation = self.ag_unlocker_component.install()
                if installation.status is ComponentStatus.INSTALLING:
                    self.root.after(0, self._track_ag_unlocker_installer)
                    self.set_message(f"Открыт официальный установщик {installation.display_name}.")
            except ComponentOperationError as exc:
                self.set_message(str(exc))
            finally:
                self._finish_component_install("ag_unlocker", self._refresh_ag_unlocker_installation)

        threading.Thread(target=worker, name="InstallAGUnlocker", daemon=True).start()

    def _track_ag_unlocker_installer(self):
        self._component_install_pending.add("ag_unlocker")
        self._component_install_deadlines["ag_unlocker"] = time.monotonic() + 300
        self.ag_unlocker_status_label.configure(text="Официальный установщик открыт · завершите настройку", fg=YELLOW)
        self.ag_unlocker_install_button.configure(text="Проверка установки…", state="disabled")
        self.root.after(2500, self._poll_ag_unlocker_installation)

    def _poll_ag_unlocker_installation(self):
        if "ag_unlocker" not in self._component_install_pending:
            return

        def worker():
            try:
                installation = self.ag_unlocker_component.detect()
            except Exception:
                installation = None
            self.root.after(0, lambda: self._finish_ag_unlocker_detection(installation))

        threading.Thread(target=worker, name="DetectAGUnlocker", daemon=True).start()

    def _finish_ag_unlocker_detection(self, installation):
        if "ag_unlocker" not in self._component_install_pending:
            return
        if installation is not None and installation.installed:
            self._component_install_pending.discard("ag_unlocker")
            self._component_install_deadlines.pop("ag_unlocker", None)
            self._refresh_ag_unlocker_installation()
            self.set_message("AG Unlocker / Relay установлен и обнаружен.")
            return
        if time.monotonic() >= self._component_install_deadlines.get("ag_unlocker", 0):
            self._component_install_pending.discard("ag_unlocker")
            self._component_install_deadlines.pop("ag_unlocker", None)
            self._refresh_ag_unlocker_installation()
            self.set_message("AG Unlocker пока не обнаружен. Закройте установщик и повторите проверку.")
            return
        self.root.after(2500, self._poll_ag_unlocker_installation)

    def _set_autostart(self):
        try:
            set_autostart(self.autostart.get())
            self.set_message("Автозапуск обновлён")
        except (OSError, RuntimeError):
            self.autostart.set(autostart_enabled())
            self.set_message("Автозапуск доступен в собранном start.exe")

    def _check_gemini_now(self):
        if self._gemini_probe_running:
            return
        self._gemini_probe_running = True
        self.gemini_site_status = "Проверяю сеть, Gemini Web, DNS и регион…"
        self.root.after(0, self._refresh)

        def worker():
            try:
                self.gemini_availability = self.gemini_availability_client.check()
                self.gemini_site_status = self._format_gemini_availability(self.gemini_availability)
            except Exception as exc:
                self.gemini_site_status = f"Диагностика Gemini не завершена: {type(exc).__name__}"
            finally:
                self._gemini_probe_running = False
                self.root.after(0, self._refresh)

        threading.Thread(target=worker, name="GeminiWebProbe", daemon=True).start()

    @staticmethod
    def _format_gemini_availability(status: GeminiNetworkStatus) -> str:
        yes_no_unknown = lambda value: "✓" if value is True else "×" if value is False else "?"
        website_text, account_text = gemini_access_summary(status)
        country = status.exit_country or "не определена"
        region = {
            RegionSupport.SUPPORTED: "поддерживается",
            RegionSupport.UNSUPPORTED: "не поддерживается",
            RegionSupport.UNKNOWN: "неизвестно",
        }[status.region_supported]
        website = yes_no_unknown(status.website_reachable)
        if status.website_http_status is not None:
            website += f" (HTTP {status.website_http_status})"
        return "\n".join((
            f"Сеть: {yes_no_unknown(status.network_available)} · "
            f"сайт: {website} · {website_text} · "
            f"DNS Suite: {yes_no_unknown(status.dns_managed)}",
            f"Страна выхода: {country} · регион Gemini: {region}",
            account_text,
        ))

    def _open_gemini(self):
        webbrowser.open("https://gemini.google.com/app")

    def _read_dns_status(self):
        if self._dns_refreshing or self._dns_busy:
            return
        self._dns_refreshing = True

        def worker():
            try:
                self._dns_status = DnsManager().status()
                self._dns_error = ""
            except Exception as exc:
                self._dns_error = str(exc)
            finally:
                self._dns_refreshing = False
                self._next_dns_refresh = time.monotonic() + 15
        threading.Thread(target=worker, name="GeminiDnsStatus", daemon=True).start()

    def _toggle_gemini_dns(self):
        if self._dns_busy or self._dns_refreshing or self._dns_status is None or self._dns_error:
            self.set_message("DNS ещё проверяется. Повторите переключение через несколько секунд.")
            return
        action = "disable" if self._dns_status["managed"] else "enable"
        self._dns_busy = True
        self.set_message("Windows запросит разрешение изменить DNS текущей сети")

        def worker():
            try:
                result = apply_action(action)
                self.set_message(result["message"])
                self.tray._notify(self.tray._notification_icon("gemini"), result["message"], "Gemini Web DNS")
                self._dns_status = DnsManager().status()
                self.gemini_site_status = "Сайт ещё не проверен"
                if action == "enable":
                    self._check_gemini_now()
            except Exception as exc:
                self.set_message(str(exc))
                self.tray._notify(self.tray._notification_icon("gemini"), str(exc), "Gemini Web — ошибка")
            finally:
                self._dns_busy = False
                self._next_dns_refresh = 0
        threading.Thread(target=worker, name="GeminiDnsChange", daemon=True).start()

    def _refresh(self):
        palette = {
            ComponentState.RUNNING: GREEN,
            ComponentState.DEGRADED: YELLOW,
            ComponentState.STARTING: YELLOW,
            ComponentState.STOPPING: YELLOW,
            ComponentState.ERROR: RED,
            ComponentState.STOPPED: MUTED,
        }
        for key, state, off_detail, on_detail in (
            ("vpn", self.tray.orchestrator.vpn.state, "VPN выключен", "VPN подключён"),
            ("ag", self.tray.orchestrator.ag_unlocker.state, "Relay выключен", "Relay работает"),
        ):
            dot, detail, button = self.cards[key]
            if key == "vpn":
                vpn_error = getattr(self.tray.orchestrator.vpn, "last_error", None)
                presentation = vpn_presentation(state, vpn_error)
                tone = {"success": GREEN, "warning": YELLOW, "busy": YELLOW,
                        "error": RED, "muted": MUTED}.get(presentation.tone, MUTED)
                dot.itemconfigure(1, fill=tone)
                detail.configure(text=presentation.label)
                button.configure(
                    text=presentation.action,
                    state="disabled" if presentation.tone == "busy" else "normal",
                )
                continue
            dot.itemconfigure(1, fill=palette.get(state, MUTED))
            detail.configure(text=on_detail if state == ComponentState.RUNNING else
                             off_detail if state == ComponentState.STOPPED else
                             "Подключение…" if state in (ComponentState.STARTING, ComponentState.STOPPING) else
                             "Ожидает подтверждения" if state == ComponentState.DEGRADED else "Ошибка")
            if state == ComponentState.ERROR and key == "vpn":
                vpn_error = getattr(self.tray.orchestrator.vpn, "last_error", None)
                if vpn_error:
                    detail.configure(text=vpn_error)
            button.configure(text="Выключить" if state in (ComponentState.RUNNING, ComponentState.DEGRADED)
                             else "Включить")
        if time.monotonic() >= self._next_dns_refresh:
            self._read_dns_status()
        dot, detail, button = self.cards["gemini"]
        status = self._dns_status
        text = self._dns_error or (status["message"] if status else "Проверяю DNS текущей сети…")
        color = RED if self._dns_error else GREEN if status and status["state"] == "on" else MUTED
        if status and status["state"] == "changed":
            color = YELLOW
        if status and status["vpn_active"]:
            text += "\nVPN может использовать собственный DNS."
        detail.configure(text=text + "\n" + self.gemini_site_status)
        dot.itemconfigure(1, fill=YELLOW if self._dns_busy else color)
        button.configure(text="Подождите…" if self._dns_busy else "Вернуть DNS" if status and status["managed"] else "Включить DNS",
                         state="disabled" if self._dns_busy or self._dns_refreshing or status is None or self._dns_error else "normal")
        vpn_state = self.tray.orchestrator.vpn.state
        ag_state = self.tray.orchestrator.ag_unlocker.state
        self.mode_status["vpn"].configure(text={
            ComponentState.RUNNING: "Подключён",
            ComponentState.STOPPED: "Выключен",
            ComponentState.ERROR: "Ошибка подключения",
        }.get(vpn_state, "Подключение…"), fg=palette.get(vpn_state, MUTED))
        dns_text = "DNS: проверка" if status is None else (
            "DNS включён" if status["managed"] else "DNS выключен")
        ag_text = "relay работает" if ag_state == ComponentState.RUNNING else (
            "relay выключен" if ag_state == ComponentState.STOPPED else "relay: проверка")
        self.mode_status["google"].configure(
            text=f"{dns_text} · {ag_text}",
            fg=RED if self._dns_error or ag_state == ComponentState.ERROR else
               GREEN if status and status["managed"] and ag_state == ComponentState.RUNNING else MUTED)
        self.mode_status["ag"].configure(
            text="Relay работает" if ag_state == ComponentState.RUNNING else
                 "Relay выключен" if ag_state == ComponentState.STOPPED else "Relay: проверка",
            fg=palette.get(ag_state, MUTED),
        )
        selected_node = self.vpn_node.get().strip() if hasattr(self, "vpn_node") else ""
        self.vpn_status_node.configure(text=selected_node or "Узел не выбран")
        self.vpn_status_ping.configure(
            text="Задержка · не измерена" if vpn_state != ComponentState.RUNNING
                 else "Задержка · доступна при проверке узлов",
        )
        if vpn_state == ComponentState.RUNNING:
            diagnostics = self.gemini_availability
            exit_country = diagnostics.exit_country if diagnostics else None
            exit_country = exit_country or getattr(self.tray.orchestrator.vpn, "last_exit_country", None)
            self.vpn_status_country.configure(
                text=f"Страна выхода · {exit_country}" if exit_country else "Страна выхода · не проверена",
            )
            external_ip = diagnostics.external_ip if diagnostics else None
            self.vpn_status_ip.configure(
                text=f"Внешний IP · {external_ip}" if external_ip else "Внешний IP · не проверен",
            )
        else:
            self.vpn_status_country.configure(text="Страна выхода · —")
            self.vpn_status_ip.configure(text="Внешний IP · —")
        for key, widget in self.key_labels.items():
            widget.configure(text=self.tray._hotkey_choices[key].label)
        self.root.after(1000, self._refresh)

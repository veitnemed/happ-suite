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
    from .core import ComponentState
    from .hotkey import HotkeyChoice, MOD_ALT, MOD_CONTROL, MOD_SHIFT, VK_F8, VK_F9
    from .official_installers import AG_UNLOCKER, download, run_installer
    from .mihomo_installer import install_mihomo
    from .node_region import country_hint
    from .windows_elevation import is_admin
    from .autostart import is_enabled as autostart_enabled, set_enabled as set_autostart
    from .gemini_dns import DnsManager, apply_action, XBOX_DNS
    from .vpn_backend import NetworkUnavailableError, ProviderFormatError, SubscriptionError
except ImportError:
    from core import ComponentState
    from hotkey import HotkeyChoice, MOD_ALT, MOD_CONTROL, MOD_SHIFT, VK_F8, VK_F9
    from official_installers import AG_UNLOCKER, download, run_installer
    from mihomo_installer import install_mihomo
    from node_region import country_hint
    from windows_elevation import is_admin
    from autostart import is_enabled as autostart_enabled, set_enabled as set_autostart
    from gemini_dns import DnsManager, apply_action, XBOX_DNS
    from vpn_backend import NetworkUnavailableError, ProviderFormatError, SubscriptionError


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
        self.gemini_site_status = "Сайт ещё не проверен"
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
        self._build()
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
        width = min(self._px(960), screen_w - self._px(48))
        height = min(self._px(680), screen_h - self._px(64))
        root.geometry(f"{width}x{height}")
        root.minsize(min(self._px(820), width), min(self._px(590), height))
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
        sidebar = tk.Frame(body, bg=SIDEBAR, width=self._px(220), padx=self._px(10), pady=self._px(15),
                           highlightthickness=1, highlightbackground=LINE)
        sidebar.pack(side="left", fill="y", padx=(0, self._px(18)))
        sidebar.pack_propagate(False)
        self._label(sidebar, "Разделы", size=9, color=MUTED, bg=SIDEBAR).pack(
            anchor="w", padx=self._px(11), pady=(0, self._px(11)))
        self._nav_buttons = {}
        self.mode_status = {}
        self._nav_button(sidebar, "vpn", "VPN", "Mihomo")
        self._nav_button(sidebar, "google", "Gemini и Antigravity", "DNS и relay")
        tk.Frame(sidebar, bg=LINE, height=1).pack(fill="x", pady=(self._px(13), self._px(10)))
        self._nav_button(sidebar, "settings", "Настройки", "Клавиши и автозапуск")
        self._label(sidebar, "Состояние каждого режима\nпоказано отдельно.", size=8, color=MUTED,
                    bg=SIDEBAR, justify="left").pack(side="bottom", anchor="w", padx=self._px(11))

        workspace = tk.Frame(body, bg=BG)
        workspace.pack(side="left", fill="both", expand=True)
        self.pages = {name: tk.Frame(workspace, bg=BG) for name in ("vpn", "google", "settings")}
        vpn, google, settings = (self.pages[name] for name in ("vpn", "google", "settings"))
        self.cards = {}

        self._page_heading(vpn, "VPN", "Подключение через Mihomo")
        self._card(vpn, "vpn", "Соединение", "Маршрут и доступность проверяются после подключения.",
                   self.tray._schedule_toggle_happ)

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

        self._page_heading(google, "Gemini и Antigravity", "Настройки работают независимо друг от друга")
        self._card(google, "gemini", "Gemini Web DNS", "Проверяю текущие настройки…",
                   self._toggle_gemini_dns)
        actions = tk.Frame(google, bg=BG)
        actions.pack(fill="x", pady=(self._px(10), self._px(17)))
        self._button(actions, "Проверить сайт", self._check_gemini_now, width=16).pack(side="left")
        self._button(actions, "Открыть Gemini", self._open_gemini, width=17).pack(side="left", padx=(self._px(8), 0))
        self._card(google, "ag", "Antigravity relay", "Запускается и выключается независимо от VPN и DNS.",
                   self.tray._schedule_toggle_ag)
        self._page_heading(settings, "Настройки", "Горячие клавиши, автозапуск и компоненты")

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
        self._label(install, "Установщики проверяются по контрольной сумме.", size=9,
                    color=MUTED, bg=CARD).pack(anchor="w", pady=(2, self._px(10)))
        bottom = tk.Frame(install, bg=CARD)
        bottom.pack(fill="x")
        self._button(bottom, "Установить Mihomo", self._install_mihomo, width=18).pack(side="left")
        self._button(bottom, "Установить AG", lambda: self._install(AG_UNLOCKER), width=16).pack(side="left", padx=(self._px(8), 0))
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
        def worker():
            try:
                self.set_message("Загружаю Mihomo и проверяю SHA-256…")
                path, _ = install_mihomo()
                self.set_message(f"Mihomo установлен: {path.name}")
            except Exception as exc:
                self.set_message(f"Не удалось установить Mihomo: {type(exc).__name__}")
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

    def _install(self, installer):
        def worker():
            try:
                self.set_message(f"Загрузка {installer.name}…")
                path = download(installer)
                self.set_message(f"Запускаю официальный {installer.name}…")
                run_installer(path)
                self.set_message("Завершите настройку в окне установщика")
            except Exception:
                self.set_message(f"Не удалось загрузить {installer.name}")

        threading.Thread(target=worker, name="InstallOfficialComponents", daemon=True).start()

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
        self.gemini_site_status = "Проверяю HTTPS…"

        def worker():
            try:
                with requests.Session() as session:
                    session.trust_env = False
                    response = session.get("https://gemini.google.com", timeout=6)
                    self.gemini_site_status = (
                        "Сайт отвечает; модель проверяется в браузере" if 200 <= response.status_code < 400
                        else f"Сайт вернул HTTP {response.status_code}"
                    )
            except requests.RequestException:
                self.gemini_site_status = "Сайт недоступен по текущему маршруту"
            finally:
                self._gemini_probe_running = False

        threading.Thread(target=worker, name="GeminiWebProbe", daemon=True).start()

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
        for key, widget in self.key_labels.items():
            widget.configure(text=self.tray._hotkey_choices[key].label)
        self.root.after(1000, self._refresh)

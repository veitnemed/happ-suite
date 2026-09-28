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

try:
    from .core import ComponentState
    from .hotkey import HotkeyChoice, MOD_ALT, MOD_CONTROL, MOD_SHIFT, VK_F8, VK_F9
    from .happ_ipc import HappIpcClient
    from .official_installers import HAPP, AG_UNLOCKER, download, run_installer
    from .mihomo_installer import install_mihomo
    from .autostart import is_enabled as autostart_enabled, set_enabled as set_autostart
    from .gemini_dns import DnsManager, apply_action, XBOX_DNS
    from .vpn_backend import NetworkUnavailableError, ProviderFormatError, SubscriptionError
except ImportError:
    from core import ComponentState
    from hotkey import HotkeyChoice, MOD_ALT, MOD_CONTROL, MOD_SHIFT, VK_F8, VK_F9
    from happ_ipc import HappIpcClient
    from official_installers import HAPP, AG_UNLOCKER, download, run_installer
    from mihomo_installer import install_mihomo
    from autostart import is_enabled as autostart_enabled, set_enabled as set_autostart
    from gemini_dns import DnsManager, apply_action, XBOX_DNS
    from vpn_backend import NetworkUnavailableError, ProviderFormatError, SubscriptionError


BG = "#0B1016"
CARD = "#121A24"
CARD_RAISED = "#17212D"
FIELD = "#0E151E"
TEXT = "#F3F6F8"
MUTED = "#91A0AF"
BLUE = "#9AAEFF"
GREEN = "#76E0BD"
YELLOW = "#F0C979"
RED = "#F17D83"
LINE = "#263442"
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
                         font=(FONT, 9, "bold"), relief="flat", bd=0,
                         bg=GREEN if filled else CARD_RAISED, fg=BG if filled else TEXT,
                         activebackground="#99EBD0" if filled else "#263545",
                         activeforeground=BG if filled else TEXT, cursor="hand2",
                         padx=self._px(13), pady=self._px(9),
                         highlightthickness=0, takefocus=True)

    def _build(self):
        root = self.root
        root.title("Happ Suite")
        root.configure(bg=BG)
        app_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
        icon_path = app_root / "assets" / "happ-suite.ico"
        if icon_path.is_file():
            try:
                root.iconbitmap(default=str(icon_path))
            except tk.TclError:
                pass
        screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
        width = max(self._px(520), min(self._px(920), screen_w - self._px(64)))
        height = max(self._px(500), min(self._px(690), screen_h - self._px(72)))
        root.geometry(f"{width}x{height}")
        root.minsize(min(self._px(720), width), min(self._px(600), height))
        root.protocol("WM_DELETE_WINDOW", root.withdraw)
        style = ttk.Style(root)
        style.configure("Suite.TCombobox", fieldbackground=FIELD, background=FIELD,
                        foreground=TEXT, arrowcolor=MUTED, bordercolor=LINE,
                        lightcolor=LINE, darkcolor=LINE, padding=self._px(7),
                        font=(FONT, 9))
        style.map("Suite.TCombobox", fieldbackground=[("readonly", FIELD)],
                  foreground=[("readonly", TEXT)])

        content = tk.Frame(root, bg=BG, padx=self._px(28), pady=self._px(22))
        content.pack(fill="both", expand=True)
        header = tk.Frame(content, bg=BG)
        header.pack(fill="x")
        brand = tk.Frame(header, bg=BG)
        brand.pack(side="left", fill="x", expand=True)
        mark = tk.Canvas(brand, width=self._px(44), height=self._px(44), bg=BG,
                         bd=0, highlightthickness=0)
        mark.pack(side="left", padx=(0, self._px(12)))
        self._draw_logo(mark)
        title_group = tk.Frame(brand, bg=BG)
        title_group.pack(side="left", anchor="center")
        self._label(title_group, "Happ Suite", size=19, weight="bold").pack(anchor="w")
        self._label(title_group, "SECURE ACCESS  ·  SYSTEM CONTROL", size=8,
                    color=MUTED, weight="bold").pack(anchor="w", pady=(1, 0))
        self._button(header, "Свернуть", root.withdraw, width=11).pack(side="right", anchor="center")

        tabs = tk.Frame(content, bg=BG)
        tabs.pack(fill="x", pady=(self._px(20), self._px(16)))
        self._nav_button(tabs, "control", "Обзор")
        self._nav_button(tabs, "settings", "Настройки")
        self.pages = {
            "control": tk.Frame(content, bg=BG),
            "settings": tk.Frame(content, bg=BG),
        }
        control = self.pages["control"]
        settings = self.pages["settings"]

        self.cards = {}
        self._label(control, "Подключение", size=10, color=MUTED, weight="bold").pack(
            anchor="w", pady=(0, self._px(8)))
        self._card(control, "happ", "Mihomo VPN", "Зашифрованное соединение через выбранный узел",
                   lambda: self.tray._schedule_toggle_happ(), hero=True)

        secondary = tk.Frame(control, bg=BG)
        secondary.pack(fill="x", pady=(self._px(12), self._px(12)))
        self._card(secondary, "ag", "Antigravity", "Локальный ретранслятор",
                   lambda: self.tray._schedule_toggle_ag(), compact=True)
        self._card(secondary, "gemini", "Gemini Web", "DNS и доступность сайта",
                   self._toggle_gemini_dns, toggle=False, compact=True)

        subscription = tk.Frame(control, bg=CARD, highlightthickness=1,
                                highlightbackground=LINE, padx=self._px(16), pady=self._px(12))
        subscription.pack(fill="x")
        heading = tk.Frame(subscription, bg=CARD)
        heading.pack(fill="x", pady=(0, self._px(8)))
        self._label(heading, "Подписка Mihomo", size=11, weight="bold", bg=CARD).pack(side="left")
        self._label(heading, "HTTPS  ·  ШИФРОВАНИЕ DPAPI", size=8, color=GREEN, weight="bold", bg=CARD).pack(side="right")
        self._label(subscription, "Ссылка хранится локально в зашифрованном виде.",
                    size=9, color=MUTED, bg=CARD).pack(anchor="w", pady=(0, self._px(8)))
        row = tk.Frame(subscription, bg=CARD)
        row.pack(fill="x")
        self.subscription = tk.Entry(row, bg=FIELD, fg=TEXT, insertbackground=GREEN,
                                     relief="flat", font=(FONT, 10), show="",
                                     highlightthickness=1, highlightbackground=LINE,
                                     highlightcolor=GREEN, bd=0)
        self.subscription.pack(side="left", fill="x", expand=True, ipady=self._px(8))
        self.subscription_visibility = self._button(row, "Скрыть", self._reveal_subscription, width=9)
        self.subscription_visibility.pack(side="left", padx=(self._px(8), 0))
        self._button(row, "Сохранить", self._import_subscription, filled=True, width=11).pack(side="left", padx=(self._px(8), 0))
        node_row = tk.Frame(subscription, bg=CARD)
        node_row.pack(fill="x", pady=(self._px(9), 0))
        self.vpn_node = ttk.Combobox(node_row, state="readonly", style="Suite.TCombobox", width=34)
        self.vpn_node.pack(side="left", fill="x", expand=True)
        self._button(node_row, "Обновить узлы", self._refresh_vpn_nodes, width=14).pack(side="left", padx=(self._px(8), 0))
        self._button(node_row, "Выбрать", self._select_vpn_node, width=10).pack(side="left", padx=(self._px(6), 0))
        self._button(node_row, "Лучший", self._choose_best_vpn_node, width=9).pack(side="left", padx=(self._px(6), 0))

        keys = tk.Frame(settings, bg=CARD, highlightthickness=1,
                        highlightbackground=LINE, padx=self._px(18), pady=self._px(15))
        keys.pack(fill="x", pady=(0, self._px(10)))
        self._label(keys, "Горячие клавиши", size=11, weight="bold", bg=CARD).pack(anchor="w")
        self._label(keys, "Быстрый доступ к основным действиям Suite.", size=9,
                    color=MUTED, bg=CARD).pack(anchor="w", pady=(2, self._px(5)))
        self.key_labels = {}
        for component, title in (("happ", "VPN"), ("gemini", "Antigravity")):
            row = tk.Frame(keys, bg=CARD)
            row.pack(fill="x", pady=(self._px(7), 0))
            self._label(row, title, bg=CARD, width=15, anchor="w").pack(side="left")
            current = self._label(row, "", color=BLUE, bg=CARD, width=16, anchor="w")
            current.pack(side="left")
            self.key_labels[component] = current
            presets = ("Ctrl+Alt+H", "Ctrl+Shift+H", "F8") if component == "happ" else (
                "Ctrl+Alt+G", "Ctrl+Shift+G", "F9")
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
        self._button(bottom, "Установить Mihomo", self._install_mihomo, filled=True, width=18).pack(side="left")
        if self.variant == "setup":
            self._button(bottom, "HAPP · совместимость", lambda: self._install(HAPP), width=20).pack(side="left", padx=(self._px(8), 0))
        self._button(bottom, "Установить AG", lambda: self._install(AG_UNLOCKER), width=16).pack(side="left", padx=(self._px(8), 0))
        self.autostart = tk.BooleanVar(value=autostart_enabled())
        tk.Checkbutton(settings, text="Запускать с Windows после входа в систему",
                       variable=self.autostart, command=self._set_autostart,
                       bg=BG, fg=MUTED, selectcolor=FIELD, activebackground=BG,
                       activeforeground=TEXT, font=(FONT, 9), bd=0,
                       highlightthickness=0).pack(anchor="w", pady=(self._px(12), 0))
        self._button(settings, "Открыть журнал", self._open_log, width=17).pack(
            anchor="w", pady=(self._px(8), 0))
        self._label(settings,
                    "Gemini Web: Xbox DNS " + " / ".join(XBOX_DNS["ipv4"]) +
                    ".\nПрименяется к текущему Wi-Fi/Ethernet и сохраняется после выхода.\n"
                    "Кнопка «Вернуть DNS» восстановит прежние настройки.\n"
                    "При смене сети сначала верните DNS предыдущего подключения.\n"
                    "VPN и безопасный DNS браузера могут использовать другие серверы.",
                    size=9, color=MUTED, justify="left", wraplength=self._px(820)).pack(anchor="w", pady=(self._px(12), 0))
        footer = tk.Frame(content, bg=BG)
        footer.pack(side="bottom", fill="x", pady=(self._px(10), 0))
        tk.Frame(footer, bg=LINE, height=1).pack(fill="x", pady=(0, self._px(8)))
        self.message = self._label(footer, "Готово к работе", size=9, color=MUTED,
                                   anchor="w", justify="left", wraplength=self._px(710))
        self.message.pack(side="left", fill="x", expand=True)
        self._label(footer, "HAPP SUITE  /  WINDOWS", size=8, color="#637283", weight="bold").pack(side="right")
        self._show_page("control")

    def _draw_logo(self, canvas):
        s = self._px
        canvas.create_oval(s(2), s(2), s(42), s(42), fill=CARD_RAISED, outline=LINE, width=s(1))
        canvas.create_oval(s(8), s(8), s(36), s(36), fill=BG, outline="#314758", width=s(1))
        canvas.create_line(s(15), s(28), s(15), s(16), fill=GREEN, width=s(3), capstyle="round")
        canvas.create_line(s(29), s(28), s(29), s(16), fill=BLUE, width=s(3), capstyle="round")
        canvas.create_line(s(15), s(22), s(29), s(22), fill=GREEN, width=s(3), capstyle="round")
        canvas.create_oval(s(20), s(19), s(24), s(23), fill=TEXT, outline="")

    def _nav_button(self, parent, page, title):
        button = tk.Button(parent, text=title, command=lambda: self._show_page(page),
                           font=(FONT, 9, "bold"), relief="flat", bd=0, cursor="hand2",
                           padx=self._px(16), pady=self._px(8), highlightthickness=0,
                           bg=FIELD, fg=MUTED, activebackground=CARD_RAISED,
                           activeforeground=TEXT)
        button.pack(side="left", padx=(0, self._px(7)))
        self._nav_buttons[page] = button

    def _show_page(self, name: str):
        for page in self.pages.values():
            page.pack_forget()
        self.pages[name].pack(fill="both", expand=True)
        for page, button in self._nav_buttons.items():
            active = page == name
            button.configure(bg=CARD_RAISED if active else FIELD,
                             fg=TEXT if active else MUTED,
                             highlightthickness=1 if active else 0,
                             highlightbackground=LINE)

    def _card(self, parent, key, title, description, action, *, toggle=True,
              hero=False, compact=False):
        frame = tk.Frame(parent, bg=CARD_RAISED if hero else CARD,
                         highlightthickness=1, highlightbackground=LINE,
                         padx=self._px(18 if hero else 14),
                         pady=self._px(15 if hero else 11))
        frame.pack(side="left" if compact else "top", fill="both" if compact else "x",
                   expand=compact, padx=(0, self._px(8)) if compact and key == "ag" else
                   (self._px(4), 0) if compact else 0,
                   pady=0 if compact else (0, self._px(10)))
        title_row = tk.Frame(frame, bg=CARD_RAISED if hero else CARD)
        title_row.pack(fill="x")
        dot = tk.Canvas(title_row, width=self._px(12), height=self._px(12),
                        bg=CARD_RAISED if hero else CARD, highlightthickness=0)
        dot.pack(side="left", padx=(0, self._px(8)))
        dot.create_oval(self._px(2), self._px(2), self._px(10), self._px(10), fill=MUTED, outline="")
        self._label(title_row, title, size=12 if hero else 10, weight="bold",
                    bg=CARD_RAISED if hero else CARD).pack(side="left")
        button = self._button(title_row, "Подключить" if toggle else "Проверить", action,
                              filled=hero, width=13 if hero else 11)
        button.pack(side="right")
        detail = self._label(frame, description, size=9, color=MUTED,
                             bg=CARD_RAISED if hero else CARD, justify="left",
                             wraplength=self._px(760 if hero else 320))
        detail.pack(anchor="w", padx=(self._px(20), 0), pady=(self._px(5), 0))
        if hero:
            self._label(frame, "MIHOMO  ·  TUN  ·  СКВОЗНОЕ ШИФРОВАНИЕ",
                        size=8, color="#7E91A4", weight="bold",
                        bg=CARD_RAISED).pack(anchor="w", padx=(self._px(20), 0),
                                             pady=(self._px(8), 0))
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
        logger.info("Subscription save requested; backend=%s", self.config.vpn_backend)

        def worker():
            try:
                if self.config.vpn_backend == "happ":
                    HappIpcClient(self.config.happ_exe).import_subscription_url(url)
                    self.root.after(0, lambda: self._subscription_saved("Ссылка передана в HAPP."))
                else:
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
        threading.Thread(target=worker, name="ImportHappSubscription", daemon=True).start()

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
        key = ord("H" if component == "happ" else "G")
        if label.startswith("Ctrl+Alt+"):
            choice = HotkeyChoice(key, MOD_CONTROL | MOD_ALT)
        elif label.startswith("Ctrl+Shift+"):
            choice = HotkeyChoice(key, MOD_CONTROL | MOD_SHIFT)
        else:
            choice = HotkeyChoice(VK_F8 if component == "happ" else VK_F9)
        self.tray._set_shortcut(component, choice)
        self.set_message(f"Shortcut assigned: {choice.label}")

    def _refresh_vpn_nodes(self):
        if self.config.vpn_backend != "mihomo":
            self.set_message("Выбор узла доступен в режиме Mihomo.")
            return
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
            self.set_message("Choose a node first.")
            return
        def worker():
            try:
                self.tray.orchestrator.vpn.select_node(name)
                self.set_message(f"Выбран узел: {name}")
            except Exception as exc:
                self.set_message(f"Не удалось выбрать узел: {type(exc).__name__}")
        threading.Thread(target=worker, name="SelectMihomoNode", daemon=True).start()

    def _choose_best_vpn_node(self):
        def worker():
            try:
                name = self.tray.orchestrator.vpn.choose_best_node()
                if name:
                    self.root.after(0, lambda: self.vpn_node.set(name))
                    self.set_message(f"Выбран лучший узел: {name}")
            except Exception as exc:
                self.set_message(str(exc) if isinstance(exc, (NetworkUnavailableError, ProviderFormatError, SubscriptionError))
                                 else f"Не удалось проверить узлы: {type(exc).__name__}")
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
            return
        action = "disable" if self._dns_status["managed"] else "enable"
        self._dns_busy = True
        self.set_message("Windows запросит разрешение изменить DNS текущей сети")

        def worker():
            try:
                result = apply_action(action)
                self.set_message(result["message"])
                self._dns_status = DnsManager().status()
                self.gemini_site_status = "Сайт ещё не проверен"
                if action == "enable":
                    self._check_gemini_now()
            except Exception as exc:
                self.set_message(str(exc))
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
            ("happ", self.tray.orchestrator.happ.state, "VPN выключен", "VPN подключён"),
            ("ag", self.tray.orchestrator.ag_unlocker.state, "Relay выключен", "Relay работает"),
        ):
            dot, detail, button = self.cards[key]
            dot.itemconfigure(1, fill=palette.get(state, MUTED))
            detail.configure(text=on_detail if state == ComponentState.RUNNING else
                             off_detail if state == ComponentState.STOPPED else
                             "Подключение…" if state in (ComponentState.STARTING, ComponentState.STOPPING) else
                             "Ожидает подтверждения" if state == ComponentState.DEGRADED else "Ошибка")
            if state == ComponentState.ERROR and key == "happ":
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
        for key, widget in self.key_labels.items():
            widget.configure(text=self.tray._hotkey_choices[key].label)
        self.root.after(1000, self._refresh)

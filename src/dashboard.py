"""Compact first-run dashboard; tray and hotkeys keep running when hidden."""

from __future__ import annotations

import json
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
    from .autostart import is_enabled as autostart_enabled, set_enabled as set_autostart
    from .gemini_dns import DnsManager, apply_action, XBOX_DNS
except ImportError:
    from core import ComponentState
    from hotkey import HotkeyChoice, MOD_ALT, MOD_CONTROL, MOD_SHIFT, VK_F8, VK_F9
    from happ_ipc import HappIpcClient
    from official_installers import HAPP, AG_UNLOCKER, download, run_installer
    from autostart import is_enabled as autostart_enabled, set_enabled as set_autostart
    from gemini_dns import DnsManager, apply_action, XBOX_DNS


BG = "#10141C"
CARD = "#1B2230"
FIELD = "#111722"
TEXT = "#F3F6FC"
MUTED = "#9DAABE"
BLUE = "#6C9DFF"
GREEN = "#54D49A"
YELLOW = "#F3CA64"
RED = "#F37476"


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
        self._build()
        self._refresh()

    def _label(self, parent, text, size=10, color=TEXT, weight="normal", **options):
        return tk.Label(parent, text=text, fg=color, bg=options.pop("bg", BG),
                        font=("Segoe UI", size, weight), **options)

    def _button(self, parent, text, command, *, filled=False, width=15):
        return tk.Button(parent, text=text, command=command, width=width,
                         font=("Segoe UI", 10, "bold"), relief="flat", bd=0,
                         bg=BLUE if filled else FIELD, fg=BG if filled else TEXT,
                         activebackground="#83AEFF" if filled else "#2B3545",
                         activeforeground=BG if filled else TEXT, cursor="hand2",
                         padx=10, pady=8)

    def _build(self):
        root = self.root
        root.title("Happ Suite")
        root.configure(bg=BG)
        root.geometry("690x735")
        root.minsize(650, 710)
        root.protocol("WM_DELETE_WINDOW", root.withdraw)

        content = tk.Frame(root, bg=BG, padx=26, pady=22)
        content.pack(fill="both", expand=True)
        header = tk.Frame(content, bg=BG)
        header.pack(fill="x")
        self._label(header, "Happ Suite", size=23, weight="bold").pack(side="left")
        self._button(header, "Свернуть в трей", root.withdraw, width=16).pack(side="right")
        self._label(content, "VPN, Antigravity и Gemini — управление с одного экрана",
                    color=MUTED).pack(anchor="w", pady=(3, 10))

        tabs = tk.Frame(content, bg=BG)
        tabs.pack(fill="x", pady=(0, 10))
        self._button(tabs, "Управление", lambda: self._show_page("control"), width=15).pack(side="left")
        self._button(tabs, "Настройки и установка", lambda: self._show_page("settings"), width=23).pack(side="left", padx=(7, 0))
        self.pages = {
            "control": tk.Frame(content, bg=BG),
            "settings": tk.Frame(content, bg=BG),
        }
        control = self.pages["control"]
        settings = self.pages["settings"]

        self.cards = {}
        self._card(control, "happ", "HAPP VPN", "Подключение через выбранный профиль HAPP",
                   lambda: self.tray._schedule_toggle_happ())
        self._card(control, "ag", "Antigravity", "Локальный relay AG Unlocker",
                   lambda: self.tray._schedule_toggle_ag())
        self._card(control, "gemini", "Gemini Web", "Проверяю DNS текущей сети…",
                   self._toggle_gemini_dns)
        web_actions = tk.Frame(control, bg=BG)
        web_actions.pack(fill="x", pady=(0, 8))
        self._button(web_actions, "Открыть Gemini", self._open_gemini, width=16).pack(side="left")
        self._button(web_actions, "Проверить сайт", self._check_gemini_now, width=16).pack(side="left", padx=(7, 0))

        subscription = tk.Frame(control, bg=CARD, padx=16, pady=10)
        subscription.pack(fill="x", pady=(3, 8))
        self._label(subscription, "Подписка HAPP", size=11, weight="bold", bg=CARD).pack(anchor="w")
        self._label(subscription, "Вставьте HTTPS-ссылку. Она не сохраняется в Suite и не попадает в журнал.",
                    color=MUTED, bg=CARD).pack(anchor="w", pady=(2, 8))
        row = tk.Frame(subscription, bg=CARD)
        row.pack(fill="x")
        self.subscription = tk.Entry(row, bg=FIELD, fg=TEXT, insertbackground=TEXT,
                                     relief="flat", font=("Segoe UI", 10), show="•")
        self.subscription.pack(side="left", fill="x", expand=True, ipady=8)
        self._button(row, "Показать", self._reveal_subscription, width=9).pack(side="left", padx=(8, 0))
        self._button(row, "Добавить", self._import_subscription, filled=True, width=10).pack(side="left", padx=(8, 0))

        keys = tk.Frame(settings, bg=CARD, padx=16, pady=12)
        keys.pack(fill="x", pady=(0, 8))
        self._label(keys, "Горячие клавиши", size=11, weight="bold", bg=CARD).pack(anchor="w")
        self.key_labels = {}
        for component, title in (("happ", "HAPP"), ("gemini", "Antigravity")):
            row = tk.Frame(keys, bg=CARD)
            row.pack(fill="x", pady=(8, 0))
            self._label(row, title, bg=CARD, width=13, anchor="w").pack(side="left")
            current = self._label(row, "", color=BLUE, bg=CARD, width=16, anchor="w")
            current.pack(side="left")
            self.key_labels[component] = current
            presets = ("Ctrl+Alt+H", "Ctrl+Shift+H", "F8") if component == "happ" else (
                "Ctrl+Alt+G", "Ctrl+Shift+G", "F9")
            choice = ttk.Combobox(row, values=presets, state="readonly", width=15)
            choice.set("Выбрать сочетание")
            choice.pack(side="left", padx=(4, 7))
            choice.bind("<<ComboboxSelected>>", lambda event, name=component, widget=choice:
                        self._choose_preset(name, widget.get()))
            self._button(row, "Своя клавиша", lambda name=component:
                         self._capture_key(name), width=13).pack(side="left")

        bottom = tk.Frame(settings, bg=BG)
        bottom.pack(fill="x", pady=(2, 0))
        if self.variant == "setup":
            self._button(bottom, "Установить HAPP", lambda: self._install(HAPP), width=16).pack(side="left")
        self._button(bottom, "Установить AG", lambda: self._install(AG_UNLOCKER), width=16).pack(side="left", padx=(7, 0))
        self._label(settings, "Установщики скачиваются с официальных GitHub-релизов и проверяются по SHA-256.",
                    color=MUTED).pack(anchor="w", pady=(7, 0))
        self.autostart = tk.BooleanVar(value=autostart_enabled())
        tk.Checkbutton(settings, text="Запускать с Windows после входа в систему",
                       variable=self.autostart, command=self._set_autostart,
                       bg=BG, fg=MUTED, selectcolor=FIELD, activebackground=BG,
                       activeforeground=TEXT, font=("Segoe UI", 9)).pack(anchor="w", pady=(8, 0))
        self._label(settings,
                    "Gemini Web: Xbox DNS " + " / ".join(XBOX_DNS["ipv4"]) +
                    ".\nПрименяется к текущему Wi-Fi/Ethernet и сохраняется после выхода.\n"
                    "Кнопка «Вернуть DNS» восстановит прежние настройки.\n"
                    "При смене сети сначала верните DNS предыдущего подключения.\n"
                    "VPN и безопасный DNS браузера могут использовать другие серверы.",
                    color=MUTED, justify="left", wraplength=590).pack(anchor="w", pady=(16, 0))
        self.message = self._label(content, "Готово к работе", color=MUTED, anchor="w")
        self.message.pack(side="bottom", fill="x", pady=(7, 0))
        self._show_page("control")

    def _show_page(self, name: str):
        for page in self.pages.values():
            page.pack_forget()
        self.pages[name].pack(fill="both", expand=True)

    def _card(self, parent, key, title, description, action, *, toggle=True):
        frame = tk.Frame(parent, bg=CARD, padx=16, pady=9)
        frame.pack(fill="x", pady=(0, 8))
        title_row = tk.Frame(frame, bg=CARD)
        title_row.pack(fill="x")
        dot = tk.Canvas(title_row, width=15, height=15, bg=CARD, highlightthickness=0)
        dot.pack(side="left", padx=(0, 8))
        dot.create_oval(2, 2, 13, 13, fill=MUTED, outline="")
        self._label(title_row, title, size=12, weight="bold", bg=CARD).pack(side="left")
        button = self._button(title_row, "Включить" if toggle else "Проверить", action,
                              filled=toggle, width=12)
        button.pack(side="right")
        detail = self._label(frame, description, color=MUTED, bg=CARD, justify="left", wraplength=550)
        detail.pack(anchor="w", padx=(23, 0), pady=(3, 0))
        self.cards[key] = (dot, detail, button)

    def set_message(self, message: str):
        self.root.after(0, lambda: self.message.configure(text=message))

    def _reveal_subscription(self):
        self.subscription.configure(show="" if self.subscription.cget("show") else "•")

    def _import_subscription(self):
        url = self.subscription.get().strip()
        self.subscription.delete(0, "end")
        self.subscription.configure(show="•")
        if not url:
            self.set_message("Вставьте ссылку подписки")
            return

        def worker():
            try:
                HappIpcClient(self.config.happ_exe).import_subscription_url(url)
                self.set_message("Передано в HAPP. Проверьте список подписок.")
            except ValueError:
                self.set_message("Нужна корректная HTTPS-ссылка подписки")
            except Exception:
                self.set_message("HAPP не принял ссылку. Проверьте, что он запущен.")

        threading.Thread(target=worker, name="ImportHappSubscription", daemon=True).start()

    def _choose_preset(self, component: str, label: str):
        key = ord("H" if component == "happ" else "G")
        if label.startswith("Ctrl+Alt+"):
            choice = HotkeyChoice(key, MOD_CONTROL | MOD_ALT)
        elif label.startswith("Ctrl+Shift+"):
            choice = HotkeyChoice(key, MOD_CONTROL | MOD_SHIFT)
        else:
            choice = HotkeyChoice(VK_F8 if component == "happ" else VK_F9)
        self.tray._set_shortcut(component, choice)
        self.set_message(f"Назначение: {choice.label}")

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

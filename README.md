# 🛡️ Happ Suite

**Единый менеджер обхода блокировок для Windows.**  
Одна кнопка — YouTube, Discord, ChatGPT и Google Gemini работают.

## Что это?

Happ Suite объединяет три инструмента обхода блокировок в одну программу с умным управлением:

| Компонент | Назначение |
|-----------|------------|
| **Zapret** (winws.exe) | DPI-обход для YouTube и Discord |
| **Happ VPN** (xray) | VPN-туннель через зарубежные серверы |
| **AG Unlocker** (ag_dns.exe) | Разблокировка Google Gemini в РФ |

## Возможности

- 🟢 **Одна кнопка** — запуск всех компонентов разом
- 🔄 **Авто-восстановление** — после сна/пробуждения всё переподключается само
- 🧠 **Детектор белых списков** — определяет глушилки БПЛА и переключает на антизаглушку
- 📡 **Авто-выбор сервера** — пингует все VPN-узлы и выбирает лучший
- 🔔 **Tray-индикатор** — иконка в трее показывает статус (🟢/🟡/🔴)
- 📊 **CLI-дашборд** — полная диагностика в консоли

## Быстрый старт

1. Скачайте последний релиз из [Releases](../../releases)
2. Распакуйте в любую папку
3. Запустите `HappSuite.exe` от имени администратора
4. Готово! Иконка в трее станет зелёной 🟢

## Требования

- Windows 10/11 (64-bit)
- [Happ VPN](https://hfreedns.com/) — установлен
- Secure DNS включён в браузере ([инструкция](https://github.com/Flowseal/zapret-discord-youtube#%EF%B8%8F%D0%B8%D1%81%D0%BF%D0%BE%D0%BB%D1%8C%D0%B7%D0%BE%D0%B2%D0%B0%D0%BD%D0%B8%D0%B5))

## Благодарности

- [Flowseal/zapret-discord-youtube](https://github.com/Flowseal/zapret-discord-youtube) — DPI-обход
- [bol-van/zapret](https://github.com/bol-van/zapret) — оригинальный zapret
- [confeden/Antigravity](https://github.com/confeden/Antigravity) — Gemini Unlocker

## Лицензия

MIT

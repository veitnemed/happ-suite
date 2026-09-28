# Happ Suite — acceptance evidence

Последнее обновление: 2026-09-27. Evidence ниже собраны из аудита и mock-only этапа 02; P1 и end-to-end F8 пока не запускались.

| ID | Статус | Evidence / ограничение |
|---|---|---|
| A01 | NOT_TESTED | Реальный F8 не запускался. |
| A02 | BLOCKED | HAPP GUI запускается только с `STARTUPINFO(SW_HIDE)` hint; установленный GUI не запускали, flash не измеряли. |
| A03 | NOT_TESTED | Реальный UI AG Unlocker в ходе F8 не запускался. |
| A04 | BLOCKED | Локальный proxy не является проверкой системного VPN; HAPP tunnel и прикладной путь AG не доказаны. |
| A05 | BLOCKED | Нет подтверждённой штатной HAPP disconnect-команды. |
| A06 | NOT_TESTED | Есть unit cancellation test, Windows integration отсутствует; активный HAPP безопасно остановить не умеем. |
| A07 | NOT_TESTED | F8 spam не испытывался. |
| A08 | NOT_TESTED | Конфликт глобальной клавиши в работающем tray не проверялся. |
| A09 | NOT_TESTED | Фактические цвета/status в работающем tray не проверялись. |
| A10 | NOT_TESTED | AG Unlocker обнаружен ранее работающим; F8 выключение не проверялось. |
| A11 | BLOCKED | Mock подтверждает отказ от relaunch обнаруженного чужого HAPP; системный tunnel и Windows integration не подтверждены. |
| A12 | NOT_TESTED | Другая реальная подписка не проверялась. |
| A13 | NOT_TESTED | Приоритет каталогов не проверялся. |
| A14 | NOT_TESTED | VPN handshake через выбранный сервер не проверялся. |
| A15 | NOT_TESTED | Реальный обрыв/переход с достоверным индикатором не проверялся. |
| A16 | NOT_TESTED | Tray без родительского терминала не проверялся. |
| A17 | PASS (dry-run scope) | Независимые worker/watchdog задачи завершились после launcher; normal heartbeat и hang timeout прочитаны из локального журнала. Это не проверяет реальный recovery. См. `STATUS.md`, раздел VPN bridge dry-run. |
| A18 | PASS (consent gate only) | Единственная live UI попытка выполнялась после явного согласия пользователя. Переподключение не состоялось; этот PASS не означает успешный rollback. |
| A19 | NOT_TESTED | Чистая Windows-среда без Python не проверялась. |
| A20 | NOT_TESTED | Полный secret scan репозитория не выполнялся. |

## Этап 02 — HAPP adapter

- 9 mock unit tests: PASS (`py -3 -m unittest discover -s tests -v`).
- Syntax: PASS (`py -3 -m py_compile src/core.py tests/test_happ_adapter.py`).
- Whitespace: PASS (`git diff --check`; Git сообщил лишь ожидаемое предупреждение LF/CRLF).
- Реальные HAPP launch, no-flash, focus, connect/disconnect и conflict с другим VPN: NOT_TESTED/BLOCKED; сеть не менялась.

## Повторные F8 при существующем HAPP

- 12 mock unit tests: PASS (`py -3 -m unittest discover -s tests -v`). Покрыты частичное состояние для внешнего HAPP и правило не отключать его на F8 off.
- Обновлённый source-процесс запущен без консольного окна; локальный журнал подтвердил `Global F8 hotkey registered`.
- Balloon уведомление после реального F8 в обновлённом процессе ещё не наблюдали. Реальное отключение внешнего или Suite-owned HAPP и статус системного TUN остаются BLOCKED/NOT_TESTED.

@echo off
chcp 65001 >nul
title Happ Suite v2.0 Launcher

echo ========================================================
echo  🛡️ Happ Suite v2.0 - Запуск
echo ========================================================
echo.

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    start "" py -3 -X utf8 -m src.main
    goto :done
)

where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    start "" python -X utf8 -m src.main
    goto :done
)

echo [ОШИБКА] Python не найден в системе!
pause
exit /b 1

:done
echo [OK] Happ Suite успешно запущен в фоновом режиме (ищите иконку в трее возле часов).
timeout /t 3 >nul
exit /b 0

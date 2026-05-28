@echo off
chcp 65001 >nul
title GenericAgent Launcher
cd /d "%~dp0"
call conda activate GenericAgent

echo.
echo  ==============================
echo    GenericAgent Frontend Select
echo  ==============================
echo    1. Feishu + WeCom  (launch.pyw)
echo    2. TUI v2          (tuiapp_v2)
echo    3. TUI v3          (tui_v3)
echo  ==============================
echo.
set /p choice="Please choose [1/2/3]: "

if "%choice%"=="2" (
    python frontends\tuiapp_v2.py
) else if "%choice%"=="3" (
    python frontends\tui_v3.py
) else (
    python launch.pyw --feishu --wecom
)
pause

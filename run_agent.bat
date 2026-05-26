@echo off
chcp 65001 >nul
title GenericAgent Launcher

:: Enter GenericAgent directory
cd /d "%~dp0"

echo.
echo  ==============================
echo    GenericAgent Frontend Select
echo  ==============================
echo    1. Feishu + WeCom  (launch.pyw, conda: GenericAgent)
echo    2. TUI Terminal    (tuiapp_v2, conda: LangChain)
echo    3. TUI v3          (tui_v3, conda: LangChain)
echo  ==============================
echo.
set /p choice="Please choose [1/2/3]: "

if "%choice%"=="2" (
    echo Activating conda env: LangChain
    call conda activate LangChain
    echo Starting TUI v2 frontend...
    python frontends\tuiapp_v2.py
) else if "%choice%"=="3" (
    echo Activating conda env: LangChain
    call conda activate LangChain
    echo Starting TUI v3 frontend...
    python frontends\tui_v3.py
) else (
    echo Activating conda env: GenericAgent
    call conda activate GenericAgent
    echo Starting Feishu + WeCom frontend...
    python launch.pyw --feishu --wecom
)

pause

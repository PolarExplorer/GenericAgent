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
echo  ==============================
echo.
set /p choice="Please choose [1/2]: "

if "%choice%"=="2" (
    echo Activating conda env: LangChain
    call conda activate LangChain
    echo Starting TUI frontend...
    python frontends\tuiapp_v2.py
) else (
    echo Activating conda env: GenericAgent
    call conda activate GenericAgent
    echo Starting Feishu + WeCom frontend...
    python launch.pyw --feishu --wecom
)

pause

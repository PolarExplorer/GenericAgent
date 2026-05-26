@echo off
chcp 65001 >nul
title GenericAgent Main Instance

cd /d "%~dp0"

echo.
echo  ==============================
echo    GenericAgent Main stapp
echo  ==============================
echo    Attaches to or starts the main instance on port 18513.
echo.

call conda activate GenericAgent
python launch.pyw 18513 --feishu --wecom

pause

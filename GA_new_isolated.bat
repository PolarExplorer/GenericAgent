@echo off
chcp 65001 >nul
title GenericAgent New Isolated Instance

cd /d "%~dp0"

echo.
echo  ==============================
echo    GenericAgent Isolated stapp
echo  ==============================
echo    Starts a new independent stapp instance.
echo    Ports are assigned from 18514 upward.
echo.

call conda activate GenericAgent
python scripts\start_isolated_stapp.py

pause

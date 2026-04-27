@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
:: === 飞书 Bot 当前用户计划任务安装器 ===
:: 默认创建“用户登录后启动”的当前用户任务，不使用 SYSTEM/最高权限。

echo ============================================
echo   飞书 Bot 当前用户计划任务 - 安装中...
echo ============================================

:: 配置变量
set TASK_NAME=GA_FeishuBot
set PYTHON=D:\soft\Anaconda\envs\GenericAgent\pythonw.exe
set DAEMON=D:\AI\GenericAgent\fsapp_daemon.pyw
set WORKDIR=D:\AI\GenericAgent

if not exist "%PYTHON%" (
    echo [FAIL] Python不存在: %PYTHON%
    goto :end
)
if not exist "%DAEMON%" (
    echo [FAIL] Daemon不存在: %DAEMON%
    goto :end
)

:: 1. 如存在旧任务，明确提示后删除
schtasks /Query /TN "%TASK_NAME%" >nul 2>&1
if %errorlevel% equ 0 (
    echo [INFO] 发现旧任务，准备删除: %TASK_NAME%
    schtasks /Delete /TN "%TASK_NAME%" /F
)

:: 2. 创建计划任务：当前用户登录后触发
::    /SC ONLOGON = 用户登录后启动
::    不设置系统账户，不设置最高权限
echo 创建计划任务: 当前用户登录后启动...
schtasks /Create ^
    /TN "%TASK_NAME%" ^
    /TR "\"%PYTHON%\" \"%DAEMON%\"" ^
    /SC ONLOGON ^
    /DELAY 0000:10 ^
    /F

if %errorlevel% equ 0 (
    echo.
    echo [OK] 计划任务创建成功！
    echo      任务名: %TASK_NAME%
    echo      触发: 当前用户登录后10秒
    echo      运行身份: 当前用户
    echo.
    echo 验证任务...
    schtasks /Query /TN "%TASK_NAME%" /FO LIST
) else (
    echo.
    echo [FAIL] 创建失败，请检查错误信息
)

echo.
echo ============================================
echo 按任意键退出...
pause >nul
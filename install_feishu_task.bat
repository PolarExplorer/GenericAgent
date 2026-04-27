@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
:: === 飞书 Bot 管理员计划任务安装器 ===
:: 双击运行，弹UAC提权后自动创建计划任务

:: 1. 自动提权：如果不是管理员则重新启动自身
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo 正在请求管理员权限...
    powershell -Command "Start-Process cmd -ArgumentList '/c \"%~f0\"' -Verb RunAs"
    exit /b
)

echo ============================================
echo   飞书 Bot 管理员计划任务 - 安装中...
echo ============================================

:: 配置变量
set TASK_NAME=GA_FeishuBot
set PYTHON=D:\soft\Anaconda\envs\GenericAgent\pythonw.exe
set DAEMON=D:\AI\GenericAgent\fsapp_daemon.pyw
set WORKDIR=D:\AI\GenericAgent

:: 2. 先删除旧任务（如果有）
schtasks /Delete /TN "%TASK_NAME%" /F >nul 2>&1

:: 3. 创建计划任务：系统启动时触发 + 用户登录时触发
::    /SC ONSTART = 系统启动时（开机即跑，不等登录）
::    /DELAY 0000:30 = 启动后延迟30秒（等网络就绪）
::    /RU SYSTEM = 以SYSTEM账户运行（无需用户登录）
::    /RL HIGHEST = 最高权限
echo 创建计划任务: 系统启动触发 + SYSTEM账户...
schtasks /Create ^
    /TN "%TASK_NAME%" ^
    /TR "\"%PYTHON%\" \"%DAEMON%\"" ^
    /SC ONSTART ^
    /DELAY 0000:30 ^
    /RU SYSTEM ^
    /RL HIGHEST ^
    /F

if %errorlevel% equ 0 (
    echo.
    echo [OK] 计划任务创建成功！
    echo      任务名: %TASK_NAME%
    echo      触发: 系统启动后30秒
    echo      运行身份: SYSTEM（无需用户登录）
    echo.
    echo 验证任务...
    schtasks /Query /TN "%TASK_NAME%" /FO LIST
) else (
    echo.
    echo [FAIL] 创建失败，请检查错误信息
    echo.
    echo 备选：尝试登录触发模式...
    schtasks /Create ^
        /TN "%TASK_NAME%" ^
        /TR "\"%PYTHON%\" \"%DAEMON%\"" ^
        /SC ONLOGON ^
        /DELAY 0000:10 ^
        /F
    if !errorlevel! equ 0 (
        echo [OK] 已改用登录触发模式
    ) else (
        echo [FAIL] 两种方式都失败了
    )
)

echo.
echo ============================================
echo 按任意键退出...
pause >nul
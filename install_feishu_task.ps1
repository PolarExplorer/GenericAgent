# 飞书 Bot 管理员计划任务安装器
# 右键 → 使用 PowerShell 运行，或双击运行

$TASK_NAME = "GA_FeishuBot"
$PYTHON = "D:\soft\Anaconda\envs\GenericAgent\pythonw.exe"
$DAEMON = "D:\AI\GenericAgent\fsapp_daemon.pyw"
$WORKDIR = "D:\AI\GenericAgent"

# 自动提权
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "正在请求管理员权限..." -ForegroundColor Yellow
    Start-Process powershell -ArgumentList "-ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  飞书 Bot 管理员计划任务 - 安装中..." -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# 删除旧任务
schtasks /Delete /TN $TASK_NAME /F 2>$null

# 创建：系统启动触发，SYSTEM账户，延迟30秒
Write-Host "`n创建计划任务: 系统启动触发 + SYSTEM账户..."
$result = schtasks /Create /TN $TASK_NAME /TR "`"$PYTHON`" `"$DAEMON`"" /SC ONSTART /DELAY 0000:30 /RU SYSTEM /RL HIGHEST /F 2>&1

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n[OK] 计划任务创建成功!" -ForegroundColor Green
    Write-Host "     任务名: $TASK_NAME"
    Write-Host "     触发: 系统启动后30秒"
    Write-Host "     运行身份: SYSTEM"
    Write-Host "`n验证任务..."
    schtasks /Query /TN $TASK_NAME /FO LIST
} else {
    Write-Host "`n[WARN] ONSTART模式失败，尝试ONLOGON模式..." -ForegroundColor Yellow
    Write-Host $result
    $result2 = schtasks /Create /TN $TASK_NAME /TR "`"$PYTHON`" `"$DAEMON`"" /SC ONLOGON /DELAY 0000:10 /F 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[OK] 已改用登录触发模式" -ForegroundColor Green
        schtasks /Query /TN $TASK_NAME /FO LIST
    } else {
        Write-Host "[FAIL] 两种方式都失败了" -ForegroundColor Red
        Write-Host $result2
    }
}

Write-Host "`n============================================"
Write-Host "按任意键退出..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
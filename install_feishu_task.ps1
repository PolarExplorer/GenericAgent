# 飞书 Bot 当前用户计划任务安装器
# 默认创建“用户登录后启动”的当前用户任务，不使用 SYSTEM/最高权限。

$TASK_NAME = "GA_FeishuBot"
$PYTHON = "D:\soft\Anaconda\envs\GenericAgent\pythonw.exe"
$DAEMON = "D:\AI\GenericAgent\fsapp_daemon.pyw"
$WORKDIR = "D:\AI\GenericAgent"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  飞书 Bot 当前用户计划任务 - 安装中..." -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

if (-not (Test-Path $PYTHON)) {
    Write-Host "[FAIL] Python不存在: $PYTHON" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $DAEMON)) {
    Write-Host "[FAIL] Daemon不存在: $DAEMON" -ForegroundColor Red
    exit 1
}

# 如存在旧任务，明确提示后删除
schtasks /Query /TN $TASK_NAME *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "[INFO] 发现旧任务，准备删除: $TASK_NAME"
    schtasks /Delete /TN $TASK_NAME /F
}

# 创建：当前用户登录后触发
Write-Host "`n创建计划任务: 当前用户登录后启动..."
$result = schtasks /Create /TN $TASK_NAME /TR "`"$PYTHON`" `"$DAEMON`"" /SC ONLOGON /DELAY 0000:10 /F 2>&1

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n[OK] 计划任务创建成功!" -ForegroundColor Green
    Write-Host "     任务名: $TASK_NAME"
    Write-Host "     触发: 当前用户登录后10秒"
    Write-Host "     运行身份: 当前用户"
    Write-Host "`n验证任务..."
    schtasks /Query /TN $TASK_NAME /FO LIST
} else {
    Write-Host "`n[FAIL] 创建失败，请检查错误信息" -ForegroundColor Red
    Write-Host $result
}

Write-Host "`n============================================"
Write-Host "按任意键退出..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

"""GA Watchdog — 零依赖外部看门狗，防止 memory/*.py 损坏导致 GA 变砖。

功能:
  1. 定期检测 GA 进程是否存活（端口18513优先，进程名fallback）
  2. GA 挂了 → 尝试重启 launch.pyw
  3. 连续 MAX_RETRIES 次重启失败 → 运行 self_test 诊断 → 精准恢复损坏文件 → 再重启
  4. 所有操作写日志到 temp/watchdog.log

Usage:
  pythonw ga_watchdog.py          # 后台守护（推荐）
  python  ga_watchdog.py          # 前台运行（调试）
  python  ga_watchdog.py --once   # 单次健康检查
"""
import subprocess, sys, os, time, datetime, argparse, signal, socket

# ── 配置 ──────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(BASE_DIR, "temp")
LOG_FILE = os.path.join(TEMP_DIR, "watchdog.log")
LAUNCH_SCRIPT = os.path.join(BASE_DIR, "launch.pyw")
MEMORY_DIR = os.path.join(BASE_DIR, "memory")
GA_PORT = 18513              # launch.pyw DEFAULT_PORT
RESTART_COUNT_FILE = os.path.join(TEMP_DIR, "watchdog_restart_count.txt")

CHECK_INTERVAL = 30        # 秒，健康检查间隔
STARTUP_GRACE = 45         # 秒，重启后等待启动的宽限期
MAX_RETRIES = 3             # 连续失败几次后触发 git 恢复
MAX_TOTAL_RESTARTS = 5      # 单次 daemon 生命周期内最多重启次数，防止无限创建窗口
CREATE_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0

# ── 持久化重启计数（跨 watchdog 实例生效）──────────────────
def _load_restart_count():
    try:
        with open(RESTART_COUNT_FILE, "r") as f:
            return int(f.read().strip())
    except Exception:
        return 0

def _save_restart_count(count):
    try:
        os.makedirs(TEMP_DIR, exist_ok=True)
        with open(RESTART_COUNT_FILE, "w") as f:
            f.write(str(count))
    except Exception:
        pass

def _reset_restart_count():
    """GA 正常运行一段时间后可重置（由外部或手动调用）"""
    _save_restart_count(0)

# ── 日志 ──────────────────────────────────────────────────
def log(msg, level="INFO"):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(TEMP_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def trim_log():
    """保留最近 500 行日志"""
    try:
        if not os.path.exists(LOG_FILE):
            return
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > 500:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines[-500:])
    except Exception:
        pass

# ── 进程检测 ─────────────────────────────────────────────
def _get_python_cmdlines_win():
    """Windows 下获取所有 python 进程的命令行（多种方法降级）"""
    # 方法1: PowerShell Get-CimInstance（推荐，Win10+ 均可用）
    try:
        r = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | Select-Object -ExpandProperty CommandLine"],
            capture_output=True, text=True, encoding="utf-8", errors="ignore",
            creationflags=CREATE_NO_WINDOW, timeout=15,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.lower()
    except Exception as e:
        log(f"PowerShell detection failed: {e}", "WARN")

    # 方法2: wmic（旧系统兼容）
    try:
        r = subprocess.run(
            'wmic process where "Name like \'python%\'" get CommandLine /format:list',
            shell=True, capture_output=True, text=True,
            encoding="utf-8", errors="ignore",
            creationflags=CREATE_NO_WINDOW, timeout=15,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.lower()
    except Exception:
        pass

    return ""

def _is_port_open(port=GA_PORT, host="127.0.0.1", timeout=2, retries=2):
    """TCP connect 检测本地端口是否在监听（带重试，防间歇性误判）"""
    for attempt in range(retries):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except (ConnectionRefusedError, OSError, socket.timeout):
            if attempt < retries - 1:
                time.sleep(1)
    return False

def _is_process_alive():
    """Fallback: 通过进程命令行关键词检测 GA"""
    try:
        if os.name == 'nt':
            output = _get_python_cmdlines_win()
        else:
            r = subprocess.run(
                ["ps", "aux"], capture_output=True, text=True, timeout=10,
            )
            output = r.stdout.lower()
        keywords = ["launch.pyw", "stapp.py"]
        return any(kw.lower() in output for kw in keywords)
    except Exception as e:
        log(f"Process detection error: {e}", "WARN")
        return False

def is_ga_alive():
    """检测 GA 是否存活：端口 18513 优先，进程名 fallback"""
    if _is_port_open():
        return True
    # 端口未开但进程可能在启动中
    if _is_process_alive():
        log("Port not open but GA process found (may be starting up)", "WARN")
        return True
    return False

# ── 恢复操作 ─────────────────────────────────────────────
def restart_ga():
    """通过 launch.pyw 重启 GA（带进程去重）"""
    # ── 去重：如果 launch.pyw 或 streamlit 进程已存在，不再拉新的 ──
    if _is_process_alive():
        log("launch.pyw/streamlit process already running — skipping restart to avoid duplicate windows", "WARN")
        # 进程在但端口不通，可能正在启动中，等一轮再判断
        return False

    log("Attempting to restart GA via launch.pyw ...")
    try:
        if os.name == 'nt':
            subprocess.Popen(
                [sys.executable.replace("python.exe", "pythonw.exe"), LAUNCH_SCRIPT],
                cwd=BASE_DIR,
                creationflags=CREATE_NO_WINDOW,
            )
        else:
            subprocess.Popen(
                [sys.executable, LAUNCH_SCRIPT],
                cwd=BASE_DIR,
                start_new_session=True,
            )
        log(f"launch.pyw started, waiting {STARTUP_GRACE}s for startup ...")
        time.sleep(STARTUP_GRACE)
        alive = is_ga_alive()
        if alive:
            log("GA restart successful!")
        else:
            log("GA still not detected after restart", "WARN")
        return alive
    except Exception as e:
        log(f"Restart failed: {e}", "ERROR")
        return False

def git_recover_memory(failed_files=None):
    """用 git checkout 恢复 memory 文件。
    failed_files: 精准恢复列表 (如 ["memory/ocr_utils.py"])，为空则全量恢复 memory/
    """
    targets = failed_files if failed_files else ["memory/"]
    log(f"=== CRITICAL: git recovery targets: {targets} ===", "ERROR")

    # 备份
    try:
        import shutil
        backup_dir = os.path.join(TEMP_DIR, f"memory_backup_{int(time.time())}")
        if failed_files:
            os.makedirs(backup_dir, exist_ok=True)
            for rel in failed_files:
                src_p = os.path.join(BASE_DIR, rel)
                if os.path.exists(src_p):
                    dst_p = os.path.join(backup_dir, os.path.basename(rel))
                    shutil.copy2(src_p, dst_p)
            log(f"Backed up {len(failed_files)} file(s) to {backup_dir}")
        else:
            shutil.copytree(MEMORY_DIR, backup_dir, dirs_exist_ok=True)
            log(f"Full backup to {backup_dir}")
    except Exception as e:
        log(f"Backup failed (continuing anyway): {e}", "WARN")

    try:
        r = subprocess.run(
            ["git", "checkout", "--"] + targets,
            cwd=BASE_DIR, capture_output=True, text=True,
            encoding="utf-8", errors="ignore", timeout=30,
        )
        if r.returncode == 0:
            log(f"git checkout SUCCESS: {targets}")
            return True
        else:
            log(f"git checkout failed: {r.stderr.strip()}", "ERROR")
            # 精准恢复失败 → 降级到全量恢复
            if failed_files:
                log("Falling back to full memory/ recovery...", "WARN")
                return git_recover_memory(failed_files=None)
            return False
    except Exception as e:
        log(f"git recovery error: {e}", "ERROR")
        return False

def script_guard_check():
    """运行 script_guard.py 健康检查"""
    guard_path = os.path.join(BASE_DIR, "script_guard.py")
    if not os.path.exists(guard_path):
        return True, "script_guard.py not found, skipping"
    try:
        r = subprocess.run(
            [sys.executable, guard_path],
            cwd=BASE_DIR, capture_output=True, text=True,
            encoding="utf-8", errors="ignore", timeout=60,
        )
        ok = r.returncode == 0
        msg = (r.stdout.strip()[-200:]) if r.stdout else "(no output)"
        return ok, msg
    except Exception as e:
        return False, str(e)

# ── 主循环 ────────────────────────────────────────────────
def script_health_check():
    """运行 memory/script_health_check.py 检测所有 self_test()。
    Returns: (ok: bool, summary: str, failed_files: list[str])
    """
    hc_path = os.path.join(MEMORY_DIR, "script_health_check.py")
    if not os.path.exists(hc_path):
        return True, "script_health_check.py not found, skipping", []
    try:
        r = subprocess.run(
            [sys.executable, hc_path, "--json"],
            cwd=BASE_DIR, capture_output=True, text=True,
            encoding="utf-8", errors="ignore", timeout=60,
        )
        import json as _json
        # --json 输出可能前面带 debug print，提取第一个 { 到最后一个 }
        raw = r.stdout.strip()
        json_start = raw.find("{")
        json_end = raw.rfind("}")
        json_str = raw[json_start:json_end+1] if json_start >= 0 and json_end >= json_start else ""
        report = _json.loads(json_str) if json_str else {}
        ok = report.get("healthy", r.returncode == 0)
        summary = f"{report.get('passed',0)} PASS / {report.get('failed',0)} FAIL / {report.get('skipped',0)} SKIP"
        # 收集失败模块的文件路径，用于精准恢复
        failed_files = []
        for item in report.get("results", []):
            if item.get("status") == "FAIL":
                mod = item.get("module", "")
                # module 格式如 "memory.ocr_utils" → memory/ocr_utils.py
                rel = mod.replace(".", "/") + ".py"
                if os.path.exists(os.path.join(BASE_DIR, rel)):
                    failed_files.append(rel)
        return ok, summary, failed_files
    except Exception as e:
        return False, str(e), []

def run_once():
    """单次健康检查 + 报告"""
    log("--- Single health check ---")
    alive = is_ga_alive()
    log(f"GA process alive: {alive}")
    
    sg_ok, sg_msg = script_guard_check()
    log(f"ScriptGuard check: {'PASS' if sg_ok else 'FAIL'} - {sg_msg}")

    hc_ok, hc_msg, hc_failed = script_health_check()
    log(f"ScriptHealthCheck: {'PASS' if hc_ok else 'FAIL'} - {hc_msg}")
    if hc_failed:
        log(f"  Failed modules: {hc_failed}", "WARN")
    
    if alive and sg_ok and hc_ok:
        log("Overall: HEALTHY")
        return 0
    else:
        log("Overall: UNHEALTHY", "WARN")
        return 1

def run_daemon():
    """持续守护模式"""
    log("========================================")
    log("GA Watchdog started (daemon mode)")
    log(f"  BASE_DIR:       {BASE_DIR}")
    log(f"  CHECK_INTERVAL: {CHECK_INTERVAL}s")
    log(f"  MAX_RETRIES:    {MAX_RETRIES}")
    log("========================================")
    
    consecutive_failures = 0
    total_restarts = _load_restart_count()
    check_count = 0
    
    while True:
        try:
            check_count += 1
            if check_count % 100 == 0:
                trim_log()
            
            alive = is_ga_alive()
            
            if alive:
                if consecutive_failures > 0:
                    log(f"GA recovered after {consecutive_failures} failure(s)")
                consecutive_failures = 0
                time.sleep(CHECK_INTERVAL)
                continue
            
            # GA is down
            consecutive_failures += 1
            log(f"GA not detected! (consecutive failure #{consecutive_failures})", "WARN")
            
            # 安全阀：总重启次数超限 → 停止重启，仅监控
            if total_restarts >= MAX_TOTAL_RESTARTS:
                if total_restarts == MAX_TOTAL_RESTARTS:  # 只在刚达到时打一次
                    log(f"Total restart limit ({MAX_TOTAL_RESTARTS}) reached. "
                        f"Watchdog will continue monitoring but NOT restart GA. "
                        f"Manual intervention required.", "ERROR")
                time.sleep(CHECK_INTERVAL * 2)
                continue
            
            if consecutive_failures < MAX_RETRIES:
                # 普通重启
                total_restarts += 1
                _save_restart_count(total_restarts)
                success = restart_ga()
                if success:
                    consecutive_failures = 0
                    continue
            else:
                # 连续失败达到阈值 → 先做 ScriptGuard 检查
                log(f"Consecutive failures reached {MAX_RETRIES}, running diagnostics...", "ERROR")
                sg_ok, sg_msg = script_guard_check()
                log(f"ScriptGuard: {'PASS' if sg_ok else 'FAIL'} - {sg_msg}")
                hc_ok, hc_msg, hc_failed = script_health_check()
                log(f"HealthCheck: {'PASS' if hc_ok else 'FAIL'} - {hc_msg}")
                if hc_failed:
                    log(f"  Failed modules: {hc_failed}", "WARN")
                
                if not sg_ok or hc_failed:
                    # memory 有问题 → 精准/全量 git 恢复
                    recovered = git_recover_memory(failed_files=hc_failed if hc_failed else None)
                    if recovered:
                        log("Memory restored via git. Attempting restart...")
                        total_restarts += 1
                        _save_restart_count(total_restarts)
                        success = restart_ga()
                        if success:
                            consecutive_failures = 0
                            log("=== RECOVERY SUCCESSFUL ===")
                            continue
                        else:
                            log("GA still failing after git recovery!", "ERROR")
                    else:
                        log("Git recovery also failed!", "ERROR")
                else:
                    # memory 没问题但 GA 还是起不来 → 可能是其他原因
                    log("ScriptGuard passed but GA still down. Trying plain restart...", "WARN")
                    total_restarts += 1
                    _save_restart_count(total_restarts)
                    success = restart_ga()
                    if success:
                        consecutive_failures = 0
                        continue
                
                # 最终兜底：冷却等待更长时间
                cooldown = min(300, CHECK_INTERVAL * consecutive_failures)
                log(f"Entering cooldown: {cooldown}s before next attempt", "WARN")
                time.sleep(cooldown)
                continue
            
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            log("Watchdog stopped by user")
            break
        except Exception as e:
            log(f"Watchdog loop error: {e}", "ERROR")
            time.sleep(CHECK_INTERVAL)

# ── 入口 ─────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GA Watchdog")
    parser.add_argument("--once", action="store_true", help="Single health check then exit")
    args = parser.parse_args()
    
    if args.once:
        sys.exit(run_once())
    else:
        run_daemon()
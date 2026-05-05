"""GenericAgent 组件重启脚本 — 通用注册表设计，支持扩展。

Usage:
    python restart_ga.py [fsapp|wxapp|all]   (default: all)
    python restart_ga.py fsapp --kill-only    (只停不启)
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(BASE_DIR, "temp")
CREATE_NO_WINDOW = 0x08000000

# ── 组件注册表 — 新组件只需加一条 ──────────────────────────
COMPONENTS = {
    "ga": {
        "stop_file": None,
        "lock_file": None,
        "ports": [],                       # streamlit 端口是动态分配的，不做端口等待
        "daemon": "launch.pyw",
        "match_keywords": ["launch.pyw", "stapp.py"],
    },
    "fsapp": {
        "stop_file": os.path.join(TEMP_DIR, "fsapp_daemon.stop"),
        "lock_file": os.path.join(TEMP_DIR, "fsapp_daemon.lock"),
        # 8765/8766 are shared local GA control/resource ports, also used by the
        # pywebview/Streamlit frontend.  They are NOT fsapp-owned health ports;
        # using them for stop/status can mis-detect fsapp or kill the frontend.
        "ports": [],
        "shared_ports": [8765, 8766],
        "daemon": "fsapp_daemon.pyw",
        "match_keywords": ["fsapp.py", "fsapp_daemon"],
    },
    "wxapp": {
        "stop_file": os.path.join(TEMP_DIR, "wxapp_daemon.stop"),
        "lock_file": os.path.join(TEMP_DIR, "wxapp_daemon.lock"),
        "ports": [],
        "daemon": "wxapp_daemon.pyw",
        "match_keywords": ["wechatapp.py", "wxapp_daemon"],
    },
}

# all = 只重启 bot 通道（安全默认，不中断当前会话）
# full = 重启全部（含 GA 主程序，会导致当前会话断连）
TARGET_GROUPS = {
    "all": ["fsapp", "wxapp"],
    "full": ["ga", "fsapp", "wxapp"],
}

# ── 工具函数 ─────────────────────────────────────────────
def log(msg):
    print(f"  [{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def pid_alive(pid):
    try:
        r = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True, text=True, encoding="utf-8",
            errors="ignore", creationflags=CREATE_NO_WINDOW, timeout=5,
        )
        return r.returncode == 0 and str(pid) in r.stdout
    except Exception:
        return False


def read_lock_pid(lock_file):
    try:
        with open(lock_file, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return None


def _wmic_python_process_rows():
    """返回 python/pythonw 进程的原始 WMIC CSV 行。"""
    try:
        r = subprocess.run(
            'wmic process where "Name like \'python%\'" get ProcessId,ParentProcessId,CreationDate,CommandLine /format:csv',
            shell=True, capture_output=True, text=True,
            encoding="utf-8", errors="ignore",
            creationflags=CREATE_NO_WINDOW, timeout=10,
        )
        return [line for line in r.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def get_python_process_table():
    """解析 python/pythonw 进程表，字段：pid/ppid/cmd/raw。"""
    rows = []
    for line in _wmic_python_process_rows():
        if line.lower().startswith("node,") or "processid" in line.lower():
            continue
        parts = line.split(",")
        if len(parts) < 5:
            continue
        pid_s = parts[-1].strip()
        ppid_s = parts[-2].strip()
        creation = parts[-3].strip()
        # CommandLine 可能包含逗号；CSV 简化解析：去掉 Node 与末尾三列后拼回。
        cmd = ",".join(parts[1:-3]).strip()
        if not pid_s.isdigit():
            continue
        rows.append({
            "pid": int(pid_s),
            "ppid": int(ppid_s) if ppid_s.isdigit() else None,
            "creation": creation,
            "cmd": cmd,
            "raw": line,
        })
    return rows


def find_pids_by_keyword(keywords):
    """通过 WMIC 查找命令行包含关键词的 pythonw/python 进程。"""
    pids = set()
    for row in get_python_process_table():
        line_lower = row["raw"].lower()
        if any(kw.lower() in line_lower for kw in keywords):
            if row["pid"] != os.getpid():
                pids.add(row["pid"])
    return pids


def find_pids_on_ports(ports):
    """通过 netstat 查找占用指定端口的 PID。"""
    pids = set()
    if not ports:
        return pids
    try:
        r = subprocess.run(
            "netstat -ano", shell=True, capture_output=True, text=True,
            creationflags=CREATE_NO_WINDOW, timeout=10,
        )
        for line in r.stdout.splitlines():
            if "LISTENING" not in line:
                continue
            for port in ports:
                if f":{port} " in line or f":{port}\t" in line:
                    parts = line.split()
                    pid_str = parts[-1].strip()
                    if pid_str.isdigit():
                        pid = int(pid_str)
                        if pid != os.getpid():
                            pids.add(pid)
    except Exception:
        pass
    return pids


def get_process_parent_pid(pid):
    for row in get_python_process_table():
        if row["pid"] == pid:
            return row.get("ppid")
    return None


def get_descendant_pids(root_pids):
    """收集 root_pids 的所有子孙进程 PID，用于保护当前控制链路。"""
    children = {}
    for row in get_python_process_table():
        ppid = row.get("ppid")
        if ppid is not None:
            children.setdefault(ppid, set()).add(row["pid"])
    seen = set(root_pids)
    stack = list(root_pids)
    while stack:
        pid = stack.pop()
        for child in children.get(pid, set()):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return seen


def pids_on_current_process_tree():
    """当前脚本及父/祖先/子孙 PID：默认绝不强杀。"""
    protected = {os.getpid()}
    cur = os.getpid()
    for _ in range(12):
        ppid = get_process_parent_pid(cur)
        if not ppid or ppid in protected:
            break
        protected.add(ppid)
        cur = ppid
    protected |= get_descendant_pids(protected)
    return protected


def active_model_response_pids(max_age_sec=180):
    """最近仍在写 model_responses_<pid>.txt 的 PID 视为活跃会话，禁止误杀。"""
    active = set()
    resp_dir = os.path.join(TEMP_DIR, "model_responses")
    now = time.time()
    if not os.path.isdir(resp_dir):
        return active
    try:
        for fn in os.listdir(resp_dir):
            if not (fn.startswith("model_responses_") and fn.endswith(".txt")):
                continue
            pid_s = fn[len("model_responses_"):-4]
            if not pid_s.isdigit():
                continue
            path = os.path.join(resp_dir, fn)
            try:
                if now - os.path.getmtime(path) <= max_age_sec:
                    active.add(int(pid_s))
            except OSError:
                pass
    except OSError:
        pass
    return active


def build_protected_pids():
    protected = pids_on_current_process_tree()
    protected |= active_model_response_pids()
    return protected


def describe_pid(pid):
    for row in get_python_process_table():
        if row["pid"] == pid:
            cmd = row.get("cmd") or row.get("raw", "")
            return f"PID={pid} PPID={row.get('ppid')} CMD={cmd[:180]}"
    return f"PID={pid}"


def kill_pid(pid, protected_pids=None, reason=""):
    protected_pids = protected_pids or set()
    if pid in protected_pids:
        log(f"[PROTECT] 跳过受保护进程 {describe_pid(pid)} reason={reason}")
        return False
    try:
        log(f"[KILL] {describe_pid(pid)} reason={reason}")
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=10,
        )
        return True
    except Exception as e:
        log(f"[WARN] kill PID={pid} 失败: {e}")
        return False


def wait_condition(check_fn, timeout, interval=1.0, desc=""):
    """等待 check_fn() 返回 True，超时返回 False。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check_fn():
            return True
        time.sleep(interval)
    log(f"[TIMEOUT] 等待超时: {desc}")
    return False


# ── 核心操作 ─────────────────────────────────────────────
def stop_component(name, cfg, force=False):
    """优雅停止组件：stop_file → 等退出 → 安全兜底强杀。force=True 跳过活跃会话保护。"""
    log(f"[STOP] 停止 {name} {'(FORCE)' if force else ''} ...")
    protected_pids = pids_on_current_process_tree()  # 始终保护当前进程树
    if not force:
        protected_pids |= active_model_response_pids()  # 非 force 时保护活跃会话
    if protected_pids:
        log(f"[PROTECT] 当前保护 PID: {sorted(protected_pids)}")

    # 1) 创建 stop_file 通知 daemon 优雅退出
    if cfg["stop_file"]:
        try:
            with open(cfg["stop_file"], "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
            log(f"已创建 stop_file: {os.path.basename(cfg['stop_file'])}")
        except Exception as e:
            log(f"创建 stop_file 失败: {e}，将直接强杀")

    # 2) 等 daemon 自行退出（检查 lock_file 中的 PID）
    daemon_pid = read_lock_pid(cfg["lock_file"]) if cfg["lock_file"] else None
    if daemon_pid and pid_alive(daemon_pid):
        log(f"等待 daemon PID={daemon_pid} 退出 ...")
        exited = wait_condition(
            lambda: not pid_alive(daemon_pid),
            timeout=15, desc=f"daemon PID={daemon_pid} exit"
        )
        if not exited:
            log(f"daemon 未自行退出，尝试安全强杀 PID={daemon_pid}")
            kill_pid(daemon_pid, protected_pids, reason=f"{name}: daemon lock pid")

    # 3) 兜底：按关键词 + 端口查找残留进程；命中保护名单时只记录不杀
    remaining = find_pids_by_keyword(cfg["match_keywords"])
    remaining |= find_pids_on_ports(cfg["ports"])
    if remaining:
        log(f"残留进程候选: {sorted(remaining)}，执行安全清理")
        for pid in sorted(remaining):
            kill_pid(pid, protected_pids, reason=f"{name}: keyword/port cleanup")

    # 4) 等端口释放
    stop_ok = True
    if cfg["ports"]:
        freed = wait_condition(
            lambda: not any(port_in_use(p) for p in cfg["ports"]),
            timeout=10, desc="ports free"
        )
        if not freed:
            log(f"[WARN] 端口 {cfg['ports']} 仍被占用")
            stop_ok = False

    # 5) 清理 stop_file 和 stale lock_file（无论端口是否释放都必须执行）
    for f in [cfg["stop_file"], cfg["lock_file"]]:
        if f is None:
            continue
        try:
            if os.path.exists(f):
                os.remove(f)
        except Exception:
            pass

    if stop_ok:
        log(f"[OK] {name} 已停止")
    else:
        log(f"[WARN] {name} 停止不完整（端口未释放），已清理信号文件")
    return stop_ok


def start_component(name, cfg):
    """通过 pythonw 直接启动 daemon .pyw 文件。"""
    daemon = cfg.get("daemon")
    if not daemon:
        log(f"[WARN] {name} 未配置 daemon，跳过启动")
        return False

    daemon_path = os.path.join(BASE_DIR, daemon)
    if not os.path.isfile(daemon_path):
        log(f"[FAIL] daemon 文件不存在: {daemon_path}")
        return False

    # 防御性清理：确保 stop_file 不会让新 daemon 一启动就退出
    if cfg.get("stop_file"):
        try:
            if os.path.exists(cfg["stop_file"]):
                os.remove(cfg["stop_file"])
                log(f"[CLEAN] 启动前清理残留 stop_file: {os.path.basename(cfg['stop_file'])}")
        except Exception:
            pass

    # 查找 pythonw.exe（与当前 python 同目录）
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pythonw):
        # 回退到 python.exe
        pythonw = sys.executable
        log(f"[WARN] 未找到 pythonw.exe，使用 {pythonw}")

    log(f"[START] 启动 {name} ({daemon}) ...")
    try:
        subprocess.Popen(
            [pythonw, daemon_path],
            cwd=BASE_DIR,
            creationflags=CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    except Exception as e:
        log(f"启动失败: {e}")
        return False

    # 等 lock 文件出现（daemon 成功获取锁）
    lock_file = cfg["lock_file"]
    if lock_file:
        log(f"等待 daemon 锁文件 ...")
        locked = wait_condition(
            lambda: os.path.isfile(lock_file),
            timeout=15, interval=1, desc="lock file appear"
        )
        if not locked:
            log(f"[WARN] {name} 锁文件未出现，daemon 可能启动失败")
            return False
    else:
        log(f"[SKIP] {name} 未配置 lock_file，跳过锁文件等待")
        time.sleep(3)  # 给 daemon 一点启动时间

    # 等端口就绪（有端口的组件才等）
    if cfg["ports"]:
        log(f"等待端口 {cfg['ports']} 就绪 ...")
        ready = wait_condition(
            lambda: all(port_in_use(p) for p in cfg["ports"]),
            timeout=30, interval=2, desc="ports ready"
        )
        if not ready:
            log(f"[WARN] {name} 端口未就绪，请检查 daemon 日志")
            return False

    log(f"[OK] {name} 已启动")
    return True


def restart_component(name, cfg, kill_only=False, force=False):
    """完整重启流程。force=True 跳过活跃会话保护。"""
    ok = stop_component(name, cfg, force=force)
    if kill_only:
        return ok
    if not ok:
        log(f"[WARN] {name} 停止异常，仍尝试启动")
    return start_component(name, cfg)


# ── CLI ──────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="GenericAgent 组件重启")
    valid_targets = list(COMPONENTS.keys()) + list(TARGET_GROUPS.keys())
    parser.add_argument(
        "target", nargs="?", default="all",
        choices=valid_targets,
        help="要重启的组件 (default: all, full=含GA主程序)",
    )
    parser.add_argument(
        "--kill-only", action="store_true",
        help="只停止，不重新启动",
    )
    parser.add_argument(
        "--force", "-f", action="store_true",
        help="强制清理，跳过活跃会话保护",
    )
    args = parser.parse_args()

    # 解析目标：组名展开，单组件直接用
    if args.target in TARGET_GROUPS:
        targets = TARGET_GROUPS[args.target]
    else:
        targets = [args.target]

    # 如果要重启 GA 主程序，提醒用户
    if "ga" in targets and not args.kill_only:
        print("  [!] 将重启 GA 主程序，当前会话会断连，重启后自动恢复。")
        if args.force:
            print("  [!] FORCE 模式：跳过活跃会话保护，可能中断正在处理的请求。")
        print()
    action = "停止" if args.kill_only else "重启"
    print(f"\n{'='*40}")
    print(f"  GenericAgent 组件{action}: {', '.join(targets)}")
    print(f"{'='*40}\n")

    results = {}
    for name in targets:
        results[name] = restart_component(name, COMPONENTS[name], args.kill_only, force=args.force)
        if len(targets) > 1:
            print()

    print(f"{'='*40}")
    for name, ok in results.items():
        status = "[OK] OK" if ok else "[FAIL] FAIL"
        print(f"  {name}: {status}")
    print(f"{'='*40}\n")

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
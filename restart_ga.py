"""GenericAgent 组件重启脚本 — 通用注册表设计，支持扩展。

Usage:
    python restart_ga.py [fsapp|wxapp|all]   (default: all)
    python restart_ga.py fsapp --kill-only    (只停不启)
"""
import argparse
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
    "fsapp": {
        "stop_file": os.path.join(TEMP_DIR, "fsapp_daemon.stop"),
        "lock_file": os.path.join(TEMP_DIR, "fsapp_daemon.lock"),
        "ports": [8765, 8766],
        "schtask": "GA_FeishuBot",
        "match_keywords": ["fsapp.py", "fsapp_daemon"],
    },
    "wxapp": {
        "stop_file": os.path.join(TEMP_DIR, "wxapp_daemon.stop"),
        "lock_file": os.path.join(TEMP_DIR, "wxapp_daemon.lock"),
        "ports": [],
        "schtask": "GA_WeChatBot",
        "match_keywords": ["wechatapp.py", "wxapp_daemon"],
    },
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


def find_pids_by_keyword(keywords):
    """通过 WMIC 查找命令行包含关键词的 pythonw/python 进程。"""
    pids = set()
    try:
        r = subprocess.run(
            'wmic process where "Name like \'python%\'" get ProcessId,CommandLine /format:csv',
            shell=True, capture_output=True, text=True,
            encoding="utf-8", errors="ignore",
            creationflags=CREATE_NO_WINDOW, timeout=10,
        )
        for line in r.stdout.splitlines():
            line_lower = line.lower()
            for kw in keywords:
                if kw.lower() in line_lower:
                    parts = line.strip().split(",")
                    for p in parts:
                        p = p.strip()
                        if p.isdigit():
                            pid = int(p)
                            if pid != os.getpid():
                                pids.add(pid)
    except Exception:
        pass
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


def kill_pid(pid):
    try:
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=10,
        )
    except Exception:
        pass


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
def stop_component(name, cfg):
    """优雅停止组件：stop_file → 等退出 → 兜底强杀。"""
    log(f"[STOP] 停止 {name} ...")

    # 1) 创建 stop_file 通知 daemon 优雅退出
    try:
        with open(cfg["stop_file"], "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        log(f"已创建 stop_file: {os.path.basename(cfg['stop_file'])}")
    except Exception as e:
        log(f"创建 stop_file 失败: {e}，将直接强杀")

    # 2) 等 daemon 自行退出（检查 lock_file 中的 PID）
    daemon_pid = read_lock_pid(cfg["lock_file"])
    if daemon_pid and pid_alive(daemon_pid):
        log(f"等待 daemon PID={daemon_pid} 退出 ...")
        exited = wait_condition(
            lambda: not pid_alive(daemon_pid),
            timeout=15, desc=f"daemon PID={daemon_pid} exit"
        )
        if not exited:
            log(f"daemon 未自行退出，强杀 PID={daemon_pid}")
            kill_pid(daemon_pid)

    # 3) 兜底：按关键词 + 端口查找残留进程
    remaining = find_pids_by_keyword(cfg["match_keywords"])
    remaining |= find_pids_on_ports(cfg["ports"])
    if remaining:
        log(f"残留进程: {remaining}，强杀")
        for pid in remaining:
            kill_pid(pid)

    # 4) 等端口释放
    if cfg["ports"]:
        freed = wait_condition(
            lambda: not any(port_in_use(p) for p in cfg["ports"]),
            timeout=10, desc="ports free"
        )
        if not freed:
            log(f"[WARN] 端口 {cfg['ports']} 仍被占用")
            return False

    # 5) 清理 stop_file 和 stale lock_file
    for f in [cfg["stop_file"], cfg["lock_file"]]:
        try:
            if os.path.exists(f):
                os.remove(f)
        except Exception:
            pass

    log(f"[OK] {name} 已停止")
    return True


def start_component(name, cfg):
    """通过 schtask 启动组件。"""
    task = cfg.get("schtask")
    if not task:
        log(f"[WARN] {name} 未配置 schtask，跳过启动")
        return False

    log(f"[START] 启动 {name} (schtask: {task}) ...")
    try:
        r = subprocess.run(
            ["schtasks", "/run", "/tn", task],
            capture_output=True, text=True, encoding="utf-8",
            errors="ignore", creationflags=CREATE_NO_WINDOW, timeout=10,
        )
        if r.returncode != 0:
            log(f"schtasks /run 失败: {r.stderr.strip()}")
            return False
    except Exception as e:
        log(f"schtasks /run 异常: {e}")
        return False

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


def restart_component(name, cfg, kill_only=False):
    """完整重启流程。"""
    ok = stop_component(name, cfg)
    if kill_only:
        return ok
    if not ok:
        log(f"[WARN] {name} 停止异常，仍尝试启动")
    return start_component(name, cfg)


# ── CLI ──────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="GenericAgent 组件重启")
    parser.add_argument(
        "target", nargs="?", default="all",
        choices=list(COMPONENTS.keys()) + ["all"],
        help="要重启的组件 (default: all)",
    )
    parser.add_argument(
        "--kill-only", action="store_true",
        help="只停止，不重新启动",
    )
    args = parser.parse_args()

    targets = list(COMPONENTS.keys()) if args.target == "all" else [args.target]
    action = "停止" if args.kill_only else "重启"
    print(f"\n{'='*40}")
    print(f"  GenericAgent 组件{action}: {', '.join(targets)}")
    print(f"{'='*40}\n")

    results = {}
    for name in targets:
        results[name] = restart_component(name, COMPONENTS[name], args.kill_only)
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
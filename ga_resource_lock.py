"""GA 跨进程资源锁 — Windows Named Mutex 实现
用法:
    from ga_resource_lock import browser_lock, hid_lock
    with browser_lock:
        ...  # 浏览器操作
    with hid_lock:
        ...  # 键鼠操作
"""
import ctypes, ctypes.wintypes, os

_k32 = ctypes.windll.kernel32
INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0 = 0
WAIT_ABANDONED = 0x80  # 持有者崩溃后自动释放
ERROR_ACCESS_DENIED = 5

# Safety guards:
#   GA_LOCK_NAMESPACE=local   -> force Local\\ locks, useful for non-admin startup
#   GA_LOCK_NO_FALLBACK=1     -> fail fast if Global\\ locks cannot be created
# Only the namespace changes on fallback; mutex semantics and timeouts are unchanged.
_LOCK_NAMESPACE = os.environ.get("GA_LOCK_NAMESPACE", "global").strip().lower()
_LOCK_NO_FALLBACK = os.environ.get("GA_LOCK_NO_FALLBACK", "").strip().lower() in {"1", "true", "yes", "on"}

class NamedMutex:
    """跨进程命名互斥锁，支持 with 语句和超时"""
    def __init__(self, name, timeout_ms=120_000):
        self.name = name
        self.timeout_ms = timeout_ms
        self._handle = _k32.CreateMutexW(None, False, name)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def acquire(self, timeout_ms=None):
        t = timeout_ms if timeout_ms is not None else self.timeout_ms
        ret = _k32.WaitForSingleObject(self._handle, t)
        if ret not in (WAIT_OBJECT_0, WAIT_ABANDONED):
            pid = os.getpid()
            print(f"[GA_LOCK] PID={pid} 获取锁 {self.name} 超时({t}ms)，跳过保护继续执行")
            return False
        if ret == WAIT_ABANDONED:
            print(f"[GA_LOCK] 锁 {self.name} 被崩溃进程遗弃，已自动恢复")
        return True

    def release(self):
        _k32.ReleaseMutex(self._handle)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False

def _is_access_denied_error(exc):
    """CreateMutexW may report WinError 0 in some ctypes setups; keep the check conservative."""
    winerror = getattr(exc, "winerror", None)
    if winerror == ERROR_ACCESS_DENIED:
        return True
    text = str(exc).lower()
    return "拒绝访问" in text or "access is denied" in text


def _with_namespace(name, namespace):
    short_name = name.split("\\", 1)[-1]
    prefix = "Local" if namespace == "local" else "Global"
    return f"{prefix}\\{short_name}"


def _make_mutex(name, timeout_ms=120_000):
    """Create a Global mutex, falling back to Local only for permission failures."""
    namespace = "local" if _LOCK_NAMESPACE == "local" else "global"
    primary_name = _with_namespace(name, namespace)
    try:
        return NamedMutex(primary_name, timeout_ms)
    except OSError as exc:
        if namespace != "global" or _LOCK_NO_FALLBACK or not _is_access_denied_error(exc):
            raise
        fallback_name = _with_namespace(name, "local")
        print(f"[GA_LOCK] 无权限创建/访问 {primary_name}，自动降级到 {fallback_name}；"
              "如需禁止降级请设置 GA_LOCK_NO_FALLBACK=1")
        return NamedMutex(fallback_name, timeout_ms)


browser_lock = _make_mutex("Global\\GA_BROWSER_LOCK")
hid_lock = _make_mutex("Global\\GA_HID_LOCK")
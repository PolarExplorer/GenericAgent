"""飞书 Agent 守护进程 — 用户登录自启 + 崩溃自重启"""
import logging
import os
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(BASE_DIR, 'temp')

PYTHON = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
if not os.path.exists(PYTHON):
    PYTHON = sys.executable

APP_NAME = 'fsapp'
TARGET = os.path.join(BASE_DIR, 'frontends', 'fsapp.py')
LOG_FILE = os.path.join(TEMP_DIR, 'fsapp_daemon.log')
STDOUT_LOG = os.path.join(TEMP_DIR, 'fsapp_stdout.log')
LOCK_FILE = os.path.join(TEMP_DIR, 'fsapp_daemon.lock')
STOP_FILE = os.path.join(TEMP_DIR, 'fsapp_daemon.stop')

RESTART_DELAY = 10        # 崩溃后等待秒数
MAX_RAPID_RESTARTS = 5    # 短时间内最大重启次数
RAPID_WINDOW = 120        # "短时间"定义（秒）
STOP_POLL_INTERVAL = 2
CHILD_STOP_TIMEOUT = 10
CREATE_NO_WINDOW = 0x08000000

os.makedirs(TEMP_DIR, exist_ok=True)
logging.basicConfig(
    filename=LOG_FILE, level=logging.INFO,
    format='%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S',
    encoding='utf-8'
)
log = logging.getLogger('fsapp_daemon')


def pid_is_running(pid):
    if not pid or pid == os.getpid():
        return False
    try:
        result = subprocess.run(
            ['tasklist', '/FI', f'PID eq {pid}'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore',
            creationflags=CREATE_NO_WINDOW,
            timeout=10
        )
        return result.returncode == 0 and str(pid) in result.stdout
    except Exception as e:
        log.warning(f'检查旧锁 PID 失败: pid={pid}, error={e}')
        return True


def acquire_lock():
    while True:
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(str(os.getpid()))
            log.info(f'已获取单实例锁: {LOCK_FILE}')
            return True
        except FileExistsError:
            try:
                with open(LOCK_FILE, 'r', encoding='utf-8') as f:
                    old_pid = int((f.read() or '0').strip())
            except Exception:
                old_pid = 0

            if pid_is_running(old_pid):
                log.warning(f'检测到已有守护进程运行，退出: pid={old_pid}')
                return False

            log.warning(f'发现陈旧锁文件，准备删除: {LOCK_FILE}, pid={old_pid}')
            try:
                os.remove(LOCK_FILE)
            except FileNotFoundError:
                pass
            except Exception as e:
                log.error(f'删除陈旧锁文件失败: {e}')
                return False


def release_lock():
    try:
        if os.path.exists(LOCK_FILE):
            with open(LOCK_FILE, 'r', encoding='utf-8') as f:
                lock_pid = (f.read() or '').strip()
            if lock_pid == str(os.getpid()):
                os.remove(LOCK_FILE)
                log.info('已释放单实例锁')
    except Exception as e:
        log.warning(f'释放单实例锁失败: {e}')


def should_stop():
    return os.path.exists(STOP_FILE)


def clear_stop_file():
    try:
        if os.path.exists(STOP_FILE):
            os.remove(STOP_FILE)
    except Exception as e:
        log.warning(f'删除停止文件失败: {e}')


def stop_child(proc):
    if proc.poll() is not None:
        return

    log.info(f'收到停止请求，准备结束 {APP_NAME}.py: PID={proc.pid}')
    try:
        proc.terminate()
        try:
            proc.wait(timeout=CHILD_STOP_TIMEOUT)
            log.info(f'{APP_NAME}.py 已正常结束, code={proc.returncode}')
        except subprocess.TimeoutExpired:
            log.warning(f'{APP_NAME}.py 未在 {CHILD_STOP_TIMEOUT}s 内退出，强制结束')
            proc.kill()
            proc.wait(timeout=CHILD_STOP_TIMEOUT)
            log.info(f'{APP_NAME}.py 已强制结束, code={proc.returncode}')
    except Exception as e:
        log.error(f'结束 {APP_NAME}.py 失败: {e}')


def validate_environment():
    if not os.path.exists(PYTHON):
        log.error(f'Python 不存在: {PYTHON}')
        return False
    if not os.path.exists(TARGET):
        log.error(f'目标脚本不存在: {TARGET}')
        return False
    return True


def sleep_with_stop_check(seconds):
    end_time = time.time() + seconds
    while time.time() < end_time:
        if should_stop():
            log.info('等待期间收到停止请求，守护进程退出')
            clear_stop_file()
            return False
        time.sleep(min(STOP_POLL_INTERVAL, max(0, end_time - time.time())))
    return True


def run():
    restart_times = []
    log.info('守护进程启动')

    if not acquire_lock():
        return

    try:
        if not validate_environment():
            return

        while True:
            if should_stop():
                log.info('检测到停止文件，守护进程退出')
                clear_stop_file()
                return

            now = time.time()
            # 清理过期的重启记录
            restart_times = [t for t in restart_times if now - t < RAPID_WINDOW]

            if len(restart_times) >= MAX_RAPID_RESTARTS:
                wait = RAPID_WINDOW
                log.warning(f'短时间内重启 {MAX_RAPID_RESTARTS} 次，等待 {wait}s 后继续')
                if not sleep_with_stop_check(wait):
                    return
                restart_times.clear()

            log.info(f'启动 {APP_NAME}.py (pid will follow)')
            try:
                with open(STDOUT_LOG, 'a', encoding='utf-8', buffering=1) as out:
                    child_env = os.environ.copy()
                    child_env['PYTHONUNBUFFERED'] = '1'
                    child_env['PYTHONDONTWRITEBYTECODE'] = '1'
                    proc = subprocess.Popen(
                        [PYTHON, TARGET],
                        cwd=BASE_DIR,
                        stdout=out,
                        stderr=subprocess.STDOUT,
                        creationflags=CREATE_NO_WINDOW,
                        env=child_env,
                    )
                    log.info(f'{APP_NAME}.py 已启动, PID={proc.pid}')

                    while proc.poll() is None:
                        if should_stop():
                            stop_child(proc)
                            clear_stop_file()
                            return
                        time.sleep(STOP_POLL_INTERVAL)

                    code = proc.returncode
                    log.warning(f'{APP_NAME}.py 退出, code={code}')
            except Exception as e:
                log.error(f'启动失败: {e}')
                code = -1

            restart_times.append(time.time())
            log.info(f'{RESTART_DELAY}s 后重启...')
            if not sleep_with_stop_check(RESTART_DELAY):
                return
    finally:
        release_lock()


if __name__ == '__main__':
    run()
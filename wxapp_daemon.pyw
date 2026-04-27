"""微信 Agent 守护进程 — 开机自启 + 崩溃自重启"""
import subprocess, sys, os, time, logging

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
if not os.path.exists(PYTHON):
    PYTHON = sys.executable
WXAPP = os.path.join(BASE_DIR, 'frontends', 'wechatapp.py')
LOG_FILE = os.path.join(BASE_DIR, 'temp', 'wxapp_daemon.log')

RESTART_DELAY = 10
MAX_RAPID_RESTARTS = 5
RAPID_WINDOW = 120

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    filename=LOG_FILE, level=logging.INFO,
    format='%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S',
    encoding='utf-8'
)
log = logging.getLogger('wxapp_daemon')


def run():
    restart_times = []
    log.info('守护进程启动')

    while True:
        now = time.time()
        restart_times = [t for t in restart_times if now - t < RAPID_WINDOW]

        if len(restart_times) >= MAX_RAPID_RESTARTS:
            wait = RAPID_WINDOW
            log.warning(f'短时间内重启 {MAX_RAPID_RESTARTS} 次，等待 {wait}s 后继续')
            time.sleep(wait)
            restart_times.clear()

        log.info('启动 wechatapp.py (pid will follow)')
        try:
            proc = subprocess.Popen(
                [PYTHON, WXAPP],
                cwd=BASE_DIR,
                stdout=open(os.path.join(BASE_DIR, 'temp', 'wxapp_stdout.log'), 'a', encoding='utf-8'),
                stderr=subprocess.STDOUT,
                creationflags=0x08000000  # CREATE_NO_WINDOW
            )
            log.info(f'wechatapp.py 已启动, PID={proc.pid}')
            proc.wait()
            code = proc.returncode
            log.warning(f'wechatapp.py 退出, code={code}')
        except Exception as e:
            log.error(f'启动失败: {e}')
            code = -1

        restart_times.append(time.time())
        log.info(f'{RESTART_DELAY}s 后重启...')
        time.sleep(RESTART_DELAY)


if __name__ == '__main__':
    run()
import webview, threading, subprocess, sys, time, os, ctypes, atexit, socket

WINDOW_WIDTH, WINDOW_HEIGHT, RIGHT_PADDING, TOP_PADDING = 600, 900, 0, 100
DEFAULT_PORT = 18513

script_dir = os.path.dirname(os.path.abspath(__file__))
frontends_dir = os.path.join(script_dir, "frontends")

def is_local_port_open(port, host='127.0.0.1', timeout=0.5):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False

def wait_for_local_port(port, host='127.0.0.1', timeout=20, poll_interval=0.2):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_local_port_open(port, host=host, timeout=min(poll_interval, 0.5)):
            return True
        time.sleep(poll_interval)
    return False

def _is_bot_running(script_name):
    """Check if a bot frontend (e.g. fsapp.py) is already running in another process."""
    try:
        import psutil
        target = script_name.lower()
        my_pid = os.getpid()
        for p in psutil.process_iter(['pid', 'cmdline']):
            if p.info['pid'] == my_pid:
                continue
            cmdline = p.info.get('cmdline') or []
            if any(target in str(arg).lower() for arg in cmdline):
                return True
    except Exception:
        pass
    return False

def get_screen_width():
    try: return ctypes.windll.user32.GetSystemMetrics(0)
    except: return 1920

def start_streamlit(port):
    global proc
    cmd = [sys.executable, "-m", "streamlit", "run", os.path.join(frontends_dir, "stapp.py"), "--server.port", str(port), "--server.address", "localhost", "--server.headless", "true", "--client.toolbarMode", "viewer"]
    proc = subprocess.Popen(cmd)
    atexit.register(proc.kill)

def inject(text):
    window.evaluate_js(f"""
        const textarea = document.querySelector('textarea[data-testid="stChatInputTextArea"]');
        if (textarea) {{
            // 1. 用原生 setter 设置值（绕过 React）
            const nativeTextAreaValueSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
            nativeTextAreaValueSetter.call(textarea, {repr(text)});
            // 2. 触发 React 的 input 事件
            textarea.dispatchEvent(new Event('input', {{ bubbles: true }}));
            // 3. 触发 change 事件（有些组件需要）
            textarea.dispatchEvent(new Event('change', {{ bubbles: true }}));
            // 4. 延迟提交
            setTimeout(() => {{
                const btn = document.querySelector('[data-testid="stChatInputSubmitButton"]');
                if (btn) {{btn.click();console.log('Submitted:', {repr(text)});}}
            }}, 200);
        }}""")

def get_last_reply_time():
    last = window.evaluate_js("""
        const el = document.getElementById('last-reply-time');
        el ? parseInt(el.textContent) : 0;
    """) or 0
    return last or int(time.time())

PASTE_HOOK_JS = """if (!window._pasteHooked) { window._pasteHooked = true;
    document.addEventListener('paste', e => {
        const items = e.clipboardData?.items; if (!items) return;
        let t = null, hasText = false;
        for (const item of items) {
            if (item.kind === 'string' && (item.type === 'text/plain' || item.type === 'text/html')) hasText = true;
            if (item.kind === 'file') { t = item.type.startsWith('image/') ? 'image in clipboard, ' : 'file in clipboard, '; }
        }
        if (!t || hasText) return;
        e.preventDefault(); e.stopImmediatePropagation();
        const el = document.querySelector('textarea[data-testid="stChatInputTextArea"]') || document.activeElement;
        if (el && (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT')) {
            const s = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
            s.call(el, el.value + t); el.dispatchEvent(new Event('input', { bubbles: true }));
        }
    }, true);
}"""

def idle_monitor():
    last_trigger_time = 0
    while True:
        time.sleep(5)
        try:
            window.evaluate_js(PASTE_HOOK_JS)
            now = time.time()
            if now - last_trigger_time < 120: continue
            last_reply = get_last_reply_time()
            if now - last_reply > 1800:
                print('[Idle Monitor] Detected idle state, injecting task...')
                inject("[AUTO]🤖 用户已经离开超过30分钟，作为自主智能体，请阅读自动化sop，执行自动任务。")
                last_trigger_time = now
        except Exception as e:
            print(f'[Idle Monitor] Error: {e}')

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('port', nargs='?', default='0'); 
    parser.add_argument('--tg', action='store_true', help='启动 Telegram Bot'); 
    parser.add_argument('--qq', action='store_true', help='启动 QQ Bot');
    parser.add_argument('--feishu', '--fs', dest='feishu', action='store_true', help='启动 Feishu Bot');
    parser.add_argument('--wechat', '--wx', dest='wechat', action='store_true', help='启动 WeChat Bot');
    parser.add_argument('--wecom', action='store_true', help='启动 WeCom Bot');
    parser.add_argument('--dingtalk', '--dt', dest='dingtalk', action='store_true', help='启动 DingTalk Bot');
    parser.add_argument('--sched', action='store_true', help='启动计划任务调度器')
    parser.add_argument('--llm_no', type=int, default=0, help='LLM编号')
    args = parser.parse_args()

    if args.port == '0':
        port = str(DEFAULT_PORT)
    else:
        port = str(args.port)

    existing_instance = is_local_port_open(port)
    if existing_instance:
        print(f'[Launch] Existing instance detected on port {port}, attaching webview only')
    else:
        print(f'[Launch] Starting new instance on port {port}')
        threading.Thread(target=start_streamlit, args=(port,), daemon=True).start()
        if not wait_for_local_port(port, timeout=20):
            raise RuntimeError(f'Streamlit did not become ready on port {port}')

    if args.tg and not existing_instance:
        tgproc = subprocess.Popen([sys.executable, os.path.join(frontends_dir, "tgapp.py")], creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        atexit.register(tgproc.kill)
        print('[Launch] Telegram Bot started')
    elif args.tg:
        print('[Launch] Telegram Bot start skipped because existing instance is reused')
    else: print('[Launch] Telegram Bot not enabled (use --tg to start)')

    if args.qq and not existing_instance:
        qqproc = subprocess.Popen([sys.executable, os.path.join(frontends_dir, "qqapp.py")], creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        atexit.register(qqproc.kill)
        print('[Launch] QQ Bot started')
    elif args.qq:
        print('[Launch] QQ Bot start skipped because existing instance is reused')
    else: print('[Launch] QQ Bot not enabled (use --qq to start)')

    if args.feishu and not existing_instance:
        if _is_bot_running('fsapp.py'):
            print('[Launch] Feishu Bot already running (daemon?), skipped')
        else:
            fsproc = subprocess.Popen([sys.executable, os.path.join(frontends_dir, "fsapp.py")], creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            atexit.register(fsproc.kill)
            print('[Launch] Feishu Bot started')
    elif args.feishu:
        print('[Launch] Feishu Bot start skipped because existing instance is reused')
    else: print('[Launch] Feishu Bot not enabled (use --feishu to start)')

    if args.wechat:
        if _is_bot_running('wechatapp.py'):
            print('[Launch] WeChat Bot already running (daemon?), skipped')
        else:
            wxproc = subprocess.Popen([sys.executable, os.path.join(frontends_dir, 'wechatapp.py')], creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            atexit.register(wxproc.kill)
            print('[Launch] WeChat Bot started')
    else: print('[Launch] WeChat Bot not enabled (use --wechat to start)')

    if args.wecom and not existing_instance:
        if _is_bot_running('wecomapp.py'):
            print('[Launch] WeCom Bot already running (daemon?), skipped')
        else:
            wcproc = subprocess.Popen([sys.executable, os.path.join(frontends_dir, "wecomapp.py")], creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            atexit.register(wcproc.kill)
            print('[Launch] WeCom Bot started')
    elif args.wecom:
        print('[Launch] WeCom Bot start skipped because existing instance is reused')
    else: print('[Launch] WeCom Bot not enabled (use --wecom to start)')

    if args.dingtalk and not existing_instance:
        if _is_bot_running('dingtalkapp.py'):
            print('[Launch] DingTalk Bot already running (daemon?), skipped')
        else:
            dtproc = subprocess.Popen([sys.executable, os.path.join(frontends_dir, "dingtalkapp.py")], creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            atexit.register(dtproc.kill)
            print('[Launch] DingTalk Bot started')
    elif args.dingtalk:
        print('[Launch] DingTalk Bot start skipped because existing instance is reused')
    else: print('[Launch] DingTalk Bot not enabled (use --dingtalk to start)')
    
    if args.sched and not existing_instance:
        scheduler_proc = subprocess.Popen([sys.executable, os.path.join(script_dir, "agentmain.py"), "--reflect", os.path.join(script_dir, "reflect", "scheduler.py"), "--llm_no", str(args.llm_no)], creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        atexit.register(scheduler_proc.kill)
        print('[Launch] Task Scheduler started (duplicate prevented by scheduler port lock)')
    elif args.sched:
        print('[Launch] Task Scheduler start skipped because existing instance is reused')
    else: print('[Launch] Task Scheduler not enabled (--sched)')

    monitor_thread = threading.Thread(target=idle_monitor, daemon=True)
    monitor_thread.start()
    if os.name == 'nt':
        screen_width = get_screen_width()
        x_pos = screen_width - WINDOW_WIDTH - RIGHT_PADDING
    else: x_pos = 100
    time.sleep(2)
    window = webview.create_window(
        title='GenericAgent', url=f'http://localhost:{port}',
        width=WINDOW_WIDTH, height=WINDOW_HEIGHT, x=x_pos, y=TOP_PADDING,
        resizable=True, text_select=True)
    webview.start()

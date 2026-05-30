import argparse, asyncio, importlib.util, json, os, queue as Q, re, sys, threading, time, uuid
from pathlib import Path

try:
    sys.path.insert(0, r'D:\zhishiku\scripts')
    from training_feishu_router import handle_training_message as _training_router
except ImportError:
    _training_router = None

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

import traceback
import lark_oapi as lark
from lark_oapi.api.im.v1 import *

# --- msg_id dedup to prevent duplicate processing (2026-05-11) ---
_seen_msg_ids_lock = threading.Lock()
_seen_msg_ids = {}  # msg_id -> timestamp
_SEEN_MSG_TTL = 300  # 5 minutes


def _is_duplicate_msg(msg_id):
    """Return True if this msg_id was already processed recently."""
    if not msg_id or msg_id == '?':
        return False
    now = time.time()
    with _seen_msg_ids_lock:
        # Cleanup old entries periodically
        if len(_seen_msg_ids) > 500:
            cutoff = now - _SEEN_MSG_TTL
            expired = [k for k, v in _seen_msg_ids.items() if v < cutoff]
            for k in expired:
                del _seen_msg_ids[k]
        if msg_id in _seen_msg_ids:
            return True
        _seen_msg_ids[msg_id] = now
        return False


def _ensure_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _workspace_root_dir():
    root = os.environ.get("GA_WORKSPACE_ROOT")
    if root:
        return _ensure_dir(Path(root).expanduser().resolve())
    return _ensure_dir(Path(PROJECT_ROOT).resolve())


def _workspace_config_dir(root=None):
    base = Path(root).expanduser().resolve() if root else _workspace_root_dir()
    if base.name == "ga_config":
        return _ensure_dir(base)
    return _ensure_dir(base / "ga_config")


def _load_dict_config(path):
    path = Path(path)
    if not path.exists():
        return None
    try:
        if path.suffix == ".py":
            mod_name = f"_fs_mykey_{uuid.uuid4().hex}"
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if not spec or not spec.loader:
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            data = {k: v for k, v in vars(module).items() if not k.startswith("_")}
        else:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception as e:
        print(f"[ERROR] load config failed {path}: {e}")
        return None


def _resolve_mykey_path():
    workspace_root = _workspace_root_dir()
    config_root = _workspace_config_dir(workspace_root)
    candidates = [
        config_root / "mykey.json",
        config_root / "mykey.py",
        workspace_root / "mykey.json",
        workspace_root / "mykey.py",
        Path(PROJECT_ROOT) / "mykey.json",
        Path(PROJECT_ROOT) / "mykey.py",
    ]
    for candidate in candidates:
        if _load_dict_config(candidate):
            return candidate
    return candidates[0]


def _ensure_runtime_paths():
    workspace_root = _workspace_root_dir()
    config_root = _workspace_config_dir(workspace_root)
    os.environ.setdefault("GA_WORKSPACE_ROOT", str(workspace_root))
    os.environ.setdefault("GA_USER_DATA_DIR", str(config_root))
    return str(workspace_root), str(config_root)


_ensure_runtime_paths()
from agentmain import GeneraticAgent
from frontends.chatapp_common import AgentChatMixin, FILE_HINT, split_text

_TAG_PATS = [r"<" + t + r">.*?</" + t + r">" for t in ("thinking", "summary", "tool_use", "file_content")]
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".tiff", ".tif"}
_AUDIO_EXTS = {".opus", ".mp3", ".wav", ".m4a", ".aac"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
_FILE_TYPE_MAP = {
    ".opus": "opus",
    ".mp4": "mp4",
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "doc",
    ".xls": "xls",
    ".xlsx": "xls",
    ".ppt": "ppt",
    ".pptx": "ppt",
}
_MSG_TYPE_MAP = {"image": "[image]", "audio": "[audio]", "file": "[file]", "media": "[media]", "sticker": "[sticker]"}

TEMP_DIR = os.path.join(PROJECT_ROOT, "temp")
MEDIA_DIR = os.path.join(TEMP_DIR, "feishu_media")
os.makedirs(MEDIA_DIR, exist_ok=True)


_TRUNC_TAIL = 300  # 截断兜底时保留原文尾部字符数
_DEDUP_TTL_SEC = 10 * 60
_DEDUP_MAX = 2000
_DEDUP_LOCK = threading.Lock()
_SEEN_MESSAGES = {}


def _claim_message_once(message_id):
    """Best-effort cross-platform dedup for Feishu reconnect redeliveries."""
    if not message_id:
        return True
    now = time.time()
    with _DEDUP_LOCK:
        expired = [mid for mid, ts in _SEEN_MESSAGES.items() if now - ts > _DEDUP_TTL_SEC]
        for mid in expired:
            _SEEN_MESSAGES.pop(mid, None)
        if len(_SEEN_MESSAGES) > _DEDUP_MAX:
            for mid, _ in sorted(_SEEN_MESSAGES.items(), key=lambda item: item[1])[:len(_SEEN_MESSAGES) - _DEDUP_MAX]:
                _SEEN_MESSAGES.pop(mid, None)
        if message_id in _SEEN_MESSAGES:
            return False
        _SEEN_MESSAGES[message_id] = now
        return True


def _clean(text):
    for pat in _TAG_PATS:
        text = re.sub(pat, "", text or "", flags=re.DOTALL)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _extract_files(text):
    return re.findall(r"\[FILE:([^\]]+)\]", text or "")


def _strip_files(text):
    return re.sub(r"\[FILE:[^\]]+\]", "", text or "").strip()


def _display_text(text):
    cleaned = _strip_files(_clean(text))
    if cleaned:
        return cleaned
    tail = (text or "").strip()[-_TRUNC_TAIL:]
    return "⚠️ 模型输出被截断或为空" + (f"\n…{tail}" if tail else "")


def _to_allowed_set(value):
    if value is None:
        return set()
    if isinstance(value, str):
        value = [value]
    return {str(x).strip() for x in value if str(x).strip()}


def _parse_json(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _extract_share_card_content(content_json, msg_type):
    parts = []
    if msg_type == "share_chat":
        parts.append(f"[shared chat: {content_json.get('chat_id', '')}]")
    elif msg_type == "share_user":
        parts.append(f"[shared user: {content_json.get('user_id', '')}]")
    elif msg_type == "interactive":
        parts.extend(_extract_interactive_content(content_json))
    elif msg_type == "share_calendar_event":
        parts.append(f"[shared calendar event: {content_json.get('event_key', '')}]")
    elif msg_type == "system":
        parts.append("[system message]")
    elif msg_type == "merge_forward":
        parts.append("[merged forward messages]")
    return "\n".join([p for p in parts if p]).strip() or f"[{msg_type}]"


def _extract_interactive_content(content):
    parts = []
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except Exception:
            return [content] if content.strip() else []
    if not isinstance(content, dict):
        return parts
    title = content.get("title")
    if isinstance(title, dict):
        title_text = title.get("content", "") or title.get("text", "")
        if title_text:
            parts.append(f"title: {title_text}")
    elif isinstance(title, str) and title:
        parts.append(f"title: {title}")
    elements = content.get("elements", [])
    if isinstance(elements, list):
        for row in elements:
            if isinstance(row, dict):
                parts.extend(_extract_element_content(row))
            elif isinstance(row, list):
                for el in row:
                    parts.extend(_extract_element_content(el))
    card = content.get("card", {})
    if card:
        parts.extend(_extract_interactive_content(card))
    header = content.get("header", {})
    if isinstance(header, dict):
        header_title = header.get("title", {})
        if isinstance(header_title, dict):
            header_text = header_title.get("content", "") or header_title.get("text", "")
            if header_text:
                parts.append(f"title: {header_text}")
    return [p for p in parts if p]


def _extract_element_content(element):
    parts = []
    if not isinstance(element, dict):
        return parts
    tag = element.get("tag", "")
    if tag in ("markdown", "lark_md"):
        content = element.get("content", "")
        if content:
            parts.append(content)
    elif tag == "div":
        text = element.get("text", {})
        if isinstance(text, dict):
            text_content = text.get("content", "") or text.get("text", "")
            if text_content:
                parts.append(text_content)
        elif isinstance(text, str) and text:
            parts.append(text)
        for field in element.get("fields", []) or []:
            if isinstance(field, dict):
                field_text = field.get("text", {})
                if isinstance(field_text, dict):
                    content = field_text.get("content", "") or field_text.get("text", "")
                    if content:
                        parts.append(content)
    elif tag == "a":
        href = element.get("href", "")
        text = element.get("text", "")
        if href:
            parts.append(f"link: {href}")
        if text:
            parts.append(text)
    elif tag == "button":
        text = element.get("text", {})
        if isinstance(text, dict):
            content = text.get("content", "") or text.get("text", "")
            if content:
                parts.append(content)
        url = element.get("url", "") or (element.get("multi_url", {}) or {}).get("url", "")
        if url:
            parts.append(f"link: {url}")
    elif tag == "img":
        alt = element.get("alt", {})
        if isinstance(alt, dict):
            parts.append(alt.get("content", "[image]") or "[image]")
        else:
            parts.append("[image]")
    for child in element.get("elements", []) or []:
        parts.extend(_extract_element_content(child))
    for col in element.get("columns", []) or []:
        for child in (col.get("elements", []) if isinstance(col, dict) else []):
            parts.extend(_extract_element_content(child))
    return parts


def _extract_post_content(content_json):
    def _parse_block(block):
        if not isinstance(block, dict) or not isinstance(block.get("content"), list):
            return None, []
        texts, images = [], []
        if block.get("title"):
            texts.append(block.get("title"))
        for row in block["content"]:
            if not isinstance(row, list):
                continue
            for el in row:
                if not isinstance(el, dict):
                    continue
                tag = el.get("tag")
                if tag in ("text", "a"):
                    texts.append(el.get("text", ""))
                elif tag == "at":
                    texts.append(f"@{el.get('user_name', 'user')}")
                elif tag == "img" and el.get("image_key"):
                    images.append(el["image_key"])
        text = " ".join([t for t in texts if t]).strip()
        return text or None, images

    root = content_json
    if isinstance(root, dict) and isinstance(root.get("post"), dict):
        root = root["post"]
    if not isinstance(root, dict):
        return "", []
    if "content" in root:
        text, imgs = _parse_block(root)
        if text or imgs:
            return text or "", imgs
    for key in ("zh_cn", "en_us", "ja_jp"):
        if key in root:
            text, imgs = _parse_block(root[key])
            if text or imgs:
                return text or "", imgs
    for val in root.values():
        if isinstance(val, dict):
            text, imgs = _parse_block(val)
            if text or imgs:
                return text or "", imgs
    return "", []


AGENT_TIMEOUT_SEC = 900

agent = None
agent_error = None
agent_thread = None
client, user_tasks, app = None, {}, None
agent_lock = threading.Lock()


def _load_config():
    path = _resolve_mykey_path()
    if not path or not path.exists():
        return {}, str(path or "")
    try:
        data = _load_dict_config(path)
        return data if isinstance(data, dict) else {}, str(path)
    except Exception as e:
        print(f"[ERROR] load mykey failed {path}: {e}")
        return {}, str(path)


def _feishu_config():
    cfg, path = _load_config()
    app_id = str(cfg.get("fs_app_id", "") or "").strip()
    app_secret = str(cfg.get("fs_app_secret", "") or "").strip()
    allowed = _to_allowed_set(cfg.get("fs_allowed_users", []))
    return app_id, app_secret, allowed, (not allowed or "*" in allowed), cfg, path


APP_ID, APP_SECRET, ALLOWED_USERS, PUBLIC_ACCESS, _feishu_cfg, CONFIG_PATH = _feishu_config()
BLOCKED_CHATS = _to_allowed_set(_feishu_cfg.get("fs_blocked_chats", []))
BLOCKED_CHATS.add("oc_a1b0b767eba2fef9ae83e83fa9f9a2e2")
TRAINING_OPEN_IDS = _to_allowed_set(_feishu_cfg.get("fs_training_open_ids", []))
TRAINING_CHATS = _to_allowed_set(_feishu_cfg.get("fs_training_chats", []))


def is_training_source(open_id, chat_id):
    return bool((open_id and open_id in TRAINING_OPEN_IDS) or (chat_id and chat_id in TRAINING_CHATS))


def get_agent():
    global agent, agent_error, agent_thread
    with agent_lock:
        if agent is not None:
            return agent
        if agent_error:
            raise RuntimeError(agent_error)
        try:
            agent = GeneraticAgent()
            agent_thread = threading.Thread(target=agent.run, daemon=True, name="GA-core")
            agent_thread.start()
            print(f"[INFO] GA core started pid={os.getpid()} thread_alive={agent_thread.is_alive()} llm={agent.get_llm_name()}")
            return agent
        except Exception as e:
            agent_error = str(e)
            raise


def create_client():
    return lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).log_level(lark.LogLevel.INFO).build()


def _mask_secret(value):
    value = str(value or "")
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def check_config(init_agent=False):
    app_id, app_secret, allowed, public_access, path = _feishu_config()
    result = {
        "config_path": path,
        "app_id": app_id,
        "app_secret": _mask_secret(app_secret),
        "app_secret_present": bool(app_secret),
        "public_access": public_access,
        "allowed_users": sorted(allowed),
        "ready": bool(app_id and app_secret),
    }
    if init_agent:
        try:
            ga = get_agent()
            result["agent_ready"] = True
            result["llm_count"] = len(ga.list_llms()) if hasattr(ga, "list_llms") else 0
            result["current_llm"] = ga.get_llm_name() if getattr(ga, "llmclient", None) else ""
        except Exception as e:
            result["agent_ready"] = False
            result["agent_error"] = str(e)
    return result


def _card_raw(elements):
    return json.dumps({
        "schema": "2.0",
        "config": {"streaming_mode": False, "width_mode": "fill"},
        "body": {"elements": elements},
    }, ensure_ascii=False)


def _card(text):
    return _card_raw([{"tag": "markdown", "content": text}])


def _send_raw(receive_id, payload, msg_type, rtype):
    try:
        body = CreateMessageRequest.builder().receive_id_type(rtype).request_body(
            CreateMessageRequestBody.builder().receive_id(receive_id).msg_type(msg_type).content(payload).build()
        ).build()
        r = client.im.v1.message.create(body)
        if r.success():
            return r.data.message_id if r.data else None
        print(f"发送失败: {r.code}, {r.msg}")
    except Exception as e:
        print(f"[ERROR] send_message failed: {e}")
        traceback.print_exc()
    return None


def _patch_card(message_id, card_json):
    try:
        body = PatchMessageRequest.builder().message_id(message_id).request_body(
            PatchMessageRequestBody.builder().content(card_json).build()
        ).build()
        r = client.im.v1.message.patch(body)
        if not r.success():
            print(f"[ERROR] patch_card 失败: {r.code}, {r.msg}")
        return r.success()
    except Exception as e:
        print(f"[ERROR] patch_card exception: {e}")
        traceback.print_exc()
        return False


def send_message(receive_id, content, msg_type="text", use_card=False, receive_id_type="open_id"):
    if use_card:
        return _send_raw(receive_id, _card(content), "interactive", receive_id_type)
    if msg_type == "text":
        return _send_raw(receive_id, json.dumps({"text": content}, ensure_ascii=False), "text", receive_id_type)
    return _send_raw(receive_id, content, msg_type, receive_id_type)


def update_message(message_id, content):
    return _patch_card(message_id, _card(content))


def _upload_image_sync(file_path):
    try:
        with open(file_path, "rb") as f:
            request = CreateImageRequest.builder().request_body(
                CreateImageRequestBody.builder().image_type("message").image(f).build()
            ).build()
            response = client.im.v1.image.create(request)
            if response.success():
                return response.data.image_key
            print(f"[ERROR] upload image failed: {response.code}, {response.msg}")
    except Exception as e:
        print(f"[ERROR] upload image failed {file_path}: {e}")
    return None


def _upload_file_sync(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    file_type = _FILE_TYPE_MAP.get(ext, "stream")
    file_name = os.path.basename(file_path)
    try:
        with open(file_path, "rb") as f:
            request = CreateFileRequest.builder().request_body(
                CreateFileRequestBody.builder().file_type(file_type).file_name(file_name).file(f).build()
            ).build()
            response = client.im.v1.file.create(request)
            if response.success():
                return response.data.file_key
            print(f"[ERROR] upload file failed: {response.code}, {response.msg}")
    except Exception as e:
        print(f"[ERROR] upload file failed {file_path}: {e}")
    return None


def _download_image_sync(message_id, image_key):
    try:
        request = GetMessageResourceRequest.builder().message_id(message_id).file_key(image_key).type("image").build()
        response = client.im.v1.message_resource.get(request)
        if response.success():
            data = response.file.read() if hasattr(response.file, "read") else response.file
            return data, response.file_name
        print(f"[ERROR] download image failed: {response.code}, {response.msg}")
    except Exception as e:
        print(f"[ERROR] download image failed {image_key}: {e}")
    return None, None


def _download_file_sync(message_id, file_key, resource_type="file"):
    if resource_type == "audio":
        resource_type = "file"
    try:
        request = GetMessageResourceRequest.builder().message_id(message_id).file_key(file_key).type(resource_type).build()
        response = client.im.v1.message_resource.get(request)
        if response.success():
            data = response.file.read() if hasattr(response.file, "read") else response.file
            return data, response.file_name
        print(f"[ERROR] download {resource_type} failed: {response.code}, {response.msg}")
    except Exception as e:
        print(f"[ERROR] download {resource_type} failed {file_key}: {e}")
    return None, None


def _download_and_save_media(msg_type, content_json, message_id):
    data, filename = None, None
    if msg_type == "image":
        image_key = content_json.get("image_key")
        if image_key and message_id:
            data, filename = _download_image_sync(message_id, image_key)
            if not filename:
                filename = f"{image_key[:16]}.jpg"
    elif msg_type in ("audio", "file", "media"):
        file_key = content_json.get("file_key")
        if file_key and message_id:
            data, filename = _download_file_sync(message_id, file_key, msg_type)
            if not filename:
                filename = file_key[:16]
            if msg_type == "audio" and filename and not filename.endswith(".opus"):
                filename = f"{filename}.opus"
    if data and filename:
        file_path = os.path.join(MEDIA_DIR, os.path.basename(filename))
        with open(file_path, "wb") as f:
            f.write(data)
        return file_path, filename
    return None, None


def _describe_media(msg_type, file_path, filename):
    if msg_type == "image":
        return f"[image: {filename}]\n[Image: source: {file_path}]"
    if msg_type == "audio":
        return f"[audio: {filename}]\n[File: source: {file_path}]"
    if msg_type in ("file", "media"):
        return f"[{msg_type}: {filename}]\n[File: source: {file_path}]"
    return f"[{msg_type}]\n[File: source: {file_path}]"


def _send_local_file(receive_id, file_path, receive_id_type="open_id"):
    if not os.path.isfile(file_path):
        send_message(receive_id, f"⚠️ 文件不存在: {file_path}", receive_id_type=receive_id_type)
        return False
    ext = os.path.splitext(file_path)[1].lower()
    if ext in _IMAGE_EXTS:
        image_key = _upload_image_sync(file_path)
        if image_key:
            send_message(receive_id, json.dumps({"image_key": image_key}, ensure_ascii=False), msg_type="image", receive_id_type=receive_id_type)
            return True
    else:
        file_key = _upload_file_sync(file_path)
        if file_key:
            msg_type = "media" if ext in _AUDIO_EXTS or ext in _VIDEO_EXTS else "file"
            send_message(receive_id, json.dumps({"file_key": file_key}, ensure_ascii=False), msg_type=msg_type, receive_id_type=receive_id_type)
            return True
    send_message(receive_id, f"⚠️ 文件发送失败: {os.path.basename(file_path)}", receive_id_type=receive_id_type)
    return False


def _send_generated_files(receive_id, raw_text, receive_id_type="open_id"):
    for file_path in _extract_files(raw_text):
        _send_local_file(receive_id, file_path, receive_id_type)


def _build_user_message(message):
    msg_type = message.message_type
    message_id = message.message_id
    content_json = _parse_json(message.content)
    parts, image_paths = [], []
    if msg_type == "text":
        text = str(content_json.get("text", "") or "").strip()
        if text:
            parts.append(text)
    elif msg_type == "post":
        text, image_keys = _extract_post_content(content_json)
        if text:
            parts.append(text)
        for image_key in image_keys:
            file_path, filename = _download_and_save_media("image", {"image_key": image_key}, message_id)
            if file_path and filename:
                parts.append(_describe_media("image", file_path, filename))
                image_paths.append(file_path)
            else:
                parts.append("[image: download failed]")
    elif msg_type in ("image", "audio", "file", "media"):
        file_path, filename = _download_and_save_media(msg_type, content_json, message_id)
        if file_path and filename:
            parts.append(_describe_media(msg_type, file_path, filename))
            if msg_type == "image":
                image_paths.append(file_path)
        else:
            parts.append(f"[{msg_type}: download failed]")
    elif msg_type in ("share_chat", "share_user", "interactive", "share_calendar_event", "system", "merge_forward"):
        parts.append(_extract_share_card_content(content_json, msg_type))
    else:
        parts.append(_MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]"))
    return "\n".join([p for p in parts if p]).strip(), image_paths


def _fmt_tool_call(tc):
    name = tc.get('tool_name', '?')
    args = {k: v for k, v in (tc.get('args') or {}).items() if not k.startswith('_')}
    return f"- `{name}`({json.dumps(args, ensure_ascii=False)[:200]})"


def _build_step_detail(resp, tool_calls):
    """从 LLM response + tool_calls 组装单步展开详情（纯函数）。"""
    parts = []
    thinking = (getattr(resp, 'thinking', '') or '').strip() if resp else ''
    if thinking:
        parts.append(f"### 💭 Thinking\n{thinking}")
    if tool_calls:
        parts.append("### 🛠 Tool Calls\n" + "\n".join(_fmt_tool_call(tc) for tc in tool_calls))
    content = _display_text((getattr(resp, 'content', '') or '')).strip() if resp else ''
    if content and content != '...':
        parts.append(f"### 📝 Output\n{content}")
    return "\n\n".join(parts)


class _TaskCard:
    """飞书任务卡片：单卡片持续 patch；每步一个独立折叠面板（header 显示 summary，展开看详情）。"""
    _DETAIL_LIMIT = 8000

    def __init__(self, receive_id, rid_type):
        self.rid, self.rtype = receive_id, rid_type
        self.steps = []          # [(summary, detail), ...]
        self.status = "🤔 思考中..."
        self.final = None
        self.msg_id = None
        self.start_fallback_sent = False
        self.final_fallback_sent = False

    def _step_panel(self, idx, summary, detail):
        detail = detail or "_(无输出)_"
        if len(detail) > self._DETAIL_LIMIT:
            detail = detail[:self._DETAIL_LIMIT] + f"\n\n…(已截断,共 {len(detail)} 字符)"
        return {
            "tag": "collapsible_panel", "expanded": False,
            "header": {"title": {"tag": "plain_text", "content": f"Turn {idx} · {summary}"}},
            "elements": [{"tag": "markdown", "content": detail}],
        }

    def _build(self):
        els = [{"tag": "markdown", "content": f"**{self.status}**"}]
        for i, (s, d) in enumerate(self.steps, 1):
            els.append(self._step_panel(i, s, d))
        if self.final:
            els += [{"tag": "hr"}, {"tag": "markdown", "content": self.final}]
        return _card_raw(els)

    def _push(self):
        card = self._build()
        if self.msg_id:
            ok = _patch_card(self.msg_id, card)
        else:
            self.msg_id = _send_raw(self.rid, card, "interactive", self.rtype)
            ok = bool(self.msg_id)
        return ok

    def _fallback_text(self, text, *, final=False):
        attr = "final_fallback_sent" if final else "start_fallback_sent"
        if getattr(self, attr):
            return
        setattr(self, attr, True)
        send_message(self.rid, text, receive_id_type=self.rtype)

    # ── 公开接口 ──

    def start(self):
        if not self._push():
            self._fallback_text("🤔 思考中...")

    def step(self, summary, detail=""):
        self.steps.append((summary, detail))
        self.status = f"⏳ 工作中 · Turn {len(self.steps)}"
        self._push()

    def done(self, text):
        self.status = "✅ 已完成"
        self.final = text or "_(无文本输出)_"
        if not self._push():
            self._fallback_text(_display_text(text), final=True)

    def waiting_reply(self, question, candidates):
        """ask_user 场景：显示问题 + 候选项，状态设为等待回复"""
        self.status = "⏳ 等待回复"
        lines = [question]
        if candidates:
            lines.append("")
            for i, c in enumerate(candidates, 1):
                lines.append(f"**{i}.** {c}")
            lines.append("\n_请直接回复选项编号或内容_")
        self.final = "\n".join(lines)
        self._push()

    def fail(self, msg):
        self.status = f"❌ {msg}"
        if not self._push():
            self._fallback_text(f"❌ {msg}", final=True)


def _make_task_hook(card, task_id, on_final):
    """飞书任务 hook：每轮 patch 卡片状态；结束触发 on_final(raw) 处理附件。"""
    def hook(ctx):
        try:
            parent = getattr(ctx.get("self"), "parent", None)
            if parent is not None and getattr(parent, "_fs_active_task_id", None) != task_id:
                return
            # DEBUG: 临时打印回调字段，排查模型显示问题
            _dbg_keys = ['summary', 'model_trace', 'exit_reason']
            _dbg = {k: ctx.get(k) for k in _dbg_keys}
            if ctx.get('exit_reason'):
                resp = ctx.get('response')
                raw = resp.content if hasattr(resp, 'content') else str(resp)
                # 检测 ask_user INTERRUPT：显示问题+候选项
                er = ctx['exit_reason']
                interrupt_data = er.get('data') if isinstance(er, dict) else None
                if isinstance(interrupt_data, dict) and interrupt_data.get('status') == 'INTERRUPT':
                    inner = interrupt_data.get('data', {})
                    question = inner.get('question', '')
                    candidates = inner.get('candidates', [])
                    if question:
                        card.waiting_reply(question, candidates)
                        done_event.set()
                        return
                mt = ctx.get('model_trace') or {}
                actual = mt.get('actual', '')
                # [方案A 2026-05-14] 若整个任务一个 step 都没记录过（如 gpt-5.3 单 turn 完成
                # 或省略 <summary> 标签的模型），在 done 之前补一条"过程快照"step，
                # 避免飞书卡片从"工作中"直接跳到"已完成"造成黑盒感。
                try:
                    if not card.steps:
                        tool_calls = ctx.get('tool_calls') or []
                        snap_detail = _build_step_detail(resp, tool_calls)
                        if tool_calls:
                            _tn = tool_calls[0].get('name') if isinstance(tool_calls[0], dict) else getattr(tool_calls[0], 'name', '?')
                            snap_summary = f"⚙️ 单轮完成 · 调用 {_tn}（共{len(tool_calls)}次工具）"
                        else:
                            snap_summary = "⚙️ 单轮完成 · 直接回答"
                        if actual:
                            snap_summary += f" | 🤖{actual}"
                        card.step(snap_summary, snap_detail)
                except Exception as _e:
                    print(f"[fs hook] snap step error: {_e}")
                display = _display_text(raw)
                if display.startswith("（无文本输出）"):
                    # fallback: show thinking + tool_calls when content is empty after cleaning
                    parts = []
                    thinking = getattr(resp, 'thinking', '') or ''
                    if thinking:
                        parts.append(f"**[Thinking]**\n{thinking.strip()}")
                    tool_calls = ctx.get('tool_calls') or []
                    for tc in tool_calls:
                        name = tc.get('tool_name') or tc.get('name', '?')
                        args = tc.get('args') or tc.get('arguments') or {}
                        if name == 'ask_user':
                            q = args.get('question', '')
                            candidates = args.get('candidates') or []
                            if candidates:
                                q += '\n' + '\n'.join(f'- {c}' for c in candidates)
                            parts.append(q)
                        else:
                            args_s = json.dumps(args, ensure_ascii=False)
                            if len(args_s) > 200:
                                args_s = args_s[:200] + '...'
                            parts.append(f"`{name}`: {args_s}")
                    display = "\n\n".join(p for p in parts if p) or display
                if actual:
                    chain = mt.get('chain', [])
                    fb = f" ⚠️fb{len(chain)-1}" if chain and len(chain) > 1 else ''
                    display += f"\n\n---\n🤖 {actual}{fb}"
                card.done(display)
                on_final(raw)
            elif ctx.get('summary'):
                detail = _build_step_detail(ctx.get('response'), ctx.get('tool_calls') or [])
                mt = ctx.get('model_trace') or {}
                summary_text = ctx['summary']
                actual = mt.get('actual', '')
                if actual:
                    chain = mt.get('chain', [])
                    fb = f" ⚠️fb{len(chain)-1}" if chain and len(chain) > 1 else ''
                    summary_text += f" | 🤖{actual}{fb}"
                card.step(summary_text, detail)
        except Exception as e:
            print(f"[fs hook] error: {e}")
    return hook


class FeishuApp(AgentChatMixin):
    label, source, split_limit = "Feishu", "feishu", 4000

    async def send_text(self, chat_id, content, *, receive_id=None, receive_id_type="open_id", **_):
        rid = receive_id or chat_id
        for part in split_text(content, self.split_limit):
            await asyncio.to_thread(send_message, rid, part, "text", False, receive_id_type)

    async def send_done(self, chat_id, raw_text, *, receive_id=None, receive_id_type="open_id", **_):
        rid = receive_id or chat_id
        text = _display_text(raw_text)
        await asyncio.to_thread(send_message, rid, text, "text", False, receive_id_type)
        await asyncio.to_thread(_send_generated_files, rid, raw_text, receive_id_type)

    async def run_agent(self, chat_id, text, *, receive_id=None, receive_id_type="open_id", images=None, **_):
        if self.user_tasks:
            await self.send_text(chat_id, "当前会话已有任务在运行，请等待完成或发送 /stop 后再试。", receive_id=receive_id, receive_id_type=receive_id_type)
            return
        state = {"running": True}
        self.user_tasks[chat_id] = state
        rid = receive_id or chat_id
        task_id = f"{chat_id}_{uuid.uuid4().hex}"
        hook_key = f"fs_{task_id}"
        card = _TaskCard(rid, receive_id_type)
        result = {"raw": None, "sent": False}
        finish_lock = threading.Lock()

        def _finish(raw):
            with finish_lock:
                if result["sent"]:
                    return
                result["raw"] = raw
                result["sent"] = True
            card.done(_display_text(raw))
            _send_generated_files(rid, raw, receive_id_type=receive_id_type)

        try:
            await asyncio.to_thread(card.start)
            if not hasattr(self.agent, '_turn_end_hooks'):
                self.agent._turn_end_hooks = {}
            self.agent._turn_end_hooks[hook_key] = _make_task_hook(card, task_id, _finish)
            self.agent._fs_active_task_id = task_id
            dq = self.agent.put_task(f"{FILE_HINT}\n\n{text}", source=self.source, images=images or None)
            start = time.time()
            while state["running"] and not result["sent"]:
                try:
                    item = await asyncio.to_thread(dq.get, True, 1)
                except Q.Empty:
                    item = None
                if item and "done" in item:
                    await asyncio.to_thread(_finish, item.get("done", ""))
                    break
                if time.time() - start > AGENT_TIMEOUT_SEC:
                    self.agent.abort()
                    await asyncio.to_thread(card.fail, "任务超时")
                    break
            if not state["running"] and not result["sent"]:
                self.agent.abort()
                await asyncio.to_thread(card.fail, "已停止")
        except Exception as e:
            traceback.print_exc()
            await asyncio.to_thread(card.fail, f"错误: {e}")
        finally:
            if getattr(self.agent, "_fs_active_task_id", None) == task_id:
                try:
                    delattr(self.agent, "_fs_active_task_id")
                except AttributeError:
                    pass
            if hasattr(self.agent, '_turn_end_hooks'):
                self.agent._turn_end_hooks.pop(hook_key, None)
            self.user_tasks.pop(chat_id, None)


def get_app():
    global app
    if app is None:
        try:
            app = FeishuApp(get_agent(), user_tasks)
        except Exception as e:
            print(f"[ERROR] GA引擎初始化失败: {e}", flush=True)
            traceback.print_exc()
            raise
    return app


def _run_async(coro):
    try:
        asyncio.run(coro)
    except Exception:
        traceback.print_exc()


def handle_message(data):
    event, message, sender = data.event, data.event.message, data.event.sender
    message_id = getattr(message, "message_id", "") or ""
    if not _claim_message_once(message_id):
        print(f"忽略重复飞书消息: {message_id}")
        return
    open_id = sender.sender_id.open_id
    chat_id = message.chat_id
    training_source = is_training_source(open_id, chat_id)
    if training_source:
        print(f"[TRAINING_SOURCE] route open_id={open_id} chat_id={chat_id}")
    if chat_id and chat_id in BLOCKED_CHATS:
        print(f"[BLOCKED_CHAT] skip chat_id={chat_id}")
        return
    if not PUBLIC_ACCESS and open_id not in ALLOWED_USERS:
        print(f"未授权用户: {open_id}")
        return
    user_input, image_paths = _build_user_message(message)
    if not user_input:
        if chat_id:
            send_message(chat_id, f"⚠️ 暂不支持处理此类飞书消息：{message.message_type}", receive_id_type="chat_id")
        else:
            send_message(open_id, f"⚠️ 暂不支持处理此类飞书消息：{message.message_type}")
        return
    msg_id = getattr(message, 'message_id', '?')
    # --- msg_id 去重：防止飞书重复投递/多实例重复处理 (2026-05-11) ---
    if _is_duplicate_msg(msg_id):
        print(f"[DEDUP] 跳过重复消息 (msg_id={msg_id}): {user_input[:100]}")
        return
    create_time = getattr(message, 'create_time', '?')
    # 防重放：丢弃超过120秒的旧消息
    import time as _time
    msg_age = None
    try:
        msg_age = _time.time() - int(create_time) / 1000
        if msg_age > 120:
            print(f"[STALE] 丢弃过期消息 (age={msg_age:.0f}s, msg_id={msg_id}): {user_input[:100]}")
            return
    except (ValueError, TypeError):
        pass
    age_str = f"{msg_age:.0f}s" if msg_age is not None else "?"
    print(f"收到消息 [{open_id}] (msg_id={msg_id}, create_time={create_time}, age={age_str}, {message.message_type}, {len(image_paths)} images): {user_input[:200]}")
    if message.message_type == "text" and user_input.startswith("/"):
        return handle_command(open_id, user_input, chat_id)

    # Training router: intercept training messages before GA
    if training_source and _training_router is not None:
        try:
            handled = _training_router(
                user_input, image_paths,
                send_reply=lambda msg: _send_message(open_id, msg, chat_id),
                send_file=None,
            )
            if handled:
                print(f"[TRAINING] message handled by training router: {user_input[:80]}")
                return
        except Exception as e:
            print(f"[TRAINING] router error, falling through to GA: {e}")

    def run_agent():
        user_tasks[open_id] = {"running": True}
        receive_id = chat_id or open_id
        rid_type = "chat_id" if chat_id else "open_id"
        done_event = threading.Event()
        hook_key = f"fs_{open_id}"
        card = _TaskCard(receive_id, rid_type)
        card.start()
        on_final = lambda raw: _send_generated_files(receive_id, raw, receive_id_type=rid_type)
        if not hasattr(agent, '_turn_end_hooks'): agent._turn_end_hooks = {}
        agent._turn_end_hooks[hook_key] = _make_task_hook(card, done_event, on_final)
        try:
            # Keep the display_queue as the authoritative completion channel.
            # The turn_end_hook updates the rich card per turn, but startup/cold-run
            # races or hook errors must not leave Feishu waiting forever.
            dq = agent.put_task(user_input, source="feishu", images=image_paths)
            start = time.time()
            while True:
                # Drain any agent output first; this is independent of desktop/Streamlit UI.
                try:
                    while True:
                        item = dq.get_nowait()
                        if 'done' in item:
                            raw = item.get('done', '')
                            if not done_event.is_set():
                                card.done(_display_text(raw))
                                on_final(raw)
                                done_event.set()
                            break
                except Q.Empty:
                    pass
                if done_event.is_set():
                    break
                if not user_tasks.get(open_id, {}).get("running", True):
                    agent.abort()
                    card.fail("已停止")
                    break
                if time.time() - start > AGENT_TIMEOUT_SEC:
                # 心跳续命：检查 agent 最近活动时间，仍活跃则不杀
                    last_active = getattr(agent, '_last_activity', start)
                    idle = time.time() - last_active
                    if idle > 300:  # 5分钟无任何活动才判定真超时
                        print(f"[FSAPP_TIMEOUT] open_id={open_id} elapsed={time.time()-start:.0f}s idle={idle:.0f}s → abort", flush=True)
                        agent.abort()
                        card.fail(f"任务超时（空闲{idle:.0f}s无活动）")
                        break
                    else:
                        # 仍有活动，续命，每60秒更新一次卡片状态（只改header，不加turn）
                        elapsed = int(time.time() - start)
                        if elapsed % 60 < 4:  # 约每60秒更新一次（循环3秒一次）
                            card.status = f"⏳ 运行中 {elapsed}s（活跃）"
                            card._push()
        except Exception as e:
            traceback.print_exc()
            card.fail(f"错误: {e}")
        finally:
            agent._turn_end_hooks.pop(hook_key, None)
            user_tasks.pop(open_id, None)

    threading.Thread(target=run_agent, daemon=True).start()


def handle_command(open_id, cmd, chat_id=None):
    def _send_cmd_response(content):
        if chat_id:
            send_message(chat_id, content, receive_id_type="chat_id")
        else:
            send_message(open_id, content)
    parts = (cmd or "").split()
    op = (parts[0] if parts else "").lower()
    if op == "/stop":
        if open_id in user_tasks:
            user_tasks[open_id]["running"] = False
        agent.abort()
        _send_cmd_response("正在停止...")
    elif op == "/new":
        _send_cmd_response(reset_conversation(agent))
    elif op == "/help":
        _send_cmd_response("命令列表:\n/stop - 停止当前任务\n/status - 查看状态\n/llm - 查看当前模型列表\n/llm [n] - 切换到第 n 个模型\n/restore - 恢复上次对话历史\n/continue - 列出可恢复会话\n/continue [n] - 恢复第 n 个会话\n/new - 开启新对话并清空当前上下文\n/help - 显示帮助")
    elif op == "/status":
        llm = agent.get_llm_name() if agent.llmclient else "未配置"
        _send_cmd_response(f"状态: {'🔴 运行中' if agent.is_running else '🟢 空闲'}\nLLM: [{agent.llm_no}] {llm}")
    elif op == "/llm":
        if not agent.llmclient:
            return _send_cmd_response("❌ 当前没有可用的 LLM 配置")
        if len(parts) > 1:
            if parts[1].lower() == 'auto':
                agent.llm_locked = False; agent.baseline_llm_no = 0
                return _send_cmd_response("🔓 已解锁，PreRouter恢复自动路由")
            try:
                agent.next_llm(int(parts[1])); agent.baseline_llm_no = agent.llm_no; agent.llm_locked = True
                return _send_cmd_response(f"✅ 已切换到 [{agent.llm_no}] {agent.get_llm_name()}（已锁定，PreRouter不再自动切换）")
            except Exception:
                return _send_cmd_response(f"用法: /llm <0-{len(agent.list_llms()) - 1}> 或 /llm auto")
        lines = [f"{'→' if cur else '  '} [{i}] {name}" for i, name, cur in agent.list_llms()]
        _send_cmd_response("LLMs:\n" + "\n".join(lines))
    elif op == "/restore":
        try:
            restored_info, err = format_restore()
            if err:
                return _send_cmd_response(err.replace("❌ ", ""))
            restored, fname, count = restored_info
            agent.history.extend(restored)
            agent.abort()
            _send_cmd_response(f"已恢复 {count} 轮对话\n来源: {fname}\n(仅恢复上下文，请输入新问题继续)")
        except Exception as e:
            _send_cmd_response(f"恢复失败: {e}")
    elif op == "/continue" or cmd.startswith("/continue"):
        _send_cmd_response(handle_continue_frontend(agent, cmd))
    else:
        agent.put_task(cmd, source="feishu")


def main():
    global client
    # --- 单实例文件锁：防止多个 fsapp.py 同时运行 (2026-05-11) ---
    lock_path = os.path.join(PROJECT_ROOT, 'temp', 'fsapp_instance.lock')
    try:
        import msvcrt
        _instance_lock_fh = open(lock_path, 'w')
        msvcrt.locking(_instance_lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
    except (OSError, IOError):
        print(f"[FATAL] 另一个 fsapp.py 实例已在运行（锁文件: {lock_path}），本进程退出。")
        sys.exit(1)

    if not APP_ID or not APP_SECRET:
        print(f"错误: 请在 mykey 配置中填写 fs_app_id 和 fs_app_secret\n配置文件: {CONFIG_PATH}", flush=True)
        sys.exit(1)
    handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(handle_message).build()
    retry_delay = 5
    while True:
        try:
            client = create_client()
            cli = lark.ws.Client(APP_ID, APP_SECRET, event_handler=handler, log_level=lark.LogLevel.INFO)
            print("=" * 50 + "\n飞书 Agent 已启动（长连接模式）\n" + f"App ID: {APP_ID}\n配置: {CONFIG_PATH}\n等待消息...\n" + "=" * 50, flush=True)
            cli.start()
            retry_delay = 5
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"[WARN] 飞书长连接断开或启动失败: {e}", flush=True)
            traceback.print_exc()
        print(f"[INFO] {retry_delay}s 后重连飞书长连接...", flush=True)
        time.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 120)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A3Agent Feishu frontend")
    parser.add_argument("--check", action="store_true", help="只检查飞书配置，不启动长连接")
    parser.add_argument("--check-agent", action="store_true", help="检查配置并初始化 Agent/LLM")
    args = parser.parse_args()
    if args.check or args.check_agent:
        print(json.dumps(check_config(init_agent=args.check_agent), ensure_ascii=False, indent=2), flush=True)
    else:
        main()

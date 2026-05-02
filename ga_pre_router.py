"""
ga_pre_router.py - T0 Pre-routing via DeepSeek lightweight classification
Called once per user message in agentmain.run() before the agent loop starts.
Classifies user query → task category → optimal model selection.
Silent fallback on any error (keeps current model).
"""

import re, time

_last_category = None   # set by pre_route(), read by dispatch_gate for coordination

def get_last_category():
    """Return the category from the most recent pre_route() call (or None)."""
    return _last_category

# Category → model search keywords (substring match on backend.model.lower())
# First keyword tried first; first matching llmclient wins
CATEGORY_MODEL_MAP = {
    'architecture': ['opus-4-7', 'opus-4-6'],           # SOP: 2→1
    'planning':     ['opus-4-6', 'gpt-5.5'],             # SOP: 1→8
    'writing':      ['opus-4-6', 'mimo-v2.5-pro'],       # SOP: 1→3
    'hard_issue':   ['opus-4-6', 'codex'],               # SOP: 1→7
    'coding':       ['codex', 'gpt-5.5', 'kimi'],        # SOP: 7→5
    'agent_flow':   ['codex', 'gpt-5.5'],                # SOP: 7(5.5)→1
    'research':     ['gpt-5.5', 'mimo-v2.5-pro'],        # SOP: 6→3
    'docs':         ['gpt-5.5', 'kimi', 'mimo-v2.5-pro'],# SOP: 6→3/5
    'cn_knowledge': ['deepseek', 'mimo-v2.5-pro'],       # SOP: 10→3
    'vision':       ['mimo'],                             # SOP: 4 fixed
    'chat':         [],                                   # keep current
}

# Categories where pre_router selects a "muscle" model — dispatch_gate should skip
MUSCLE_CATEGORIES = frozenset({'coding', 'agent_flow', 'research', 'docs'})

_PROMPT_PREFIX = """Classify this user request into exactly ONE category. Reply ONLY the category name, nothing else.

Categories:
- architecture: system design, tech selection, architecture planning
- planning: project planning, strategy, decision-making
- writing: articles, copywriting, creative writing, scripts
- coding: write/fix/refactor code, debugging, testing
- agent_flow: multi-step automation, end-to-end engineering workflow
- hard_issue: complex cross-domain problem requiring deep reasoning
- research: investigation, analysis, competitive research
- docs: document processing, translation, data analysis
- cn_knowledge: Chinese-specific knowledge (civil service exam, TCM, policy, national studies)
- vision: image/visual tasks
- chat: casual conversation, simple Q&A, greetings

User request:
"""


def _get_ds_config():
    """Find DeepSeek config from mykey."""
    import mykey
    for k in dir(mykey):
        v = getattr(mykey, k)
        if isinstance(v, dict) and 'deepseek' in str(v.get('model', '')).lower():
            return v
    return None


def classify(raw_query, images=None, source='user', timeout=8):
    """Classify query → (category, note). Returns (None, reason) to skip routing."""
    # Skip conditions
    if source == 'reflect':
        return None, 'skip:reflect'
    if images:
        return 'vision', 'has_images'
    q = (raw_query or '').strip()
    if len(q) < 8:
        return None, 'skip:short'
    if q.startswith('/'):
        return None, 'skip:slash'

    cfg = _get_ds_config()
    if not cfg:
        return None, 'skip:no_ds_cfg'

    import requests
    base = cfg.get('base_url', cfg.get('base', 'https://api.deepseek.com/v1')).rstrip('/')
    url = base + '/chat/completions'
    q_trunc = q[:1500]

    # Use flash model for classification (non-reasoning, cheaper, faster)
    cls_model = cfg.get('model', 'deepseek-v4-pro').replace('-pro', '-flash')

    resp = requests.post(url,
        headers={'Authorization': f'Bearer {cfg["apikey"]}', 'Content-Type': 'application/json'},
        json={
            'model': cls_model,
            'messages': [{'role': 'user', 'content': _PROMPT_PREFIX + q_trunc}],
            'max_tokens': 50,
            'temperature': 0,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    msg = resp.json()['choices'][0]['message']
    # Prefer content; fall back to reasoning_content for reasoning models
    raw = (msg.get('content') or msg.get('reasoning_content') or '').strip().lower()
    cat = re.sub(r'[^a-z_]', '', raw)
    if cat in CATEGORY_MODEL_MAP:
        return cat, 'ok'
    # Fuzzy match (skip if too short to be meaningful)
    if len(cat) >= 3:
        for c in CATEGORY_MODEL_MAP:
            if c in cat or cat in c:
                return c, 'fuzzy'
    return None, f'unknown:{raw[:60]}'


def resolve_llm_no(agent, category):
    """Find best matching llm_no index for the given category."""
    if not category or category not in CATEGORY_MODEL_MAP:
        return None
    keywords = CATEGORY_MODEL_MAP[category]
    if not keywords:
        return None  # 'chat' keeps current
    agent.load_llm_sessions()
    for kw in keywords:
        for i, client in enumerate(agent.llmclients):
            try:
                name = agent.get_llm_name(client, model=True)
                if kw in name:
                    return i
            except Exception:
                continue
    return None


def pre_route(agent, raw_query, images=None, source='user'):
    """Main entry point. Classify query + switch model if needed.
    Returns (category, llm_no, note). Silent on all errors."""
    global _last_category
    _last_category = None
    t0 = time.time()
    try:
        category, note = classify(raw_query, images, source)
    except Exception as e:
        print(f'[PreRouter] classify error: {e}')
        return None, None, f'error:{e}'

    if category is None:
        print(f'[PreRouter] {note} [{time.time()-t0:.1f}s]')
        return None, None, note
    _last_category = category

    target = resolve_llm_no(agent, category)
    elapsed = time.time() - t0

    if target is not None and target != agent.llm_no:
        old_no, old_name = agent.llm_no, agent.get_llm_name(model=True)
        agent.next_llm(target)
        new_name = agent.get_llm_name(model=True)
        print(f'[PreRouter] {category} → #{target}({new_name}) from #{old_no}({old_name}) [{elapsed:.1f}s]')
        return category, target, f'switched:{old_no}→{target}'
    else:
        cur = agent.get_llm_name(model=True)
        print(f'[PreRouter] {category} → keep #{agent.llm_no}({cur}) [{elapsed:.1f}s]')
        return category, agent.llm_no, 'keep'
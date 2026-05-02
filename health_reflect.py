"""
health_reflect.py — Reflect script for GA health check system.
Used with: agentmain --reflect health_reflect.py

check() is called each loop iteration (~60s). Returns a task string
when health check fails, None otherwise.
"""
import os, sys, time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_MEMORY_DIR = os.path.join(_SCRIPT_DIR, 'memory')
_CHECK_INTERVAL = 3600  # Run health check at most once per hour
_last_check = 0

def check():
    """Called by agentmain reflect loop. Return task string or None."""
    global _last_check
    now = time.time()
    if now - _last_check < _CHECK_INTERVAL:
        return None
    _last_check = now

    # Import and run health check
    sys.path.insert(0, _MEMORY_DIR) if _MEMORY_DIR not in sys.path else None
    try:
        from script_health_check import health_check
        report = health_check(_MEMORY_DIR)
    except Exception as e:
        return f"[HealthCheck] Import/run error: {e}. Please investigate memory/script_health_check.py"

    if report.get('healthy', True):
        return None  # All good, no task needed

    # Build task description with failed items
    failed_items = [r for r in report.get('results', []) if r['status'] in ('FAIL', 'ERROR', 'TIMEOUT')]
    details = '; '.join(f"{r['module']}: {r['message'][:60]}" for r in failed_items[:5])
    return f"[HealthCheck ALERT] {report['failed']} script(s) failed self_test: {details}. Please diagnose and fix."

def on_done(result):
    """Called after agent processes the task."""
    print(f"[HealthReflect] Agent response: {str(result)[:200]}")
# ga_audit _run_checks crash on string detection fields

**Date**: 2026-04-29
**Component**: ga_audit.py → _run_checks / _extract_tool_args_text / _on_turn_end
**Symptom**: Dashboard (8765) stopped updating; audit_log.json frozen at 2026-04-28T23:10

## Root Cause

`constraints_registry.json` contains 24 items with `"detection": "engine"` (a string, not a dict).
These are constraint-engine-only rules. `_run_checks` assumed `detection` is always a dict and
called `det.get("type", "")` → `AttributeError: 'str' object has no attribute 'get'`.

Secondary: `tool_calls` list can contain raw strings (not dicts), causing `.get()` crashes in
`_extract_tool_args_text` and `_on_turn_end`.

The exception was caught by the top-level handler but aborted the entire `_on_turn_end`,
so `_append_event` never ran → audit_log never updated.

## Patches Applied (3)

1. **_extract_tool_args_text** (~line 225): normalize string tool_calls to `{"name": s}` dicts
2. **_on_turn_end** (~line 1200): same normalization for `ctx["tool_calls"]`
3. **_run_checks** (~line 954): `if not isinstance(det, dict): continue` to skip engine-only items

## Verification

- Compile: py_compile OK
- Isolated test: `_on_turn_end` with mixed string+dict tool_calls → audit_log written successfully
- Live: hot-reload picked up patches at 09:01:00; audit_log updated to turn 31 (500 events);
  no new errors in audit_error.log after reload

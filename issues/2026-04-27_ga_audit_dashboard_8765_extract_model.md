# ga_audit Dashboard 8765 server + model extraction fix

## Verified symptom
- Dashboard assets existed under `temp/dashboard/`, but `ga_audit.py` did not start the 8765 static server from `install()`, so browser access to `http://127.0.0.1:8765/dashboard.html` depended on an external server.
- `_extract_model()` needed a final safety boundary so unusual GA/SDK response or agent shapes could not break audit logging.

## Fix
- Added `_ensure_dashboard_server()` in `ga_audit.py` using `ThreadingHTTPServer` + `SimpleHTTPRequestHandler(directory=_DASHBOARD_DIR)` on `127.0.0.1:8765`.
- `install(agent)` now calls `_ensure_dashboard_assets()`, `_ensure_control_server()` and `_ensure_dashboard_server()`.
- Wrapped `_extract_model()` in outer `try/except Exception: return ""`.

## Verification
- `py_compile.compile("ga_audit.py", doraise=True)` passed.
- Direct isolation test: `_extract_model({}, FakeAgent(), None)` and `_extract_model({}, None, None)` returned empty string without exception; `GET /dashboard.html` and `GET /audit_log.json` on 8765 returned 200.
- End-to-end test through `ga_audit.install(DummyAgent)` passed: hook installed, dashboard/audit/constraints snapshot URLs returned 200, fake hook event wrote `model="e2e-model"`, and constructed memory-write violation produced `C005 写记忆前必须读取 META-SOP`.
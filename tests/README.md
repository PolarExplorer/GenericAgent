# Tests

## Dashboard smoke test

Run from repository root:

```bash
python tests/dashboard_smoke_test.py --report temp/dashboard_smoke_report.json
```

This test is intentionally isolated:

- uses dynamic localhost ports, not the real dashboard/control ports `8765/8766`;
- runs `ga_audit` against a temporary dashboard directory, not the real `fsapp` runtime;
- creates synthetic audit turns and validates dashboard data/template signals;
- exercises only the isolated `/api/stop` endpoint;
- writes a JSON report, then shuts down the servers it started.

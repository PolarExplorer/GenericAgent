"""
preflight_check.py — 阶段启动预检
引用: pipeline_execution_sop.md 步骤2
用法: PreflightCheck(stage_name, checks).run() → bool

self_test: python preflight_check.py --self-test
"""
import json, pathlib, sys, logging, time, os, subprocess, importlib.util
from urllib import request, error
from typing import Optional

logger = logging.getLogger(__name__)


class PreflightCheck:
    """阶段启动前的自动拦截器。全部通过才放行。"""

    def __init__(self, stage_name: str, checks: list[dict], state_path: Optional[str] = None):
        """
        checks: [{"name": str, "type": "file_exists"|"gate_pass"|"schema"|"callable"|..., ...}]
          file_exists:       {"path": str}
          dir_writable:      {"path": str}
          gate_pass:         {"state_path": str, "stage": str}  — 检查上阶段Gate
          schema:            {"path": str, "required_keys": list[str]}
          callable:          {"fn": callable, "desc": str}
          command_available: {"command": str}
          python_import:     {"module": str}
          file_fresh:        {"path": str, "max_age_seconds": int}
          env_or_key_ref:    {"name": str, "env": str?}  — 只检查引用存在，不读取密钥
          http_probe:        {"url": str, "method": "GET"|"HEAD", "timeout": int}
        """
        self.stage_name = stage_name
        self.checks = checks
        self.state_path = state_path
        self._results = []

    def run(self) -> bool:
        logger.info(f"Preflight for stage '{self.stage_name}': {len(self.checks)} checks")
        all_pass = True
        for chk in self.checks:
            name = chk.get("name", chk.get("type", "unknown"))
            ok, msg = self._run_one(chk)
            status = "PASS" if ok else "FAIL"
            self._results.append({"name": name, "status": status, "msg": msg})
            logger.info(f"  [{status}] {name}: {msg}")
            if not ok:
                all_pass = False

        verdict = "ALL PASS" if all_pass else "BLOCKED"
        logger.info(f"Preflight '{self.stage_name}': {verdict}")
        return all_pass

    def _run_one(self, chk: dict) -> tuple[bool, str]:
        t = chk["type"]
        try:
            if t == "file_exists":
                p = pathlib.Path(chk["path"])
                if p.exists():
                    return True, f"{p} exists ({p.stat().st_size}B)"
                return False, f"{p} NOT FOUND"

            elif t == "gate_pass":
                sp = pathlib.Path(chk.get("state_path", self.state_path or "stage_state.json"))
                if not sp.exists():
                    return False, f"state file {sp} not found"
                data = json.loads(sp.read_text(encoding="utf-8"))
                stage = chk["stage"]
                status = data.get("stages", {}).get(stage, {}).get("gate_status", "UNKNOWN")
                if status == "PASS":
                    return True, f"stage '{stage}' gate=PASS"
                return False, f"stage '{stage}' gate={status}"

            elif t == "schema":
                p = pathlib.Path(chk["path"])
                if not p.exists():
                    return False, f"{p} not found"
                if p.suffix == ".jsonl":
                    line = p.open(encoding="utf-8").readline()
                    sample = json.loads(line)
                elif p.suffix == ".json":
                    sample = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(sample, list):
                        sample = sample[0] if sample else {}
                else:
                    return False, f"unsupported format: {p.suffix}"
                missing = [k for k in chk["required_keys"] if k not in sample]
                if missing:
                    return False, f"missing keys: {missing}"
                return True, f"schema OK, has {list(sample.keys())[:5]}"

            elif t == "callable":
                result = chk["fn"]()
                if result:
                    return True, chk.get("desc", "callable returned truthy")
                return False, chk.get("desc", "callable returned falsy")

            elif t == "dir_writable":
                p = pathlib.Path(chk["path"])
                p.mkdir(parents=True, exist_ok=True)
                probe = p / f".preflight_write_{int(time.time()*1000)}.tmp"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink(missing_ok=True)
                return True, f"{p} writable"

            elif t == "command_available":
                cmd = chk["command"]
                found = None
                for part in os.environ.get("PATH", "").split(os.pathsep):
                    for suffix in ("", ".exe", ".bat", ".cmd"):
                        cand = pathlib.Path(part) / (cmd + suffix)
                        if cand.exists():
                            found = cand
                            break
                    if found:
                        break
                if found:
                    return True, f"{cmd} found at {found}"
                return False, f"{cmd} not found in PATH"

            elif t == "python_import":
                module = chk["module"]
                if importlib.util.find_spec(module) is not None:
                    return True, f"module {module} importable"
                return False, f"module {module} not importable"

            elif t == "file_fresh":
                p = pathlib.Path(chk["path"])
                if not p.exists():
                    return False, f"{p} not found"
                max_age = int(chk.get("max_age_seconds", 86400))
                age = time.time() - p.stat().st_mtime
                if age <= max_age:
                    return True, f"{p} fresh age={int(age)}s <= {max_age}s"
                return False, f"{p} stale age={int(age)}s > {max_age}s"

            elif t == "env_or_key_ref":
                name = chk.get("name", "secret_ref")
                env_name = chk.get("env") or chk.get("key") or name
                if os.environ.get(env_name):
                    return True, f"{name} env ref exists ({env_name}); value not read"
                ref_path = chk.get("ref_path")
                if ref_path and pathlib.Path(ref_path).exists():
                    return True, f"{name} ref file exists; content not read"
                return False, f"{name} reference missing"

            elif t == "http_probe":
                url = chk["url"]
                method = chk.get("method", "HEAD")
                timeout = int(chk.get("timeout", 5))
                req = request.Request(url, method=method)
                try:
                    with request.urlopen(req, timeout=timeout) as resp:
                        return True, f"{method} {url} -> HTTP {resp.status}"
                except error.HTTPError as e:
                    if 200 <= e.code < 500:
                        return True, f"{method} {url} reachable HTTP {e.code}"
                    return False, f"{method} {url} HTTP {e.code}"

            else:
                return False, f"unknown check type: {t}"
        except Exception as e:
            return False, f"exception: {e}"

    def report(self) -> str:
        lines = [f"# Preflight: {self.stage_name}", ""]
        for r in self._results:
            icon = "✅" if r["status"] == "PASS" else "❌"
            lines.append(f"- {icon} **{r['name']}**: {r['msg']}")
        return "\n".join(lines)


def _self_test():
    import tempfile, os
    tmp = tempfile.mkdtemp()

    # create test files
    test_file = pathlib.Path(tmp) / "input.jsonl"
    test_file.write_text('{"id":"1","title":"test"}\n', encoding="utf-8")

    state_file = pathlib.Path(tmp) / "state.json"
    state_file.write_text(json.dumps({
        "stages": {"stage_a": {"gate_status": "PASS"}, "stage_b": {"gate_status": "FAIL"}}
    }), encoding="utf-8")

    checks = [
        {"name": "input_exists", "type": "file_exists", "path": str(test_file)},
        {"name": "missing_file", "type": "file_exists", "path": os.path.join(tmp, "nope.txt")},
        {"name": "prev_gate", "type": "gate_pass", "state_path": str(state_file), "stage": "stage_a"},
        {"name": "failed_gate", "type": "gate_pass", "state_path": str(state_file), "stage": "stage_b"},
        {"name": "schema_ok", "type": "schema", "path": str(test_file), "required_keys": ["id", "title"]},
        {"name": "schema_miss", "type": "schema", "path": str(test_file), "required_keys": ["id", "missing_key"]},
        {"name": "custom_ok", "type": "callable", "fn": lambda: True, "desc": "always true"},
        {"name": "dir_writable", "type": "dir_writable", "path": os.path.join(tmp, "out")},
        {"name": "python_import", "type": "python_import", "module": "json"},
        {"name": "file_fresh", "type": "file_fresh", "path": str(test_file), "max_age_seconds": 3600},
        {"name": "command_available", "type": "command_available", "command": pathlib.Path(sys.executable).stem},
    ]

    pf = PreflightCheck("test_stage", checks)
    result = pf.run()
    assert result is False, "Should fail (has failing checks)"
    assert pf._results[0]["status"] == "PASS"
    assert pf._results[1]["status"] == "FAIL"
    assert pf._results[2]["status"] == "PASS"
    assert pf._results[3]["status"] == "FAIL"
    assert pf._results[4]["status"] == "PASS"
    assert pf._results[5]["status"] == "FAIL"
    assert pf._results[6]["status"] == "PASS"
    assert pf._results[7]["status"] == "PASS"
    assert pf._results[8]["status"] == "PASS"
    assert pf._results[9]["status"] == "PASS"
    assert pf._results[10]["status"] == "PASS"
    md = pf.report()
    assert "✅" in md and "❌" in md
    print("preflight_check self_test PASSED")


def self_test() -> bool:
    """Health-check entrypoint: run isolated _self_test and return boolean."""
    try:
        _self_test()
        return True
    except Exception:
        return False


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        logging.basicConfig(level=logging.INFO)
        _self_test()
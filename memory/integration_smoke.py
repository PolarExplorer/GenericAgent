"""
integration_smoke.py — 外部集成冒烟检查工具
引用: pipeline_execution_sop.md 外部集成三层验收

目标:
- 对配置、目录写入、命令/import、HTTP 连通、脚本命令做统一冒烟
- 默认 dry-run；真实发送/上传等不可逆动作必须由调用方显式提供命令
- 只检查凭证引用存在，不读取密钥内容

self_test: python integration_smoke.py --self-test
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
from dataclasses import dataclass, field
from urllib import request, error


LEVELS = {"dry-run", "sandbox-real", "production-smoke"}


@dataclass
class SmokeResult:
    name: str
    mode: str
    status: str
    timestamp: str
    checks: list[dict] = field(default_factory=list)
    boundary: str = ""
    report_path: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "mode": self.mode,
            "status": self.status,
            "timestamp": self.timestamp,
            "checks": self.checks,
            "boundary": self.boundary,
            "report_path": self.report_path,
        }


class IntegrationSmoke:
    def __init__(
        self,
        name: str,
        checks: list[dict],
        output_dir: str = "integration_smoke_reports",
        mode: str = "dry-run",
        boundary: str = "",
        cwd: str | None = None,
        timeout: int = 60,
    ):
        if mode not in LEVELS:
            raise ValueError(f"invalid mode: {mode}")
        self.name = name
        self.checks = checks
        self.output_dir = pathlib.Path(output_dir)
        self.mode = mode
        self.boundary = boundary
        self.cwd = cwd
        self.timeout = timeout

    def _run_check(self, chk: dict) -> tuple[bool, str, dict]:
        t = chk["type"]

        if t == "config_exists":
            p = pathlib.Path(chk["path"])
            if not p.is_absolute():
                p = pathlib.Path(self.cwd or os.getcwd()) / p
            return p.exists(), f"{p} exists={p.exists()}", {"path": str(p)}

        if t == "dir_writable":
            p = pathlib.Path(chk["path"])
            if not p.is_absolute():
                p = pathlib.Path(self.cwd or os.getcwd()) / p
            p.mkdir(parents=True, exist_ok=True)
            probe = p / f".smoke_write_{int(time.time()*1000)}.tmp"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return True, f"{p} writable", {"path": str(p)}

        if t == "env_ref":
            env_name = chk["env"]
            ok = bool(os.environ.get(env_name))
            return ok, f"env {env_name} exists={ok}; value not read", {"env": env_name}

        if t == "http_probe":
            url = chk["url"]
            method = chk.get("method", "HEAD")
            timeout = int(chk.get("timeout", self.timeout))
            req = request.Request(url, method=method)
            try:
                with request.urlopen(req, timeout=timeout) as resp:
                    return True, f"{method} {url} -> HTTP {resp.status}", {"url": url, "status": resp.status}
            except error.HTTPError as e:
                # 4xx 也说明网络/API 入口可达，权限另做专门探测
                ok = 200 <= e.code < 500
                return ok, f"{method} {url} -> HTTP {e.code}", {"url": url, "status": e.code}
            except Exception as e:
                return False, f"{method} {url} failed: {e}", {"url": url}

        if t == "command":
            cmd = chk["cmd"]
            allow_real = bool(chk.get("allow_real", False))
            if self.mode != "dry-run" and not allow_real:
                return False, "real mode command requires allow_real=true", {"cmd": cmd}
            p = subprocess.run(
                cmd,
                cwd=self.cwd,
                shell=bool(chk.get("shell", False)),
                text=True,
                capture_output=True,
                timeout=int(chk.get("timeout", self.timeout)),
            )
            ok = p.returncode == int(chk.get("expect_rc", 0))
            return ok, f"rc={p.returncode}", {
                "cmd": cmd,
                "returncode": p.returncode,
                "stdout_tail": (p.stdout or "")[-2000:],
                "stderr_tail": (p.stderr or "")[-2000:],
            }

        if t == "python_import":
            import importlib.util
            module = chk["module"]
            ok = importlib.util.find_spec(module) is not None
            return ok, f"module {module} importable={ok}", {"module": module}

        return False, f"unknown check type: {t}", {"type": t}

    def run(self) -> SmokeResult:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        rows = []
        all_ok = True

        for chk in self.checks:
            started = time.time()
            try:
                ok, msg, extra = self._run_check(chk)
            except Exception as e:
                ok, msg, extra = False, f"EXCEPTION: {e}", {}
            if not ok:
                all_ok = False
            rows.append({
                "name": chk.get("name", chk.get("type", "unnamed")),
                "type": chk.get("type"),
                "status": "PASS" if ok else "FAIL",
                "message": msg,
                "duration_sec": round(time.time() - started, 3),
                **extra,
            })

        status = "PASS" if all_ok else "FAIL"
        safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in self.name)
        report_path = self.output_dir / f"{safe_name}_{time.strftime('%Y%m%d_%H%M%S')}.json"
        result = SmokeResult(
            name=self.name,
            mode=self.mode,
            status=status,
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            checks=rows,
            boundary=self.boundary,
            report_path=str(report_path),
        )
        report_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return result


def _self_test():
    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp())
    cfg = tmp / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    checks = [
        {"name": "config", "type": "config_exists", "path": str(cfg)},
        {"name": "out", "type": "dir_writable", "path": str(tmp / "out")},
        {"name": "import_json", "type": "python_import", "module": "json"},
        {"name": "cmd", "type": "command", "cmd": [sys.executable, "-c", "print('ok')"]},
    ]
    smoke = IntegrationSmoke("self_test", checks, output_dir=str(tmp / "reports"), boundary="self-test only")
    result = smoke.run()
    assert result.status == "PASS", result.to_dict()
    assert pathlib.Path(result.report_path).exists()
    data = json.loads(pathlib.Path(result.report_path).read_text(encoding="utf-8"))
    assert len(data["checks"]) == 4
    print("integration_smoke self_test PASSED")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
    else:
        print("Use IntegrationSmoke class or run --self-test")
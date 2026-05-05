"""
script_gate_runner.py — 脚本型 Gate 统一执行与证据包
引用: pipeline_execution_sop.md 外部集成验收/脚本型 Gate
用法:
  ScriptGateRunner(stage, commands, evidence_dir).run()
  python script_gate_runner.py --self-test

设计原则:
- 记录命令、returncode、stdout/stderr 尾部、产物 sha256、validation_level
- 默认只执行调用方传入的命令；不读取密钥内容
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional


VALIDATION_LEVELS = {
    "PASS_LOCAL",
    "PASS_DRY_RUN",
    "PASS_SANDBOX",
    "PASS_REAL_SMOKE",
    "PASS_PRODUCTION",
    "PARTIAL",
    "BLOCKED_BY_USER_CONFIG",
}


def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class ScriptGateResult:
    stage: str
    status: str
    validation_level: str
    timestamp: str
    commands: list[dict] = field(default_factory=list)
    reports: list[dict] = field(default_factory=list)
    boundary: str = ""
    evidence_path: str = ""

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "status": self.status,
            "validation_level": self.validation_level,
            "timestamp": self.timestamp,
            "commands": self.commands,
            "reports": self.reports,
            "boundary": self.boundary,
            "evidence_path": self.evidence_path,
        }


class ScriptGateRunner:
    def __init__(
        self,
        stage: str,
        commands: Iterable[str | list[str]],
        evidence_dir: str = "gate_evidence",
        expected_reports: Optional[Iterable[str]] = None,
        validation_level: str = "PASS_DRY_RUN",
        boundary: str = "",
        cwd: Optional[str] = None,
        timeout: int = 300,
        shell: bool = False,
    ):
        if validation_level not in VALIDATION_LEVELS:
            raise ValueError(f"invalid validation_level: {validation_level}")
        self.stage = stage
        self.commands = list(commands)
        self.evidence_dir = pathlib.Path(evidence_dir)
        self.expected_reports = [pathlib.Path(p) for p in (expected_reports or [])]
        self.validation_level = validation_level
        self.boundary = boundary
        self.cwd = cwd
        self.timeout = timeout
        self.shell = shell

    def run(self) -> ScriptGateResult:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        command_results = []
        all_ok = True

        for cmd in self.commands:
            started = time.time()
            try:
                p = subprocess.run(
                    cmd,
                    cwd=self.cwd,
                    shell=self.shell,
                    text=True,
                    capture_output=True,
                    timeout=self.timeout,
                )
                rc = p.returncode
                stdout_tail = (p.stdout or "")[-4000:]
                stderr_tail = (p.stderr or "")[-4000:]
            except subprocess.TimeoutExpired as e:
                rc = -999
                stdout_tail = (e.stdout or "")[-4000:] if isinstance(e.stdout, str) else ""
                stderr_tail = f"TIMEOUT after {self.timeout}s\n"
                all_ok = False
            except Exception as e:
                rc = -998
                stdout_tail = ""
                stderr_tail = f"EXCEPTION: {e}"
                all_ok = False

            if rc != 0:
                all_ok = False

            command_results.append({
                "cmd": cmd,
                "returncode": rc,
                "duration_sec": round(time.time() - started, 3),
                "stdout_tail": stdout_tail,
                "stderr_tail": stderr_tail,
            })

        report_results = []
        for p in self.expected_reports:
            rp = p if p.is_absolute() else pathlib.Path(self.cwd or os.getcwd()) / p
            info = {
                "path": str(rp),
                "exists": rp.exists(),
            }
            if rp.exists():
                info.update({
                    "size": rp.stat().st_size,
                    "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rp.stat().st_mtime)),
                    "sha256": _sha256(rp),
                })
            else:
                all_ok = False
            report_results.append(info)

        status = "PASS" if all_ok else "FAIL"
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        safe_stage = "".join(c if c.isalnum() or c in "-_." else "_" for c in self.stage)
        evidence_path = self.evidence_dir / f"{safe_stage}_{time.strftime('%Y%m%d_%H%M%S')}.json"

        result = ScriptGateResult(
            stage=self.stage,
            status=status,
            validation_level=self.validation_level if all_ok else "PARTIAL",
            timestamp=ts,
            commands=command_results,
            reports=report_results,
            boundary=self.boundary,
            evidence_path=str(evidence_path),
        )
        evidence_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return result


def _self_test():
    import tempfile

    tmp = pathlib.Path(tempfile.mkdtemp())
    report = tmp / "report.json"
    cmd = [sys.executable, "-c", f"import pathlib; pathlib.Path(r'{report}').write_text('{{\"ok\":true}}', encoding='utf-8')"]
    runner = ScriptGateRunner(
        stage="self_test",
        commands=[cmd],
        evidence_dir=str(tmp / "evidence"),
        expected_reports=[str(report)],
        validation_level="PASS_LOCAL",
        boundary="self-test only",
    )
    result = runner.run()
    assert result.status == "PASS", result.to_dict()
    assert result.validation_level == "PASS_LOCAL"
    assert pathlib.Path(result.evidence_path).exists()
    data = json.loads(pathlib.Path(result.evidence_path).read_text(encoding="utf-8"))
    assert data["reports"][0]["exists"] is True
    assert data["reports"][0]["sha256"]
    print("script_gate_runner self_test PASSED")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        _self_test()
        return 0
    ap.print_help()
    return 2


def self_test() -> bool:
    """Health-check entrypoint: run isolated _self_test and return boolean."""
    try:
        _self_test()
        return True
    except Exception:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
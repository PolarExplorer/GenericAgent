"""Script Guard — memory/*.py 写入前验证，防止语法错误的代码破坏GA启动链。

设计原则：
- 仅拦截 memory/ 目录下 .py 文件的写入
- ast.parse 语法检查 + py_compile 编译检查（双重保险）
- 零外部依赖（仅 stdlib）
- 被 ga.py 的 file_patch / do_file_write 在写入前调用

用法：
    from script_guard import validate_python_write
    ok, err = validate_python_write(path, content)
    if not ok: refuse_write(err)
"""
import ast
import os
import subprocess
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MEMORY_DIR = os.path.join(SCRIPT_DIR, 'memory')


def is_memory_python(path: str) -> bool:
    """判断路径是否指向 memory/ 下的 .py 文件"""
    try:
        path = os.path.abspath(str(path))
        return (path.endswith('.py')
                and os.path.normcase(MEMORY_DIR) == os.path.normcase(os.path.dirname(path)))
    except Exception:
        return False


def validate_python_content(content: str, filepath: str = "<unknown>") -> tuple:
    """验证 Python 内容的正确性。
    Returns: (ok: bool, error_msg: str)
    """
    # ── Step 1: AST 语法解析 ──
    try:
        ast.parse(content, filename=os.path.basename(filepath))
    except SyntaxError as e:
        return False, f"SyntaxError @ line {e.lineno}: {e.msg}"

    # ── Step 2: py_compile 编译检查（捕获 AST 漏掉的编码等问题）──
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix='.py', mode='w', encoding='utf-8',
            delete=False, dir=os.path.join(SCRIPT_DIR, 'temp')
        ) as f:
            f.write(content)
            tmp_path = f.name
        result = subprocess.run(
            [sys.executable, '-c',
             f'import py_compile; py_compile.compile(r"{tmp_path}", doraise=True)'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            err = result.stderr.strip().split('\n')[-1] if result.stderr.strip() else "compile failed"
            return False, f"CompileError: {err}"
    except subprocess.TimeoutExpired:
        return False, "编译检查超时(10s)"
    except Exception as e:
        # 编译检查本身出错时，已通过Step1的AST检查，放行但警告
        print(f"[ScriptGuard] ⚠ py_compile check skipped: {e}")
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    return True, ""


def validate_python_write(path: str, content: str) -> tuple:
    """Guard 入口点，供 ga.py 调用。
    Returns: (allowed: bool, error_msg: str)
    - allowed=True  → 可以安全写入
    - allowed=False → 拒绝写入，error_msg 说明原因
    """
    if not is_memory_python(path):
        return True, ""  # 非 memory/*.py，不拦截
    return validate_python_content(content, filepath=path)


# ── 独立运行：扫描所有 memory/*.py 健康状态 ──
if __name__ == '__main__':
    print(f"ScriptGuard Health Check — scanning {MEMORY_DIR}")
    print("=" * 60)
    errors = []
    for f in sorted(os.listdir(MEMORY_DIR)):
        if not f.endswith('.py') or f.endswith('.template.py'):
            continue
        fp = os.path.join(MEMORY_DIR, f)
        with open(fp, 'r', encoding='utf-8', errors='replace') as fh:
            content = fh.read()
        ok, err = validate_python_content(content, filepath=fp)
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {f}")
        if not ok:
            print(f"     -> {err}")
            errors.append((f, err))
    print("=" * 60)
    if errors:
        print(f"FAIL: {len(errors)} file(s) have issues!")
        sys.exit(1)
    else:
        print(f"ALL OK: {len([f for f in os.listdir(MEMORY_DIR) if f.endswith('.py')])} files passed")
        sys.exit(0)
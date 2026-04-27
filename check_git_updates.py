#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Git更新检查脚本 - 默认只检查，传入 --apply 才拉取更新"""
import subprocess
import os
import sys
from datetime import datetime


def run_git(args, project_root):
    return subprocess.run(
        ['git'] + args,
        capture_output=True, text=True, cwd=project_root, encoding='utf-8'
    )


def is_worktree_clean(project_root):
    result = run_git(['status', '--porcelain'], project_root)
    if result.returncode != 0:
        print(f"[FAIL] Status check failed: {result.stderr}")
        return False
    if result.stdout.strip():
        print("[FAIL] Working tree is not clean; aborting update.")
        print(result.stdout)
        return False
    return True


def check_git_updates(apply=False):
    project_root = os.path.dirname(os.path.abspath(__file__))

    try:
        # fetch远程；默认不修改本地代码
        result = run_git(['fetch', 'origin'], project_root)
        if result.returncode != 0:
            print(f"[FAIL] Fetch failed: {result.stderr}")
            return False

        # 检查差异
        result = run_git(['log', 'HEAD..origin/main', '--oneline'], project_root)

        if result.stdout.strip():
            commits = result.stdout.strip().split('\n')
            print(f"[UPDATE] Found {len(commits)} new commits:")
            print(result.stdout)

            if not apply:
                print("[INFO] Check-only mode; rerun with --apply to pull updates.")
                return True

            if not is_worktree_clean(project_root):
                return False

            pull_result = run_git(['pull', '--ff-only', 'origin', 'main'], project_root)
            if pull_result.returncode == 0:
                print("[OK] Code updated successfully")
                print(pull_result.stdout)
                return True
            else:
                print(f"[FAIL] Pull failed: {pull_result.stderr}")
                return False
        else:
            print("[OK] Already up to date")
            return True
    except Exception as e:
        print(f"[ERROR] {e}")
        return False


if __name__ == "__main__":
    print(f"\n{'='*50}")
    print(f"Git Update Check - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")
    check_git_updates(apply='--apply' in sys.argv[1:])

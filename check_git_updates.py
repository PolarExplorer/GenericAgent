#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Git自动更新检查脚本 - 每次运行时检查并拉取更新"""
import subprocess
import os
import sys
from datetime import datetime

def check_git_updates():
    project_root = os.path.dirname(os.path.abspath(__file__))

    try:
        # fetch远程
        result = subprocess.run(
            ['git', 'fetch', 'origin'],
            capture_output=True, text=True, cwd=project_root, encoding='utf-8'
        )
        if result.returncode != 0:
            print(f"[FAIL] Fetch failed: {result.stderr}")
            return False

        # 检查差异
        result = subprocess.run(
            ['git', 'log', 'HEAD..origin/main', '--oneline'],
            capture_output=True, text=True, cwd=project_root, encoding='utf-8'
        )

        if result.stdout.strip():
            commits = result.stdout.strip().split('\n')
            print(f"[UPDATE] Found {len(commits)} new commits:")
            print(result.stdout)

            pull_result = subprocess.run(
                ['git', 'pull', 'origin', 'main'],
                capture_output=True, text=True, cwd=project_root, encoding='utf-8'
            )
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
    check_git_updates()

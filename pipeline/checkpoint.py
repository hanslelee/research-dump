"""Commit and push the data repository mid-run, so a cancelled or timed-out run keeps its progress.

Active only when CHECKPOINT=1 (set by the GitHub workflow). Locally it does nothing.
"""
import os
import subprocess
from pathlib import Path


def save(data_dir: Path, message: str, log=print) -> bool:
    if os.environ.get("CHECKPOINT") != "1":
        return False
    git = lambda *a: subprocess.run(["git", *a], cwd=data_dir, capture_output=True, text=True)
    git("add", "-A")
    if git("diff", "--cached", "--quiet").returncode == 0:
        return False
    git("commit", "-q", "-m", message)
    for _ in range(3):
        if git("pull", "--rebase", "-q").returncode == 0 and git("push", "-q").returncode == 0:
            log(f"checkpoint: saved ({message})")
            return True
    log(f"checkpoint: push failed, will retry at the end ({message})")
    return False

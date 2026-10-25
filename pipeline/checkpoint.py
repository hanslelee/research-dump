"""Commit and push the data repository mid-run, so a cancelled or timed-out run keeps its progress.

Active only when CHECKPOINT=1 (set by the GitHub workflow). Locally it does nothing.
"""
import os
import subprocess
import time
from pathlib import Path


def save(data_dir: Path, message: str, log=print) -> bool:
    if os.environ.get("CHECKPOINT") != "1":
        return False
    git = lambda *a: subprocess.run(["git", *a], cwd=data_dir, capture_output=True, text=True)
    git("add", "-A")
    if git("diff", "--cached", "--quiet").returncode == 0:
        return False
    git("commit", "-q", "-m", message)
    for i in range(3):
        pulled = git("pull", "--rebase", "-q")
        if pulled.returncode == 0 and git("push", "-q").returncode == 0:
            log(f"checkpoint: saved ({message})")
            return True
        if pulled.returncode != 0:
            git("rebase", "--abort")  # never leave the repository half-rebased
            log(f"checkpoint: pull failed ({(pulled.stderr or pulled.stdout).strip()[:200]})")
        time.sleep(3 * (i + 1))
    log(f"checkpoint: push failed, will retry at the end ({message})")
    return False

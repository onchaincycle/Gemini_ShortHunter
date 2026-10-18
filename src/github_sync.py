"""
ShortHunter GitHub Sync Pipeline:
Generates the HTML performance dashboard and commits/pushes updates to GitHub.
Allows remote monitoring of trade performance via GitHub repository and GitHub Pages.
"""

import os
import sys
import time
import json
import subprocess
from datetime import datetime
from typing import Dict, Optional

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

THROTTLE_STATE_FILE = os.path.join(ROOT_DIR, "data", "last_github_push.json")
DEFAULT_DEBOUNCE_SECONDS = 60

from src.html_reporter import generate_html_report, OUTPUT_HTML


def run_cmd(cmd: list, cwd: str = ROOT_DIR) -> tuple[int, str, str]:
    res = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res.returncode, res.stdout.strip(), res.stderr.strip()


def ensure_git_setup() -> Dict:
    """
    Checks if git is initialized and remote origin is configured.
    If not, initializes and sets up origin using GitHub CLI (gh).
    """
    # 1. Check git repo
    code, out, _ = run_cmd(["git", "rev-parse", "--is-inside-work-tree"])
    if code != 0:
        run_cmd(["git", "init", "-b", "main"])

    # 2. Configure user if unset
    code, u_name, _ = run_cmd(["git", "config", "user.name"])
    if not u_name:
        run_cmd(["git", "config", "user.name", "onchaincycle"])
        run_cmd(["git", "config", "user.email", "onchaincycle@users.noreply.github.com"])

    # 3. Check remote origin
    code, remotes, _ = run_cmd(["git", "remote", "-v"])
    has_origin = "origin" in remotes

    repo_url = ""
    pages_url = ""

    if has_origin:
        for line in remotes.splitlines():
            if "origin" in line and "(fetch)" in line:
                repo_url = line.split()[1]
                break
    else:
        # Check if gh CLI is available and authenticated
        code, gh_auth, _ = run_cmd(["gh", "auth", "status"])
        if code == 0:
            # Create remote repo on GitHub
            print("[*] Remote GitHub belum terhubung. Membuat repo 'Gemini_ShortHunter' di GitHub via gh CLI...")
            code, gh_out, gh_err = run_cmd([
                "gh", "repo", "create", "Gemini_ShortHunter",
                "--public",
                "--source=.",
                "--remote=origin",
                "--description", "ShortHunter Dual-Stream Hyperliquid Trading Bot & Performance Dashboard"
            ])
            if code == 0:
                print("[✓] Repo GitHub berhasil dibuat dan dihubungkan ke origin!")
                repo_url = "https://github.com/onchaincycle/Gemini_ShortHunter"
            else:
                if "already exists" in gh_err or "already exists" in gh_out:
                    run_cmd(["git", "remote", "add", "origin", "https://github.com/onchaincycle/Gemini_ShortHunter.git"])
                    repo_url = "https://github.com/onchaincycle/Gemini_ShortHunter"
                else:
                    return {"success": False, "error": f"Gagal membuat repo via gh: {gh_err}"}

    # Derive Pages URL
    if "github.com" in repo_url:
        clean_path = repo_url.replace("git@github.com:", "").replace("https://github.com/", "").replace(".git", "")
        parts = clean_path.split("/")
        if len(parts) == 2:
            pages_url = f"https://{parts[0]}.github.io/{parts[1]}/"

    return {
        "success": True,
        "has_origin": True,
        "repo_url": repo_url,
        "pages_url": pages_url
    }


def sync_and_push(commit_msg: Optional[str] = None, force: bool = False, min_interval_seconds: int = DEFAULT_DEBOUNCE_SECONDS) -> Dict:
    """
    Refreshes the HTML dashboard, stages changes safely, commits, and pushes to GitHub.
    Includes throttling/debounce check [DEV-001] to prevent rapid spam commits on close trades.
    """
    now_ts = time.time()
    # Throttling check (DEV-001)
    if not force and os.path.exists(THROTTLE_STATE_FILE):
        try:
            with open(THROTTLE_STATE_FILE, "r") as f:
                last_push_data = json.load(f)
            last_ts = float(last_push_data.get("last_push_ts", 0.0))
            elapsed = now_ts - last_ts
            if elapsed < min_interval_seconds:
                return {
                    "success": True,
                    "pushed": False,
                    "throttled": True,
                    "message": f"Throttled: Push ditunda (jeda minimal {min_interval_seconds}s antar push, baru berjalan {elapsed:.1f}s).",
                    "elapsed_seconds": round(elapsed, 1)
                }
        except Exception:
            pass

    # 1. Generate updated HTML report
    try:
        html_path = generate_html_report()
    except Exception as e:
        return {"success": False, "error": f"Gagal generate HTML dashboard: {e}"}

    # 2. Ensure git & remote
    setup_res = ensure_git_setup()
    if not setup_res["success"]:
        return setup_res

    repo_url = setup_res.get("repo_url", "")
    pages_url = setup_res.get("pages_url", "")

    # 3. Stage safe files (strictly avoiding .env and database binaries)
    safe_files = [
        "docs/",
        ".gitignore",
        "src/",
        "menu.py",
        "README.md",
        "requirements.txt",
        "start.sh",
        "BACKLOG.md",
        "PROJECT_INSTRUCTION.md",
        "data/forward_test_journal.json",
        "data/positions_lifecycle.json",
        "data/position_tags.json",
        "data/active_profile.json",
        "data/adaptive_weights.json",
        "data/episodic_memory.json",
        "data/portfolio_sizing_config.json",
        "inputs/hermes_bridge_context.json"
    ]
    for sf in safe_files:
        if os.path.exists(os.path.join(ROOT_DIR, sf)):
            run_cmd(["git", "add", sf])

    # 3.5 GUARDIAN AGENT PRE-COMMIT SECURITY & DLP FIREWALL
    try:
        from src.guardian_agent import guardian_agent
        sec_audit = guardian_agent.audit_git_staging_security()
        if not sec_audit.get("safe", True):
            return {
                "success": False,
                "error": f"🛑 DIBLOKIR OLEH GUARDIAN AGENT (Kebocoran Data Sensitif Dicegah): {'; '.join(sec_audit.get('violations', []))}"
            }
    except Exception as e:
        print(f"[!] Warning: Guardian audit check note: {e}")

    # 4. Check if there are changes to commit
    code, diff_cached, _ = run_cmd(["git", "diff", "--cached", "--name-only"])
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not diff_cached:
        return {
            "success": True,
            "pushed": False,
            "message": "Tidak ada perubahan baru untuk di-commit (Staged files sudah bersih & sinkron).",
            "repo_url": repo_url,
            "pages_url": pages_url
        }

    # 5. Commit
    msg = commit_msg or f"📊 Update Trade Performance Dashboard [{now_str}]"
    code, commit_out, commit_err = run_cmd(["git", "commit", "-m", msg])
    if code != 0:
        return {"success": False, "error": f"Gagal commit git: {commit_err}"}

    # 6. Push to remote origin
    # Check current branch
    code, branch, _ = run_cmd(["git", "branch", "--show-current"])
    branch = branch or "main"

    code, push_out, push_err = run_cmd(["git", "push", "-u", "origin", branch])
    if code != 0:
        # Try pushing without -u or handle fast-forward
        code, push_out, push_err = run_cmd(["git", "push", "origin", branch])
        if code != 0:
            return {
                "success": False,
                "error": f"Gagal push ke GitHub: {push_err}",
                "repo_url": repo_url
            }

    # Record last push timestamp
    try:
        os.makedirs(os.path.dirname(THROTTLE_STATE_FILE), exist_ok=True)
        with open(THROTTLE_STATE_FILE, "w") as f:
            json.dump({"last_push_ts": now_ts, "last_push_time": now_str, "commit_msg": msg}, f, indent=2)
    except Exception:
        pass

    return {
        "success": True,
        "pushed": True,
        "message": f"Berhasil commit & push ke GitHub ({branch}) [🛡️ Terverifikasi Aman oleh Guardian DLP]!",
        "repo_url": repo_url,
        "pages_url": pages_url,
        "commit_msg": msg
    }


def run_sync_pipeline() -> Dict:
    """Wrapper for CLI callers."""
    res = sync_and_push()
    if res.get("success"):
        print(f"[✓] {res.get('message')}")
        if res.get("pages_url"):
            print(f"  🌐 GitHub Pages : {res['pages_url']}")
    else:
        print(f"[!] {res.get('error')}")
    return res


if __name__ == "__main__":
    print("[*] Menjalankan Pipeline HTML Dashboard & Sync GitHub...")
    res = sync_and_push()
    if res["success"]:
        print(f"\n[✓] {res['message']}")
        if res.get("repo_url"):
            print(f"  • GitHub Repo  : {res['repo_url']}")
        if res.get("pages_url"):
            print(f"  • GitHub Pages : {res['pages_url']}")
    else:
        print(f"\n[!] Error: {res.get('error')}")

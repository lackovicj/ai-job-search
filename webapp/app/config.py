"""Runtime configuration, read once at import time.

Secrets: the container entrypoint moves ANTHROPIC_API_KEY and APP_PASSWORD out of
the process environment into files under SECRETS_DIR, which load_secret() reads once
and deletes. The LaTeX compiler runs LLM-written source; keeping the key out of
/proc/<pid>/environ means a hostile posting that steers the drafter into reading
files cannot lift the key into a PDF. Plain env vars still work for local runs.
"""

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("DATA_DIR", REPO / "webapp" / "data")).resolve()
SECRETS_DIR = Path(os.environ.get("SECRETS_DIR", "/run/app-secrets"))

SKILL_DIR = REPO / ".claude" / "skills" / "job-application-assistant"
COVER_ASSETS = REPO / "cover_letters"
MASTER_CV_SEED = REPO / "cv" / "main_example.tex"
COVER_EXAMPLE = REPO / "cover_letters" / "cover_example.tex"

MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
APP_USER = os.environ.get("APP_USER", "admin")

# The repo's own helpers (posting guard, robots gate, PDF verifier) are imported,
# not copied, so the web app and the Claude Code workflow share one implementation.
sys.path.insert(0, str(REPO / "tools"))


def load_secret(name: str) -> str:
    path = SECRETS_DIR / name.lower()
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        try:
            path.unlink()
        except OSError:
            pass
        return value
    return os.environ.pop(name, "")


ANTHROPIC_API_KEY = load_secret("ANTHROPIC_API_KEY")
APP_PASSWORD = load_secret("APP_PASSWORD")

DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "applications").mkdir(exist_ok=True)

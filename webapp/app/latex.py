"""Compile and measure CV / cover letter PDFs.

Engines follow the repo rules: lualatex for the moderncv CV, xelatex for cover.cls.
Page counts and the ATS text layer come from tools/verify_pdf.py, layout from
tools/verify_layout.py - the same checks /apply Step 5 runs.
"""

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import verify_pdf

from . import config

ENGINES = {"cv": "lualatex", "cover": "xelatex"}
EXPECTED_PAGES = {"cv": 2, "cover": 1}

# The source is written by a model that has read an untrusted posting. Nothing in
# the stock templates needs file input, Lua, catcode games or shell access, so any
# of these is refused before the compiler sees it.
FORBIDDEN = re.compile(
    r"\\(directlua|luaexec|luadirect|latelua|input|include|import|subimport|InputIfFileExists|"
    r"openin|openout|read|readline|write18|immediate|ShellEscape|catcode|csname|"
    r"lstinputlisting|verbatiminput|includepdf|includegraphics|special|pdfobj|"
    r"luacode|newwrite|newread|everyeof|scantokens)(?![A-Za-z])"
)


class UnsafeSource(Exception):
    pass


@dataclass
class Result:
    ok: bool
    pages: int | None = None
    problems: list[str] = field(default_factory=list)
    log_tail: str = ""
    text: str = ""


def check_source(tex: str) -> None:
    # Strip comments first so a commented-out example in the template is not flagged.
    code = re.sub(r"(?<!\\)%.*", "", tex)
    hits = sorted({m.group(0) for m in FORBIDDEN.finditer(code)})
    if hits:
        raise UnsafeSource("refused LaTeX commands: " + ", ".join(hits))


def _sandbox_env(workdir: Path) -> dict:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(workdir),
        "LANG": "C.UTF-8",
        "openout_any": "p",
        "shell_escape": "f",
    }
    for key in ("TEXMFVAR", "TEXMFCACHE", "TEXMFCONFIG"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def prepare_dir(workdir: Path) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    # cover.cls loads fonts by relative path (OpenFonts/fonts/...), so the class and
    # font tree must sit next to the cover letter source.
    if not (workdir / "cover.cls").exists():
        shutil.copy2(config.COVER_ASSETS / "cover.cls", workdir / "cover.cls")
    fonts = workdir / "OpenFonts"
    if not fonts.exists():
        shutil.copytree(config.COVER_ASSETS / "OpenFonts", fonts)


def compile_doc(workdir: Path, kind: str, tex: str) -> Result:
    """Write <kind>.tex, compile it, and run the page/layout/ATS checks."""
    check_source(tex)
    prepare_dir(workdir)
    src = workdir / f"{kind}.tex"
    pdf = workdir / f"{kind}.pdf"
    src.write_text(tex, encoding="utf-8")
    pdf.unlink(missing_ok=True)

    cmd = [ENGINES[kind], "-interaction=nonstopmode", "-halt-on-error",
           "-no-shell-escape", src.name]
    try:
        proc = subprocess.run(cmd, cwd=workdir, env=_sandbox_env(workdir),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=180)
    except FileNotFoundError:
        return Result(False, problems=[f"{ENGINES[kind]} is not installed on this server"])
    except subprocess.TimeoutExpired:
        return Result(False, problems=["compiler timed out after 180s"])
    finally:
        for ext in ("aux", "log", "out", "fls", "synctex.gz"):
            if ext != "log":
                (workdir / f"{kind}.{ext}").unlink(missing_ok=True)

    log_path = workdir / f"{kind}.log"
    log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else proc.stdout
    log_path.unlink(missing_ok=True)
    tail = _error_excerpt(log)

    if proc.returncode != 0 or not pdf.exists():
        return Result(False, problems=["compile failed"], log_tail=tail)

    problems = []
    text, pages, _extractor = verify_pdf.extract_text_layer(pdf)
    expected = EXPECTED_PAGES[kind]
    if pages != expected:
        problems.append(f"PDF has {pages} page(s); it must be exactly {expected}.")
    if kind == "cv":
        hits = verify_pdf.find_non_ascii_date_ranges(text)
        if hits:
            problems.append(
                "Date ranges joined by a non-ASCII dash (write `2016-2024` with one ASCII "
                "hyphen, never `--`): " + "; ".join(line.strip()[:60] for line, _ in hits))
        if "(cid:" in text or "�" in text:
            problems.append("The PDF text layer contains unreadable glyphs ((cid:) or U+FFFD).")
    layout = _layout_check(pdf)
    if layout:
        problems.append(layout)
    return Result(not problems, pages=pages, problems=problems, log_tail=tail, text=text)


def _layout_check(pdf: Path) -> str:
    script = config.REPO / "tools" / "verify_layout.py"
    try:
        proc = subprocess.run([sys.executable, str(script), str(pdf)], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=60)
    except subprocess.TimeoutExpired:
        return ""
    if proc.returncode == 1:  # 2 means "skipped" (no Poppler), never a verdict
        return "Layout check failed:\n" + (proc.stdout + proc.stderr).strip()[-1500:]
    return ""


def _error_excerpt(log: str) -> str:
    lines = log.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("!"):
            return "\n".join(lines[max(0, i - 3): i + 12])
    return "\n".join(lines[-30:])


def keyword_coverage(text: str, keywords: list[dict]) -> list[dict]:
    folded = verify_pdf.normalize_text(text).lower()
    rows = []
    for kw in keywords or []:
        term = (kw.get("term") or "").strip()
        if not term:
            continue
        covered = verify_pdf.normalize_text(term).lower() in folded
        rows.append({"term": term, "priority": kw.get("priority", ""),
                     "status": "covered" if covered else "missing"})
    return rows

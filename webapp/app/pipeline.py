"""Background jobs: evaluate a posting, then draft -> compile -> review -> revise -> verify."""

import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, db, intake, latex, llm

_pool = ThreadPoolExecutor(max_workers=2)
MAX_FIX_ROUNDS = 3


def app_dir(app_id: int) -> Path:
    return config.DATA_DIR / "applications" / str(app_id)


def _run(app_id: int, label: str, fn) -> None:
    db.update_application(app_id, busy=1, error="", stage=label)
    db.append_log(app_id, f"{label}...")
    try:
        fn(app_id)
    except (intake.IntakeError, llm.LLMError, latex.UnsafeSource) as exc:
        db.update_application(app_id, error=str(exc))
        db.append_log(app_id, f"ERROR: {exc}")
    except Exception as exc:  # keep the worker alive; show the failure in the UI
        db.update_application(app_id, error=f"Unexpected error: {exc}")
        db.append_log(app_id, "ERROR: " + traceback.format_exc(limit=3))
    finally:
        db.update_application(app_id, busy=0)


def submit(app_id: int, label: str, fn) -> None:
    db.update_application(app_id, busy=1, stage=f"queued: {label}")
    _pool.submit(_run, app_id, label, fn)


# ------------------------------------------------------------------ steps

def intake_and_evaluate(url: str, text: str):
    def step(app_id: int) -> None:
        taken = intake.take_posting(url, text)
        db.update_application(app_id, **taken)
        db.append_log(app_id, taken["guard_verdict"] or "posting_guard: done")
        db.append_log(app_id, f"source: {taken['host_class']}")
        evaluate(app_id)
    return step


def evaluate(app_id: int) -> None:
    app = db.get_application(app_id)
    db.update_application(app_id, stage="evaluating fit")
    ev = llm.evaluate(app)
    fit, verdict = llm.score(ev)
    db.update_application(
        app_id, evaluation=ev, fit_score=fit, fit_verdict=verdict,
        company=ev["company"], role=ev["role"], location=ev["location"],
        sector=ev["sector"], contact_person=ev["contact_person"],
        posting_language=ev["posting_language"], deadline=ev["deadline"],
        stage="evaluated",
    )
    db.append_log(app_id, f"fit {fit}/100 - {verdict}")


def _compile_until_clean(app_id: int, kind: str, tex: str) -> tuple[str, latex.Result]:
    workdir = app_dir(app_id)
    name = "CV" if kind == "cv" else "cover letter"
    for attempt in range(MAX_FIX_ROUNDS + 1):
        db.update_application(app_id, stage=f"compiling {name}")
        try:
            result = latex.compile_doc(workdir, kind, tex)
        except latex.UnsafeSource as exc:
            result = latex.Result(False, problems=[str(exc)])
        if result.ok:
            db.append_log(app_id, f"{name}: compiled, {result.pages} page(s), checks pass")
            return tex, result
        db.append_log(app_id, f"{name}: " + " | ".join(p.splitlines()[0] for p in result.problems))
        if attempt == MAX_FIX_ROUNDS:
            break
        db.update_application(app_id, stage=f"fixing {name} (round {attempt + 1})")
        fixed = llm.fix(kind, tex, result.problems, result.log_tail)
        db.append_log(app_id, f"{name} fix: {fixed['change_summary'][:200]}")
        tex = fixed["tex"]
    return tex, result


def draft(app_id: int) -> None:
    app = db.get_application(app_id)
    if not app.get("evaluation"):
        raise llm.LLMError("Evaluate the posting before drafting.")

    db.update_application(app_id, stage="drafting CV and cover letter")
    docs = llm.draft(app)
    cv_tex, _ = _compile_until_clean(app_id, "cv", docs["cv_tex"])
    cover_tex, _ = _compile_until_clean(app_id, "cover", docs["cover_tex"])

    db.update_application(app_id, stage="reviewer: researching company and critiquing")
    critique = llm.review(app, cv_tex, cover_tex)
    db.update_application(app_id, review=critique)
    db.append_log(app_id, "reviewer feedback received")

    db.update_application(app_id, stage="revising from reviewer feedback")
    revised = llm.revise(app, cv_tex, cover_tex, critique)
    cv_tex, cv_res = _compile_until_clean(app_id, "cv", revised["cv_tex"])
    cover_tex, cover_res = _compile_until_clean(app_id, "cover", revised["cover_tex"])

    checks = {
        "cv": {"pages": cv_res.pages, "ok": cv_res.ok, "problems": cv_res.problems},
        "cover": {"pages": cover_res.pages, "ok": cover_res.ok, "problems": cover_res.problems},
        "keywords": latex.keyword_coverage(cv_res.text, app["evaluation"].get("keywords")),
    }
    fields = {"decisions": revised["decisions"], "checks": checks, "stage": "drafted"}
    if not app.get("status"):
        fields["status"] = "drafted"
    elif app["status"] not in db.FINAL_STATUSES:
        fields["notes"] = (app.get("notes") or "") + " redrafted"
    db.update_application(app_id, **fields)
    if not (cv_res.ok and cover_res.ok):
        db.update_application(app_id, error="Documents were produced but some checks still fail - "
                                             "see Checks. Edit the LaTeX and recompile, or redraft.")


def recompile(app_id: int, cv_tex: str, cover_tex: str) -> None:
    app = db.get_application(app_id)
    checks = app.get("checks") or {}
    for kind, tex in (("cv", cv_tex), ("cover", cover_tex)):
        res = latex.compile_doc(app_dir(app_id), kind, tex)
        checks[kind] = {"pages": res.pages, "ok": res.ok, "problems": res.problems}
        if kind == "cv" and res.text:
            checks["keywords"] = latex.keyword_coverage(res.text, (app.get("evaluation") or {}).get("keywords"))
        db.append_log(app_id, f"manual recompile {kind}: {'ok' if res.ok else '; '.join(res.problems)}")
    db.update_application(app_id, checks=checks, stage="drafted")

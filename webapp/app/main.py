"""FastAPI entry point: tracker, application detail, profile editor."""

import csv
import io
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auth, config, db, keystore, llm, locations, pipeline

HERE = Path(__file__).parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    seed_settings()
    auth.init_setup_code()
    yield


app = FastAPI(title="AI Job Search", docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")

PROFILE_KEYS = {
    "profile": ("Candidate profile", "01-candidate-profile.md"),
    "behavioral": ("Behavioral profile", "02-behavioral-profile.md"),
    "writing_style": ("Writing style", "03-writing-style.md"),
    "framework": ("Evaluation framework", "04-job-evaluation.md"),
}
DOWNLOADS = {"cv.pdf", "cv.tex", "cover.pdf", "cover.tex"}


def seed_settings() -> None:
    """First boot: copy the repo's profile files into the editable store."""
    for key, (_label, filename) in PROFILE_KEYS.items():
        if not db.get_setting(key):
            db.set_setting(key, (config.SKILL_DIR / filename).read_text(encoding="utf-8"))
    if not db.get_setting("master_cv"):
        db.set_setting("master_cv", config.MASTER_CV_SEED.read_text(encoding="utf-8"))
    if not db.get_setting("cv_language"):
        db.set_setting("cv_language", "English")


PUBLIC_PATHS = {"/healthz", "/login", "/setup"}


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if path == "/healthz":
        return await call_next(request)
    # State-changing requests must come from this origin (login and setup included).
    if request.method not in ("GET", "HEAD") and not _same_origin(request):
        return PlainTextResponse("Cross-site request refused", 403)
    if not path.startswith("/static/"):
        if not auth.has_account():
            if path != "/setup":
                return RedirectResponse("/setup", 303)
        elif path not in PUBLIC_PATHS and not auth.valid_session(request.cookies.get(auth.SESSION_COOKIE)):
            return RedirectResponse("/login", 303)
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Cache-Control", "no-store")
    return response


def _start_session(request: Request, password: str) -> RedirectResponse:
    keystore.unlock(password)
    response = RedirectResponse("/", 303)
    https = (request.headers.get("x-forwarded-proto") or request.url.scheme) == "https"
    response.set_cookie(auth.SESSION_COOKIE, auth.new_session(), max_age=auth.SESSION_SECONDS,
                        httponly=True, samesite="lax", secure=https, path="/")
    return response


@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, error: str = ""):
    if auth.has_account():
        return RedirectResponse("/login", 303)
    return templates.TemplateResponse(request, "setup.html", {"error": error, "hide_nav": True})


@app.post("/setup")
def setup(request: Request, code: str = Form(""), username: str = Form(""),
          password: str = Form(""), confirm: str = Form("")):
    if auth.has_account():
        return RedirectResponse("/login", 303)
    username = username.strip()
    problem = ("Choose a username." if not username
               else auth.check_new_password(password, confirm))
    if not problem and not auth.create_account(code, username, password):
        problem = "Wrong setup code. Copy it from the app's log in Coolify (Logs tab)."
    if problem:
        return RedirectResponse("/setup?" + urlencode({"error": problem}), 303)
    return _start_session(request, password)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = ""):
    if auth.valid_session(request.cookies.get(auth.SESSION_COOKIE)):
        return RedirectResponse("/", 303)
    return templates.TemplateResponse(request, "login.html", {"error": error, "hide_nav": True})


@app.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form("")):
    if auth.locked_out():
        return RedirectResponse("/login?error=Too+many+failed+attempts.+Try+again+in+15+minutes.", 303)
    if not auth.verify(username.strip(), password):
        return RedirectResponse("/login?error=Wrong+username+or+password.", 303)
    return _start_session(request, password)


@app.post("/logout")
def logout(request: Request):
    auth.end_session(request.cookies.get(auth.SESSION_COOKIE))
    response = RedirectResponse("/login", 303)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return response


def _same_origin(request: Request) -> bool:
    source = request.headers.get("origin") or request.headers.get("referer") or ""
    hosts = {request.headers.get("host", ""), request.headers.get("x-forwarded-host", "")}
    return bool(source) and urlsplit(source).netloc in hosts - {""}


def _get(app_id: int) -> dict:
    item = db.get_application(app_id)
    if not item:
        raise HTTPException(404, "No such application")
    return item


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "apps": db.list_applications(),
        "final": db.FINAL_STATUSES,
        "profile_unset": "[YOUR_NAME]" in db.get_setting("profile"),
        "api_key_set": bool(keystore.active()),
    })


@app.post("/applications")
def create(url: str = Form(""), text: str = Form("")):
    if not url.strip() and not text.strip():
        raise HTTPException(400, "Give a URL or paste the posting text.")
    if url.strip() and urlsplit(url.strip()).scheme not in ("http", "https"):
        raise HTTPException(400, "Only http(s) URLs are supported.")
    app_id = db.create_application(source_url=url.strip())
    pipeline.submit(app_id, "fetching and evaluating posting", pipeline.intake_and_evaluate(url, text))
    return RedirectResponse(f"/applications/{app_id}", 303)


@app.get("/applications/{app_id}", response_class=HTMLResponse)
def detail(request: Request, app_id: int):
    item = _get(app_id)
    folder = pipeline.app_dir(app_id)
    files = {name: (folder / name).exists() for name in DOWNLOADS}
    tex = {k: (folder / f"{k}.tex").read_text(encoding="utf-8") if files[f"{k}.tex"] else ""
           for k in ("cv", "cover")}
    return templates.TemplateResponse(request, "detail.html", {
        "a": item, "files": files, "tex": tex, "statuses": db.STATUSES,
    })


@app.post("/applications/{app_id}/evaluate")
def re_evaluate(app_id: int):
    item = _get(app_id)
    if item["busy"]:
        raise HTTPException(409, "A step is already running")
    if not item["posting"]:
        raise HTTPException(400, "No posting text stored; create a new application instead.")
    pipeline.submit(app_id, "re-evaluating fit", pipeline.evaluate)
    return RedirectResponse(f"/applications/{app_id}", 303)


@app.post("/applications/{app_id}/draft")
def draft(app_id: int):
    item = _get(app_id)
    if item["busy"]:
        raise HTTPException(409, "A step is already running")
    pipeline.submit(app_id, "drafting application", pipeline.draft)
    return RedirectResponse(f"/applications/{app_id}", 303)


@app.post("/applications/{app_id}/recompile")
def recompile(app_id: int, cv_tex: str = Form(...), cover_tex: str = Form(...)):
    item = _get(app_id)
    if item["busy"]:
        raise HTTPException(409, "A step is already running")
    pipeline.submit(app_id, "recompiling edited LaTeX",
                    lambda i: pipeline.recompile(i, cv_tex, cover_tex))
    return RedirectResponse(f"/applications/{app_id}", 303)


@app.post("/applications/{app_id}/status")
def set_status(app_id: int, status: str = Form(...), notes: str = Form("")):
    _get(app_id)
    if status and status not in db.STATUSES:
        raise HTTPException(400, "Unknown status")
    db.update_application(app_id, status=status, notes=notes)
    return RedirectResponse(f"/applications/{app_id}", 303)


@app.post("/applications/{app_id}/delete")
def delete(app_id: int):
    item = _get(app_id)
    if item["busy"]:
        raise HTTPException(409, "A step is still running")
    folder = pipeline.app_dir(app_id)
    if folder.exists():
        shutil.rmtree(folder)
    db.delete_application(app_id)
    return RedirectResponse("/", 303)


@app.get("/applications/{app_id}/files/{name}")
def download(app_id: int, name: str):
    item = _get(app_id)
    if name not in DOWNLOADS:
        raise HTTPException(404)
    path = pipeline.app_dir(app_id) / name
    if not path.exists():
        raise HTTPException(404)
    slug = "_".join(filter(None, [item["company"], item["role"]])).replace(" ", "_")
    slug = "".join(c for c in slug if c.isalnum() or c in "_-")[:60] or f"app{app_id}"
    prefix = "main" if name.startswith("cv") else "cover"
    return FileResponse(path, filename=f"{prefix}_{slug}{path.suffix}")


@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request, msg: str = "", error: str = ""):
    return templates.TemplateResponse(request, "settings.html", {
        "key": keystore.status(), "msg": msg, "error": error, "model": config.MODEL,
        "username": auth.username(), "loc": locations.get(), "regions": locations.REGIONS,
        "countries": locations.COUNTRIES, "modes": locations.MODES,
    })


@app.post("/settings/location")
async def save_location(request: Request):
    locations.save(locations.from_form(await request.form()))
    return RedirectResponse("/settings?msg=Job+search+location+saved.#location", 303)


@app.post("/settings/password")
def change_password(request: Request, current: str = Form(""), password: str = Form(""),
                    confirm: str = Form("")):
    problem = auth.check_new_password(password, confirm)
    if not problem and not auth.verify(auth.username(), current):
        problem = "The current password is wrong."
    if problem:
        return RedirectResponse("/settings?" + urlencode({"error": problem}), 303)
    keystore.rekey(password)
    auth.set_password(auth.username(), password)
    auth.end_other_sessions(request.cookies.get(auth.SESSION_COOKIE))
    return RedirectResponse("/settings?msg=Password+changed.", 303)


@app.post("/settings/api-key")
def save_api_key(api_key: str = Form("")):
    key = api_key.strip()
    if not key:
        return RedirectResponse("/settings?error=Enter+a+key.", 303)
    if not key.startswith("sk-ant-") or len(key) > 300 or any(c.isspace() for c in key):
        return RedirectResponse("/settings?error=That+does+not+look+like+an+Anthropic+API+key+(sk-ant-...).", 303)
    try:
        llm.check_key(key)
    except llm.LLMError as exc:
        return RedirectResponse("/settings?" + urlencode({"error": str(exc)}), 303)
    try:
        keystore.save(key)
    except RuntimeError:
        return RedirectResponse("/login", 303)
    return RedirectResponse("/settings?msg=API+key+verified+and+saved.", 303)


@app.post("/settings/api-key/delete")
def delete_api_key():
    keystore.clear()
    return RedirectResponse("/settings?msg=Stored+API+key+removed.", 303)


@app.get("/profile", response_class=HTMLResponse)
def profile(request: Request, saved: int = 0):
    fields = [(k, label, db.get_setting(k)) for k, (label, _f) in PROFILE_KEYS.items()]
    fields.append(("master_cv", "Master CV (LaTeX, source of truth for facts)", db.get_setting("master_cv")))
    return templates.TemplateResponse(request, "profile.html", {
        "fields": fields, "cv_language": db.get_setting("cv_language"), "saved": saved,
        "import_state": pipeline.import_state(),
    })


MAX_PDF_BYTES = 15 * 1024 * 1024


@app.post("/profile/import")
async def import_cv(file: UploadFile = File(...)):
    if pipeline.import_state().get("status") == "running":
        return RedirectResponse("/profile/import", 303)
    data = await file.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(400, "The PDF is larger than 15 MB.")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(400, "That file is not a PDF.")
    if not keystore.active():
        return RedirectResponse("/settings?error=Add+your+Anthropic+API+key+first.", 303)
    pipeline.start_import(data, (file.filename or "cv.pdf")[:120])
    return RedirectResponse("/profile/import", 303)


@app.get("/profile/import", response_class=HTMLResponse)
def import_review(request: Request):
    state = pipeline.import_state()
    if not state:
        return RedirectResponse("/profile", 303)
    loc = locations.get()
    home = (state.get("result") or {}).get("home_city", "").strip()
    return templates.TemplateResponse(request, "import.html", {
        "s": state, "offer_city": bool(home) and home.lower() not in loc["cities"].lower(),
    })


@app.post("/profile/import/apply")
async def import_apply(request: Request):
    form = await request.form()
    state = pipeline.import_state()
    if state.get("status") != "done":
        return RedirectResponse("/profile", 303)
    text = lambda k: str(form.get(k, "")).replace("\r\n", "\n")  # noqa: E731
    if form.get("use_profile") == "on":
        db.set_setting("profile", text("candidate_profile"))
    if form.get("use_master_cv") == "on":
        db.set_setting("master_cv", text("master_cv"))
    if form.get("use_language") == "on" and text("cv_language").strip():
        db.set_setting("cv_language", text("cv_language").strip())
    home = state["result"].get("home_city", "").strip()
    if form.get("use_city") == "on" and home:
        loc = locations.get()
        loc["cities"] = ", ".join(filter(None, [loc["cities"].strip(), home]))
        locations.save(loc)
    pipeline.clear_import()
    return RedirectResponse("/profile?saved=1", 303)


@app.post("/profile/import/discard")
def import_discard():
    if pipeline.import_state().get("status") != "running":
        pipeline.clear_import()
    return RedirectResponse("/profile", 303)


@app.post("/profile")
async def save_profile(request: Request):
    form = await request.form()
    for key in list(PROFILE_KEYS) + ["master_cv", "cv_language"]:
        if key in form:
            db.set_setting(key, str(form[key]).replace("\r\n", "\n"))
    return RedirectResponse("/profile?saved=1", 303)


def _cell(value) -> str:
    """Neutralise spreadsheet formulas in free-text cells."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


TRACKER_HEADER = ["date", "company", "sector", "role", "role_type", "channel", "status",
                  "contact_person", "fit_rating", "notes", "cv_file", "cover_letter_file",
                  "source", "deadline"]


@app.get("/tracker.csv")
def tracker_csv():
    """Same header as job_search_tracker.csv, so /outcome and /upskill can read it."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(TRACKER_HEADER)
    for a in reversed(db.list_applications()):
        if not a["status"]:
            continue
        writer.writerow([_cell(v) for v in (
            a["created"][:10], a["company"], a["sector"], a["role"], "", "",
            a["status"], a["contact_person"], a["fit_score"] or "", a["notes"],
            "cv.tex", "cover.tex", a["source_url"], a["deadline"])])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=job_search_tracker.csv"})

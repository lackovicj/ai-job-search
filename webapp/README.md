# AI Job Search - web app

A self-hosted web version of the `/apply` workflow. Paste a job posting (or its URL), get a
fit evaluation, then a tailored CV and cover letter as compiled PDFs. The app also keeps an
application tracker. It runs the same rules as the Claude Code workspace: it reads the
evaluation framework, writing style and template guides from
`.claude/skills/job-application-assistant/` and reuses `tools/posting_guard.py`,
`tools/robots_check.py`, `tools/verify_pdf.py` and `tools/verify_layout.py`.

## What it does

1. **Intake.** Takes a URL or pasted text. A URL is fetched only after the robots.txt gate
   passes and only when it resolves to a public address. Pasted text and fetched pages both go
   through the posting integrity gate, and the source host is classified as a portal board, an
   official ATS, or unverified.
2. **Evaluate.** Claude scores the posting against your profile using `04-job-evaluation.md`:
   the eligibility and language gates, then the four scored dimensions. The server computes
   the weighted score and the verdict itself.
3. **Draft.** Claude writes a moderncv CV and a cover.cls letter. Each document is compiled
   (lualatex for the CV, xelatex for the letter) and checked for exactly 2 pages and exactly
   1 page, layout holes and orphaned entries, and ATS-safe date ranges. A failing document goes
   back to Claude to fix, up to 3 rounds.
4. **Review.** A reviewer pass researches the company with web search and audits every claim
   against your profile. Claude then revises both documents and recompiles them.
5. **Track.** Each application gets a status from the `/outcome` vocabulary and notes. The
   CSV export uses the same header as `job_search_tracker.csv`.

You can edit the generated LaTeX in the browser and recompile it.

## Deploy on Coolify

1. **New Resource → Public/Private Repository**, pick this repo and branch.
2. **Build Pack:** `Dockerfile`. Base directory `/`, Dockerfile location `/Dockerfile`.
3. **Ports Exposes:** `8000`.
4. **Environment variables:** none are required. Everything is set in the app itself.
   Optional:

   | Variable | Purpose |
   |---|---|
   | `ANTHROPIC_API_KEY` | Fallback Claude API key, used only when none is saved in **Settings** |
   | `CLAUDE_MODEL` | Default `claude-opus-5-5` |

5. **Persistent Storage:** add a volume mounted at `/data`. It holds the SQLite database
   (profile + tracker) and every generated document. Without it, a redeploy wipes everything.
6. Add your domain and deploy. Coolify's proxy terminates HTTPS. Use an `https://` domain:
   the login form sends your password, and the session cookie is marked secure over HTTPS.

The first build takes a while. TeX Live with `texlive-fonts-extra` is a download of over a
gigabyte, and the build also compiles both stock templates as a smoke test. A broken TeX
install therefore fails the build instead of failing your first application.

## First use

1. **Create your login.** On first start the app has no account and shows *Create your login*.
   It asks for a one-time **setup code** printed in the app's log (Coolify → your app →
   **Logs**), which looks like this:

   ```
   AI Job Search setup code: 3F9A-0C21-B7E4
   ```

   The code stops anyone else who finds the URL first from claiming your app. Pick a username
   and a password of at least 10 characters. The code is then no longer needed.
2. **Add your API key.** Open **Settings** and paste your Anthropic API key (from
   console.anthropic.com). The app checks it against the API before saving it and stores it
   encrypted with your login password.

Then open **Profile** and replace the placeholder content: the candidate profile, behavioral
profile, writing style, evaluation framework (your strong and weak skill areas, career goals,
deal-breakers) and master CV. These documents are the only source of facts the drafter may
use. Anything missing from them is left out of your CV.

**Forgotten password:** open your app's **Terminal** in Coolify and run
`python /app/webapp/reset_login.py`, then restart the app and create a new login with the new
setup code from the log. Applications, profile and documents are kept. The API key has to be
entered again, because it was encrypted with the old password.

## Cost

Each application makes several Claude Opus calls: one for evaluation, then drafting, reviewer
research with web search, revision, and any layout fixes. Prompt caching covers the large shared
system prompt. Expect cents for an evaluation and roughly a dollar or two for a full draft,
depending on posting length and how many fix rounds the layout needs.

## Security notes

- Single user. Login with an HttpOnly, SameSite session cookie; the password is stored only as
  an scrypt hash. After 10 failed logins in 15 minutes, login pauses for 15 minutes. Account
  creation needs the setup code from the server log. State-changing requests must come from the
  app's own origin.
- Postings are treated as untrusted. The integrity gate strips hidden text, and every prompt
  carries the trust-boundary rule from `/apply`.
- Claude writes the LaTeX after reading an untrusted posting, so the server refuses any source
  that uses file input, Lua, shell escape, `\catcode` or `\csname`. The compiler also runs
  with shell escape off and with an environment stripped of secrets. A key entered in
  Settings is stored only as ciphertext, under a key derived from your login password. That
  derived key exists only in the server's memory: logging in unlocks it, and a restart forgets
  it until you log in again. An `ANTHROPIC_API_KEY` from the environment is moved out of the
  process environment before the server starts.

## Local development

```bash
pip install -r webapp/requirements.txt pytest
uvicorn app.main:app --app-dir webapp --reload
pytest webapp/tests        # stubbed Claude, no LaTeX needed
```

The setup code is printed in the uvicorn output.


Compiling locally needs `lualatex` and `xelatex` on `PATH` (see `SETUP.md`). Data goes to
`webapp/data/` unless `DATA_DIR` is set.

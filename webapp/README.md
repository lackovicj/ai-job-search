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
4. **Environment variables:**

   | Variable | Required | Purpose |
   |---|---|---|
   | `ANTHROPIC_API_KEY` | yes | Claude API key (console.anthropic.com) |
   | `APP_PASSWORD` | yes | Password for HTTP basic auth. The app refuses to serve without it |
   | `APP_USER` | no | Basic-auth username, default `admin` |
   | `CLAUDE_MODEL` | no | Default `claude-opus-5-5` |

5. **Persistent Storage:** add a volume mounted at `/data`. It holds the SQLite database
   (profile + tracker) and every generated document. Without it, a redeploy wipes everything.
6. Add your domain and deploy. Coolify's proxy terminates HTTPS. Don't expose the app over
   plain HTTP: basic auth sends the password with every request.

The first build takes a while. TeX Live with `texlive-fonts-extra` is a download of over a
gigabyte, and the build also compiles both stock templates as a smoke test. A broken TeX
install therefore fails the build instead of failing your first application.

## First use

Open **Profile** and replace the placeholder content: the candidate profile, behavioral
profile, writing style, evaluation framework (your strong and weak skill areas, career goals,
deal-breakers) and master CV. These documents are the only source of facts the drafter may
use. Anything missing from them is left out of your CV.

## Cost

Each application makes several Claude Opus calls: one for evaluation, then drafting, reviewer
research with web search, revision, and any layout fixes. Prompt caching covers the large shared
system prompt. Expect cents for an evaluation and roughly a dollar or two for a full draft,
depending on posting length and how many fix rounds the layout needs.

## Security notes

- Single user, HTTP basic auth. State-changing requests must come from the app's own origin.
- Postings are treated as untrusted. The integrity gate strips hidden text, and every prompt
  carries the trust-boundary rule from `/apply`.
- Claude writes the LaTeX after reading an untrusted posting, so the server refuses any source
  that uses file input, Lua, shell escape, `\catcode` or `\csname`. The compiler also runs
  with shell escape off and with an environment stripped of secrets. The entrypoint moves the
  API key and password out of the process environment before the server starts.

## Local development

```bash
pip install -r webapp/requirements.txt pytest
APP_PASSWORD=dev ANTHROPIC_API_KEY=sk-... uvicorn app.main:app --app-dir webapp --reload
pytest webapp/tests        # stubbed Claude, no LaTeX needed
```

Compiling locally needs `lualatex` and `xelatex` on `PATH` (see `SETUP.md`). Data goes to
`webapp/data/` unless `DATA_DIR` is set.

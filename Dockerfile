# AI Job Search web app - single container for Coolify (Dockerfile build pack).
# Serves on $PORT (default 8000); persist /data. See webapp/README.md.
FROM python:3.13-slim-trixie

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data \
    SECRETS_DIR=/run/app-secrets \
    TEXMFVAR=/opt/texmf-var \
    TEXMFCACHE=/opt/texmf-var \
    PORT=8000

# lualatex compiles the moderncv CV, xelatex the cover.cls letter (fontspec).
# texlive-fonts-extra carries fontawesome5/academicons that moderncv needs;
# poppler-utils gives tools/verify_layout.py its pdftotext -bbox.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      texlive-luatex texlive-xetex texlive-latex-recommended texlive-latex-extra \
      texlive-fonts-recommended texlive-fonts-extra lmodern fontconfig \
      poppler-utils util-linux \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 app \
 && mkdir -p /data /opt/texmf-var \
 && chown app:app /data /opt/texmf-var

WORKDIR /app
COPY webapp/requirements.txt webapp/requirements.txt
RUN pip install --no-cache-dir -r webapp/requirements.txt

COPY . .

# Build-time smoke test: both stock templates must compile with the same engines and
# flags the app uses. Also warms the luaotfload font cache so the first CV is fast.
USER app
RUN set -e; d=$(mktemp -d); \
    cp cv/main_example.tex cover_letters/cover_example.tex cover_letters/cover.cls "$d"/; \
    cp -r cover_letters/OpenFonts "$d"/; cd "$d"; \
    lualatex -interaction=nonstopmode -halt-on-error -no-shell-escape main_example.tex >/dev/null \
      || { tail -n 40 main_example.log; exit 1; }; \
    xelatex -interaction=nonstopmode -halt-on-error -no-shell-escape cover_example.tex >/dev/null \
      || { tail -n 40 cover_example.log; exit 1; }; \
    test -s main_example.pdf && test -s cover_example.pdf; \
    cd /; rm -rf "$d"
USER root

# Normalise line endings in case the repo was checked out on Windows with autocrlf.
RUN sed -i 's/\r$//' webapp/docker-entrypoint.sh && chmod +x webapp/docker-entrypoint.sh

EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/healthz', timeout=4)"

ENTRYPOINT ["/app/webapp/docker-entrypoint.sh"]

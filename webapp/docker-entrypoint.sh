#!/bin/sh
# Starts as root only to fix volume ownership and park an optional env API key in a
# file, then drops to the unprivileged `app` user. The key is unset before exec so it
# never appear in the server's /proc/<pid>/environ, which LLM-written LaTeX running
# as the same user could otherwise read (see webapp/app/config.py).
set -eu

mkdir -p "$DATA_DIR" "$SECRETS_DIR"
chown app:app "$DATA_DIR"
chown app:app "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"

umask 077
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  printf '%s' "$ANTHROPIC_API_KEY" > "$SECRETS_DIR/anthropic_api_key"
fi
chown -R app:app "$SECRETS_DIR"
unset ANTHROPIC_API_KEY

# One worker: drafting runs in background threads of this process and state is SQLite.
exec setpriv --reuid=app --regid=app --init-groups \
  env HOME=/home/app uvicorn app.main:app --app-dir /app/webapp --host 0.0.0.0 --port "${PORT:-8000}" \
  --workers 1 --proxy-headers --forwarded-allow-ips='*'

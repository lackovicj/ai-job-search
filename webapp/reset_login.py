"""Forgotten password: remove the login so the app shows account setup again.

Run inside the container (Coolify -> your app -> Terminal):

    python /app/webapp/reset_login.py

Then restart the app and use the new setup code from its log. Applications, the
profile and documents are kept. The stored Anthropic API key is removed, because
it was encrypted with the forgotten password; enter it again in Settings.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import auth, db  # noqa: E402

db.init()
auth.reset_account()
print("Login removed. Restart the app, then create a new login with the setup code from its log.")

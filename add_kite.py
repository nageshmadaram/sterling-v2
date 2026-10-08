"""Dev helper: register a Zerodha Kite account via the local API.

Every account Sterling creates is LIVE; it stays disconnected until you complete a
real Kite login in the Sterling UI. Credentials must come from the environment —
never hardcode secrets.
  KITE_API_KEY / KITE_API_SECRET  (preferred)
  or ZERODHA_API_KEY / ZERODHA_API_SECRET
Optional: STERLING_API_TOKEN (a Sterling login token, required in production).
"""
import os
import sys

import requests

api_key = os.environ.get("KITE_API_KEY") or os.environ.get("ZERODHA_API_KEY")
api_secret = os.environ.get("KITE_API_SECRET") or os.environ.get("ZERODHA_API_SECRET")
if not api_key or not api_secret:
    print(
        "Set KITE_API_KEY and KITE_API_SECRET (or ZERODHA_API_KEY / ZERODHA_API_SECRET) "
        "in the environment before running this script.",
        file=sys.stderr,
    )
    sys.exit(1)

base_url = os.environ.get("STERLING_API_URL", "http://127.0.0.1:8000").rstrip("/")

headers = {}
if os.environ.get("STERLING_API_TOKEN"):
    headers["Authorization"] = f"Bearer {os.environ['STERLING_API_TOKEN']}"

try:
    response = requests.post(
        f"{base_url}/api/v1/kite/accounts",
        headers=headers,
        json={
            "label": "Zerodha Kite",
            "api_key": api_key,
            "api_secret": api_secret,
        },
        timeout=30,
    )
    print(response.status_code, response.text)
except Exception as e:
    print(e)

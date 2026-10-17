"""Check the frontend API/WebSocket proxy against an isolated test backend.

This creates no broker account, sends no broker orders, and uses a temporary DB.
It verifies transport and tenant rejection; it is not live broker acceptance.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import websockets


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def check(base_url: str) -> dict:
    async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
        deadline = time.monotonic() + 35
        while True:
            try:
                response = await client.get(f"{base_url}/api/v1/health/live")
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("test backend did not become reachable through the frontend")
            await asyncio.sleep(0.2)

        response = await client.post(f"{base_url}/api/v1/auth/login", data={
            "username": "proxy-check-admin", "password": "proxy-check-password-only-for-tests",
        })
        response.raise_for_status()
        token = response.json()["access_token"]

    ws_url = base_url.replace("http://", "ws://") + "/api/v1/stream/ws"
    async with websockets.connect(ws_url, origin=base_url, proxy=None, open_timeout=5) as ws:
        await ws.send(json.dumps({"action": "auth", "token": token}))
        async def receive_result():
            async with asyncio.timeout(5):
                while True:
                    message = json.loads(await ws.recv())
                    if message.get("type") not in {"heartbeat", "ping", "pong"}:
                        return message
        authenticated = await receive_result()
        if authenticated.get("type") != "auth_ok":
            raise RuntimeError("frontend stream authentication failed")
        await ws.send(json.dumps({"action": "subscribe", "channel": "kite_ticks:other-user"}))
        message = await receive_result()
        if message.get("error") != "unauthorized_channel":
            raise RuntimeError("frontend stream did not enforce the tenant boundary")
    return {"frontend_api": "PASS", "websocket_upgrade": "PASS",
            "tenant_subscription_boundary": "PASS", "live_broker_acceptance": "NOT_RUN"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    backend_port, frontend_port = free_port(), free_port()
    # Strip broker/alert credentials before starting any application workers.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("KITE_", "ZERODHA_", "STERLING_", "TRUEDATA_")) and "TELEGRAM" not in key}
    with tempfile.TemporaryDirectory(prefix="sterling-proxy-check-") as temp:
        state = Path(temp)
        env.update({
            "STERLING_ROOT": str(root), "STERLING_STATE_ROOT": str(state),
            "STERLING_LIVE_EXECUTION": "false",
            "STERLING_SECRET_KEY": "proxy-test-encryption-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "STERLING_JWT_SECRET": "proxy-test-auth-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "STERLING_ADMIN_USERNAME": "proxy-check-admin",
            "STERLING_ADMIN_PASSWORD": "proxy-check-password-only-for-tests",
            "ENVIRONMENT": "test", "REAL_PUBLIC_DATA": "false", "PAPER_TRADING": "true",
            "KITE_API_KEY": "", "KITE_API_SECRET": "", "ZERODHA_API_KEY": "", "ZERODHA_API_SECRET": "",
            "STERLING_DB_PATH": str(state / "test.db"),
            "STERLING_SHADOW_DIR": str(state / "shadow"),
            "STERLING_OBSERVATIONS_DB_PATH": str(state / "observations.db"),
            "STERLING_INTENT_DB": str(state / "intents.db"),
            "STERLING_SAFE_MODE_FILE": str(state / "safe-mode.json"),
            "API_TARGET": f"http://127.0.0.1:{backend_port}", "VITE_API_BASE_URL": "",
            "CORS_ORIGINS": json.dumps([f"http://127.0.0.1:{frontend_port}"]),
            "LOG_LEVEL": "WARNING",
        })
        processes = []
        with (state / "backend.log").open("w") as backend_log, (state / "frontend.log").open("w") as frontend_log:
            try:
                processes.append(subprocess.Popen(
                    [sys.executable, "-c",
                     "from app.core import runtime_env, config; "
                     "runtime_env.load_sterling_env=lambda *a, **k: []; "
                     "config.settings=config.Settings(_env_file=None); "
                     "import uvicorn; "
                     f"uvicorn.run('main:app', host='127.0.0.1', port={backend_port})"],
                    cwd=root / "backend", env=env, stdout=backend_log, stderr=subprocess.STDOUT,
                ))
                processes.append(subprocess.Popen(
                    ["node", "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", str(frontend_port), "--strictPort"],
                    cwd=root / "frontend", env=env, stdout=frontend_log, stderr=subprocess.STDOUT,
                ))
                result = asyncio.run(check(f"http://127.0.0.1:{frontend_port}"))
                rendered = json.dumps(result, indent=2) + "\n"
                if args.report:
                    args.report.write_text(rendered)
                print(rendered, end="")
            finally:
                for process in reversed(processes):
                    process.terminate()
                for process in reversed(processes):
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


if __name__ == "__main__":
    main()

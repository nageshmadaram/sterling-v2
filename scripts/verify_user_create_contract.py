#!/usr/bin/env python3
"""Read-only probe: does the *running* Sterling backend accept initial user grants?

The UI and repository can be newer than the API process (for example after a
frontend-only refresh). This script does not authenticate, create users, or send
credentials. It checks the runtime OpenAPI schema, not the files on disk.
"""
import argparse
import json
import sys
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def resolve(schema, components):
    for _ in range(8):
        if not isinstance(schema, dict):
            return {}
        ref = schema.get("$ref")
        if not isinstance(ref, str):
            return schema
        if not ref.startswith("#/components/schemas/"):
            return {}
        schema = components.get(ref.rsplit("/", 1)[-1], {})
    return {}


def supported(document):
    if not isinstance(document, dict):
        return False
    spec = document.get("paths", {}).get("/api/v1/auth/users", {}).get("post", {})
    body = spec.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
    components = document.get("components", {}).get("schemas", {})
    schema = resolve(body, components)
    properties = schema.get("properties", {})
    # In Pydantic v2 UserCreate directly inherits fields from UserEdit, but
    # tolerate an OpenAPI allOf composition if the schema generator changes.
    for entry in schema.get("allOf", []):
        properties = {**resolve(entry, components).get("properties", {}), **properties}
    return "features" in properties and "configuration" in properties


def run(url, timeout):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Use a valid HTTP(S) origin without embedded credentials.")
    base = parsed.geturl().rstrip("/")
    target = base if base.endswith("/openapi.json") else base + "/openapi.json"
    request = Request(target, headers={"Accept": "application/json", "Cache-Control": "no-cache"})
    with urlopen(request, timeout=timeout) as response:
        document = json.load(response)
    if supported(document):
        print("PASS: Running API supports features + configuration in Add User.")
        return 0
    print("BLOCKED: Running API does not advertise both initial access fields.")
    print("The frontend and backend contracts do not match. Deploy the matching backend.")
    return 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000",
                        help="Sterling backend origin, or full /openapi.json URL")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    try:
        sys.exit(run(args.url, args.timeout))
    except Exception as exc:
        print(f"UNKNOWN: Cannot inspect running API schema ({type(exc).__name__}: {exc}).")
        print("Do not infer compatibility or create a user with stripped permissions.")
        sys.exit(3)

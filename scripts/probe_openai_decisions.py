"""Probe Decisions with API-key billing or a fresh ChatGPT OAuth login.

Run from the repository root:
    python scripts/probe_openai_decisions.py --auth api-key
    python scripts/probe_openai_decisions.py --auth chatgpt

Uses httpx and cryptography already present in the Dorker environment. Sends only
synthetic text. Tokens stay in memory; cache/ stores only host/client identifiers.
The OAuth mode never falls back to an API key. This is a diagnostic, not a
production authentication implementation or a guarantee of general availability.

References:
https://developers.openai.com/siwc/token-sharing-open-source/sign-in
https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
https://developers.openai.com/api/docs/guides/decisions
"""

import argparse
import base64
import hashlib
import json
import os
import secrets
import shlex
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

API = "https://api.openai.com/v1"
ISSUER = "https://auth.openai.com"
STATE_FILE = Path(__file__).resolve().parents[1] / "cache/decisions-probe-registration.json"
SAMPLE = "This job requires Python. It is fully remote and accepts candidates worldwide."


def b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def safe_text(value: str, sensitive: list[str]) -> str:
    for secret in sensitive:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value


def report(response: httpx.Response, sensitive: list[str]) -> None:
    print(f"HTTP {response.status_code}; request_id={response.headers.get('x-request-id', '-')}")
    print(safe_text(response.text, sensitive)[:6000])


def load_api_key(env_file: Path) -> str:
    if key := os.environ.get("OPENAI_API_KEY"):
        return key
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            name, separator, value = line.removeprefix("export ").partition("=")
            if separator and name.strip() == "OPENAI_API_KEY":
                parts = shlex.split(value, comments=True)
                if len(parts) == 1 and parts[0]:
                    return parts[0]
    raise RuntimeError("OPENAI_API_KEY is missing from the environment and selected .env file.")


def save_registration(record: dict[str, str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    with temporary.open("w") as handle:
        os.chmod(temporary, 0o600)
        json.dump(record, handle)
    temporary.replace(STATE_FILE)


def validate_identity(
    client: httpx.Client, token: str, client_id: str, nonce: str
) -> str:
    """Validate RS256 signature, issuer, audience, times, subject, and nonce."""
    header_part, payload_part, signature_part = token.split(".")
    header = json.loads(b64decode(header_part))
    if header.get("alg") != "RS256":
        raise RuntimeError("Unexpected ID-token algorithm; this probe supports RS256 only.")
    discovery = client.get(f"{ISSUER}/.well-known/openid-configuration")
    discovery.raise_for_status()
    metadata = discovery.json()
    jwks_uri = metadata["jwks_uri"]
    if metadata["issuer"] != ISSUER or urlsplit(jwks_uri).scheme != "https":
        raise RuntimeError("Unexpected OpenID discovery metadata.")
    response = client.get(jwks_uri)
    response.raise_for_status()
    jwk = next(key for key in response.json()["keys"] if key["kid"] == header["kid"])
    key = rsa.RSAPublicNumbers(
        int.from_bytes(b64decode(jwk["e"]), "big"),
        int.from_bytes(b64decode(jwk["n"]), "big"),
    ).public_key()
    key.verify(
        b64decode(signature_part),
        f"{header_part}.{payload_part}".encode("ascii"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    claims = json.loads(b64decode(payload_part))
    audience = claims["aud"]
    audiences = audience if isinstance(audience, list) else [audience]
    if (
        claims["iss"] != ISSUER
        or client_id not in audiences
        or (len(audiences) > 1 and claims.get("azp") != client_id)
        or claims.get("azp", client_id) != client_id
        or claims["exp"] <= time.time()
        or claims.get("nbf", 0) > time.time()
        or claims.get("iat", 0) > time.time() + 60
        or claims.get("nonce") != nonce
        or not isinstance(claims.get("sub"), str)
        or not claims["sub"]
    ):
        raise RuntimeError("ID-token claims failed validation.")
    return str(claims["sub"])


def oauth_login(client: httpx.Client, no_browser: bool) -> str:
    record = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    record.setdefault("ext_agent_host_id", f"urn:uuid:{uuid.uuid4()}")
    save_registration(record)
    client_id = record.get("client_id", "dynamic_agent_client")
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    callback: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass  # Callback URLs contain authorization codes.

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            valid = (
                parsed.path == "/auth/callback"
                and len(query.get("state", [])) == 1
                and secrets.compare_digest(query["state"][0], state)
                and all(len(values) == 1 for values in query.values())
            )
            if valid:
                callback.update({key: values[0] for key, values in query.items()})
            self.send_response(200 if valid else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b"Return to the terminal." if valid else b"Invalid callback.")

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = 1
        redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
        parameters = {
            "client_id": client_id,
            "ext_agent_host_id": record["ext_agent_host_id"],
            "response_type": "code",
            "redirect_uri": redirect,
            "scope": "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
            "resource": API,
            "state": state,
            "nonce": nonce,
            "code_challenge_method": "S256",
            "code_challenge": base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode()).digest()
            ).rstrip(b"=").decode(),
        }
        if client_id == "dynamic_agent_client":
            parameters["agent_name_hint"] = "Dorker Decisions Probe"
        url = f"{ISSUER}/api/accounts/authorize?{urlencode(parameters)}"
        print("Sign in and approve ChatGPT plan usage in your browser (5-minute timeout).")
        if no_browser or not webbrowser.open(url):
            print(url)
        deadline = time.monotonic() + 300
        while not callback and time.monotonic() < deadline:
            server.handle_request()
    if not callback:
        raise RuntimeError("Login timed out. No subscription compatibility result.")
    if callback.get("error"):
        raise RuntimeError(f"OAuth failed: {callback['error']}. No inference was attempted.")
    issued_id = callback.get("client_id", client_id)
    if issued_id == "dynamic_agent_client" or (
        client_id != "dynamic_agent_client" and issued_id != client_id
    ):
        raise RuntimeError("Missing or mismatched issued client ID.")
    # Retain the registration even if code exchange fails; never save credentials.
    record["client_id"] = issued_id
    save_registration(record)
    response = client.post(
        f"{ISSUER}/api/accounts/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": issued_id,
            "code": callback["code"],
            "code_verifier": verifier,
            "redirect_uri": redirect,
            "resource": API,
        },
    )
    if response.is_error:
        # Do not print a token-endpoint body, even on failure.
        raise RuntimeError(f"Token exchange failed: HTTP {response.status_code}.")
    tokens = response.json()
    subject = validate_identity(client, tokens["id_token"], issued_id, nonce)
    if record.get("subject", subject) != subject:
        raise RuntimeError("Returning login belongs to a different identity.")
    record["subject"] = subject
    save_registration(record)
    if "chatgpt.tokens.use.direct" not in tokens.get("scope", "").split():
        raise RuntimeError("ChatGPT plan permission was not granted; no inference attempted.")
    print("ChatGPT identity and plan permission validated. Tokens remain in memory only.")
    return str(tokens["access_token"])


def responses_control(client: httpx.Client, token: str, model: str) -> bool:
    print(f"\nOAuth control: POST /v1/responses (model={model})")
    with client.stream(
        "POST", f"{API}/responses",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": model, "input": [{"role": "user", "content": "Reply OK."}],
              "store": False, "stream": True},
    ) as response:
        print(f"HTTP {response.status_code}; request_id={response.headers.get('x-request-id', '-')}")
        if response.is_error:
            response.read()
            print(safe_text(response.text, [token])[:6000])
            return False
        for line in response.iter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                event = json.loads(line[6:])
                if event.get("type") == "response.completed":
                    print("response.completed: OAuth inference works.")
                    return True
                if event.get("type") in {"response.failed", "response.incomplete", "error"}:
                    print(safe_text(json.dumps(event), [token])[:6000])
                    return False
    print("Stream ended without response.completed; control inconclusive.")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auth", choices=["api-key", "chatgpt"], default="chatgpt")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--no-browser", action="store_true", help="Print the login URL instead.")
    parser.add_argument("--model", default="gpt-6-luna", help="Decisions model to probe.")
    parser.add_argument("--control-model", help="Optional Responses control model override.")
    args = parser.parse_args()
    sensitive: list[str] = []
    try:
        with httpx.Client(timeout=60, follow_redirects=False) as client:
            token = load_api_key(args.env_file) if args.auth == "api-key" else oauth_login(
                client, args.no_browser
            )
            sensitive.append(token)
            control_ok = False
            if args.auth == "chatgpt":
                control_model = args.control_model
                if not control_model:
                    catalog = client.get(f"{API}/models", headers={"Authorization": f"Bearer {token}"})
                    if catalog.is_success:
                        models = catalog.json().get("models", [])
                        control_model = next(
                            (model["slug"] for model in models if model.get("visibility") == "list"),
                            None,
                        )
                    else:
                        report(catalog, sensitive)
                if control_model:
                    control_ok = responses_control(client, token, control_model)
                else:
                    print("No control model discovered; use --control-model to select one.")
            print(f"\nDecisions probe: auth={args.auth}; model={args.model}")
            response = client.post(
                f"{API}/decisions",
                headers={"Authorization": f"Bearer {token}"},
                json={"model": args.model, "input": SAMPLE, "questions": [
                    {"type": "predicate", "name": "requires_python",
                     "instructions": "Does this job require Python?"}
                ]},
            )
            report(response, sensitive)
            if response.is_success and response.json().get("answers"):
                print("RESULT: Decisions returned answers with " + args.auth + " authentication.")
                if args.auth == "api-key":
                    print("This tests API billing only; ChatGPT subscription support is untested.")
                return 0
            if args.auth == "chatgpt" and control_ok:
                print("RESULT: OAuth Responses works, but this Decisions request did not succeed.")
            else:
                print("RESULT: Probe failed; do not infer general subscription support from this.")
            return 1
    except (
        httpx.HTTPError, OSError, ValueError, KeyError, StopIteration, RuntimeError, InvalidSignature
    ) as error:
        print(safe_text(f"Probe stopped ({type(error).__name__}): {error}", sensitive))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

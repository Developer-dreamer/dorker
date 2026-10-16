"""Offline checks for credential separation and compatibility-probe interpretation."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from scripts import probe_openai_decisions as probe


class DecisionsProbeTests(unittest.TestCase):
    def run_probe(self, auth: str, decisions_status: int) -> tuple[int, str, list[str]]:
        credentials: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            credentials.append(request.headers["Authorization"])
            if request.url.path == "/v1/models":
                return httpx.Response(200, json={
                    "models": [{"slug": "control-model", "visibility": "list"}]
                })
            if request.url.path == "/v1/responses":
                body = json.loads(request.content)
                self.assertFalse(body["store"])
                self.assertTrue(body["stream"])
                return httpx.Response(200, text='data: {"type":"response.completed"}\n\n')
            self.assertEqual(request.url.path, "/v1/decisions")
            self.assertEqual(json.loads(request.content)["input"], probe.SAMPLE)
            body = {"answers": [{"type": "predicate", "probability": 1}]} if (
                decisions_status == 200
            ) else {"error": {"code": "subscription_sharing_route_not_supported"}}
            return httpx.Response(decisions_status, json=body)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with (
            patch("scripts.probe_openai_decisions.httpx.Client", return_value=client),
            patch.object(probe, "oauth_login", return_value="oauth-secret"),
            patch.object(probe, "load_api_key", return_value="api-secret") as api_key,
            patch("sys.argv", ["probe", "--auth", auth]),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            code = probe.main()
            if auth == "chatgpt":
                api_key.assert_not_called()
        return code, output.getvalue(), credentials

    def test_api_key_success_does_not_claim_subscription_support(self) -> None:
        code, output, credentials = self.run_probe("api-key", 200)
        self.assertEqual(code, 0)
        self.assertIn("subscription support is untested", output)
        self.assertEqual(credentials, ["Bearer api-secret"])

    def test_oauth_success_uses_only_oauth_token(self) -> None:
        code, output, credentials = self.run_probe("chatgpt", 200)
        self.assertEqual(code, 0)
        self.assertIn("Decisions returned answers with chatgpt", output)
        self.assertEqual(credentials, ["Bearer oauth-secret"] * 3)

    def test_route_rejection_is_distinct_from_successful_control(self) -> None:
        code, output, credentials = self.run_probe("chatgpt", 403)
        self.assertEqual(code, 1)
        self.assertIn("OAuth Responses works, but this Decisions request did not succeed", output)
        self.assertIn("subscription_sharing_route_not_supported", output)
        self.assertNotIn("oauth-secret", output)
        self.assertEqual(credentials, ["Bearer oauth-secret"] * 3)

    def test_missing_key_fails_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY is missing"):
                probe.load_api_key(Path(directory) / ".env")

    def test_env_file_is_parsed_without_executing_shell(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            env_file = Path(directory) / ".env"
            env_file.write_text('export OPENAI_API_KEY="literal-$(echo unsafe)" # comment\n')
            self.assertEqual(probe.load_api_key(env_file), "literal-$(echo unsafe)")


if __name__ == "__main__":
    unittest.main()

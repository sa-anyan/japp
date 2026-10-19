"""Microsoft desktop browser sign-in using OAuth authorization code and PKCE."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx


class MicrosoftBrowserTokenProvider:
    """Use a temporary loopback callback; keep credentials and tokens in memory."""

    def __init__(self, client_id: str, tenant: str, scopes: list[str], *,
                 client: httpx.Client | None = None, browser_open=webbrowser.open,
                 output=print, timeout_seconds: float = 300):
        if not client_id:
            raise ValueError("Configure mail.outlook.client_id or JAPP_MICROSOFT_CLIENT_ID first")
        if not tenant or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for c in tenant):
            raise ValueError("Invalid Microsoft tenant")
        self.client_id, self.tenant, self.scopes = client_id, tenant, scopes
        self.client = client or httpx.Client(timeout=30)
        self.browser_open, self.output = browser_open, output
        self.timeout_seconds = timeout_seconds
        self._token = None

    def get_token(self) -> str:
        if self._token:
            return self._token
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(32)
        result = {}

        class Callback(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                supplied_state = query.get("state", [""])[0]
                if parsed.path != "/" or not hmac.compare_digest(supplied_state, state):
                    self.send_error(400, "Invalid sign-in callback")
                    return
                if query.get("error"):
                    result["error"] = query["error"][0]
                elif query.get("code"):
                    result["code"] = query["code"][0]
                else:
                    self.send_error(400, "Missing authorization result")
                    return
                body = b"JAPP received the sign-in response. Return to the terminal to check the connection."
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        with HTTPServer(("127.0.0.1", 0), Callback) as server:
            server.timeout = 0.5
            redirect = f"http://localhost:{server.server_port}"
            authority = f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0"
            url = authority + "/authorize?" + urlencode({
                "client_id": self.client_id, "response_type": "code", "redirect_uri": redirect,
                "response_mode": "query", "scope": " ".join(self.scopes), "state": state,
                "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account",
            })
            self.output("Open Microsoft sign-in and approve JAPP's requested permissions:")
            self.output(url)
            self.browser_open(url)
            deadline = time.monotonic() + self.timeout_seconds
            while not result and time.monotonic() < deadline:
                server.handle_request()
            if not result:
                raise TimeoutError("Microsoft browser sign-in expired; run connect outlook again")
        if result.get("error"):
            raise RuntimeError(f"Microsoft sign-in was not completed ({result['error']})")
        response = self.client.post(authority + "/token", data={
            "client_id": self.client_id, "grant_type": "authorization_code",
            "code": result["code"], "redirect_uri": redirect, "code_verifier": verifier,
            "scope": " ".join(self.scopes),
        })
        if not response.is_success:
            error = response.json().get("error", "token_exchange_failed")
            raise RuntimeError(f"Microsoft token exchange failed ({error}); check the app's desktop redirect configuration")
        self._token = response.json().get("access_token")
        if not self._token:
            raise RuntimeError("Microsoft sign-in did not return an access token")
        return self._token

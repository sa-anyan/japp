"""Construct only the mail provider explicitly selected in local config."""

from __future__ import annotations

import os
from typing import Any

import httpx

from .mail import (
    GmailProvider,
    GraphOutlookProvider,
    MockMailProvider,
    MicrosoftDeviceCodeTokenProvider,
)


class GoogleDeviceCodeTokenProvider:
    def __init__(self, client_id: str, client_secret: str, scopes: list[str],
                 *, client: httpx.Client | None = None, output=print):
        if not client_id or not client_secret:
            raise ValueError("Set Google OAuth client ID and secret environment variables first")
        self.client_id = client_id
        self.client_secret = client_secret
        self.scopes = scopes
        self.client = client or httpx.Client(timeout=20)
        self.output = output
        self._token = None

    def get_token(self) -> str:
        if self._token:
            return self._token
        response = self.client.post(
            "https://oauth2.googleapis.com/device/code",
            data={"client_id": self.client_id, "scope": " ".join(self.scopes)},
        )
        response.raise_for_status()
        device = response.json()
        self.output(device.get("verification_url", "Open the Google verification URL."))
        self.output(f"Enter code: {device.get('user_code', '')}")
        import time
        interval = max(1, int(device.get("interval", 5)))
        expires = time.monotonic() + int(device.get("expires_in", 900))
        while time.monotonic() < expires:
            time.sleep(interval)
            token_response = self.client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "device_code": device["device_code"],
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
            result = token_response.json()
            if token_response.is_success and result.get("access_token"):
                self._token = result["access_token"]
                return self._token
            error = result.get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            raise RuntimeError(f"Google sign-in failed: {result.get('error_description', error)}")
        raise TimeoutError("Google device-code sign-in expired")


def create_mail_provider(config: dict[str, Any], *, client: httpx.Client | None = None):
    mail = config["mail"]
    provider = mail["provider"]
    if provider == "mock":
        return MockMailProvider()
    if provider == "outlook":
        settings = mail["outlook"]
        token = MicrosoftDeviceCodeTokenProvider(
            client_id=os.environ.get(settings["client_id_env"], ""),
            tenant=settings["tenant"],
            scopes=settings["scopes"],
            client=client,
        )
        return GraphOutlookProvider(token.get_token, client=client)
    if provider == "gmail":
        settings = mail["gmail"]
        token = GoogleDeviceCodeTokenProvider(
            client_id=os.environ.get(settings["client_id_env"], ""),
            client_secret=os.environ.get(settings["client_secret_env"], ""),
            scopes=settings["scopes"],
            client=client,
        )
        return GmailProvider(token.get_token, client=client)
    raise ValueError(f"Unsupported mail provider: {provider}")
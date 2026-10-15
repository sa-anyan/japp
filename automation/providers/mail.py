"""Mock, Microsoft Graph, and Gmail REST mail providers."""

from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Callable, Protocol

import httpx


class MailProvider(Protocol):
    def create_draft(self, *, recipient: str, subject: str, body: str,
                     attachments: list[Path]) -> str: ...

    def send_draft(self, provider_id: str) -> str: ...

    def delete_draft(self, provider_id: str) -> None: ...


class DeliveryUncertain(RuntimeError):
    """The provider request may have been accepted; do not retry automatically."""


@dataclass
class MockMailProvider:
    drafts: dict[str, dict]
    sent: list[str]

    def __init__(self):
        self.drafts = {}
        self.sent = []

    def create_draft(self, *, recipient: str, subject: str, body: str,
                     attachments: list[Path]) -> str:
        message_id = f"mock-{len(self.drafts) + 1}"
        self.drafts[message_id] = {
            "recipient": recipient,
            "subject": subject,
            "body": body,
            "attachments": [str(path) for path in attachments],
        }
        return message_id

    def send_draft(self, provider_id: str) -> str:
        if provider_id not in self.drafts:
            raise ValueError(f"Unknown mock draft {provider_id}")
        self.sent.append(provider_id)
        return provider_id

    def delete_draft(self, provider_id: str) -> None:
        self.drafts.pop(provider_id, None)


class MicrosoftDeviceCodeTokenProvider:
    """Acquire a delegated Graph token using the public-client device flow."""

    def __init__(self, client_id: str, tenant: str = "common", scopes: list[str] | None = None,
                 *, client: httpx.Client | None = None, output: Callable[[str], None] = print):
        if not client_id:
            raise ValueError("Set JAPP_MICROSOFT_CLIENT_ID before Outlook authentication")
        self.client_id = client_id
        self.tenant = tenant
        self.scopes = scopes or ["Mail.ReadWrite", "Mail.Send", "User.Read", "offline_access"]
        self.client = client or httpx.Client(timeout=20)
        self.output = output
        self._token: str | None = None

    def get_token(self) -> str:
        if self._token:
            return self._token
        authority = f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0"
        response = self.client.post(
            f"{authority}/devicecode",
            data={"client_id": self.client_id, "scope": " ".join(self.scopes)},
        )
        response.raise_for_status()
        device = response.json()
        self.output(device.get("message", "Complete Microsoft device-code sign-in in your browser."))
        interval = max(1, int(device.get("interval", 5)))
        expires_at = __import__("time").monotonic() + int(device.get("expires_in", 900))
        while __import__("time").monotonic() < expires_at:
            __import__("time").sleep(interval)
            token_response = self.client.post(
                f"{authority}/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": self.client_id,
                    "device_code": device["device_code"],
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
            raise RuntimeError(f"Microsoft sign-in failed: {result.get('error_description', error)}")
        raise TimeoutError("Microsoft device-code sign-in expired; run login again")


class GraphOutlookProvider:
    def __init__(self, token_provider: Callable[[], str], *, client: httpx.Client | None = None,
                 graph_url: str = "https://graph.microsoft.com/v1.0"):
        self.token_provider = token_provider
        self.client = client or httpx.Client(timeout=30)
        self.graph_url = graph_url.rstrip("/")

    def _request(self, method: str, endpoint: str, **kwargs) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self.token_provider()}"
        try:
            response = self.client.request(method, f"{self.graph_url}{endpoint}", headers=headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise DeliveryUncertain("Microsoft Graph timed out; reconcile the message before retrying") from exc
        if response.status_code == 429:
            raise RuntimeError("Microsoft Graph rate limited the request; no automatic retry was attempted")
        response.raise_for_status()
        return response

    def create_draft(self, *, recipient: str, subject: str, body: str,
                     attachments: list[Path]) -> str:
        response = self._request("POST", "/me/messages", json={
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": recipient}}],
        })
        message_id = response.json()["id"]
        for path in attachments:
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            main_type, sub_type = content_type.split("/", 1)
            self._request("POST", f"/me/messages/{message_id}/attachments", json={
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": path.name,
                "contentType": content_type,
                "contentBytes": base64.b64encode(path.read_bytes()).decode("ascii"),
            })
        return message_id

    def send_draft(self, provider_id: str) -> str:
        self._request("POST", f"/me/messages/{provider_id}/send")
        return provider_id

    def delete_draft(self, provider_id: str) -> None:
        self._request("DELETE", f"/me/messages/{provider_id}")


class GmailProvider:
    def __init__(self, token_provider: Callable[[], str], *, client: httpx.Client | None = None,
                 api_url: str = "https://gmail.googleapis.com/gmail/v1"):
        self.token_provider = token_provider
        self.client = client or httpx.Client(timeout=30)
        self.api_url = api_url.rstrip("/")

    def _request(self, method: str, endpoint: str, **kwargs) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self.token_provider()}"
        try:
            response = self.client.request(method, f"{self.api_url}{endpoint}", headers=headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise DeliveryUncertain("Gmail API timed out; reconcile the message before retrying") from exc
        if response.status_code == 429:
            raise RuntimeError("Gmail rate limited the request; no automatic retry was attempted")
        response.raise_for_status()
        return response

    def create_draft(self, *, recipient: str, subject: str, body: str,
                     attachments: list[Path]) -> str:
        message = EmailMessage()
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        for path in attachments:
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            main_type, sub_type = content_type.split("/", 1)
            message.add_attachment(path.read_bytes(), maintype=main_type, subtype=sub_type, filename=path.name)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        response = self._request("POST", "/users/me/drafts", json={"message": {"raw": raw}})
        return response.json()["id"]

    def send_draft(self, provider_id: str) -> str:
        self._request("POST", "/users/me/drafts/send", json={"id": provider_id})
        return provider_id

    def delete_draft(self, provider_id: str) -> None:
        self._request("DELETE", f"/users/me/drafts/{provider_id}")
"""Supported Apollo people search and work-email enrichment; no website scraping."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import httpx


def domain(value: str) -> str:
    host = urlparse(value if "://" in value else f"https://{value}").hostname or ""
    host = host.casefold().removeprefix("www.")
    if not host or "." not in host or host in {"linkedin.com", "facebook.com", "apollo.io"}:
        raise ValueError("An official company website/domain is required for Apollo matching")
    return host


class ApolloAPIProvider:
    def __init__(self, config: dict[str, Any], *, client: httpx.Client | None = None):
        key = os.environ.get(config["apollo"]["api_key_env"], "")
        if not key:
            raise ValueError(f"Set {config['apollo']['api_key_env']} for the terminal Apollo API integration")
        self.key = key
        self.client = client or httpx.Client(timeout=30)

    def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.client.post(f"https://api.apollo.io/api/v1/{endpoint}", json=payload,
                                        headers={"X-Api-Key": self.key, "Cache-Control": "no-cache"})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # No request headers, response bodies, or API keys in exception output.
            code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else "transport"
            raise RuntimeError(f"Apollo {endpoint} failed ({code}); check API access or rate limits") from None
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("Apollo returned an invalid response")
        return result

    def search(self, company_domain: str, titles: list[str], limit: int) -> list[dict[str, Any]]:
        result = self._post("mixed_people/api_search", {
            "q_organization_domains_list": [company_domain], "person_titles": titles,
            "include_similar_titles": True, "page": 1, "per_page": limit,
        })
        return result.get("people", [])[:limit]

    def enrich(self, person_id: str) -> dict[str, Any]:
        # Email enrichment can consume credits. Never request personal emails,
        # phone numbers, waterfall vendors, or automatic retries.
        return self._post("people/match", {"id": person_id, "reveal_personal_emails": False,
                                           "reveal_phone_number": False})

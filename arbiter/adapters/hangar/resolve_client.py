"""Public Hangar resolve surface: POST /approvals/{id}/resolve (Hangar 2.6.0)."""

from __future__ import annotations

from typing import Any, Protocol

import httpx2


class ApprovalResolver(Protocol):
    async def resolve(
        self, approval_id: str, *, approved: bool, reason: str
    ) -> None: ...


class HttpApprovalResolver:
    """Call Hangar's public REST resolve endpoint with an authenticated principal.

    Auth (probed 2.6.0): ``X-API-Key`` (ApiKeyAuthenticator) and/or ``Authorization:
    Bearer``. Arbiter uses the API key of a principal that has ``approval:resolve``
    and nothing beyond that.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_seconds

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-API-Key": self._api_key,
        }

    async def resolve(
        self, approval_id: str, *, approved: bool, reason: str
    ) -> None:
        url = f"{self._base_url}/approvals/{approval_id}/resolve"
        body: dict[str, Any] = {
            "decision": "approve" if approved else "deny",
            "reason": reason,
        }
        async with httpx2.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, json=body, headers=self._auth_headers())
            if response.status_code >= 400:
                raise RuntimeError(
                    f"hangar resolve failed status={response.status_code} "
                    f"body={response.text[:500]}"
                )


class CallbackApprovalResolver:
    """Test/double: invoke an injected async callback (no Hangar process)."""

    def __init__(self, callback: Any) -> None:
        self._callback = callback

    async def resolve(
        self, approval_id: str, *, approved: bool, reason: str
    ) -> None:
        await self._callback(approval_id, approved, reason)

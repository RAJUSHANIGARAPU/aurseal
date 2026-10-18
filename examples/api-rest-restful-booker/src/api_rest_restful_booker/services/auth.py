"""``POST /auth``: trade the admin credentials for a token."""

from __future__ import annotations

from ..client import ApiClient


class AuthenticationFailed(Exception):
    """The API refused the credentials (it answers 200 with a reason, not 401)."""


class AuthService:
    def __init__(self, client: ApiClient) -> None:
        self.client = client

    def token(self, username: str, password: str) -> str:
        response = self.client.post("/auth", json={"username": username, "password": password})
        response.raise_for_status()
        payload = response.json()
        if "token" not in payload:
            raise AuthenticationFailed(payload.get("reason", "no token in the answer"))
        return str(payload["token"])

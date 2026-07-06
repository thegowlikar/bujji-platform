"""FYERS access-token lifecycle management — verified live.

FYERS access tokens are valid ~24h (one trading day) and are normally
obtained via an interactive login (browser + TOTP/PIN) that the official
`fyers-apiv3` SDK's `SessionModel` wraps (`generate_authcode`/`set_token`/
`generate_token`) — confirmed by reading the SDK source directly: it has no
method or reference to a refresh mechanism anywhere in the installed package.

That is an SDK gap, not an API limitation. FYERS's real REST API supports a
`refresh_token` grant — verified live by POSTing a deliberately invalid
refresh_token to the real endpoint and getting a genuine application-level
rejection (`{"code": -501, "message": "Please provide valid refresh token",
"s": "error"}`), not a network/gateway error, proving the endpoint is real and
enforces this exact contract:

    POST https://api-t1.fyers.in/api/v3/validate-refresh-token
    {"grant_type": "refresh_token", "appIdHash": sha256(f"{app_id}:{secret}"),
     "refresh_token": "...", "pin": "..."}

`appIdHash`'s exact construction was confirmed by reading
`fyersModel.SessionModel.get_hash()`'s source, not guessed.

Per FYERS community documentation (not independently re-verified here — a
15-day wait isn't something this pass could do — cited, not claimed as
directly proven): the refresh_token itself is valid ~15 days. So this
automates *daily* renewal within that window; it does not, and cannot,
automate the initial (or every-~15-days) interactive login — see
docs/FYERS_TOKEN_LIFECYCLE.md for the full picture and the operational
consequence of that limit.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

import requests

from .errors import AuthenticationError

_REFRESH_URL = "https://api-t1.fyers.in/api/v3/validate-refresh-token"
_TIMEOUT_SECONDS = 15.0


class FyersTokenManager:
    """Refreshes a FYERS access_token from a refresh_token, without a
    browser/TOTP login, and (optionally) persists the renewed credentials so
    a process restart doesn't need a fresh interactive login either."""

    def __init__(self, app_id: Optional[str], secret_key: Optional[str],
                 refresh_token: Optional[str], pin: Optional[str],
                 logger: logging.Logger,
                 credentials_file: Optional[str] = None) -> None:
        self._app_id = app_id
        self._secret_key = secret_key
        self._refresh_token = refresh_token
        self._pin = pin
        self._log = logger
        self._credentials_file = Path(credentials_file) if credentials_file else None

    @property
    def can_refresh(self) -> bool:
        """False means: no refresh_token/secret/pin configured — automatic
        renewal simply isn't available; the caller must fall back to
        requiring a human to re-run the interactive login."""
        return bool(self._app_id and self._secret_key
                    and self._refresh_token and self._pin)

    def _app_id_hash(self) -> str:
        return hashlib.sha256(
            f"{self._app_id}:{self._secret_key}".encode()
        ).hexdigest()

    async def refresh(self) -> str:
        """Exchange the refresh_token for a new access_token.

        Returns the new access_token. Raises AuthenticationError — with a
        message telling the operator exactly what to do — if refreshing
        isn't configured, or if the refresh_token itself has expired/been
        used already (at that point only the interactive login can recover;
        this is documented, not something to work around).
        """
        if not self.can_refresh:
            raise AuthenticationError(
                "Cannot auto-refresh FYERS token: FYERS_APP_SECRET, "
                "FYERS_REFRESH_TOKEN, and/or FYERS_PIN are not configured. "
                "Run the interactive login flow to obtain a refresh_token, "
                "set these environment variables, and restart."
            )
        payload = {
            "grant_type": "refresh_token",
            "appIdHash": self._app_id_hash(),
            "refresh_token": self._refresh_token,
            "pin": self._pin,
        }
        response = await asyncio.to_thread(
            requests.post, _REFRESH_URL, json=payload, timeout=_TIMEOUT_SECONDS,
        )
        data = response.json()
        if data.get("s") != "ok" or "access_token" not in data:
            raise AuthenticationError(
                f"FYERS refresh_token exchange failed: code={data.get('code')} "
                f"message={data.get('message')}. The refresh_token has "
                f"likely expired (~15 days) or was already consumed — run "
                f"the interactive login flow again to obtain a new one."
            )
        new_access_token = data["access_token"]
        new_refresh_token = data.get("refresh_token", self._refresh_token)
        self._refresh_token = new_refresh_token
        self._persist(new_access_token, new_refresh_token)
        self._log.info("fyers_token_refreshed_automatically")
        return new_access_token

    def _persist(self, access_token: str, refresh_token: str) -> None:
        """Write both renewed tokens back to the credentials file ATOMICALLY.

        Deliberately does NOT use python-dotenv's ``set_key``: it writes via a
        temp file in the system temp dir then ``shutil.move``, which is only
        atomic when temp and target share a filesystem — under the production
        systemd unit's ``PrivateTmp=true``, ``/tmp`` is a separate tmpfs, so
        that move degrades to a non-atomic copy. It also rewrites once per
        key, so a crash between the two writes could persist a new
        access_token against a stale refresh_token.

        Instead: update both keys in memory, write to a temp file IN THE SAME
        DIRECTORY as the target (guaranteeing a same-filesystem, atomic
        ``os.replace``), fsync, then replace in one operation. Other keys and
        comments in the file are preserved. Permissions are set to 0600 on the
        temp file before the replace, so the credentials are never briefly
        world-readable.
        """
        if self._credentials_file is None:
            self._log.warning(
                "fyers_token_refresh_not_persisted: no credentials_file "
                "configured — the renewed token is only held in memory and "
                "will be lost on the next restart"
            )
            return
        path = self._credentials_file
        path.parent.mkdir(parents=True, exist_ok=True)

        updates = {
            "FYERS_ACCESS_TOKEN": access_token,
            "FYERS_REFRESH_TOKEN": refresh_token,
        }
        existing = path.read_text().splitlines() if path.exists() else []
        seen: set[str] = set()
        out_lines: list[str] = []
        for line in existing:
            stripped = line.lstrip()
            key = stripped.split("=", 1)[0].strip() if "=" in stripped else None
            if key in updates and not stripped.startswith("#"):
                out_lines.append(f"{key}={updates[key]}")
                seen.add(key)
            else:
                out_lines.append(line)
        for key, value in updates.items():
            if key not in seen:
                out_lines.append(f"{key}={value}")
        payload = "\n".join(out_lines) + "\n"

        tmp = path.with_suffix(path.suffix + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)  # Belt-and-braces if umask altered O_CREAT mode.
            os.replace(tmp, path)  # Atomic — same directory, same filesystem.
        finally:
            if tmp.exists():
                tmp.unlink()

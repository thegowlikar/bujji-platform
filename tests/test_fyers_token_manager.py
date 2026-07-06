"""FyersTokenManager — automatic daily access-token renewal.

Verified live (see docs/FYERS_TOKEN_LIFECYCLE.md): the real
validate-refresh-token endpoint, hit with a deliberately invalid
refresh_token, returned a genuine application-level rejection
({"code": -501, "message": "Please provide valid refresh token", "s":
"error"}), confirming the endpoint is real and enforces this exact contract.
These tests mock `requests.post` so the suite has no live network dependency;
the contract itself (URL, payload shape, hash construction) was verified
live, not guessed.
"""
import hashlib
from unittest.mock import MagicMock, patch

import pytest

from bujji.broker.errors import AuthenticationError
from bujji.broker.fyers import FyersBroker
from bujji.broker.fyers_token_manager import FyersTokenManager, _REFRESH_URL


def _mock_response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    return resp


# ---------------------------------------------------------------------- #
# can_refresh / configuration gating
# ---------------------------------------------------------------------- #
def test_cannot_refresh_without_all_four_pieces(logger):
    assert not FyersTokenManager("app", None, "rt", "1234", logger).can_refresh
    assert not FyersTokenManager("app", "secret", None, "1234", logger).can_refresh
    assert not FyersTokenManager("app", "secret", "rt", None, logger).can_refresh
    assert not FyersTokenManager(None, "secret", "rt", "1234", logger).can_refresh


def test_can_refresh_when_fully_configured(logger):
    assert FyersTokenManager("app", "secret", "rt", "1234", logger).can_refresh


@pytest.mark.asyncio
async def test_refresh_raises_clearly_when_not_configured(logger):
    manager = FyersTokenManager(None, None, None, None, logger)
    with pytest.raises(AuthenticationError, match="not configured"):
        await manager.refresh()


# ---------------------------------------------------------------------- #
# Hash construction — verified against fyersModel.SessionModel.get_hash()
# ---------------------------------------------------------------------- #
def test_app_id_hash_matches_sdk_construction(logger):
    manager = FyersTokenManager("myapp", "mysecret", "rt", "1234", logger)
    expected = hashlib.sha256(b"myapp:mysecret").hexdigest()
    assert manager._app_id_hash() == expected  # noqa: SLF001


# ---------------------------------------------------------------------- #
# refresh(): success and failure paths, against the verified real contract
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_refresh_success_updates_token_and_posts_verified_payload(logger):
    manager = FyersTokenManager("app", "secret", "old-rt", "1234", logger)
    with patch("bujji.broker.fyers_token_manager.requests.post") as post:
        post.return_value = _mock_response({
            "s": "ok", "access_token": "NEW-TOKEN", "refresh_token": "NEW-RT",
        })
        token = await manager.refresh()

    assert token == "NEW-TOKEN"
    assert manager._refresh_token == "NEW-RT"  # noqa: SLF001 - rotated.
    call = post.call_args
    assert call.args[0] == _REFRESH_URL
    payload = call.kwargs["json"]
    assert payload["grant_type"] == "refresh_token"
    assert payload["refresh_token"] == "old-rt"
    assert payload["pin"] == "1234"
    assert payload["appIdHash"] == hashlib.sha256(b"app:secret").hexdigest()


@pytest.mark.asyncio
async def test_refresh_failure_raises_clear_authentication_error(logger):
    """Mirrors the real live rejection observed: code -501, "Please provide
    valid refresh token" — the same shape a genuinely expired (~15 day)
    refresh_token would produce."""
    manager = FyersTokenManager("app", "secret", "bad-rt", "1234", logger)
    with patch("bujji.broker.fyers_token_manager.requests.post") as post:
        post.return_value = _mock_response({
            "s": "error", "code": -501, "message": "Please provide valid refresh token",
        })
        with pytest.raises(AuthenticationError, match="expired"):
            await manager.refresh()


@pytest.mark.asyncio
async def test_refresh_without_credentials_file_warns_but_still_returns_token(logger):
    manager = FyersTokenManager("app", "secret", "rt", "1234", logger,
                                credentials_file=None)
    with patch("bujji.broker.fyers_token_manager.requests.post") as post:
        post.return_value = _mock_response({"s": "ok", "access_token": "T"})
        token = await manager.refresh()
    assert token == "T"  # In-memory renewal still works; just not persisted.


@pytest.mark.asyncio
async def test_refresh_persists_to_credentials_file(logger, tmp_path):
    cred_file = tmp_path / "creds.env"
    manager = FyersTokenManager("app", "secret", "rt", "1234", logger,
                                credentials_file=str(cred_file))
    with patch("bujji.broker.fyers_token_manager.requests.post") as post:
        post.return_value = _mock_response({
            "s": "ok", "access_token": "PERSISTED-TOKEN",
            "refresh_token": "PERSISTED-RT",
        })
        await manager.refresh()

    content = cred_file.read_text()
    assert "FYERS_ACCESS_TOKEN=PERSISTED-TOKEN" in content
    assert "FYERS_REFRESH_TOKEN=PERSISTED-RT" in content


@pytest.mark.asyncio
async def test_persist_is_atomic_preserves_other_keys_and_sets_0600(logger, tmp_path):
    """Atomic write must: update both token keys in one replace, preserve
    other keys/comments, leave no .tmp file behind, and be owner-only (0600)."""
    import os
    import stat

    cred_file = tmp_path / "creds.env"
    cred_file.write_text(
        "# bujji credentials\n"
        "FYERS_APP_ID=myapp\n"
        "FYERS_ACCESS_TOKEN=OLD-TOKEN\n"
        "FYERS_PIN=1234\n"
    )
    manager = FyersTokenManager("app", "secret", "rt", "1234", logger,
                                credentials_file=str(cred_file))
    with patch("bujji.broker.fyers_token_manager.requests.post") as post:
        post.return_value = _mock_response({
            "s": "ok", "access_token": "NEW-TOKEN", "refresh_token": "NEW-RT",
        })
        await manager.refresh()

    content = cred_file.read_text()
    # Updated in place (not duplicated) and other keys/comments preserved.
    assert content.count("FYERS_ACCESS_TOKEN=") == 1
    assert "FYERS_ACCESS_TOKEN=NEW-TOKEN" in content
    assert "FYERS_REFRESH_TOKEN=NEW-RT" in content  # New key appended.
    assert "FYERS_APP_ID=myapp" in content          # Preserved.
    assert "FYERS_PIN=1234" in content              # Preserved.
    assert "# bujji credentials" in content         # Comment preserved.
    # No temp file left behind.
    assert not (tmp_path / "creds.env.tmp").exists()
    # Owner read/write only.
    mode = stat.S_IMODE(os.stat(cred_file).st_mode)
    assert mode == 0o600, oct(mode)


# ---------------------------------------------------------------------- #
# FyersBroker.connect(): auto-refresh-then-retry integration
# ---------------------------------------------------------------------- #
class _RefreshableFakeFyers(FyersBroker):
    """profile() fails once with an expired-token shape, then succeeds —
    simulating exactly the scenario connect() must recover from automatically."""

    def __init__(self, config, logger):
        super().__init__(config, logger)
        self.profile_calls = 0

    async def _call(self, action, **params):
        if action == "profile":
            self.profile_calls += 1
            if self.profile_calls == 1:
                return {"s": "error", "code": -8, "message": "Your token has expired"}
            return {"s": "ok", "code": 200}
        raise AssertionError(f"unexpected call: {action}")


@pytest.mark.asyncio
async def test_connect_auto_refreshes_on_expired_token_when_configured(config, logger):
    config.broker.app_id = "app"
    config.broker.access_token = "stale-token"
    config.broker.app_secret = "secret"
    config.broker.refresh_token = "rt"
    config.broker.pin = "1234"

    broker = _RefreshableFakeFyers(config.broker, logger)
    with patch("bujji.broker.fyers_token_manager.requests.post") as post:
        post.return_value = _mock_response({
            "s": "ok", "access_token": "FRESH-TOKEN", "refresh_token": "FRESH-RT",
        })
        await broker.connect()  # Must not raise.

    assert broker.profile_calls == 2  # Failed once, refreshed, succeeded.
    assert config.broker.access_token == "FRESH-TOKEN"


@pytest.mark.asyncio
async def test_connect_still_fails_clearly_when_refresh_not_configured(config, logger):
    """No refresh_token/secret/pin configured — must raise the ORIGINAL
    AuthenticationError unchanged, not silently swallow it."""
    config.broker.app_id = "app"
    config.broker.access_token = "stale-token"
    # app_secret/refresh_token/pin intentionally left unset.

    broker = _RefreshableFakeFyers(config.broker, logger)
    with pytest.raises(AuthenticationError, match="expired"):
        await broker.connect()
    assert broker.profile_calls == 1  # No refresh attempted — not configured.

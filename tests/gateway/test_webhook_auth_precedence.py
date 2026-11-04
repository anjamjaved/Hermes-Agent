"""Legacy scheme compatibility and the new Bearer fallback boundary."""
import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from multidict import CIMultiDict

from gateway.config import PlatformConfig
from gateway.platforms.webhook import WebhookAdapter
from tests.gateway.fixtures.webhook_route_authority import mount_authority

SECRET = "synthetic-secret"
BODY = b'{}'
SIGNATURE = hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()
STRONGER = ["svix-signature", "webhook-signature", "linear-signature",
            "X-Hub-Signature-256", "X-Gitlab-Token", "X-Webhook-Signature-V2"]


def adapter():
    return WebhookAdapter(PlatformConfig(enabled=True, extra={
        "host": "127.0.0.1", "port": 0,
        "routes": {"probe": {"secret": SECRET, "deliver": "log"}},
    }))


def validate(headers):
    request = MagicMock()
    request.headers = CIMultiDict(headers)
    request.match_info = {"route_name": "probe"}
    return adapter()._validate_signature(request, BODY, SECRET)


@pytest.mark.parametrize("name", STRONGER)
@pytest.mark.parametrize("bearer", [False, True])
def test_empty_earlier_header_preserves_valid_v1(name, bearer):
    headers = {name: "", "X-Webhook-Signature": SIGNATURE}
    if bearer:
        headers["Authorization"] = "Bearer " + SECRET
    assert validate(headers)


@pytest.mark.parametrize("name", STRONGER + ["X-Webhook-Signature"])
@pytest.mark.parametrize("value", ["", "invalid"])
def test_invalid_or_empty_signature_does_not_fall_back_to_bearer(name, value):
    assert not validate({name: value, "Authorization": "Bearer " + SECRET})


@pytest.mark.parametrize("bearer", [False, True])
def test_nonempty_v2_without_timestamp_never_falls_back(bearer):
    headers = {"X-Webhook-Signature-V2": "invalid", "X-Webhook-Signature": SIGNATURE}
    if bearer:
        headers["Authorization"] = "Bearer " + SECRET
    assert not validate(headers)


@pytest.mark.asyncio
async def test_http_legacy_compatibility_and_bearer_boundary():
    a = adapter()
    a.handle_message = AsyncMock()
    app = web.Application(client_max_size=a._max_body_bytes)
    mount_authority(app, a)
    app.router.add_post('/webhooks/{route_name}', a._handle_webhook)
    async with TestClient(TestServer(app)) as client:
        for bearer in (False, True):
            headers = {"X-Webhook-Signature-V2": "", "X-Webhook-Signature": SIGNATURE}
            if bearer:
                headers["Authorization"] = "Bearer " + SECRET
            response = await client.post('/webhooks/probe', data=BODY, headers=headers)
            assert response.status == 202
        response = await client.post('/webhooks/probe', data=BODY, headers={
            "X-Webhook-Signature-V2": "", "Authorization": "Bearer " + SECRET,
        })
        assert response.status == 401
        response = await client.post('/webhooks/probe', data=BODY, headers={
            "X-Webhook-Signature-V2": "invalid", "X-Webhook-Signature": SIGNATURE,
            "Authorization": "Bearer " + SECRET,
        })
        assert response.status == 401
    assert a.handle_message.await_count == 2

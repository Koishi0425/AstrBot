from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.core.platform.sources.weixin_oc import weixin_oc_adapter
from astrbot.core.platform.sources.weixin_oc.weixin_oc_adapter import WeixinOCAdapter


def _build_adapter(responses: list[dict]) -> WeixinOCAdapter:
    """Build a minimal adapter for outbound retry tests.

    Args:
        responses: API payloads returned by consecutive send attempts.

    Returns:
        Adapter configured with mocked delivery dependencies.
    """
    adapter = object.__new__(WeixinOCAdapter)
    adapter.metadata = SimpleNamespace(id="weixin-test")
    adapter.token = "bot-token"
    adapter.account_id = "account-id"
    adapter._context_tokens = {"user-id": "context-token"}
    adapter.client = SimpleNamespace(request_json=AsyncMock(side_effect=responses))
    adapter._cache_recent_message = MagicMock()
    return adapter


@pytest.mark.asyncio
async def test_send_retries_prepare_failure_and_caches_success(monkeypatch):
    adapter = _build_adapter(
        [
            {"ret": -2, "errcode": 0, "errmsg": "prepare failed"},
            {"ret": 0, "errcode": 0},
        ]
    )
    sleep = AsyncMock()
    monkeypatch.setattr(weixin_oc_adapter.asyncio, "sleep", sleep)

    sent = await adapter._send_items_to_session(
        "user-id",
        [{"type": 1, "text_item": {"text": "hello"}}],
    )

    assert sent is True
    assert adapter.client.request_json.await_count == 2
    sleep.assert_awaited_once_with(1)
    adapter._cache_recent_message.assert_called_once()


@pytest.mark.asyncio
async def test_send_stops_after_prepare_failure_retries(monkeypatch):
    adapter = _build_adapter(
        [{"ret": -2, "errcode": 0, "errmsg": "prepare failed"}] * 3
    )
    sleep = AsyncMock()
    monkeypatch.setattr(weixin_oc_adapter.asyncio, "sleep", sleep)

    sent = await adapter._send_items_to_session(
        "user-id",
        [{"type": 1, "text_item": {"text": "hello"}}],
    )

    assert sent is False
    assert adapter.client.request_json.await_count == 3
    assert [call.args[0] for call in sleep.await_args_list] == [1, 3]
    adapter._cache_recent_message.assert_not_called()


@pytest.mark.asyncio
async def test_send_does_not_retry_other_failures(monkeypatch):
    adapter = _build_adapter([{"ret": -3, "errcode": 0, "errmsg": "denied"}])
    sleep = AsyncMock()
    monkeypatch.setattr(weixin_oc_adapter.asyncio, "sleep", sleep)

    sent = await adapter._send_items_to_session(
        "user-id",
        [{"type": 1, "text_item": {"text": "hello"}}],
    )

    assert sent is False
    adapter.client.request_json.assert_awaited_once()
    sleep.assert_not_awaited()
    adapter._cache_recent_message.assert_not_called()


@pytest.mark.asyncio
async def test_send_reuses_client_id_across_retries(monkeypatch):
    payloads: list[dict] = []

    async def request_json(*_args, **kwargs):
        payloads.append(deepcopy(kwargs["payload"]))
        if len(payloads) == 1:
            return {"ret": -2, "errcode": 0, "errmsg": " PREPARE FAILED "}
        return {"ret": 0, "errcode": 0}

    adapter = _build_adapter([])
    adapter.client.request_json = AsyncMock(side_effect=request_json)
    monkeypatch.setattr(weixin_oc_adapter.asyncio, "sleep", AsyncMock())

    sent = await adapter._send_items_to_session(
        "user-id",
        [{"type": 1, "text_item": {"text": "hello"}}],
    )

    assert sent is True
    assert len(payloads) == 2
    assert payloads[0]["msg"]["client_id"] == payloads[1]["msg"]["client_id"]

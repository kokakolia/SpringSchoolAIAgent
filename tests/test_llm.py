import json

import httpx
import pytest

from src.config import settings
from src.llm import ModelUnavailable, YandexClient


def mock_provider(monkeypatch, handler):
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(settings, "yandex_api_key", "test-key")
    monkeypatch.setattr(settings, "yandex_folder_id", "test-folder")
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original_client(transport=transport, **kw)
    )


async def test_tokenizer_uses_same_model_and_messages(monkeypatch):
    messages = [{"role": "user", "text": "Привет"}]

    def handler(request):
        assert request.url.path.endswith("/tokenizeCompletion")
        payload = json.loads(request.content)
        assert payload["messages"] == messages
        assert payload["modelUri"] == "gpt://test-folder/yandexgpt/latest"
        return httpx.Response(200, json={"tokens": [{"id": "1"}, {"id": "2"}]})

    mock_provider(monkeypatch, handler)
    assert await YandexClient().count_tokens(messages) == 2


@pytest.mark.parametrize("envelope", [False, True])
async def test_complete_supports_response_envelopes_and_records_usage(monkeypatch, envelope):
    def handler(request):
        data = {
            "alternatives": [{"status": "ALTERNATIVE_STATUS_FINAL", "message": {"text": "Ответ"}}],
            "usage": {"inputTextTokens": "15", "completionTokens": "2"},
        }
        return httpx.Response(200, json={"result": data} if envelope else data)

    mock_provider(monkeypatch, handler)
    text, usage = await YandexClient().complete([{"role": "user", "text": "Вопрос"}])
    assert text == "Ответ"
    assert usage["inputTextTokens"] == "15"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "alternatives": [
                {
                    "status": "ALTERNATIVE_STATUS_TRUNCATED_FINAL",
                    "message": {"text": "Незавершённый JSON"},
                }
            ]
        },
        {"alternatives": []},
        [],
        {"result": None},
        {"alternatives": [{"status": "ALTERNATIVE_STATUS_FINAL", "message": {"text": 123}}]},
        {"alternatives": [{"status": "ALTERNATIVE_STATUS_FINAL", "message": {"text": None}}]},
    ],
)
async def test_invalid_and_truncated_responses_fail_explicitly(monkeypatch, payload):
    mock_provider(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with pytest.raises(ModelUnavailable):
        await YandexClient().complete([{"role": "user", "text": "Вопрос"}])


async def test_summary_rejects_invalid_json(monkeypatch):
    def handler(request):
        if request.url.path.endswith("tokenizeCompletion"):
            return httpx.Response(200, json={"tokens": []})
        assert json.loads(request.content)["jsonObject"] is True
        return httpx.Response(
            200,
            json={
                "alternatives": [
                    {
                        "status": "ALTERNATIVE_STATUS_FINAL",
                        "message": {"text": '{"unexpected":true}'},
                    }
                ]
            },
        )

    mock_provider(monkeypatch, handler)
    with pytest.raises(ModelUnavailable, match="некорректную краткую память"):
        await YandexClient().summarize([{"role": "user", "text": "Вопрос"}])


async def test_oversized_history_is_summarized_losslessly_in_bounded_parts(monkeypatch):
    from src.memory_schemas import MemorySummary

    monkeypatch.setattr(settings, "context_budget_tokens", 1000)
    monkeypatch.setattr(settings, "response_reserve_tokens", 100)
    seen, carried = [], []

    class ChunkClient(YandexClient):
        async def count_tokens(self, messages):
            return len(messages[-1]["text"]) + 100

        async def complete(self, messages, *, json_mode=False):
            assert json_mode
            assert await self.count_tokens(messages) + 100 <= 1000
            data = json.loads(messages[-1]["text"])
            seen.extend(message["text"] for message in data["messages"])
            carried.append(data["previous_summary"])
            return MemorySummary(
                goal="Ship", user_objections=["Keep animation"]
            ).model_dump_json(), {}

    original = [{"role": "user", "text": "x" * 2500}, {"role": "assistant", "text": "y" * 500}]
    result = await ChunkClient().summarize(original)
    assert "".join(seen) == "x" * 2500 + "y" * 500
    assert carried[0] is None and all(item is not None for item in carried[1:])
    assert result.user_objections == ["Keep animation"]
    assert original[0]["text"] == "x" * 2500


async def test_partial_compression_failure_never_returns_a_partial_summary(monkeypatch):
    from src.memory_schemas import MemorySummary

    monkeypatch.setattr(settings, "context_budget_tokens", 1000)
    monkeypatch.setattr(settings, "response_reserve_tokens", 100)

    class FailingClient(YandexClient):
        calls = 0

        async def count_tokens(self, messages):
            return len(messages[-1]["text"]) + 100

        async def complete(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 2:
                raise ModelUnavailable("Synthetic failure")
            return MemorySummary(goal="Ship").model_dump_json(), {}

    with pytest.raises(ModelUnavailable, match="Synthetic failure"):
        await FailingClient().summarize([{"role": "user", "text": "x" * 2500}])

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

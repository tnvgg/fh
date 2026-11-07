import pytest

from flakehunt import ai


class FakeResp:
    status_code = 200

    def json(self):
        return {"choices": [{"message": {"content": "<think>hidden</think>Answer"}}]}


def test_disabled_without_key(monkeypatch):
    monkeypatch.delenv("BHARATCODE_API_KEY", raising=False)
    assert not ai.enabled()
    with pytest.raises(ai.AIError):
        ai.chat("hi")


def test_chat_strips_think_blocks_and_uses_bharatcode_url(monkeypatch):
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen["url"], seen["auth"], seen["model"] = url, headers["Authorization"], json["model"]
        return FakeResp()

    monkeypatch.setenv("BHARATCODE_API_KEY", "k123")
    monkeypatch.setattr(ai.requests, "post", fake_post)
    assert ai.chat("hi") == "Answer"
    assert seen["url"].startswith("https://bharatcode.ai/") and seen["url"].endswith("/chat/completions")
    assert seen["auth"] == "Bearer k123" and seen["model"] == "qwen-3.8-27b"

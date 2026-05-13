"""Tests for the OpenAI LLM provider."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import patch, MagicMock
from src.llm import provider as pmod
import config


def test_get_active_provider():
    assert pmod.get_active_provider() == "openai"


def test_get_model_default():
    assert pmod.get_model(use_simple=False) == config.OPENAI_MODEL_EXTRACTION
    assert pmod.get_model(use_simple=True)  == config.OPENAI_MODEL_SIMPLE


def test_provider_status_keys():
    status = pmod.provider_status()
    assert status["active"] == "openai"
    assert "openai_key_set" in status
    assert "models" in status
    assert "extraction" in status["models"]
    assert "fallback"   in status["models"]


def test_complete_text_calls_openai(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-fake")
    pmod._client = None   # reset lazy client
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "hello world"
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

    with patch("openai.OpenAI", return_value=mock_client):
        result = pmod.complete_text("sys", "user")
    assert result == "hello world"


def test_complete_json_strict_schema(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-fake")
    pmod._client = None
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = '{"segments": [{"name": "A"}], "total_revenue": 100}'
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    with patch("openai.OpenAI", return_value=mock_client):
        result = pmod.complete_json("sys", "user", schema=schema)

    # Verify response_format was passed with json_schema + strict
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["response_format"]["type"] == "json_schema"
    assert call_kwargs["response_format"]["json_schema"]["strict"] is True
    assert result["total_revenue"] == 100


def test_complete_json_escalates_on_empty(monkeypatch):
    """If primary model returns empty-ish JSON, fallback model is tried."""
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-fake")
    pmod._client = None
    mock_client = MagicMock()

    empty_choice    = MagicMock(); empty_choice.message.content    = '{"segments": []}'
    good_choice     = MagicMock(); good_choice.message.content     = '{"segments": [{"n":"A"}], "total_revenue": 5}'
    mock_client.chat.completions.create.side_effect = [
        MagicMock(choices=[empty_choice]),
        MagicMock(choices=[good_choice]),
    ]
    schema = {"type": "object"}
    with patch("openai.OpenAI", return_value=mock_client):
        result = pmod.complete_json("sys", "user", schema=schema)
    assert result["total_revenue"] == 5
    assert mock_client.chat.completions.create.call_count == 2


def test_complete_raises_without_key(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    pmod._client = None
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        pmod.complete_text("s", "u")

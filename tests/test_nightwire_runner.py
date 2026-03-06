"""Tests for NightwireRunner — M2 structured output, naming cleanup, metadata.

Covers:
- ask() happy path and error handling
- ask_with_metadata() returns AssistantResponse model
- ask_structured() with Pydantic model validation and fallback
- AssistantResponse model parsing
- Message prefix cleaning
- Deprecated ask_jarvis alias
- Removed backward-compat aliases (SidechannelRunner, etc.)
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from nightwire.nightwire_runner import (
    AssistantResponse,
    NightwireRunner,
)

# ---------------------------------------------------------------------------
# Test Pydantic model for structured output
# ---------------------------------------------------------------------------


class WeatherResponse(BaseModel):
    """Simple model for testing ask_structured."""

    city: str
    temperature: float
    unit: str


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_api_response(
    content: str = "Hello!",
    model: str = "grok-3-latest",
    total_tokens: int = 42,
) -> dict:
    """Build a mock OpenAI-compatible API response dict."""
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"total_tokens": total_tokens},
        "model": model,
    }


class MockResponse:
    """Mock aiohttp response context manager."""

    def __init__(self, status: int, json_data: dict = None, text: str = ""):
        self.status = status
        self._json_data = json_data
        self._text = text

    async def json(self):
        return self._json_data

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


@pytest.fixture
def runner():
    """Create a NightwireRunner with a mock session."""
    with patch("nightwire.nightwire_runner.logger"):
        r = NightwireRunner(
            api_url="https://api.example.com/v1/chat/completions",
            api_key="test-key-123",
            model="test-model",
            max_tokens=1024,
        )
    # Inject mock session
    r._session = MagicMock()
    r._session.closed = False
    return r


def _set_mock_response(runner, status=200, json_data=None, text=""):
    """Configure the runner's mock session to return a specific response."""
    mock_resp = MockResponse(status=status, json_data=json_data, text=text)
    runner._session.post = MagicMock(return_value=mock_resp)


# ---------------------------------------------------------------------------
# ask() tests
# ---------------------------------------------------------------------------


async def test_ask_happy_path(runner):
    """ask() should return (True, content_string) on success."""
    _set_mock_response(runner, json_data=_make_api_response("The answer is 42."))

    success, response = await runner.ask("What is the answer?")

    assert success is True
    assert response == "The answer is 42."


async def test_ask_passes_full_long_response(runner):
    """ask() should return full response without truncation."""
    long_content = "x" * 5000
    _set_mock_response(runner, json_data=_make_api_response(long_content))

    success, response = await runner.ask("Long question")

    assert success is True
    assert len(response) == 5000


async def test_ask_api_error_status(runner):
    """ask() should return (False, error_msg) on non-200 status."""
    _set_mock_response(runner, status=500, text="Internal Server Error")

    success, response = await runner.ask("test")

    assert success is False
    assert "status 500" in response


async def test_ask_no_api_key():
    """ask() should fail gracefully when API key is empty."""
    with patch("nightwire.nightwire_runner.logger"):
        r = NightwireRunner(
            api_url="https://api.example.com/v1/chat/completions",
            api_key="",
            model="test-model",
        )

    success, response = await r.ask("test")

    assert success is False
    assert "not configured" in response


async def test_ask_malformed_response(runner):
    """ask() should handle malformed API responses (no choices)."""
    _set_mock_response(runner, json_data={"error": "something"})

    success, response = await runner.ask("test")

    assert success is False
    assert "unexpected response" in response.lower()


async def test_ask_empty_content(runner):
    """ask() should handle empty content in API response."""
    data = {"choices": [{"message": {"content": ""}}], "model": "m"}
    _set_mock_response(runner, json_data=data)

    success, response = await runner.ask("test")

    assert success is False


async def test_ask_timeout(runner):
    """ask() should handle timeout errors."""
    runner._session.post = MagicMock(side_effect=asyncio.TimeoutError())

    success, response = await runner.ask("test", timeout=1)

    assert success is False
    assert "processing time" in response


async def test_ask_generic_exception(runner):
    """ask() should handle unexpected exceptions."""
    runner._session.post = MagicMock(side_effect=ConnectionError("refused"))

    success, response = await runner.ask("test")

    assert success is False
    assert "temporary issue" in response


# ---------------------------------------------------------------------------
# Message cleaning tests
# ---------------------------------------------------------------------------


async def test_ask_strips_nightwire_prefix(runner):
    """ask() should strip 'nightwire:' prefix from message."""
    _set_mock_response(runner, json_data=_make_api_response("response"))

    await runner.ask("nightwire: What is Python?")

    # Verify the payload sent to the API has the cleaned message
    call_args = runner._session.post.call_args
    payload = call_args.kwargs.get("json") or call_args[1].get("json")
    user_msg = payload["messages"][1]["content"]
    assert user_msg == "What is Python?"


async def test_ask_strips_sidechannel_prefix(runner):
    """ask() should strip 'sidechannel:' prefix (backward compat)."""
    _set_mock_response(runner, json_data=_make_api_response("response"))

    await runner.ask("sidechannel: Hello")

    call_args = runner._session.post.call_args
    payload = call_args.kwargs.get("json") or call_args[1].get("json")
    user_msg = payload["messages"][1]["content"]
    assert user_msg == "Hello"


async def test_ask_bare_nightwire_gets_default(runner):
    """ask() should use default greeting for bare 'nightwire' message."""
    _set_mock_response(runner, json_data=_make_api_response("Hi!"))

    await runner.ask("nightwire")

    call_args = runner._session.post.call_args
    payload = call_args.kwargs.get("json") or call_args[1].get("json")
    user_msg = payload["messages"][1]["content"]
    assert user_msg == "Hello, how can you help me?"


# ---------------------------------------------------------------------------
# ask_with_metadata() tests
# ---------------------------------------------------------------------------


async def test_ask_with_metadata_returns_model(runner):
    """ask_with_metadata() should return AssistantResponse on success."""
    api_data = _make_api_response("Paris is great.", model="gpt-4o", total_tokens=100)
    _set_mock_response(runner, json_data=api_data)

    success, result = await runner.ask_with_metadata("Tell me about Paris")

    assert success is True
    assert isinstance(result, AssistantResponse)
    assert result.content == "Paris is great."
    assert result.tokens_used == 100
    assert result.model == "gpt-4o"


async def test_ask_with_metadata_error_returns_string(runner):
    """ask_with_metadata() should return error string on failure."""
    _set_mock_response(runner, status=503, text="Overloaded")

    success, result = await runner.ask_with_metadata("test")

    assert success is False
    assert isinstance(result, str)
    assert "status 503" in result


async def test_ask_with_metadata_truncates_long_content(runner):
    """ask_with_metadata() should return full content without truncation."""
    long_content = "a" * 5000
    _set_mock_response(runner, json_data=_make_api_response(long_content))

    success, result = await runner.ask_with_metadata("test")

    assert success is True
    assert isinstance(result, AssistantResponse)
    assert len(result.content) == 5000


# ---------------------------------------------------------------------------
# ask_structured() tests
# ---------------------------------------------------------------------------


async def test_ask_structured_happy_path(runner):
    """ask_structured() should parse JSON into Pydantic model."""
    json_content = json.dumps({"city": "Tokyo", "temperature": 22.5, "unit": "celsius"})
    _set_mock_response(runner, json_data=_make_api_response(json_content))

    success, result = await runner.ask_structured(
        "What's the weather in Tokyo?",
        response_model=WeatherResponse,
    )

    assert success is True
    assert isinstance(result, WeatherResponse)
    assert result.city == "Tokyo"
    assert result.temperature == 22.5
    assert result.unit == "celsius"


async def test_ask_structured_sends_response_format(runner):
    """ask_structured() should include response_format in the API payload."""
    json_content = json.dumps({"city": "NYC", "temperature": 15.0, "unit": "fahrenheit"})
    _set_mock_response(runner, json_data=_make_api_response(json_content))

    await runner.ask_structured("weather?", response_model=WeatherResponse)

    call_args = runner._session.post.call_args
    payload = call_args.kwargs.get("json") or call_args[1].get("json")
    assert payload["response_format"] == {"type": "json_object"}


async def test_ask_structured_parse_failure(runner):
    """ask_structured() should return error when JSON doesn't match model."""
    bad_json = json.dumps({"wrong_field": "value"})
    _set_mock_response(runner, json_data=_make_api_response(bad_json))

    success, result = await runner.ask_structured("test", response_model=WeatherResponse)

    assert success is False
    assert isinstance(result, str)
    assert "parse" in result.lower() or "validation" in result.lower()


async def test_ask_structured_api_error(runner):
    """ask_structured() should propagate API errors."""
    _set_mock_response(runner, status=429, text="Rate limited")

    success, result = await runner.ask_structured("test", response_model=WeatherResponse)

    assert success is False
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# AssistantResponse model tests
# ---------------------------------------------------------------------------


class TestAssistantResponse:
    """Tests for the AssistantResponse Pydantic model."""

    def test_full_fields(self):
        r = AssistantResponse(content="hello", tokens_used=50, model="gpt-4o")
        assert r.content == "hello"
        assert r.tokens_used == 50
        assert r.model == "gpt-4o"

    def test_optional_tokens(self):
        r = AssistantResponse(content="hi", model="grok-3")
        assert r.tokens_used is None

    def test_serialization_roundtrip(self):
        r = AssistantResponse(content="test", tokens_used=10, model="m")
        data = r.model_dump()
        r2 = AssistantResponse.model_validate(data)
        assert r == r2


# ---------------------------------------------------------------------------
# Deprecated alias tests
# ---------------------------------------------------------------------------


async def test_ask_jarvis_alias_works(runner):
    """ask_jarvis should still work as a deprecated alias for ask."""
    _set_mock_response(runner, json_data=_make_api_response("alias works"))

    success, response = await runner.ask_jarvis("test")

    assert success is True
    assert response == "alias works"


# ---------------------------------------------------------------------------
# Removed alias tests
# ---------------------------------------------------------------------------


def test_sidechannel_runner_alias_removed():
    """SidechannelRunner alias should no longer exist."""
    import nightwire.nightwire_runner as mod
    assert not hasattr(mod, "SidechannelRunner")


def test_get_sidechannel_runner_alias_removed():
    """get_sidechannel_runner alias should no longer exist."""
    import nightwire.nightwire_runner as mod
    assert not hasattr(mod, "get_sidechannel_runner")


def test_sidechannel_runner_error_alias_removed():
    """SidechannelRunnerError alias should no longer exist in exceptions."""
    import nightwire.exceptions as mod
    assert not hasattr(mod, "SidechannelRunnerError")


# ---------------------------------------------------------------------------
# Constructor validation tests
# ---------------------------------------------------------------------------


def test_constructor_rejects_http():
    """NightwireRunner should reject non-HTTPS URLs."""
    with patch("nightwire.nightwire_runner.logger"):
        with pytest.raises(ValueError, match="HTTPS"):
            NightwireRunner(
                api_url="http://api.example.com/v1/chat",
                api_key="key",
                model="m",
            )


def test_constructor_rejects_no_hostname():
    """NightwireRunner should reject URLs without a hostname."""
    with patch("nightwire.nightwire_runner.logger"):
        with pytest.raises(ValueError, match="hostname"):
            NightwireRunner(
                api_url="https:///v1/chat",
                api_key="key",
                model="m",
            )

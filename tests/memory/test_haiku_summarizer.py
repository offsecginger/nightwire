"""Tests for HaikuSummarizer CLI-based implementation."""

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from nightwire.memory.haiku_summarizer import (
    HaikuSummarizer,
    close_summarizer,
)
from nightwire.memory.models import SearchResult


def _make_search_result(content="test memory", role="user"):
    """Create a SearchResult for testing."""
    return SearchResult(
        id=1,
        content=content,
        role=role,
        timestamp=datetime(2026, 1, 15, 10, 0),
        project_name="test_project",
        similarity_score=0.9,
        source_type="conversation",
    )


def _make_cli_response(result_text="Summary: key points"):
    """Create a mock CLI JSON response as bytes."""
    response = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": result_text,
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }
    return json.dumps(response).encode("utf-8")


def _make_mock_process(stdout_bytes=None, returncode=0):
    """Create a mock async subprocess process."""
    mock = AsyncMock()
    if stdout_bytes is None:
        stdout_bytes = _make_cli_response()
    mock.communicate = AsyncMock(
        return_value=(stdout_bytes, b"")
    )
    mock.returncode = returncode
    return mock


class TestHaikuSummarizer:
    @patch("nightwire.config.get_config")
    @patch("asyncio.create_subprocess_exec")
    async def test_summarize_returns_text(
        self, mock_exec, mock_config,
    ):
        mock_config.return_value.claude_path = "claude"
        mock_exec.return_value = _make_mock_process(
            _make_cli_response("Key insight from past")
        )

        summarizer = HaikuSummarizer()
        result = await summarizer.summarize_for_context(
            [_make_search_result()], "current query"
        )
        assert result == "Key insight from past"

    async def test_summarize_empty_memories_returns_none(self):
        summarizer = HaikuSummarizer()
        result = await summarizer.summarize_for_context(
            [], "query"
        )
        assert result is None

    @patch("nightwire.config.get_config")
    @patch("asyncio.create_subprocess_exec")
    async def test_summarize_timeout_returns_none(
        self, mock_exec, mock_config,
    ):
        mock_config.return_value.claude_path = "claude"
        mock_exec.return_value = _make_mock_process()
        mock_exec.return_value.communicate = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )

        summarizer = HaikuSummarizer(timeout=1)
        result = await summarizer.summarize_for_context(
            [_make_search_result()], "query"
        )
        assert result is None

    @patch("nightwire.config.get_config")
    @patch("asyncio.create_subprocess_exec")
    async def test_summarize_error_returns_none(
        self, mock_exec, mock_config,
    ):
        mock_config.return_value.claude_path = "claude"
        mock_exec.side_effect = OSError("No such file")

        summarizer = HaikuSummarizer()
        result = await summarizer.summarize_for_context(
            [_make_search_result()], "query"
        )
        assert result is None

    async def test_close_is_noop(self):
        summarizer = HaikuSummarizer()
        await summarizer.close()  # Should not raise
        await summarizer.close()  # Idempotent

    @patch("nightwire.config.get_config")
    @patch("asyncio.create_subprocess_exec")
    async def test_summarize_truncates_long_memories(
        self, mock_exec, mock_config,
    ):
        mock_config.return_value.claude_path = "claude"
        mock_exec.return_value = _make_mock_process(
            _make_cli_response("summary")
        )

        long_memory = _make_search_result(content="x" * 1000)
        summarizer = HaikuSummarizer()
        result = await summarizer.summarize_for_context(
            [long_memory], "query"
        )
        assert result == "summary"
        mock_exec.assert_called_once()

    @patch("nightwire.config.get_config")
    @patch("asyncio.create_subprocess_exec")
    async def test_empty_response_returns_none(
        self, mock_exec, mock_config,
    ):
        mock_config.return_value.claude_path = "claude"
        mock_exec.return_value = _make_mock_process(
            _make_cli_response("")
        )

        summarizer = HaikuSummarizer()
        result = await summarizer.summarize_for_context(
            [_make_search_result()], "query"
        )
        assert result is None

    @patch("nightwire.config.get_config")
    @patch("asyncio.create_subprocess_exec")
    async def test_model_flag_passed(
        self, mock_exec, mock_config,
    ):
        """Verify --model haiku is passed to CLI."""
        mock_config.return_value.claude_path = "claude"
        mock_exec.return_value = _make_mock_process()

        summarizer = HaikuSummarizer(model="haiku")
        await summarizer.summarize_for_context(
            [_make_search_result()], "query"
        )
        call_args = mock_exec.call_args[0]
        assert "--model" in call_args
        idx = list(call_args).index("--model")
        assert call_args[idx + 1] == "haiku"

    @patch("nightwire.config.get_config")
    @patch("asyncio.create_subprocess_exec")
    async def test_non_json_stdout_returns_none(
        self, mock_exec, mock_config,
    ):
        """Non-JSON stdout is handled gracefully."""
        mock_config.return_value.claude_path = "claude"
        mock_exec.return_value = _make_mock_process(
            b"not json at all"
        )

        summarizer = HaikuSummarizer()
        result = await summarizer.summarize_for_context(
            [_make_search_result()], "query"
        )
        assert result is None


class TestCloseSummarizer:
    async def test_close_summarizer_function(self):
        import nightwire.memory.haiku_summarizer as mod

        mock_summarizer = MagicMock()
        mock_summarizer.close = AsyncMock()
        mod._summarizer = mock_summarizer

        await close_summarizer()
        mock_summarizer.close.assert_called_once()
        assert mod._summarizer is None

    async def test_close_summarizer_noop_when_none(self):
        import nightwire.memory.haiku_summarizer as mod

        mod._summarizer = None
        await close_summarizer()
        assert mod._summarizer is None

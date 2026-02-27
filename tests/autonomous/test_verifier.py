"""Tests for verifier security prompt."""

from unittest.mock import MagicMock

from nightwire.autonomous.models import Task
from nightwire.autonomous.verifier import VerificationAgent


def test_verification_prompt_includes_security_questions():
    """Verification prompt must explicitly ask about backdoors, miners, exfil."""
    db = MagicMock()
    agent = VerificationAgent.__new__(VerificationAgent)
    agent.db = db
    agent.config = MagicMock()
    agent._cache = {}

    task = MagicMock(spec=Task)
    task.title = "Add feature"
    task.description = "Implement user login"

    prompt = agent._build_verification_prompt(
        task=task,
        claude_output="done",
        files_changed=["auth.py"],
        acceptance_criteria="Users can log in",
        git_diff="+def login():\n+    pass",
    )

    assert "backdoor" in prompt.lower()
    assert "miner" in prompt.lower() or "cryptocurrency" in prompt.lower()
    assert "exfil" in prompt.lower() or "data exfiltration" in prompt.lower()

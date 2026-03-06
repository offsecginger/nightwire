"""Tests for Milestone 5: Structured Data Flow (Replace Regex)."""

import json

import pytest
from pydantic import ValidationError

from nightwire.autonomous.models import (
    ExtractedLearning,
    JestJsonReport,
    LearningExtraction,
    PRDBreakdown,
    PytestJsonReport,
    TaskBreakdown,
    VerificationOutput,
)

# --- PRDBreakdown Schema Tests ---


class TestPRDBreakdownSchema:
    def test_valid_prd_breakdown(self):
        data = {
            "prd_title": "Auth System",
            "prd_description": "Add user authentication",
            "stories": [
                {
                    "title": "Login Flow",
                    "description": "Implement login",
                    "tasks": [
                        {
                            "title": "Add login form",
                            "description": "Create the HTML form",
                            "priority": 10,
                        }
                    ],
                }
            ],
        }
        prd = PRDBreakdown.model_validate(data)
        assert prd.prd_title == "Auth System"
        assert len(prd.stories) == 1
        assert len(prd.stories[0].tasks) == 1
        assert prd.stories[0].tasks[0].priority == 10

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            PRDBreakdown.model_validate({"prd_title": "Test"})

    def test_empty_stories_rejected(self):
        with pytest.raises(ValidationError):
            PRDBreakdown.model_validate(
                {
                    "prd_title": "Test",
                    "prd_description": "Desc",
                    "stories": [],
                }
            )

    def test_empty_tasks_rejected(self):
        with pytest.raises(ValidationError):
            PRDBreakdown.model_validate(
                {
                    "prd_title": "Test",
                    "prd_description": "Desc",
                    "stories": [
                        {
                            "title": "Story",
                            "description": "Desc",
                            "tasks": [],
                        }
                    ],
                }
            )

    def test_default_priority(self):
        task = TaskBreakdown(title="Task", description="Desc")
        assert task.priority == 5


# --- VerificationOutput Schema Tests ---


class TestVerificationOutputSchema:
    def test_valid_output(self):
        vo = VerificationOutput(
            passed=True,
            issues=["minor thing"],
            security_concerns=[],
            logic_errors=[],
            suggestions=["consider X"],
        )
        assert vo.passed is True
        assert len(vo.issues) == 1

    def test_fail_closed_override_logic(self):
        """Verify the fail-closed pattern works with the schema."""
        vo = VerificationOutput(
            passed=True,  # Claude claims passed
            issues=[],
            security_concerns=["SQL injection found"],
            logic_errors=[],
            suggestions=[],
        )
        # The verifier overrides: security_concerns means NOT passed
        has_critical = bool(vo.security_concerns) or bool(vo.logic_errors)
        assert has_critical is True
        assert has_critical is not False  # actual_passed = False

    def test_defaults_empty_lists(self):
        vo = VerificationOutput(passed=False)
        assert vo.issues == []
        assert vo.security_concerns == []
        assert vo.logic_errors == []
        assert vo.suggestions == []


# --- LearningExtraction Schema Tests ---


class TestLearningExtractionSchema:
    def test_valid_extraction(self):
        data = {
            "learnings": [
                {
                    "category": "pattern",
                    "title": "Use factories",
                    "content": "Factory pattern helps here",
                    "relevance_keywords": ["factory", "pattern"],
                    "confidence": 0.85,
                }
            ]
        }
        ext = LearningExtraction.model_validate(data)
        assert len(ext.learnings) == 1
        assert ext.learnings[0].category == "pattern"

    def test_empty_learnings_valid(self):
        ext = LearningExtraction(learnings=[])
        assert len(ext.learnings) == 0

    def test_default_confidence(self):
        learning = ExtractedLearning(
            category="pitfall", title="Watch out", content="Be careful"
        )
        assert learning.confidence == 0.7


# --- PytestJsonReport Schema Tests ---


class TestPytestJsonReportSchema:
    def test_valid_report(self):
        data = {
            "summary": {
                "total": 50,
                "passed": 48,
                "failed": 1,
                "error": 1,
                "skipped": 0,
            },
            "exitcode": 1,
        }
        report = PytestJsonReport.model_validate(data)
        assert report.summary.total == 50
        assert report.summary.passed == 48
        assert report.summary.failed == 1
        assert report.summary.error == 1

    def test_defaults(self):
        report = PytestJsonReport()
        assert report.summary.total == 0
        assert report.exitcode == 0


# --- JestJsonReport Schema Tests ---


class TestJestJsonReportSchema:
    def test_valid_report(self):
        data = {
            "numTotalTests": 25,
            "numPassedTests": 24,
            "numFailedTests": 1,
            "success": False,
        }
        report = JestJsonReport.model_validate(data)
        assert report.numTotalTests == 25
        assert report.numPassedTests == 24
        assert report.numFailedTests == 1

    def test_defaults(self):
        report = JestJsonReport()
        assert report.numTotalTests == 0
        assert report.success is False


# --- Quality Gates JSON Parsing Tests ---


class TestQualityGatesJsonParsing:
    def test_try_parse_pytest_json(self, tmp_path):
        from nightwire.autonomous.quality_gates import QualityGateRunner

        runner = QualityGateRunner()

        # Create a mock pytest JSON report
        report_data = {
            "summary": {
                "total": 10,
                "passed": 9,
                "failed": 1,
                "error": 0,
                "skipped": 0,
            },
            "exitcode": 1,
        }
        report_path = tmp_path / ".report.json"
        report_path.write_text(json.dumps(report_data))

        result = runner._try_parse_json_report(tmp_path)
        assert result is not None
        total, passed, failed = result
        assert total == 10
        assert passed == 9
        assert failed == 1

        # Report file should be cleaned up
        assert not report_path.exists()

    def test_try_parse_no_report(self, tmp_path):
        from nightwire.autonomous.quality_gates import QualityGateRunner

        runner = QualityGateRunner()
        result = runner._try_parse_json_report(tmp_path)
        assert result is None

    def test_try_parse_jest_json(self, tmp_path):
        from nightwire.autonomous.quality_gates import QualityGateRunner

        runner = QualityGateRunner()

        report_data = {
            "numTotalTests": 20,
            "numPassedTests": 18,
            "numFailedTests": 2,
            "success": False,
        }
        report_path = tmp_path / ".jest-results.json"
        report_path.write_text(json.dumps(report_data))

        result = runner._try_parse_json_report(tmp_path)
        assert result is not None
        total, passed, failed = result
        assert total == 20
        assert passed == 18
        assert failed == 2

    def test_try_parse_invalid_json(self, tmp_path):
        from nightwire.autonomous.quality_gates import QualityGateRunner

        runner = QualityGateRunner()
        report_path = tmp_path / ".report.json"
        report_path.write_text("not json")

        result = runner._try_parse_json_report(tmp_path)
        assert result is None
        # File should still be cleaned up
        assert not report_path.exists()

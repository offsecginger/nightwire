"""Quality gate runner for autonomous tasks.

Runs automated checks (tests, type checking, linting, security
scanning) against a project after task implementation. Supports
baseline comparison to distinguish pre-existing failures from
regressions introduced by the current task.

Auto-detects the appropriate test runner, type checker, and
linter based on project files (Python, Node.js, Rust, Go).
Prefers JSON test reports when available (pytest-json-report,
Jest --json) over regex-based output parsing.

Classes:
    QualityGateRunner: Runs quality checks and aggregates
        results into a QualityGateResult model.
"""

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import structlog

from .models import QualityGateResult

logger = structlog.get_logger("nightwire.autonomous")

# Dangerous patterns for security scanning
_DANGEROUS_PATTERNS = [
    (r'\bos\.system\s*\(', "os.system() call — use subprocess with argument list instead"),
    (r'\bos\.popen\s*\(', "os.popen() call — use subprocess with argument list instead"),
    (r'subprocess\.\w+\([^)]*shell\s*=\s*True', "subprocess with shell=True — use argument list"),
    (r'\beval\s*\(', "eval() call — potential code injection"),
    (r'\bexec\s*\(', "exec() call — potential code injection"),
    (r'__import__\s*\(', "__import__() call — suspicious dynamic import"),
    (r'(?:API_KEY|SECRET|PASSWORD|TOKEN)\s*=\s*["\'][^"\']{8,}["\']',
     "Possible hardcoded secret/API key"),
    (r'requests\.(?:get|post|put|delete)\s*\(\s*["\']https?://\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}',
     "HTTP request to raw IP address — possible data exfiltration"),
    (r'urllib\.request\.urlopen\s*\(\s*["\']https?://\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}',
     "URL request to raw IP address — possible data exfiltration"),
    (r'\bpickle\.loads?\s*\(', "pickle.load() — deserializing untrusted data is dangerous"),
]


class QualityGateRunner:
    """Runs quality checks (tests, typecheck, lint) on projects.

    Auto-detects the appropriate commands for the project's
    language/framework. Supports baseline comparison to detect
    regressions vs pre-existing failures.
    """

    def __init__(
        self,
        test_timeout: int = 300,  # 5 minutes
        typecheck_timeout: int = 120,  # 2 minutes
        lint_timeout: int = 60,  # 1 minute
    ):
        """Initialize the quality gate runner.

        Args:
            test_timeout: Max seconds for test execution.
            typecheck_timeout: Max seconds for type checking.
            lint_timeout: Max seconds for linting.
        """
        self.test_timeout = test_timeout
        self.typecheck_timeout = typecheck_timeout
        self.lint_timeout = lint_timeout
        self._json_report_cache: dict = {}  # project_path_str -> bool

    def security_scan(self, project_path: Path) -> list:
        """Scan Python files for dangerous code patterns.

        Checks for eval/exec, shell injection, hardcoded secrets,
        raw IP HTTP requests, and pickle deserialization.

        Args:
            project_path: Root directory to scan recursively.

        Returns:
            List of finding strings (``file:line: description``).
            Empty list if no issues found.
        """
        findings = []

        # Scan Python files only
        for py_file in project_path.rglob("*.py"):
            # Skip venv, __pycache__, .git
            parts = py_file.parts
            skip_dirs = ("venv", ".venv", "__pycache__", ".git", "node_modules")
            if any(skip in parts for skip in skip_dirs):
                continue

            try:
                content = py_file.read_text(errors="replace")
            except OSError:
                continue

            relative = py_file.relative_to(project_path)
            for line_num, line in enumerate(content.splitlines(), 1):
                for pattern, description in _DANGEROUS_PATTERNS:
                    if re.search(pattern, line):
                        findings.append(
                            f"{relative}:{line_num}: {description}"
                        )

        return findings

    async def snapshot_baseline(self, project_path: Path) -> Optional[QualityGateResult]:
        """Take a test baseline snapshot BEFORE task execution.

        Runs tests only (no typecheck/lint) to capture the
        pre-task test state for regression detection.

        Args:
            project_path: Root directory of the project.

        Returns:
            QualityGateResult with test counts, or None if no
            test runner was detected.
        """
        logger.info("baseline_snapshot_start", path=str(project_path))
        return await self.run(project_path, run_tests=True, run_typecheck=False, run_lint=False)

    async def run(
        self,
        project_path: Path,
        run_tests: bool = True,
        run_typecheck: bool = True,
        run_lint: bool = False,
        baseline: Optional[QualityGateResult] = None,
    ) -> QualityGateResult:
        """Run all enabled quality gates and aggregate results.

        When a baseline is provided and tests fail, compares
        the failure count against the baseline to detect
        regressions (new failures only).

        Args:
            project_path: Root directory of the project.
            run_tests: Run test suite.
            run_typecheck: Run type checker.
            run_lint: Run linter.
            baseline: Pre-task test snapshot for comparison.

        Returns:
            Aggregated QualityGateResult.
        """
        start_time = datetime.now()

        test_result = None
        typecheck_result = None
        lint_result = None

        # Run enabled checks
        if run_tests:
            test_result = await self._run_tests(project_path)

        if run_typecheck:
            typecheck_result = await self._run_typecheck(project_path)

        if run_lint:
            lint_result = await self._run_lint(project_path)

        # Aggregate results
        tests_passed = test_result[0] if test_result else True
        typecheck_passed = typecheck_result[0] if typecheck_result else True
        lint_passed = lint_result[0] if lint_result else True

        overall_passed = tests_passed and typecheck_passed and lint_passed

        # Baseline comparison: if tests were already failing before task,
        # only fail if we introduced NEW failures
        tests_failed_count = test_result[3] if test_result else 0
        regression_detected = False
        if baseline and test_result and not tests_passed:
            baseline_failures = baseline.tests_failed or 0
            new_failures = tests_failed_count - baseline_failures
            if new_failures <= 0:
                # No new failures - tests were already failing before this task
                logger.info(
                    "baseline_comparison_pass",
                    baseline_failures=baseline_failures,
                    current_failures=tests_failed_count,
                )
                tests_passed = True
                overall_passed = tests_passed and typecheck_passed and lint_passed
            else:
                regression_detected = True
                logger.warning(
                    "regression_detected",
                    baseline_failures=baseline_failures,
                    current_failures=tests_failed_count,
                    new_failures=new_failures,
                )

        logger.debug(
            "quality_gate_details",
            tests_passed=tests_passed,
            typecheck_passed=typecheck_passed,
            lint_passed=lint_passed,
            total_tests=test_result[1] if test_result else 0,
            overall_passed=overall_passed,
        )

        return QualityGateResult(
            passed=overall_passed,
            tests_run=test_result[1] if test_result else 0,
            tests_passed=test_result[2] if test_result else 0,
            tests_failed=tests_failed_count,
            test_output=test_result[4] if test_result else None,
            typecheck_passed=typecheck_passed if typecheck_result else None,
            typecheck_output=typecheck_result[1] if typecheck_result else None,
            lint_passed=lint_passed if lint_result else None,
            lint_output=lint_result[1] if lint_result else None,
            execution_time_seconds=(datetime.now() - start_time).total_seconds(),
            regression_detected=regression_detected,
        )

    async def _run_tests(
        self, project_path: Path
    ) -> Optional[Tuple[bool, int, int, int, str]]:
        """Run project tests. Returns (passed, total, passed_count, failed_count, output)."""
        test_cmd = self._detect_test_command(project_path)

        if not test_cmd:
            logger.info("no_test_runner_detected", path=str(project_path))
            return None

        logger.info("running_tests", command=test_cmd, path=str(project_path))

        try:
            process = await asyncio.create_subprocess_exec(
                *test_cmd,
                cwd=str(project_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=self.test_timeout
            )

            output = stdout.decode("utf-8", errors="replace")
            passed = process.returncode == 0

            # Try JSON report parsing first, fall back to regex
            json_result = self._try_parse_json_report(project_path)
            if json_result is not None:
                total, passed_count, failed_count = json_result
            else:
                total, passed_count, failed_count = self._parse_test_counts(
                    output, test_cmd[0]
                )

            logger.info(
                "tests_completed",
                passed=passed,
                total=total,
                passed_count=passed_count,
                failed_count=failed_count,
            )

            # Truncate output for storage
            return (passed, total, passed_count, failed_count, output[-2000:])

        except asyncio.TimeoutError:
            logger.warning("test_timeout", timeout=self.test_timeout)
            return (False, 0, 0, 0, f"Test timeout exceeded ({self.test_timeout}s)")
        except FileNotFoundError as e:
            logger.warning("test_runner_not_found", error=str(e))
            return None
        except (OSError, RuntimeError) as e:
            logger.error("test_error", error=str(e), exc_type=type(e).__name__)
            return (False, 0, 0, 0, f"Test execution failed [{type(e).__name__}]: {e}")

    async def _run_typecheck(
        self, project_path: Path
    ) -> Optional[Tuple[bool, str]]:
        """Run type checking. Returns (passed, output)."""
        typecheck_cmd = self._detect_typecheck_command(project_path)

        if not typecheck_cmd:
            logger.info("no_typecheck_detected", path=str(project_path))
            return None

        logger.info("running_typecheck", command=typecheck_cmd, path=str(project_path))

        try:
            process = await asyncio.create_subprocess_exec(
                *typecheck_cmd,
                cwd=str(project_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=self.typecheck_timeout
            )

            output = stdout.decode("utf-8", errors="replace")
            passed = process.returncode == 0

            logger.info("typecheck_completed", passed=passed)

            return (passed, output[-1500:])

        except asyncio.TimeoutError:
            logger.warning("typecheck_timeout", timeout=self.typecheck_timeout)
            return (False, f"Typecheck timeout exceeded ({self.typecheck_timeout}s)")
        except FileNotFoundError as e:
            logger.warning("typecheck_not_found", error=str(e))
            return None
        except (OSError, RuntimeError) as e:
            logger.error("typecheck_error", error=str(e), exc_type=type(e).__name__)
            return (False, f"Typecheck failed [{type(e).__name__}]: {e}")

    async def _run_lint(
        self, project_path: Path
    ) -> Optional[Tuple[bool, str]]:
        """Run linter. Returns (passed, output)."""
        lint_cmd = self._detect_lint_command(project_path)

        if not lint_cmd:
            return None

        logger.info("running_lint", command=lint_cmd, path=str(project_path))

        try:
            process = await asyncio.create_subprocess_exec(
                *lint_cmd,
                cwd=str(project_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=self.lint_timeout
            )

            output = stdout.decode("utf-8", errors="replace")
            passed = process.returncode == 0

            return (passed, output[-1000:])

        except asyncio.TimeoutError:
            return (False, f"Lint timeout exceeded ({self.lint_timeout}s)")
        except FileNotFoundError:
            return None
        except (OSError, RuntimeError) as e:
            return (False, f"Lint failed [{type(e).__name__}]: {e}")

    def _detect_test_command(self, project_path: Path) -> Optional[List[str]]:
        """Detect appropriate test command for the project.

        Checks for Python (pytest), Node.js (npm/jest), Rust
        (cargo), and Go (go test) projects in that order.

        Args:
            project_path: Root directory of the project.

        Returns:
            Command as argument list, or None if not detected.
        """
        # Python projects
        is_python = (
            (project_path / "pytest.ini").exists()
            or (project_path / "setup.py").exists()
            or (project_path / "tests").is_dir()
        )
        if not is_python and (project_path / "pyproject.toml").exists():
            try:
                content = (project_path / "pyproject.toml").read_text()
                if "pytest" in content or "tool.pytest" in content:
                    is_python = True
            except (OSError, UnicodeDecodeError):
                pass
            if not is_python:
                is_python = True  # Default for pyproject.toml

        if is_python:
            cmd = ["python", "-m", "pytest", "-v", "--tb=short"]
            # Add JSON report if plugin is available
            if self._has_json_report(project_path):
                report_path = str(project_path / ".report.json")
                cmd.extend([
                    "--json-report",
                    f"--json-report-file={report_path}",
                ])
            return cmd

        # Node.js projects
        if (project_path / "package.json").exists():
            try:
                import json as _json
                pkg = _json.loads(
                    (project_path / "package.json").read_text()
                )
                if "scripts" in pkg and "test" in pkg["scripts"]:
                    test_script = pkg["scripts"]["test"]
                    # Jest supports --json output
                    if "jest" in test_script.lower():
                        result_path = str(
                            project_path / ".jest-results.json"
                        )
                        return [
                            "npx", "jest", "--json",
                            f"--outputFile={result_path}",
                        ]
                    return ["npm", "test"]
            except (OSError, UnicodeDecodeError, _json.JSONDecodeError, KeyError):
                pass

        # Rust projects
        if (project_path / "Cargo.toml").exists():
            return ["cargo", "test"]

        # Go projects
        if (project_path / "go.mod").exists():
            return ["go", "test", "./..."]

        return None

    def _has_json_report(self, project_path: Path) -> bool:
        """Check if pytest-json-report plugin is available.

        Result is cached per project path to avoid repeated
        subprocess spawns.

        Args:
            project_path: Root directory of the project.

        Returns:
            True if the plugin is installed and usable.
        """
        cache_key = str(project_path)
        if cache_key in self._json_report_cache:
            return self._json_report_cache[cache_key]

        import subprocess
        try:
            result = subprocess.run(
                ["python", "-m", "pytest", "--json-report", "--co", "-q"],
                capture_output=True, timeout=10, cwd=str(project_path),
            )
            # Exit code 4 = usage error (plugin not found)
            available = result.returncode != 4
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            available = False

        self._json_report_cache[cache_key] = available
        return available

    def _detect_typecheck_command(self, project_path: Path) -> Optional[List[str]]:
        """Detect appropriate type checker for the project.

        Checks for mypy (Python), tsc (TypeScript), and cargo
        check (Rust).

        Args:
            project_path: Root directory of the project.

        Returns:
            Command as argument list, or None if not detected.
        """
        # Python with mypy
        if (project_path / "mypy.ini").exists():
            return ["python", "-m", "mypy", "."]
        if (project_path / "pyproject.toml").exists():
            try:
                content = (project_path / "pyproject.toml").read_text()
                if "mypy" in content:
                    return ["python", "-m", "mypy", "."]
            except (OSError, UnicodeDecodeError):
                pass

        # TypeScript
        if (project_path / "tsconfig.json").exists():
            return ["npx", "tsc", "--noEmit"]

        # Rust (cargo check)
        if (project_path / "Cargo.toml").exists():
            return ["cargo", "check"]

        return None

    def _detect_lint_command(self, project_path: Path) -> Optional[List[str]]:
        """Detect appropriate linter for the project.

        Checks for ruff (Python), eslint (JS/TS), and cargo
        clippy (Rust).

        Args:
            project_path: Root directory of the project.

        Returns:
            Command as argument list, or None if not detected.
        """
        # Python with ruff
        if (project_path / "ruff.toml").exists():
            return ["python", "-m", "ruff", "check", "."]
        if (project_path / "pyproject.toml").exists():
            try:
                content = (project_path / "pyproject.toml").read_text()
                if "ruff" in content:
                    return ["python", "-m", "ruff", "check", "."]
            except (OSError, UnicodeDecodeError):
                pass

        # ESLint
        if (project_path / ".eslintrc.js").exists() or (
            project_path / ".eslintrc.json"
        ).exists():
            return ["npx", "eslint", "."]

        # Rust clippy
        if (project_path / "Cargo.toml").exists():
            return ["cargo", "clippy"]

        return None

    def _try_parse_json_report(
        self, project_path: Path,
    ) -> Optional[Tuple[int, int, int]]:
        """Try parsing JSON test report (pytest or Jest).

        Checks for ``.report.json`` (pytest-json-report) and
        ``.jest-results.json`` (Jest --json). Cleans up the
        report file after parsing.

        Args:
            project_path: Root directory of the project.

        Returns:
            Tuple of (total, passed, failed) or None if no
            report file exists or parsing fails.
        """
        import json as _json

        from .models import JestJsonReport, PytestJsonReport

        # pytest-json-report
        pytest_report = project_path / ".report.json"
        if pytest_report.exists():
            try:
                data = _json.loads(pytest_report.read_text())
                report = PytestJsonReport.model_validate(data)
                total = report.summary.total
                passed_count = report.summary.passed
                failed_count = (
                    report.summary.failed + report.summary.error
                )
                logger.debug(
                    "pytest_json_report_parsed",
                    total=total, passed=passed_count, failed=failed_count,
                )
                return (total, passed_count, failed_count)
            except Exception as e:
                logger.debug("pytest_json_parse_failed", error=str(e))
            finally:
                pytest_report.unlink(missing_ok=True)

        # Jest --json output
        jest_report = project_path / ".jest-results.json"
        if jest_report.exists():
            try:
                data = _json.loads(jest_report.read_text())
                report = JestJsonReport.model_validate(data)
                total = report.numTotalTests
                passed_count = report.numPassedTests
                failed_count = report.numFailedTests
                logger.debug(
                    "jest_json_report_parsed",
                    total=total, passed=passed_count, failed=failed_count,
                )
                return (total, passed_count, failed_count)
            except Exception as e:
                logger.debug("jest_json_parse_failed", error=str(e))
            finally:
                jest_report.unlink(missing_ok=True)

        return None

    def _parse_test_counts(
        self, output: str, runner: str
    ) -> Tuple[int, int, int]:
        """Parse test counts from runner output using regex.

        Supports pytest, npm/Jest, cargo test, and go test
        output formats.

        Args:
            output: Raw test runner stdout.
            runner: First element of the test command (e.g.,
                ``"python"``, ``"npm"``, ``"cargo"``).

        Returns:
            Tuple of (total, passed, failed).
        """
        total = 0
        passed = 0
        failed = 0

        if "pytest" in runner or "python" in runner:
            # pytest format: "5 passed, 2 failed" or "5 passed"
            match = re.search(r"(\d+) passed", output)
            if match:
                passed = int(match.group(1))

            match = re.search(r"(\d+) failed", output)
            if match:
                failed = int(match.group(1))

            match = re.search(r"(\d+) error", output)
            if match:
                failed += int(match.group(1))

            total = passed + failed

        elif "npm" in runner:
            # Various formats from Jest, Mocha, etc.
            match = re.search(r"Tests:\s*(\d+) passed", output)
            if match:
                passed = int(match.group(1))

            match = re.search(r"Tests:\s*(\d+) failed", output)
            if match:
                failed = int(match.group(1))

            total = passed + failed

        elif "cargo" in runner:
            # Rust test format: "test result: ok. 5 passed; 0 failed"
            match = re.search(r"(\d+) passed", output)
            if match:
                passed = int(match.group(1))

            match = re.search(r"(\d+) failed", output)
            if match:
                failed = int(match.group(1))

            total = passed + failed

        elif "go" in runner:
            # Go test format: "ok" or "FAIL"
            passed = output.count("\n--- PASS:")
            failed = output.count("\n--- FAIL:")
            total = passed + failed

        return (total, passed, failed)

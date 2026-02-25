"""Pydantic models for the autonomous task system."""

from datetime import datetime
from enum import Enum
from typing import Optional, List, Any
from pydantic import BaseModel, Field


class PRDStatus(str, Enum):
    """Status of a Product Requirements Document."""
    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class StoryStatus(str, Enum):
    """Status of a user story."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class TaskStatus(str, Enum):
    """Status of an autonomous task."""
    PENDING = "pending"
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    RUNNING_TESTS = "running_tests"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class EffortLevel(str, Enum):
    """Claude effort level for adaptive thinking."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"


class TaskType(str, Enum):
    """Task type for effort level mapping."""
    PRD_BREAKDOWN = "prd_breakdown"
    IMPLEMENTATION = "implementation"
    BUG_FIX = "bug_fix"
    REFACTOR = "refactor"
    TESTING = "testing"
    VERIFICATION = "verification"


class LearningCategory(str, Enum):
    """Category of extracted learning."""
    PATTERN = "pattern"
    PITFALL = "pitfall"
    BEST_PRACTICE = "best_practice"
    PROJECT_CONTEXT = "project_context"
    DEBUGGING = "debugging"
    ARCHITECTURE = "architecture"
    TESTING = "testing"
    TOOL_USAGE = "tool_usage"


class PRD(BaseModel):
    """Product Requirements Document."""

    id: Optional[int] = None
    phone_number: str = Field(..., description="Owner's phone number (E.164)")
    project_name: str = Field(..., description="Associated project name")
    title: str = Field(..., description="PRD title")
    description: str = Field(..., description="Full PRD description")
    status: PRDStatus = Field(default=PRDStatus.DRAFT)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    metadata: Optional[dict[str, Any]] = None

    # Computed/joined fields (not stored in DB)
    stories: List["Story"] = Field(default_factory=list)
    total_stories: int = 0
    completed_stories: int = 0
    failed_stories: int = 0


class Story(BaseModel):
    """User story within a PRD."""

    id: Optional[int] = None
    prd_id: int = Field(..., description="Parent PRD ID")
    phone_number: str = Field(..., description="Owner's phone number")
    title: str = Field(..., description="Story title")
    description: str = Field(..., description="Story description")
    acceptance_criteria: Optional[List[str]] = Field(
        default=None, description="List of acceptance criteria"
    )
    priority: int = Field(default=0, description="Execution priority (higher = first)")
    story_order: int = Field(default=0, description="Order within PRD")
    status: StoryStatus = Field(default=StoryStatus.PENDING)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    embedding_id: Optional[int] = None
    metadata: Optional[dict[str, Any]] = None

    # Computed/joined fields
    tasks: List["Task"] = Field(default_factory=list)
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0


class Task(BaseModel):
    """Atomic task for autonomous execution."""

    id: Optional[int] = None
    story_id: int = Field(..., description="Parent story ID")
    phone_number: str = Field(..., description="Owner's phone number")
    project_name: str = Field(..., description="Project to execute in")
    title: str = Field(..., description="Task title")
    description: str = Field(..., description="Detailed task description")
    task_order: int = Field(default=0, description="Order within story")
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    priority: int = Field(default=0, description="Execution priority")
    retry_count: int = Field(default=0, description="Current retry count")
    max_retries: int = Field(default=2, description="Maximum retry attempts")
    effort_level: Optional[EffortLevel] = Field(
        default=None, description="Claude effort level override (auto-detected if None)"
    )
    task_type: Optional[TaskType] = Field(
        default=None, description="Task type for effort mapping (auto-detected if None)"
    )
    depends_on: Optional[List[int]] = Field(
        default=None, description="Task IDs this task depends on (for parallel execution)"
    )
    created_at: datetime = Field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    claude_output: Optional[str] = None
    files_changed: Optional[List[str]] = Field(
        default=None, description="List of modified file paths"
    )
    quality_gate_results: Optional[dict[str, Any]] = None
    verification_result: Optional[dict[str, Any]] = None
    embedding_id: Optional[int] = None
    metadata: Optional[dict[str, Any]] = None


class Learning(BaseModel):
    """Persistent learning extracted from task execution."""

    id: Optional[int] = None
    phone_number: str = Field(..., description="Owner's phone number")
    project_name: Optional[str] = Field(
        default=None, description="Associated project (optional)"
    )
    task_id: Optional[int] = Field(
        default=None, description="Source task ID (nullable for manual)"
    )
    category: LearningCategory = Field(..., description="Learning category")
    title: str = Field(..., description="Brief learning title")
    content: str = Field(..., description="Full learning content")
    relevance_keywords: Optional[List[str]] = Field(
        default=None, description="Keywords for search matching"
    )
    usage_count: int = Field(default=0, description="Times used in context")
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Confidence score"
    )
    created_at: datetime = Field(default_factory=datetime.now)
    last_used: Optional[datetime] = None
    embedding_id: Optional[int] = None
    is_active: bool = Field(default=True, description="Whether learning is active")
    metadata: Optional[dict[str, Any]] = None


class QualityGateResult(BaseModel):
    """Results from quality gate checks."""

    passed: bool = Field(..., description="Overall pass/fail")
    tests_run: int = Field(default=0, description="Total tests executed")
    tests_passed: int = Field(default=0, description="Tests that passed")
    tests_failed: int = Field(default=0, description="Tests that failed")
    test_output: Optional[str] = Field(
        default=None, description="Test runner output (truncated)"
    )
    typecheck_passed: Optional[bool] = Field(
        default=None, description="Type check result"
    )
    typecheck_output: Optional[str] = Field(
        default=None, description="Type checker output"
    )
    lint_passed: Optional[bool] = Field(default=None, description="Linter result")
    lint_output: Optional[str] = Field(default=None, description="Linter output")
    execution_time_seconds: float = Field(
        default=0.0, description="Total gate execution time"
    )
    regression_detected: bool = Field(
        default=False, description="Whether new test failures were introduced vs baseline"
    )


class VerificationResult(BaseModel):
    """Result from independent verification agent."""

    passed: bool = Field(..., description="Whether verification passed")
    issues: List[str] = Field(
        default_factory=list, description="Issues found during verification"
    )
    security_concerns: List[str] = Field(
        default_factory=list, description="Security issues found"
    )
    logic_errors: List[str] = Field(
        default_factory=list, description="Logic errors found"
    )
    suggestions: List[str] = Field(
        default_factory=list, description="Improvement suggestions"
    )
    verification_output: str = Field(
        default="", description="Full verification output"
    )
    execution_time_seconds: float = Field(
        default=0.0, description="Verification execution time"
    )


class TaskExecutionResult(BaseModel):
    """Result of autonomous task execution."""

    task_id: int = Field(..., description="Executed task ID")
    success: bool = Field(..., description="Overall success status")
    claude_output: str = Field(..., description="Claude's response")
    files_changed: List[str] = Field(
        default_factory=list, description="Files modified"
    )
    quality_gate: Optional[QualityGateResult] = Field(
        default=None, description="Quality gate results"
    )
    verification: Optional[VerificationResult] = Field(
        default=None, description="Independent verification results"
    )
    learnings_extracted: List[Learning] = Field(
        default_factory=list, description="Extracted learnings"
    )
    error_message: Optional[str] = Field(default=None, description="Error if failed")
    execution_time_seconds: float = Field(
        default=0.0, description="Total execution time"
    )


class AutonomousContext(BaseModel):
    """Context assembled for task execution."""

    learnings: List[Learning] = Field(
        default_factory=list, description="Relevant learnings"
    )
    story: Optional[Story] = Field(default=None, description="Parent story")
    prd: Optional[PRD] = Field(default=None, description="Parent PRD")
    previous_tasks: List[Task] = Field(
        default_factory=list, description="Previous tasks in story"
    )
    token_count: int = Field(default=0, description="Approximate token count")


class LoopStatus(BaseModel):
    """Status of the autonomous processing loop."""

    is_running: bool = Field(default=False, description="Whether loop is active")
    is_paused: bool = Field(default=False, description="Whether loop is paused")
    current_task_id: Optional[int] = Field(
        default=None, description="Currently executing task"
    )
    parallel_task_ids: List[int] = Field(
        default_factory=list, description="Tasks executing in parallel"
    )
    max_parallel: int = Field(default=1, description="Max parallel workers")
    tasks_queued: int = Field(default=0, description="Tasks in queue")
    tasks_completed_today: int = Field(default=0, description="Tasks completed today")
    tasks_failed_today: int = Field(default=0, description="Tasks failed today")
    last_task_completed_at: Optional[datetime] = None
    uptime_seconds: float = Field(default=0.0, description="Loop uptime")

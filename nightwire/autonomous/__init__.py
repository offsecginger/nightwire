"""Autonomous task execution system for Signal Claude Bot."""

from .commands import AutonomousCommands
from .database import AutonomousDatabase
from .executor import TaskExecutor
from .learnings import LearningExtractor
from .loop import AutonomousLoop
from .manager import AutonomousManager
from .models import (
    PRD,
    AutonomousContext,
    Learning,
    LearningCategory,
    LoopStatus,
    PRDStatus,
    QualityGateResult,
    Story,
    StoryStatus,
    Task,
    TaskExecutionResult,
    TaskStatus,
)
from .quality_gates import QualityGateRunner

__all__ = [
    # Models
    "PRD",
    "PRDStatus",
    "Story",
    "StoryStatus",
    "Task",
    "TaskStatus",
    "Learning",
    "LearningCategory",
    "QualityGateResult",
    "TaskExecutionResult",
    "AutonomousContext",
    "LoopStatus",
    # Database
    "AutonomousDatabase",
    # Components
    "QualityGateRunner",
    "LearningExtractor",
    "TaskExecutor",
    "AutonomousLoop",
    # Main interfaces
    "AutonomousManager",
    "AutonomousCommands",
]

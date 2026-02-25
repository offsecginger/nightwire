"""Autonomous task execution system for Signal Claude Bot."""

from .models import (
    PRD,
    PRDStatus,
    Story,
    StoryStatus,
    Task,
    TaskStatus,
    Learning,
    LearningCategory,
    QualityGateResult,
    TaskExecutionResult,
    AutonomousContext,
    LoopStatus,
)
from .database import AutonomousDatabase
from .quality_gates import QualityGateRunner
from .learnings import LearningExtractor
from .executor import TaskExecutor
from .loop import AutonomousLoop
from .manager import AutonomousManager
from .commands import AutonomousCommands

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

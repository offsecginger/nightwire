"""Resource guard — checks system resources before spawning workers."""

from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

# Thresholds
MAX_MEMORY_PERCENT = 90  # Don't spawn if memory > 90%
MIN_AVAILABLE_MB = 512   # Need at least 512MB free


@dataclass
class ResourceStatus:
    """Result of a resource check."""
    ok: bool
    memory_percent: float
    memory_available_mb: float
    cpu_count: int
    reason: str = ""


def check_resources() -> ResourceStatus:
    """Check if system has enough resources to spawn a worker.

    Uses psutil if available, falls back gracefully.
    Returns ResourceStatus with ok=True if resources are sufficient.
    """
    try:
        import psutil
        mem = psutil.virtual_memory()
        memory_percent = mem.percent
        memory_available_mb = mem.available / (1024 * 1024)
        cpu_count = psutil.cpu_count() or 1

        ok = True
        reason = ""
        if memory_percent > MAX_MEMORY_PERCENT:
            ok = False
            reason = f"Memory usage too high: {memory_percent:.1f}%"
        elif memory_available_mb < MIN_AVAILABLE_MB:
            ok = False
            reason = f"Available memory too low: {memory_available_mb:.0f}MB"

        return ResourceStatus(
            ok=ok,
            memory_percent=memory_percent,
            memory_available_mb=memory_available_mb,
            cpu_count=cpu_count,
            reason=reason,
        )
    except ImportError:
        # psutil not installed — allow execution but warn
        logger.warning("psutil_not_installed", msg="Resource checks disabled")
        return ResourceStatus(
            ok=True,
            memory_percent=0.0,
            memory_available_mb=0.0,
            cpu_count=1,
            reason="psutil not installed, checks disabled",
        )
    except Exception as e:
        logger.warning("resource_check_error", error=str(e))
        return ResourceStatus(
            ok=True,
            memory_percent=0.0,
            memory_available_mb=0.0,
            cpu_count=1,
            reason=f"Check failed: {e}",
        )

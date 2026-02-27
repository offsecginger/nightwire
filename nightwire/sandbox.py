"""Docker sandbox for task execution.

DEPRECATED: This module was used when Claude ran as a local subprocess.
The ClaudeRunner now uses the Anthropic SDK (server-side execution),
so local sandboxing is no longer applicable. This module is retained
for backward compatibility but is not used by the SDK-based runner.
"""

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import List

import structlog

warnings.warn(
    "nightwire.sandbox is deprecated — ClaudeRunner now uses the Anthropic SDK",
    DeprecationWarning,
    stacklevel=2,
)

logger = structlog.get_logger("nightwire.claude")


@dataclass
class SandboxConfig:
    """Configuration for Docker sandbox.

    Attributes:
        enabled: Whether sandboxing is active.
        image: Docker image to use (default python:3.11-slim).
        network: Allow network access (default False).
        memory_limit: Container memory cap (e.g. "2g").
        cpu_limit: CPU core limit (e.g. 2.0).
        tmpfs_size: Size of /tmp tmpfs mount (e.g. "256m").
    """

    enabled: bool = False
    image: str = "python:3.11-slim"
    network: bool = False
    memory_limit: str = "2g"
    cpu_limit: float = 2.0
    tmpfs_size: str = "256m"


def build_sandbox_command(
    cmd: List[str],
    project_path: Path,
    config: SandboxConfig,
) -> List[str]:
    """Wrap a command in a Docker sandbox if enabled.

    Mounts only project_path read-write, /tmp as tmpfs, no network
    by default. Returns original command unchanged if sandbox is
    disabled.

    Args:
        cmd: Original command as a list of strings.
        project_path: Project directory to mount into container.
        config: Sandbox configuration.

    Returns:
        Docker-wrapped command list, or original cmd if disabled.
    """
    if not config.enabled:
        return cmd

    docker_cmd = [
        "docker",
        "run",
        "--rm",
        "--interactive",
        f"--memory={config.memory_limit}",
        f"--cpus={config.cpu_limit}",
        "--tmpfs",
        f"/tmp:size={config.tmpfs_size}",
        "-v",
        f"{project_path}:{project_path}:rw",
        "-w",
        str(project_path),
    ]

    if not config.network:
        docker_cmd.append("--network=none")

    # Pass through essential env vars
    docker_cmd.extend([
        "-e",
        "HOME",
        "-e",
        "PATH",
        "-e",
        "ANTHROPIC_API_KEY",
    ])

    docker_cmd.append(config.image)
    docker_cmd.extend(cmd)

    logger.info("sandbox_command_built", project=str(project_path), network=config.network)

    return docker_cmd

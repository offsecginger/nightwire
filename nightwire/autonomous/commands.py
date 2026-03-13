"""Command handlers for the autonomous task system.

Provides Signal slash-command handlers for managing PRDs, stories, tasks,
the autonomous execution loop, task queue, and learnings. Each ``handle_*``
method is registered via ``register_external()`` in bot.py.
"""

import structlog

from .manager import AutonomousManager
from .models import LearningCategory, TaskStatus

logger = structlog.get_logger("nightwire.autonomous")


class AutonomousCommands:
    """Handlers for autonomous-related slash commands."""

    def __init__(
        self,
        manager: AutonomousManager,
        get_current_project: callable,
        is_prd_creating: callable = lambda phone: False,
        create_prd_fn: callable = None,
    ):
        """
        Initialize command handlers.

        Args:
            manager: AutonomousManager instance
            get_current_project: Callable(phone_number) -> (project_name, project_path)
            is_prd_creating: Callable(phone_number) -> bool, checks if a PRD is
                currently being generated for this sender.
            create_prd_fn: Callable(sender, description, auto_queue) -> str,
                creates a PRD from a description (for /prd ingest).
        """
        self.manager = manager
        self.get_current_project = get_current_project
        self.is_prd_creating = is_prd_creating
        self.create_prd_fn = create_prd_fn

    # ========== /prd Command ==========

    async def handle_prd(self, phone: str, args: str) -> str:
        """Create, list, view, activate, ingest, delete, or archive PRDs.

        Signal usage::

            /prd Build a REST API           — Create a new PRD
            /prd ingest                     — Analyze CLAUDE.md and create PRD
            /prd ingest src/main.py         — Analyze a specific file and create PRD
            /prd list                       — List all PRDs
            /prd 3                          — Show PRD #3 details
            /prd activate 3                 — Activate PRD #3
            /prd archive 3                  — Archive PRD #3
            /prd delete 3                   — Delete PRD #3 + all stories/tasks

        Args:
            phone: Phone number or UUID of the sender.
            args: Subcommand or PRD title for creation.

        Returns:
            PRD details, list, or confirmation message.
        """
        if not args.strip():
            return self._prd_help()

        parts = args.strip().split(maxsplit=1)
        subcommand = parts[0].lower()
        subargs = parts[1] if len(parts) > 1 else ""

        if subcommand == "ingest":
            return await self._ingest_prd(phone, subargs)
        elif subcommand == "list":
            return await self._list_prds(phone)
        elif subcommand == "activate":
            return await self._activate_prd(phone, subargs)
        elif subcommand == "archive":
            return await self._archive_prd(phone, subargs)
        elif subcommand == "delete":
            return await self._delete_prd(phone, subargs)
        elif subcommand.isdigit():
            return await self._show_prd(phone, int(subcommand))
        else:
            # Create new PRD
            return await self._create_prd(phone, args)

    async def _create_prd(self, phone: str, title: str) -> str:
        """Create a new PRD."""
        project_name, _ = self.get_current_project(phone)
        if not project_name:
            return "No project selected. Use /select <project> first."

        prd = await self.manager.create_prd(
            phone_number=phone,
            project_name=project_name,
            title=title,
            description=f"PRD for: {title}",
        )

        return (
            f"Created PRD #{prd.id}: {title}\n\n"
            f"Add stories with:\n"
            f"/story {prd.id} <story title> | <description>\n\n"
            f"Example:\n"
            f"/story {prd.id} User registration | Users can register with email"
        )

    async def _ingest_prd(self, phone: str, args: str) -> str:
        """Analyze a project file and create a PRD from it.

        Reads the specified file (default: CLAUDE.md) and all
        referenced files, then creates a PRD with stories and tasks.
        Does NOT auto-queue — user must /queue prd <id> to start.
        """
        project_name, _ = self.get_current_project(phone)
        if not project_name:
            return "No project selected. Use /select <project> first."

        if not self.create_prd_fn:
            return "PRD ingestion is not configured."

        # Default to CLAUDE.md if no file specified
        entry_file = args.strip() if args.strip() else "CLAUDE.md"

        prompt = (
            f"Read and analyze the file '{entry_file}' in this project. "
            f"Follow all references it makes to other files "
            f"(subagents, rules, configs, source code, etc.) to "
            f"understand the full project structure, architecture, "
            f"and current state.\n\n"
            f"Based on your analysis, create a comprehensive PRD "
            f"that breaks the project's remaining work or improvements "
            f"into stories and tasks. Focus on actionable work items "
            f"that would move the project forward."
        )

        try:
            return await self.create_prd_fn(
                phone, prompt, auto_queue=False,
            )
        except Exception as e:
            logger.error("prd_ingest_error", error=str(e))
            return f"PRD ingestion failed: {str(e)[:200]}"

    async def _list_prds(self, phone: str) -> str:
        """List PRDs for user."""
        project_name, _ = self.get_current_project(phone)
        prds = await self.manager.list_prds(phone, project_name)

        if not prds:
            return "No PRDs found. Create one with /prd <title>"

        lines = ["Your PRDs:"]
        for prd in prds:
            status_emoji = {
                "draft": "[ ]",
                "active": "[>]",
                "completed": "[x]",
                "archived": "[-]",
            }.get(prd.status.value, "[ ]")

            lines.append(
                f"{status_emoji} #{prd.id} {prd.title} "
                f"({prd.completed_stories}/{prd.total_stories} stories)"
            )

        return "\n".join(lines)

    async def _show_prd(self, phone: str, prd_id: int) -> str:
        """Show PRD details."""
        prd = await self.manager.get_prd(prd_id)
        if not prd:
            return f"PRD #{prd_id} not found."

        stories = await self.manager.list_stories(prd_id=prd_id)

        lines = [
            f"PRD #{prd.id}: {prd.title}",
            f"Status: {prd.status.value}",
            f"Project: {prd.project_name}",
            f"Stories: {prd.completed_stories}/{prd.total_stories}",
            "",
            "Stories:",
        ]

        if stories:
            for story in stories:
                status_emoji = {
                    "pending": "[ ]",
                    "in_progress": "[>]",
                    "completed": "[x]",
                    "blocked": "[!]",
                    "failed": "[X]",
                }.get(story.status.value, "[ ]")
                lines.append(
                    f"  {status_emoji} #{story.id} {story.title} "
                    f"({story.completed_tasks}/{story.total_tasks} tasks)"
                )
        else:
            lines.append("  No stories yet. Add with /story")

        return "\n".join(lines)

    async def _activate_prd(self, phone: str, args: str) -> str:
        """Activate a PRD."""
        if not args.strip().isdigit():
            return "Usage: /prd activate <prd_id>"

        prd_id = int(args.strip())
        prd = await self.manager.get_prd(prd_id)
        if not prd:
            return f"PRD #{prd_id} not found."

        await self.manager.activate_prd(prd_id)
        return f"PRD #{prd_id} activated: {prd.title}"

    async def _archive_prd(self, phone: str, args: str) -> str:
        """Archive a PRD."""
        if not args.strip().isdigit():
            return "Usage: /prd archive <prd_id>"

        prd_id = int(args.strip())
        prd = await self.manager.get_prd(prd_id)
        if not prd:
            return f"PRD #{prd_id} not found."

        await self.manager.archive_prd(prd_id)
        return f"PRD #{prd_id} archived: {prd.title}"

    async def _delete_prd(self, phone: str, args: str) -> str:
        """Delete a PRD and all its stories and tasks."""
        if not args.strip().isdigit():
            return "Usage: /prd delete <prd_id>"

        prd_id = int(args.strip())
        try:
            result = await self.manager.delete_prd(prd_id)
        except ValueError as e:
            return str(e)

        if result is None:
            return f"PRD #{prd_id} not found."

        return (
            f"Deleted PRD #{prd_id}: {result['prd_title']}\n"
            f"Removed {result['stories']} story(ies) "
            f"and {result['tasks']} task(s)."
        )

    def _prd_help(self) -> str:
        return """PRD Commands:
/prd <title> - Create new PRD
/prd ingest [file] - Analyze project file and create PRD (default: CLAUDE.md)
/prd list - List all PRDs
/prd <id> - Show PRD details
/prd activate <id> - Activate for processing
/prd archive <id> - Archive a PRD
/prd delete <id> - Delete PRD and all stories/tasks

Examples:
/prd User Authentication System
/prd ingest
/prd ingest CLAUDE.md
/prd delete 3"""

    # ========== /story Command ==========

    async def handle_story(self, phone: str, args: str) -> str:
        """Create, list, view, or delete user stories within a PRD.

        Signal usage::

            /story 1 User login | Users can log in with email
            /story list                     — List all stories
            /story list 1                   — List stories for PRD #1
            /story 5                        — Show story #5 details
            /story delete 5                 — Delete story #5 + all its tasks

        Args:
            phone: Phone number or UUID of the sender.
            args: Subcommand, story ID, or ``<prd_id> <title> | <desc>``.

        Returns:
            Story details, list, or confirmation message.
        """
        if not args.strip():
            return self._story_help()

        parts = args.strip().split(maxsplit=1)
        subcommand = parts[0].lower()
        subargs = parts[1] if len(parts) > 1 else ""

        if subcommand == "list":
            return await self._list_stories(phone, subargs)
        elif subcommand == "delete":
            return await self._delete_story(phone, subargs)
        elif subcommand.isdigit():
            if not subargs:
                # No args after number: show story details (/story <id>)
                return await self._show_story(phone, int(subcommand))
            else:
                # Has args: create a story in PRD (/story <prd_id> <title> | <desc>)
                return await self._create_story(phone, int(subcommand), subargs)
        else:
            return self._story_help()

    async def _create_story(self, phone: str, prd_id: int, args: str) -> str:
        """Create a story in a PRD."""
        prd = await self.manager.get_prd(prd_id)
        if not prd:
            return f"PRD #{prd_id} not found."

        if "|" in args:
            title, description = args.split("|", 1)
            title = title.strip()
            description = description.strip()
        else:
            title = args.strip() if args.strip() else "Untitled Story"
            description = title

        if not title:
            return "Usage: /story <prd_id> <title> | <description>"

        story = await self.manager.create_story(
            prd_id=prd_id,
            phone_number=phone,
            title=title,
            description=description,
        )

        return (
            f"Created Story #{story.id}: {title}\n"
            f"In PRD #{prd_id}: {prd.title}\n\n"
            f"Add tasks with:\n"
            f"/task {story.id} <task title> | <description>"
        )

    async def _list_stories(self, phone: str, args: str) -> str:
        """List stories."""
        prd_id = int(args.strip()) if args.strip().isdigit() else None
        stories = await self.manager.list_stories(prd_id=prd_id, phone_number=phone)

        if not stories:
            return "No stories found."

        lines = ["Stories:"]
        for story in stories:
            status_emoji = {
                "pending": "[ ]",
                "in_progress": "[>]",
                "completed": "[x]",
                "blocked": "[!]",
                "failed": "[X]",
            }.get(story.status.value, "[ ]")
            lines.append(
                f"{status_emoji} #{story.id} (PRD {story.prd_id}) {story.title} "
                f"({story.completed_tasks}/{story.total_tasks} tasks)"
            )

        return "\n".join(lines)

    async def _show_story(self, phone: str, story_id: int) -> str:
        """Show story details."""
        story = await self.manager.get_story(story_id)
        if not story:
            return f"Story #{story_id} not found."

        tasks = await self.manager.list_tasks(story_id=story_id)

        lines = [
            f"Story #{story.id}: {story.title}",
            f"Status: {story.status.value}",
            f"PRD: #{story.prd_id}",
            f"Tasks: {story.completed_tasks}/{story.total_tasks}",
            "",
            f"Description: {story.description[:200]}",
        ]

        if story.acceptance_criteria:
            lines.append("\nAcceptance Criteria:")
            for ac in story.acceptance_criteria:
                lines.append(f"  - {ac}")

        lines.append("\nTasks:")
        if tasks:
            for task in tasks:
                status_emoji = {
                    "pending": "[ ]",
                    "queued": "[Q]",
                    "in_progress": "[>]",
                    "running_tests": "[T]",
                    "completed": "[x]",
                    "failed": "[X]",
                    "blocked": "[!]",
                    "cancelled": "[-]",
                }.get(task.status.value, "[ ]")
                lines.append(f"  {status_emoji} #{task.id} {task.title}")
        else:
            lines.append("  No tasks yet. Add with /task")

        return "\n".join(lines)

    async def _delete_story(self, phone: str, args: str) -> str:
        """Delete a story and all its tasks."""
        if not args.strip().isdigit():
            return "Usage: /story delete <story_id>"

        story_id = int(args.strip())
        try:
            result = await self.manager.delete_story(story_id)
        except ValueError as e:
            return str(e)

        if result is None:
            return f"Story #{story_id} not found."

        return (
            f"Deleted Story #{story_id}: {result['story_title']}\n"
            f"Removed {result['tasks']} task(s) "
            f"(was in PRD #{result['prd_id']})."
        )

    def _story_help(self) -> str:
        return """Story Commands:
/story <prd_id> <title> | <description> - Create story
/story list [prd_id] - List stories
/story <id> - Show story details
/story delete <id> - Delete story and all its tasks

Example:
/story 1 User login | Users can log in with email and password
/story delete 5"""

    # ========== /task Command ==========

    async def handle_task(self, phone: str, args: str) -> str:
        """Create or view tasks within a story.

        Signal usage::

            /task 1 Create login form | Build HTML form with validation
            /task 7                         — Show task #7 details

        Args:
            phone: Phone number or UUID of the sender.
            args: Task ID to view, or ``<story_id> <title> | <desc>`` to create.

        Returns:
            Task details or confirmation message.
        """
        if not args.strip():
            return self._task_help()

        parts = args.strip().split(maxsplit=1)
        subcommand = parts[0].lower()
        subargs = parts[1] if len(parts) > 1 else ""

        if subcommand.isdigit():
            story_id = int(subcommand)
            if "|" in subargs or subargs:
                return await self._create_task(phone, story_id, subargs)
            else:
                return await self._show_task(phone, story_id)
        else:
            return self._task_help()

    async def _create_task(self, phone: str, story_id: int, args: str) -> str:
        """Create a task in a story."""
        story = await self.manager.get_story(story_id)
        if not story:
            return f"Story #{story_id} not found."

        prd = await self.manager.get_prd(story.prd_id)
        project_name = prd.project_name if prd else None

        if not project_name:
            project_name, _ = self.get_current_project(phone)
        if not project_name:
            return "No project found. Select a project first."

        if "|" in args:
            title, description = args.split("|", 1)
            title = title.strip()
            description = description.strip()
        else:
            title = args.strip()
            description = title

        if not title:
            return "Usage: /task <story_id> <title> | <description>"

        task = await self.manager.create_task(
            story_id=story_id,
            phone_number=phone,
            project_name=project_name,
            title=title,
            description=description,
        )

        return (
            f"Created Task #{task.id}: {title}\n"
            f"In Story #{story_id}: {story.title}\n\n"
            f"Queue with: /queue story {story_id}"
        )

    async def _show_task(self, phone: str, task_id: int) -> str:
        """Show task details."""
        task = await self.manager.get_task(task_id)
        if not task:
            return f"Task #{task_id} not found."

        lines = [
            f"Task #{task.id}: {task.title}",
            f"Status: {task.status.value}",
            f"Story: #{task.story_id}",
            f"Project: {task.project_name}",
            f"Retries: {task.retry_count}/{task.max_retries}",
            "",
            f"Description:\n{task.description[:500]}",
        ]

        if task.error_message:
            lines.append(f"\nError: {task.error_message[:300]}")

        if task.files_changed:
            lines.append(f"\nFiles changed: {', '.join(task.files_changed[:5])}")

        return "\n".join(lines)

    def _task_help(self) -> str:
        return """Task Commands:
/task <story_id> <title> | <description> - Create task
/task <id> - Show task details

Example:
/task 1 Create login form | Build HTML form with email/password fields"""

    # ========== /tasks Command ==========

    async def handle_tasks(self, phone: str, args: str) -> str:
        """List tasks grouped by status, with optional status filter or purge.

        Signal usage::

            /tasks                          — List all tasks
            /tasks queued                   — Show only queued tasks
            /tasks completed                — Show only completed tasks
            /tasks purge                    — Cancel all pending/queued/blocked tasks

        Args:
            phone: Phone number or UUID of the sender.
            args: Optional status filter, or ``purge`` to cancel non-terminal tasks.

        Returns:
            Formatted task list grouped by status with summary stats.
        """
        project_name, _ = self.get_current_project(phone)

        if args.strip().lower().startswith("purge"):
            purge_parts = args.strip().lower().split()
            purge_scope = purge_parts[1] if len(purge_parts) > 1 else ""

            if purge_scope == "failed":
                count = await self.manager.purge_failed_tasks(
                    phone, project_name
                )
                if count == 0:
                    return "No failed tasks to purge."
                return f"Purged {count} failed task(s)."
            elif purge_scope == "all":
                count_queue = await self.manager.purge_non_terminal_tasks(
                    phone, project_name
                )
                count_failed = await self.manager.purge_failed_tasks(
                    phone, project_name
                )
                total = count_queue + count_failed
                if total == 0:
                    return "No tasks to purge."
                return (
                    f"Purged {total} task(s) "
                    f"({count_queue} queued, {count_failed} failed)."
                )
            else:
                count = await self.manager.purge_non_terminal_tasks(
                    phone, project_name
                )
                if count == 0:
                    return "No pending/queued/blocked tasks to purge."
                return (
                    f"Purged {count} task(s) "
                    "(pending/queued/blocked → cancelled).\n"
                    "Tip: /tasks purge failed for failed tasks, "
                    "/tasks purge all for everything."
                )

        status_filter = None
        if args.strip():
            try:
                status_filter = TaskStatus(args.strip().lower())
            except ValueError:
                return (
                    f"Invalid status. Use: "
                    f"{', '.join(s.value for s in TaskStatus)}"
                    f", or 'purge'"
                )

        tasks = await self.manager.list_tasks(
            phone_number=phone,
            project_name=project_name,
            status=status_filter,
        )

        if not tasks:
            if self.is_prd_creating(phone):
                return (
                    "No tasks found. A PRD is currently being generated "
                    "— tasks will appear once it completes."
                )
            return "No tasks found."

        # Group by status
        by_status = {}
        for task in tasks:
            status = task.status.value
            if status not in by_status:
                by_status[status] = []
            by_status[status].append(task)

        lines = ["Tasks:"]
        for status in ["queued", "in_progress", "pending", "completed", "failed"]:
            if status in by_status:
                lines.append(f"\n{status.upper()}:")
                for task in by_status[status][:10]:
                    lines.append(f"  #{task.id} {task.title[:60]}")
                if len(by_status[status]) > 10:
                    lines.append(f"  ... and {len(by_status[status]) - 10} more")

        stats = await self.manager.get_task_stats(phone, project_name)
        total_part = f"Total: {stats['total']}"
        if stats.get("failed", 0) > 0:
            total_part += f" ({stats['failed']} failed)"
        lines.append(
            f"\n{total_part} | "
            f"Today: {stats['completed_today']} done, "
            f"{stats['failed_today']} failed"
        )

        return "\n".join(lines)

    # ========== /autonomous Command ==========

    async def handle_autonomous(self, phone: str, args: str) -> str:
        """Control the autonomous task execution loop.

        Signal usage::

            /autonomous                     — Show loop status (default)
            /autonomous status              — Same as above
            /autonomous start               — Start processing queued tasks
            /autonomous pause               — Pause (finishes current task)
            /autonomous resume              — Resume from paused state
            /autonomous stop                — Stop the loop entirely

        Args:
            phone: Phone number or UUID of the sender.
            args: Subcommand — start, stop, pause, resume, or status.

        Returns:
            Loop status or confirmation of the action taken.
        """
        if not args.strip():
            return await self._autonomous_status(phone)

        subcommand = args.strip().lower().split()[0]

        if subcommand == "start":
            status = await self.manager.get_loop_status()
            if status.is_paused:
                await self.manager.resume_loop()
                return "Autonomous loop resumed (was paused)."
            if status.is_running:
                return "Autonomous loop is already running."
            await self.manager.start_loop()
            return "Autonomous loop started. Tasks will be processed automatically."
        elif subcommand == "stop":
            await self.manager.stop_loop()
            return "Autonomous loop stopped."
        elif subcommand == "pause":
            await self.manager.pause_loop()
            return "Autonomous loop paused. Current task will finish first."
        elif subcommand == "resume":
            await self.manager.resume_loop()
            return "Autonomous loop resumed."
        elif subcommand == "status":
            return await self._autonomous_status(phone)
        else:
            return """Autonomous Commands:
/autonomous - Show status
/autonomous start - Start processing
/autonomous pause - Pause (finishes current task)
/autonomous resume - Resume processing
/autonomous stop - Stop processing"""

    async def _autonomous_status(self, phone: str) -> str:
        """Show autonomous loop status."""
        status = await self.manager.get_loop_status()

        state = "RUNNING" if status.is_running else "STOPPED"
        if status.is_paused:
            state = "PAUSED"

        lines = [
            f"Autonomous Loop: {state}",
            f"Tasks in queue: {status.tasks_queued}",
            f"Today: {status.tasks_completed_today} completed, {status.tasks_failed_today} failed",
        ]

        if status.current_task_id:
            lines.append(f"Currently executing: Task #{status.current_task_id}")

        if status.is_running:
            uptime_min = int(status.uptime_seconds / 60)
            lines.append(f"Uptime: {uptime_min} minutes")

        return "\n".join(lines)

    # ========== /queue Command ==========

    async def handle_queue(self, phone: str, args: str) -> str:
        """Queue tasks for autonomous execution by story or PRD.

        Signal usage::

            /queue story 1                  — Queue all tasks in story #1
            /queue prd 2                    — Queue all tasks in PRD #2

        Args:
            phone: Phone number or UUID of the sender.
            args: ``story <id>`` or ``prd <id>``.

        Returns:
            Count of tasks queued, or usage help.
        """
        if not args.strip():
            return """Queue Commands:
/queue story <id> - Queue all tasks for a story
/queue prd <id> - Queue all tasks for a PRD"""

        parts = args.strip().split()
        if len(parts) < 2:
            return "Usage: /queue story <id> or /queue prd <id>"

        target_type = parts[0].lower()
        target_id = parts[1]

        if not target_id.isdigit():
            return "ID must be a number."

        target_id = int(target_id)

        if target_type == "story":
            count = await self.manager.queue_story(target_id)
            return f"Queued {count} tasks for Story #{target_id}"
        elif target_type == "prd":
            count = await self.manager.queue_prd(target_id)
            return f"Queued {count} tasks for PRD #{target_id}"
        else:
            return "Use: /queue story <id> or /queue prd <id>"

    # ========== /learnings Command ==========

    async def handle_learnings(self, phone: str, args: str) -> str:
        """View, search, or manually add learnings extracted from task execution.

        Signal usage::

            /learnings                      — List recent learnings
            /learnings search auth          — Search learnings
            /learnings auth patterns        — Also searches (implicit)
            /learnings add pattern | Title | Content details here

        Args:
            phone: Phone number or UUID of the sender.
            args: Empty for list, ``search <query>``, or ``add <cat> | <title> | <content>``.

        Returns:
            Formatted learnings list, search results, or confirmation.
        """
        if not args.strip():
            return await self._list_learnings(phone)

        parts = args.strip().split(maxsplit=1)
        subcommand = parts[0].lower()
        subargs = parts[1] if len(parts) > 1 else ""

        if subcommand == "search":
            return await self._search_learnings(phone, subargs)
        elif subcommand == "add":
            return await self._add_learning(phone, subargs)
        else:
            # Treat as search query
            return await self._search_learnings(phone, args)

    async def _list_learnings(self, phone: str) -> str:
        """List recent learnings."""
        project_name, _ = self.get_current_project(phone)
        learnings = await self.manager.get_learnings(
            phone_number=phone,
            project_name=project_name,
            limit=20,
        )

        if not learnings:
            return "No learnings yet. They'll be extracted as tasks complete."

        lines = ["Recent Learnings:"]
        for learning in learnings[:15]:
            lines.append(f"  [{learning.category.value}] {learning.title[:50]}")

        return "\n".join(lines)

    async def _search_learnings(self, phone: str, query: str) -> str:
        """Search learnings."""
        if not query:
            return "Usage: /learnings search <query>"

        project_name, _ = self.get_current_project(phone)
        learnings = await self.manager.search_learnings(
            phone_number=phone,
            query=query,
            project_name=project_name,
        )

        if not learnings:
            return f"No learnings found for: {query}"

        lines = [f"Learnings for '{query}':"]
        for learning in learnings[:10]:
            lines.append(f"\n[{learning.category.value}] {learning.title}")
            lines.append(f"  {learning.content[:150]}...")

        return "\n".join(lines)

    async def _add_learning(self, phone: str, args: str) -> str:
        """Manually add a learning."""
        if "|" not in args:
            return (
                "Usage: /learnings add <category> | <title> | "
                "<content>\n\nCategories: pattern, pitfall, "
                "best_practice, debugging, testing"
            )

        parts = args.split("|")
        if len(parts) < 3:
            return "Usage: /learnings add <category> | <title> | <content>"

        category_str = parts[0].strip().lower()
        title = parts[1].strip()
        content = "|".join(parts[2:]).strip()

        try:
            category = LearningCategory(category_str)
        except ValueError:
            valid = ", ".join(c.value for c in LearningCategory)
            return f"Invalid category. Use: {valid}"

        project_name, _ = self.get_current_project(phone)

        learning_id = await self.manager.add_learning(
            phone_number=phone,
            category=category,
            title=title,
            content=content,
            project_name=project_name,
        )

        return f"Learning #{learning_id} added: {title}"


def get_autonomous_help_metadata():
    """Return HelpMetadata for all autonomous commands."""
    from ..commands.base import HelpMetadata

    return {
        "prd": HelpMetadata(
            description="Create, ingest, view, or delete PRDs",
            usage="/prd <title> | /prd ingest [file] | /prd list | /prd <id> | /prd delete <id>",
            examples=[
                "/prd Add OAuth2 login", "/prd list",
                "/prd 5", "/prd ingest", "/prd ingest CLAUDE.md",
                "/prd delete 3",
            ],
        ),
        "story": HelpMetadata(
            description="Add, view, or delete user stories in a PRD",
            usage="/story <prd_id> <title> | <description> | /story delete <id>",
            examples=[
                "/story 5 Google OAuth | Implement Google OAuth2 flow",
                "/story delete 5",
            ],
        ),
        "task": HelpMetadata(
            description="Add a task to a story",
            usage="/task <story_id> <title> | <description>",
            examples=["/task 12 Add callback endpoint | Create GET /auth/callback"],
        ),
        "tasks": HelpMetadata(
            description="List tasks, filter by status, or purge queue",
            usage="/tasks [status | purge | purge failed | purge all]",
            examples=[
                "/tasks", "/tasks queued",
                "/tasks completed", "/tasks purge",
                "/tasks purge failed", "/tasks purge all",
            ],
        ),
        "queue": HelpMetadata(
            description="Queue tasks for autonomous execution",
            usage="/queue story <id> | /queue prd <id>",
            examples=["/queue story 12", "/queue prd 5"],
        ),
        "autonomous": HelpMetadata(
            description="Control the autonomous task execution loop",
            usage="/autonomous <start|stop|pause|resume|status>",
            examples=[
                "/autonomous start",
                "/autonomous pause",
                "/autonomous status",
            ],
        ),
        "learnings": HelpMetadata(
            description="View or search learnings from completed tasks",
            usage="/learnings [search <query>] | /learnings add <cat> | <title> | <content>",
            examples=[
                "/learnings",
                "/learnings search oauth",
                "/learnings add pattern | Use JWT | Short-lived tokens",
            ],
        ),
    }

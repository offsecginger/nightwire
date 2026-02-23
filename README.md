# sidechannel

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Claude](https://img.shields.io/badge/Claude-AI-purple.svg)](https://anthropic.com)
[![Signal](https://img.shields.io/badge/Signal-Messenger-blue.svg)](https://signal.org)

A Signal messaging bot that integrates Claude AI for intelligent conversations, code assistance, and autonomous development tasks with independent verification, parallel execution, and production-grade reliability.

## What's New in v1.0.0

- **Independent Verification** - Every autonomous task is reviewed by a separate Claude context using a fail-closed security model: security concerns or logic errors cause the task to fail, while only infrastructure failures (timeout, crash, parse error) pass through
- **Parallel Workers** - Up to N concurrent task workers via configurable `max_parallel` setting, with dependency resolution ensuring tasks execute in the correct order
- **Error Classification + Retry** - Transient, permanent, and infrastructure error categories with exponential backoff retry for recoverable failures
- **Baseline Test Snapshots** - Pre-task test state is captured so post-task regressions can be distinguished from pre-existing failures
- **Git Safety with Checkpoints** - Automatic git checkpoints before task execution and atomic commits after, protected by asyncio locks for concurrent access
- **Rate Limiting** - Per-user configurable rate limits to prevent abuse and ensure fair resource allocation
- **Stale Task Recovery** - On startup, stuck tasks older than 60 minutes are automatically re-queued for execution

## Features

- **Claude Integration** - Full Claude CLI integration for code analysis, generation, and project work
- **Episodic Memory** - Conversation context persists across sessions using vector embeddings
- **Autonomous Tasks** - Break down complex projects into PRDs, stories, and tasks with quality gates
- **Verification System** - Independent code review per task with fail-closed security posture and an auto-fix loop (up to 2 attempts)
- **Parallel Task Execution** - Concurrent task workers with dependency resolution and adaptive effort levels
- **Rate Limiting** - Per-user request throttling with configurable window and max request count
- **Error Retry with Backoff** - Classified error handling with exponential backoff for transient failures
- **Regression Detection** - Baseline test snapshots detect new failures introduced by a task without flagging pre-existing ones
- **Stale Task Recovery** - Automatic re-queuing of stuck tasks on startup
- **Project Management** - Work on multiple codebases with project-aware context
- **Nova Assistant** - Optional Grok-powered quick responses for general questions
- **Security** - Phone number allowlist, rate limiting, path validation hardening, phone number masking in logs, independent code verification

## Architecture

```
+-------------------+     +-------------------+     +---------------------+
|                   |     |                   |     |                     |
|  Signal Mobile    |<--->|  Signal CLI API   |<--->|    sidechannel      |
|                   |     |  (Docker)         |     |    (Python)         |
+-------------------+     +-------------------+     +---------------------+
                                                       |             |
                                              +--------+------+  +--+-------------+
                                              |               |  |                |
                                              | Autonomous    |  | Memory System  |
                                              | Loop          |  | (SQLite + Vec) |
                                              +-------+-------+  +----------------+
                                                      |
                              +-------------------------+-------------------------+
                              |                         |                         |
                    +---------+---------+   +-----------+---------+   +-----------+---------+
                    |  Worker 1         |   |  Worker 2           |   |  Worker N           |
                    |  (Claude CLI)     |   |  (Claude CLI)       |   |  (Claude CLI)       |
                    +---------+---------+   +-----------+---------+   +-----------+---------+
                              |                         |                         |
                    +---------+---------+   +-----------+---------+   +-----------+---------+
                    |  Verifier 1       |   |  Verifier 2         |   |  Verifier N         |
                    |  (Independent     |   |  (Independent       |   |  (Independent       |
                    |   Claude Context) |   |   Claude Context)   |   |   Claude Context)   |
                    +-------------------+   +---------------------+   +---------------------+
                              |                         |                         |
                    +---------+---------+   +-----------+---------+   +-----------+---------+
                    |  Quality Gate     |   |  Quality Gate       |   |  Quality Gate       |
                    |  (Test Baseline   |   |  (Test Baseline     |   |  (Test Baseline     |
                    |   + Regression)   |   |   + Regression)     |   |   + Regression)     |
                    +-------------------+   +---------------------+   +---------------------+
                                                      |
                                              +-------+-------+
                                              |               |
                                              | Your Projects |
                                              | /home/*/proj  |
                                              +---------------+
```

## Quick Start

```bash
# Clone the repository
git clone https://github.com/hackingdave/sidechannel.git
cd sidechannel

# Run the installer
./install.sh
```

The installer will:
1. Check prerequisites (Python 3.10+, Docker, Claude CLI)
2. Create a virtual environment and install dependencies
3. Set up Signal CLI REST API with QR code device linking
4. Configure your phone number and API keys
5. Optionally install as a systemd service

## Requirements

- **Python 3.10+**
- **Docker** - For Signal CLI REST API
- **Claude CLI** - [Installation guide](https://docs.anthropic.com/en/docs/claude-code)
- **Signal account** - Linked to the bot as a secondary device

## Commands

### Core Commands

| Command | Description |
|---------|-------------|
| `/help` | Show all available commands |
| `/select <project>` | Set active project for code work |
| `/projects` | List available projects |
| `/status` | Show current session status |

### Claude Integration

| Command | Description |
|---------|-------------|
| `/ask <question>` | Ask Claude about the current project |
| `/do <task>` | Have Claude perform a task in the project |
| `/complex <task>` | Complex task with PRD breakdown |
| `/summary` | Get a summary of recent work |

### Autonomous Development

| Command | Description |
|---------|-------------|
| `/autonomous start` | Start the autonomous task loop |
| `/autonomous pause` | Pause task execution |
| `/autonomous stop` | Stop and clear the queue |
| `/autonomous status` | Show queue and progress |
| `/prd list` | List all PRDs |
| `/prd <id>` | View PRD details |

### Memory & Context

| Command | Description |
|---------|-------------|
| `/remember <fact>` | Store a fact for future sessions |
| `/forget` | Clear session memory |
| `/context` | Show current context window |

### Nova Assistant (Optional)

When Grok integration is enabled, you can ask nova questions:

```
nova what is the capital of France?
hey nova explain kubernetes
```

## Configuration

### settings.yaml

```yaml
# Phone numbers authorized to use the bot (E.164 format)
allowed_numbers:
  - "+15551234567"

# Signal CLI REST API
signal_api_url: "http://127.0.0.1:8080"

# Claude CLI settings
claude:
  timeout: 600
  max_turns: 25

# Memory System
memory:
  session_timeout: 30        # Minutes before session expires
  max_context_tokens: 1500   # Max tokens for context window

# Autonomous Tasks
autonomous:
  enabled: true
  poll_interval: 30          # Seconds between task checks
  quality_gates: true        # Require approval for PRDs
  max_parallel: 3            # Concurrent task workers (1-10)
  verification: true         # Independent code review per task

# Rate limiting (per-user)
# rate_limit:
#   window: 60
#   max_requests: 30

# Optional: Grok AI (nova assistant)
grok:
  enabled: false
```

### Environment Variables (.env)

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...

# Optional (for nova assistant)
GROK_API_KEY=xai-...
```

### Adding Projects

Edit `config/projects.yaml`:

```yaml
projects:
  myapp:
    path: /home/user/projects/myapp
    description: "My web application"

  backend:
    path: /home/user/projects/backend-api
    description: "REST API service"
```

## Running

### Manual Start

```bash
cd ~/sidechannel
./run.sh
```

### Systemd Service

```bash
# Start
systemctl --user start sidechannel

# Enable on boot
systemctl --user enable sidechannel
loginctl enable-linger $USER

# View logs
journalctl --user -u sidechannel -f
```

## Memory System

sidechannel uses an episodic memory system to maintain context across conversations:

- **Short-term memory** - Current session context (clears on timeout)
- **Long-term memory** - Persistent facts stored with `/remember`
- **Project memory** - Per-project context and history
- **Semantic search** - Uses sentence-transformers for relevant memory retrieval

## Autonomous Development

The autonomous system can handle complex, multi-step development tasks with parallel execution and independent verification:

1. **PRD Creation** - Break down requests into Product Requirements Documents
2. **Story Generation** - Convert PRDs into implementable user stories
3. **Dependency Resolution** - Analyze task dependencies to determine execution order
4. **Parallel Execution** - Dispatch independent tasks to concurrent workers (up to `max_parallel`)
5. **Task Execution** - Each worker runs Claude CLI with adaptive effort levels based on task type
6. **Independent Verification** - A separate Claude context reviews each task's git diff for security concerns and logic errors
7. **Auto-Fix Loop** - If verification fails, a fresh Claude context attempts to fix the issues (up to 2 attempts)
8. **Quality Gates** - Baseline test snapshots detect regressions introduced by the task
9. **Progress Updates** - Regular status notifications via Signal

Example workflow:
```
You: /complex Add user authentication with JWT tokens
Bot: Created PRD #1 with 5 stories. Review at /prd 1
You: /autonomous start
Bot: Starting autonomous loop (3 parallel workers)...
Bot: [2 min] Working on Story 1/5: User registration endpoint
Bot: [3 min] Working on Story 2/5: Login endpoint (parallel)
Bot: [4 min] Story 1 verified and passed quality gates
...
Bot: PRD #1 complete! 5/5 stories implemented and verified.
```

## Security Considerations

- Only phone numbers in `allowed_numbers` can interact with the bot
- **Rate limiting** - Per-user configurable request throttling prevents abuse
- **Path validation hardening** - Directory traversal protection with strict prefix matching
- **Phone number masking in logs** - Sensitive identifiers are redacted in all log output
- **Independent code verification** - Every autonomous task is reviewed by a separate Claude context with a fail-closed security model
- API keys are stored in `.env` (not committed to git)
- Signal messages are end-to-end encrypted
- No message content is logged by default
- Claude CLI runs with your local user permissions

## Troubleshooting

### Signal not receiving messages

1. Check Signal CLI is running: `docker ps | grep signal`
2. Verify device is linked: `curl http://127.0.0.1:8080/v1/accounts`
3. Check logs: `docker logs signal-api`

### Claude commands failing

1. Verify Claude CLI works: `claude --version`
2. Check API key: `echo $ANTHROPIC_API_KEY`
3. Test manually: `claude "hello"`

### Memory not persisting

1. Check data directory exists: `ls ~/sidechannel/data`
2. Verify SQLite database: `ls ~/sidechannel/data/*.db`

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT License - see [LICENSE](LICENSE) for details.

## Acknowledgments

- [Anthropic](https://anthropic.com) for Claude
- [Signal](https://signal.org) for secure messaging
- [signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api) for the Signal API wrapper

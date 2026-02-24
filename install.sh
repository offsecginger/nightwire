#!/bin/bash
#
# sidechannel installer
# Signal + Claude AI Bot
#
# Usage: ./install.sh [--skip-signal] [--skip-systemd] [--docker] [--local] [--uninstall]
#

set -e

# Portable sed -i (BSD/macOS sed requires backup extension arg)
sed_inplace() {
    if sed --version 2>/dev/null | grep -q GNU; then
        sed -i "$@"
    else
        sed -i '' "$@"
    fi
}

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuration
INSTALL_DIR="${SIDECHANNEL_DIR:-$HOME/sidechannel}"
VENV_DIR="$INSTALL_DIR/venv"
CONFIG_DIR="$INSTALL_DIR/config"
DATA_DIR="$INSTALL_DIR/data"
LOGS_DIR="$INSTALL_DIR/logs"
SIGNAL_DATA_DIR="$INSTALL_DIR/signal-data"

# Flags
SKIP_SIGNAL=false
SKIP_SYSTEMD=false
INSTALL_MODE=""
UNINSTALL=false

# Parse arguments
for arg in "$@"; do
    case $arg in
        --skip-signal)
            SKIP_SIGNAL=true
            shift
            ;;
        --skip-systemd)
            SKIP_SYSTEMD=true
            shift
            ;;
        --docker)
            INSTALL_MODE="docker"
            shift
            ;;
        --local)
            INSTALL_MODE="local"
            shift
            ;;
        --uninstall)
            UNINSTALL=true
            shift
            ;;
        --help|-h)
            echo "Usage: ./install.sh [options]"
            echo ""
            echo "Options:"
            echo "  --docker         Install using Docker (recommended)"
            echo "  --local          Install using local Python venv"
            echo "  --skip-signal    Skip Signal CLI REST API setup (local mode)"
            echo "  --skip-systemd   Skip systemd service installation (local mode)"
            echo "  --uninstall      Remove sidechannel service and containers"
            echo "  --help, -h       Show this help message"
            exit 0
            ;;
    esac
done

# =============================================================================
# UNINSTALL MODE
# =============================================================================
if [ "$UNINSTALL" = true ]; then
    echo ""
    echo -e "${CYAN}sidechannel uninstaller${NC}"
    echo ""

    REMOVED_SOMETHING=false

    # --- Stop and disable systemd service ---
    if [ "$(uname)" = "Linux" ] && command -v systemctl &> /dev/null; then
        SERVICE_FILE="$HOME/.config/systemd/user/sidechannel.service"
        if systemctl --user is-active sidechannel &> /dev/null || [ -f "$SERVICE_FILE" ]; then
            echo -e "${BLUE}Removing systemd service...${NC}"
            systemctl --user stop sidechannel 2>/dev/null || true
            systemctl --user disable sidechannel 2>/dev/null || true
            rm -f "$SERVICE_FILE"
            systemctl --user daemon-reload
            echo -e "  ${GREEN}✓${NC} Service stopped and removed"
            REMOVED_SOMETHING=true
        fi
    fi

    # --- Stop Docker containers ---
    if command -v docker &> /dev/null; then
        for CONTAINER in sidechannel signal-api; do
            if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
                echo -e "${BLUE}Stopping Docker container: ${CONTAINER}...${NC}"
                docker stop "$CONTAINER" 2>/dev/null || true
                docker rm "$CONTAINER" 2>/dev/null || true
                echo -e "  ${GREEN}✓${NC} Container $CONTAINER removed"
                REMOVED_SOMETHING=true
            fi
        done
    fi

    # --- Remove install directory (with confirmation) ---
    if [ -d "$INSTALL_DIR" ]; then
        echo ""
        echo -e "${YELLOW}The install directory contains your configuration and data:${NC}"
        echo -e "  ${CYAN}$INSTALL_DIR${NC}"
        echo ""
        echo "  This includes settings.yaml, .env (API keys), Signal data,"
        echo "  and any plugin data."
        echo ""
        read -p "Remove install directory? [y/N] " -n 1 -r
        echo ""
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm -rf "$INSTALL_DIR"
            echo -e "  ${GREEN}✓${NC} Removed $INSTALL_DIR"
            REMOVED_SOMETHING=true
        else
            echo "  Kept $INSTALL_DIR"
        fi
    fi

    if [ "$REMOVED_SOMETHING" = true ]; then
        echo ""
        echo -e "${GREEN}sidechannel has been uninstalled.${NC}"
    else
        echo -e "${YELLOW}Nothing to uninstall.${NC} No service, containers, or install directory found."
        echo ""
        echo "  Expected install dir: $INSTALL_DIR"
        echo "  Set SIDECHANNEL_DIR if installed elsewhere."
    fi
    echo ""
    exit 0
fi

# Banner
echo -e "${CYAN}"
cat << 'EOF'
     _     _           _                            _
 ___(_) __| | ___  ___| |__   __ _ _ __  _ __   ___| |
/ __| |/ _` |/ _ \/ __| '_ \ / _` | '_ \| '_ \ / _ \ |
\__ \ | (_| |  __/ (__| | | | (_| | | | | | | |  __/ |
|___/_|\__,_|\___|\___|_| |_|\__,_|_| |_|_| |_|\___|_|

EOF
echo -e "${NC}"
echo -e "${GREEN}Signal + Claude AI Bot Installer${NC}"
echo ""

# -----------------------------------------------------------------------------
# Install mode selection
# -----------------------------------------------------------------------------
if [ -z "$INSTALL_MODE" ]; then
    echo -e "${BLUE}How would you like to install?${NC}"
    echo ""
    echo "  1) Docker (recommended) — everything runs in containers"
    echo "  2) Local  — Python venv with optional systemd service"
    echo ""
    read -p "> " INSTALL_CHOICE
    case "$INSTALL_CHOICE" in
        1|docker|Docker)
            INSTALL_MODE="docker"
            ;;
        2|local|Local)
            INSTALL_MODE="local"
            ;;
        *)
            INSTALL_MODE="docker"
            echo -e "  Defaulting to Docker install."
            ;;
    esac
    echo ""
fi

# =============================================================================
# DOCKER INSTALL MODE
# =============================================================================
if [ "$INSTALL_MODE" = "docker" ]; then

    # -------------------------------------------------------------------------
    # Docker prerequisites
    # -------------------------------------------------------------------------
    echo -e "${BLUE}Checking prerequisites...${NC}"

    if ! command -v docker &> /dev/null; then
        echo -e "${RED}Error: Docker not found${NC}"
        echo -e "Install Docker: https://docs.docker.com/get-docker/"
        exit 1
    fi
    echo -e "  ${GREEN}✓${NC} Docker"

    if ! docker info &> /dev/null; then
        echo -e "${RED}Error: Docker daemon is not running${NC}"
        echo -e "Start Docker: sudo systemctl start docker"
        exit 1
    fi
    echo -e "  ${GREEN}✓${NC} Docker daemon running"

    # Check for docker compose (v2 plugin or standalone)
    if docker compose version &> /dev/null; then
        COMPOSE_CMD="docker compose"
    elif command -v docker-compose &> /dev/null; then
        COMPOSE_CMD="docker-compose"
    else
        echo -e "${RED}Error: Docker Compose not found${NC}"
        echo -e "Install Docker Compose: https://docs.docker.com/compose/install/"
        exit 1
    fi
    echo -e "  ${GREEN}✓${NC} Docker Compose"

    # Claude CLI (required for /ask, /do, /complex commands)
    if command -v claude &> /dev/null; then
        echo -e "  ${GREEN}✓${NC} Claude CLI"
    elif [ -f "$HOME/.local/bin/claude" ]; then
        echo -e "  ${GREEN}✓${NC} Claude CLI ($HOME/.local/bin/claude)"
    else
        echo -e "${YELLOW}Warning: Claude CLI not found${NC}"
        echo -e "  sidechannel requires Claude CLI for code commands (/ask, /do, /complex)."
        echo -e "  Install: ${CYAN}https://docs.anthropic.com/en/docs/claude-code${NC}"
        read -p "  Continue anyway? [y/N] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
    echo ""

    # -------------------------------------------------------------------------
    # Create directory structure
    # -------------------------------------------------------------------------
    echo -e "${BLUE}Creating directory structure...${NC}"

    mkdir -p "$INSTALL_DIR"
    mkdir -p "$CONFIG_DIR"
    mkdir -p "$DATA_DIR"
    mkdir -p "$LOGS_DIR"
    mkdir -p "$SIGNAL_DATA_DIR"

    echo -e "  ${GREEN}✓${NC} Created $INSTALL_DIR"

    # -------------------------------------------------------------------------
    # Copy source files
    # -------------------------------------------------------------------------
    echo -e "${BLUE}Copying source files...${NC}"

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    if [ -d "$SCRIPT_DIR/sidechannel" ]; then
        cp -r "$SCRIPT_DIR/sidechannel" "$INSTALL_DIR/"
        echo -e "  ${GREEN}✓${NC} Copied sidechannel package"
    else
        echo -e "${RED}Error: sidechannel package not found in $SCRIPT_DIR${NC}"
        exit 1
    fi

    # Copy plugins if present
    if [ -d "$SCRIPT_DIR/plugins" ]; then
        cp -r "$SCRIPT_DIR/plugins" "$INSTALL_DIR/"
        echo -e "  ${GREEN}✓${NC} Copied plugins"
    fi

    cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"

    # Copy Docker files
    cp "$SCRIPT_DIR/Dockerfile" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/docker-compose.yml" "$INSTALL_DIR/"
    echo -e "  ${GREEN}✓${NC} Copied Docker files"

    # Copy config templates
    if [ -d "$SCRIPT_DIR/config" ]; then
        cp "$SCRIPT_DIR/config/"*.example "$CONFIG_DIR/" 2>/dev/null || true
        cp "$SCRIPT_DIR/config/CLAUDE.md" "$CONFIG_DIR/" 2>/dev/null || true
        echo -e "  ${GREEN}✓${NC} Copied config templates"
    fi

    # -------------------------------------------------------------------------
    # Interactive configuration (same prompts, with fixed sed)
    # -------------------------------------------------------------------------
    echo ""
    echo -e "${BLUE}Configuration${NC}"
    echo ""

    SETTINGS_FILE="$CONFIG_DIR/settings.yaml"
    if [ ! -f "$SETTINGS_FILE" ]; then
        if [ -f "$CONFIG_DIR/settings.yaml.example" ]; then
            cp "$CONFIG_DIR/settings.yaml.example" "$SETTINGS_FILE"
        else
            cat > "$SETTINGS_FILE" << 'YAML'
# sidechannel configuration

# Phone numbers authorized to use the bot (E.164 format)
allowed_numbers:
  - "+1XXXXXXXXXX"  # Replace with your number

# Signal CLI REST API (container name resolves via Docker network)
signal_api_url: "http://signal-api:8080"

# Memory System
memory:
  session_timeout: 30
  max_context_tokens: 1500

# Autonomous Tasks
autonomous:
  enabled: true
  poll_interval: 30
  quality_gates: true

# Optional: sidechannel AI assistant (OpenAI or Grok)
sidechannel_assistant:
  enabled: false
YAML
        fi
    fi

    echo -e "Enter your phone number in E.164 format (e.g., +15551234567):"
    read -p "> " PHONE_NUMBER

    if [ -n "$PHONE_NUMBER" ]; then
        if [[ ! "$PHONE_NUMBER" =~ ^\+[1-9][0-9]{6,14}$ ]]; then
            echo -e "${YELLOW}Warning: Phone number doesn't appear to be in E.164 format${NC}"
            read -p "Continue anyway? [y/N] " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                echo "Please re-run the installer with a valid phone number."
                exit 1
            fi
        fi
        sed_inplace "s/+1XXXXXXXXXX/$PHONE_NUMBER/" "$SETTINGS_FILE"
        echo -e "  ${GREEN}✓${NC} Phone number configured"
    fi

    ENV_FILE="$CONFIG_DIR/.env"
    if [ ! -f "$ENV_FILE" ]; then
        cat > "$ENV_FILE" << EOF
# sidechannel environment variables

# Optional: OpenAI API key (for sidechannel AI assistant)
# OPENAI_API_KEY=

# Optional: Grok API key (for sidechannel AI assistant)
# GROK_API_KEY=
EOF
    fi

    echo ""
    read -p "Enable sidechannel AI assistant (OpenAI or Grok)? [y/N] " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        sed_inplace "s/enabled: false/enabled: true/" "$SETTINGS_FILE"
        echo ""
        echo "  Which provider? (1) OpenAI  (2) Grok"
        read -p "  > " PROVIDER_CHOICE
        echo ""
        if [ "$PROVIDER_CHOICE" = "1" ]; then
            echo -e "Enter your OpenAI API key:"
            read -p "> " -s OPENAI_KEY
            echo ""
            if [ -n "$OPENAI_KEY" ]; then
                sed_inplace "s/^# OPENAI_API_KEY=.*/OPENAI_API_KEY=$OPENAI_KEY/" "$ENV_FILE"
                echo -e "  ${GREEN}✓${NC} OpenAI enabled and configured"
            fi
        else
            echo -e "Enter your Grok API key:"
            read -p "> " -s GROK_KEY
            echo ""
            if [ -n "$GROK_KEY" ]; then
                sed_inplace "s/^# GROK_API_KEY=.*/GROK_API_KEY=$GROK_KEY/" "$ENV_FILE"
                echo -e "  ${GREEN}✓${NC} Grok enabled and configured"
            fi
        fi
    fi

    # -------------------------------------------------------------------------
    # Signal device linking (Docker mode)
    # -------------------------------------------------------------------------
    echo ""
    echo -e "${BLUE}Signal Device Linking${NC}"
    echo ""

    read -p "Set up Signal device linking now? [Y/n] " -n 1 -r
    echo ""

    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        echo -e "${CYAN}Starting Signal container for device linking...${NC}"

        docker stop signal-api 2>/dev/null || true
        docker rm signal-api 2>/dev/null || true

        docker run -d \
            --name signal-api \
            --restart unless-stopped \
            -p "127.0.0.1:8080:8080" \
            -v "$SIGNAL_DATA_DIR:/home/.local/share/signal-cli" \
            -e MODE=native \
            bbernhard/signal-cli-rest-api:0.80

        echo "Waiting for container to start..."
        sleep 5

        if ! docker ps | grep -q signal-api; then
            echo -e "${RED}Error: Signal container failed to start${NC}"
            docker logs signal-api 2>&1 | tail -10
            exit 1
        fi

        echo ""
        echo -e "${GREEN}╔════════════════════════════════════════════════════════════════╗${NC}"
        echo -e "${GREEN}║                   SIGNAL DEVICE LINKING                        ║${NC}"
        echo -e "${GREEN}╠════════════════════════════════════════════════════════════════╣${NC}"
        echo -e "${GREEN}║                                                                ║${NC}"
        echo -e "${GREEN}║  1. Open Signal on your phone                                  ║${NC}"
        echo -e "${GREEN}║  2. Go to Settings > Linked Devices                            ║${NC}"
        echo -e "${GREEN}║  3. Tap 'Link New Device'                                      ║${NC}"
        echo -e "${GREEN}║  4. Scan the QR code at the URL below                          ║${NC}"
        echo -e "${GREEN}║                                                                ║${NC}"
        echo -e "${GREEN}╚════════════════════════════════════════════════════════════════╝${NC}"
        echo ""
        echo "  QR code: http://127.0.0.1:8080/v1/qrcodelink?device_name=sidechannel"
        echo ""

        LINK_URI=$(curl -s "http://127.0.0.1:8080/v1/qrcodelink?device_name=sidechannel" | grep -o 'sgnl://[^"]*' 2>/dev/null || true)

        if command -v qrencode &> /dev/null && [ -n "$LINK_URI" ]; then
            echo -e "${GREEN}Terminal QR Code:${NC}"
            echo ""
            echo "$LINK_URI" | qrencode -t ANSIUTF8
            echo ""
        fi

        read -p "Press Enter after you've scanned the QR code and linked the device..."

        echo ""
        echo -e "${CYAN}Verifying device link...${NC}"
        sleep 2

        # Stop the linking container — docker compose will manage it
        docker stop signal-api 2>/dev/null || true
        docker rm signal-api 2>/dev/null || true
        echo -e "  ${GREEN}✓${NC} Signal device linked"
    fi

    # -------------------------------------------------------------------------
    # Build and start containers
    # -------------------------------------------------------------------------
    echo ""
    echo -e "${BLUE}Building and starting containers...${NC}"

    cd "$INSTALL_DIR"
    $COMPOSE_CMD build
    $COMPOSE_CMD up -d

    echo ""
    echo -e "  ${GREEN}✓${NC} Containers started"
    echo ""

    # -------------------------------------------------------------------------
    # Docker summary
    # -------------------------------------------------------------------------
    echo -e "${GREEN}╔════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║              sidechannel installation complete!                ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "Installation directory: ${CYAN}$INSTALL_DIR${NC}"
    echo ""
    echo -e "${YELLOW}Useful commands:${NC}"
    echo ""
    echo "  View logs:          $COMPOSE_CMD -f $INSTALL_DIR/docker-compose.yml logs -f sidechannel"
    echo "  Stop:               $COMPOSE_CMD -f $INSTALL_DIR/docker-compose.yml down"
    echo "  Restart:            $COMPOSE_CMD -f $INSTALL_DIR/docker-compose.yml restart"
    echo "  Rebuild after edit: $COMPOSE_CMD -f $INSTALL_DIR/docker-compose.yml up -d --build"
    echo ""
    echo -e "Configuration: ${CYAN}$CONFIG_DIR/settings.yaml${NC}"
    echo -e "Environment:   ${CYAN}$CONFIG_DIR/.env${NC}"
    echo ""
    echo -e "${CYAN}Documentation: https://github.com/hackingdave/sidechannel${NC}"
    echo ""

    exit 0
fi

# =============================================================================
# LOCAL INSTALL MODE
# =============================================================================

# -----------------------------------------------------------------------------
# Prerequisite checks
# -----------------------------------------------------------------------------
echo -e "${BLUE}Checking prerequisites...${NC}"

# Python 3.10+
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
    MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)
    if [ "$MAJOR" -lt 3 ] || ([ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 10 ]); then
        echo -e "${RED}Error: Python 3.10+ required (found $PYTHON_VERSION)${NC}"
        exit 1
    fi
    echo -e "  ${GREEN}✓${NC} Python $PYTHON_VERSION"
else
    echo -e "${RED}Error: Python 3 not found${NC}"
    exit 1
fi

# Claude CLI
if command -v claude &> /dev/null; then
    echo -e "  ${GREEN}✓${NC} Claude CLI"
elif [ -f "$HOME/.local/bin/claude" ]; then
    echo -e "  ${GREEN}✓${NC} Claude CLI ($HOME/.local/bin/claude)"
else
    echo -e "${YELLOW}Warning: Claude CLI not found${NC}"
    echo -e "  sidechannel requires Claude CLI for code commands (/ask, /do, /complex)."
    echo -e "  Install: ${CYAN}https://docs.anthropic.com/en/docs/claude-code${NC}"
    read -p "  Continue anyway? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo ""

# -----------------------------------------------------------------------------
# Create directory structure
# -----------------------------------------------------------------------------
echo -e "${BLUE}Creating directory structure...${NC}"

mkdir -p "$INSTALL_DIR"
mkdir -p "$CONFIG_DIR"
mkdir -p "$DATA_DIR"
mkdir -p "$LOGS_DIR"
if [ "$SKIP_SIGNAL" = false ]; then
    mkdir -p "$SIGNAL_DATA_DIR"
fi

echo -e "  ${GREEN}✓${NC} Created $INSTALL_DIR"

# -----------------------------------------------------------------------------
# Copy source files
# -----------------------------------------------------------------------------
echo -e "${BLUE}Copying source files...${NC}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Copy Python package
if [ -d "$SCRIPT_DIR/sidechannel" ]; then
    cp -r "$SCRIPT_DIR/sidechannel" "$INSTALL_DIR/"
    echo -e "  ${GREEN}✓${NC} Copied sidechannel package"
else
    echo -e "${RED}Error: sidechannel package not found in $SCRIPT_DIR${NC}"
    exit 1
fi

# Copy config templates
if [ -d "$SCRIPT_DIR/config" ]; then
    cp "$SCRIPT_DIR/config/"*.example "$CONFIG_DIR/" 2>/dev/null || true
    cp "$SCRIPT_DIR/config/CLAUDE.md" "$CONFIG_DIR/" 2>/dev/null || true
    echo -e "  ${GREEN}✓${NC} Copied config templates"
fi

# Copy requirements
cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"

# -----------------------------------------------------------------------------
# Create virtual environment
# -----------------------------------------------------------------------------
echo -e "${BLUE}Setting up Python virtual environment...${NC}"

if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo -e "  ${GREEN}✓${NC} Virtual environment created"
fi

source "$VENV_DIR/bin/activate"

if "$VENV_DIR/bin/pip" freeze 2>/dev/null | grep -q aiohttp; then
    echo -e "  ${GREEN}✓${NC} Dependencies already installed"
else
    pip install --upgrade pip -q
    pip install -r "$INSTALL_DIR/requirements.txt" -q
    echo -e "  ${GREEN}✓${NC} Dependencies installed"
fi

# -----------------------------------------------------------------------------
# Interactive configuration
# -----------------------------------------------------------------------------
echo ""
echo -e "${BLUE}Configuration${NC}"
echo ""

# Create settings.yaml from template
SETTINGS_FILE="$CONFIG_DIR/settings.yaml"
if [ ! -f "$SETTINGS_FILE" ]; then
    if [ -f "$CONFIG_DIR/settings.yaml.example" ]; then
        cp "$CONFIG_DIR/settings.yaml.example" "$SETTINGS_FILE"
    else
        cat > "$SETTINGS_FILE" << 'YAML'
# sidechannel configuration

# Phone numbers authorized to use the bot (E.164 format)
allowed_numbers:
  - "+1XXXXXXXXXX"  # Replace with your number

# Signal CLI REST API
signal_api_url: "http://127.0.0.1:8080"

# Memory System
memory:
  session_timeout: 30
  max_context_tokens: 1500

# Autonomous Tasks
autonomous:
  enabled: true
  poll_interval: 30
  quality_gates: true

# Optional: sidechannel AI assistant (OpenAI or Grok)
sidechannel_assistant:
  enabled: false
YAML
    fi
fi

# Get phone number
echo -e "Enter your phone number in E.164 format (e.g., +15551234567):"
read -p "> " PHONE_NUMBER

if [ -n "$PHONE_NUMBER" ]; then
    # Validate E.164 format
    if [[ ! "$PHONE_NUMBER" =~ ^\+[1-9][0-9]{6,14}$ ]]; then
        echo -e "${YELLOW}Warning: Phone number doesn't appear to be in E.164 format (e.g., +15551234567)${NC}"
        read -p "Continue anyway? [y/N] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            echo "Please re-run the installer with a valid phone number."
            exit 1
        fi
    fi
    # Update settings.yaml with phone number
    sed_inplace "s/+1XXXXXXXXXX/$PHONE_NUMBER/" "$SETTINGS_FILE"
    echo -e "  ${GREEN}✓${NC} Phone number configured"
fi

# Create .env file
ENV_FILE="$CONFIG_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" << EOF
# sidechannel environment variables

# Optional: OpenAI API key (for sidechannel AI assistant)
# OPENAI_API_KEY=

# Optional: Grok API key (for sidechannel AI assistant)
# GROK_API_KEY=
EOF
fi

# Ask about optional AI assistant
echo ""
read -p "Enable sidechannel AI assistant (OpenAI or Grok)? [y/N] " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sed_inplace "s/enabled: false/enabled: true/" "$SETTINGS_FILE"
    echo ""
    echo "  Which provider? (1) OpenAI  (2) Grok"
    read -p "  > " PROVIDER_CHOICE
    echo ""
    if [ "$PROVIDER_CHOICE" = "1" ]; then
        echo -e "Enter your OpenAI API key:"
        read -p "> " -s OPENAI_KEY
        echo ""
        if [ -n "$OPENAI_KEY" ]; then
            sed_inplace "s/^# OPENAI_API_KEY=.*/OPENAI_API_KEY=$OPENAI_KEY/" "$ENV_FILE"
            echo -e "  ${GREEN}✓${NC} OpenAI enabled and configured"
        fi
    else
        echo -e "Enter your Grok API key:"
        read -p "> " -s GROK_KEY
        echo ""
        if [ -n "$GROK_KEY" ]; then
            sed_inplace "s/^# GROK_API_KEY=.*/GROK_API_KEY=$GROK_KEY/" "$ENV_FILE"
            echo -e "  ${GREEN}✓${NC} Grok enabled and configured"
        fi
    fi
fi

# -----------------------------------------------------------------------------
# Signal Protocol Bridge Setup
# -----------------------------------------------------------------------------
if [ "$SKIP_SIGNAL" = false ]; then
    echo ""
    echo -e "${BLUE}Signal Protocol Bridge${NC}"
    echo ""
    echo "  sidechannel needs a Signal protocol bridge (signal-cli-rest-api)"
    echo "  to send and receive messages. How would you like to run it?"
    echo ""

    # Determine available options
    DOCKER_AVAILABLE=false
    if command -v docker &> /dev/null && docker info &> /dev/null; then
        DOCKER_AVAILABLE=true
    fi

    if [ "$DOCKER_AVAILABLE" = true ]; then
        echo "  1) Docker container (recommended)"
        echo "  2) Native signal-cli (install separately)"
        echo "  3) Already running / set up later"
        echo ""
        read -p "  > " SIGNAL_CHOICE
    else
        echo "  1) Native signal-cli (install separately)"
        echo "  2) Already running / set up later"
        echo ""
        read -p "  > " SIGNAL_NATIVE_CHOICE
        # Remap choices since Docker isn't an option
        case "$SIGNAL_NATIVE_CHOICE" in
            1) SIGNAL_CHOICE="2" ;;
            *) SIGNAL_CHOICE="3" ;;
        esac
    fi

    echo ""

    case "$SIGNAL_CHOICE" in
        1)
            # Docker-based Signal bridge
            REMOTE_MODE=false
            SIGNAL_BIND="127.0.0.1"

            read -p "Is this a remote/headless server (VPS, cloud)? [y/N] " -n 1 -r
            echo ""

            if [[ $REPLY =~ ^[Yy]$ ]]; then
                REMOTE_MODE=true
                SIGNAL_BIND="0.0.0.0"
                echo -e "${YELLOW}  Note: Signal API will be temporarily exposed on 0.0.0.0:8080"
                echo -e "  for QR code access. It will be locked to localhost after linking.${NC}"
                echo ""
            fi

            mkdir -p "$SIGNAL_DATA_DIR"

            echo -e "${CYAN}Starting Signal bridge container...${NC}"
            docker pull bbernhard/signal-cli-rest-api:0.80 -q

            docker stop signal-api 2>/dev/null || true
            docker rm signal-api 2>/dev/null || true

            docker run -d \
                --name signal-api \
                --restart unless-stopped \
                -p "$SIGNAL_BIND:8080:8080" \
                -v "$SIGNAL_DATA_DIR:/home/.local/share/signal-cli" \
                -e MODE=native \
                bbernhard/signal-cli-rest-api:0.80

            echo "  Waiting for container..."
            sleep 5

            if ! docker ps | grep -q signal-api; then
                echo -e "${RED}  Signal container failed to start${NC}"
                docker logs signal-api 2>&1 | tail -5
            else
                echo -e "  ${GREEN}✓${NC} Signal bridge running"
                echo ""

                # --- Device linking ---
                echo -e "${GREEN}  Link your phone to sidechannel:${NC}"
                echo ""
                echo "  1. Open Signal on your phone"
                echo "  2. Settings > Linked Devices > Link New Device"
                echo "  3. Scan the QR code:"
                echo ""

                LINK_URI=$(curl -s "http://127.0.0.1:8080/v1/qrcodelink?device_name=sidechannel" | grep -o 'sgnl://[^"]*' 2>/dev/null || true)

                if command -v qrencode &> /dev/null && [ -n "$LINK_URI" ]; then
                    echo "$LINK_URI" | qrencode -t ANSIUTF8
                    echo ""
                fi

                if [ "$REMOTE_MODE" = true ]; then
                    SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
                    [ -z "$SERVER_IP" ] && SERVER_IP="<your-server-ip>"
                    echo -e "     Browser: ${CYAN}http://${SERVER_IP}:8080/v1/qrcodelink?device_name=sidechannel${NC}"
                else
                    echo -e "     Browser: ${CYAN}http://127.0.0.1:8080/v1/qrcodelink?device_name=sidechannel${NC}"
                fi

                if ! command -v qrencode &> /dev/null; then
                    echo ""
                    echo -e "     ${YELLOW}Tip:${NC} Install 'qrencode' for terminal QR display"
                fi

                echo ""
                read -p "Press Enter after scanning the QR code..."

                echo ""
                echo -e "${CYAN}Verifying...${NC}"
                sleep 2

                ACCOUNTS=$(curl -s "http://127.0.0.1:8080/v1/accounts" 2>/dev/null)
                if echo "$ACCOUNTS" | grep -q "+"; then
                    LINKED_NUMBER=$(echo "$ACCOUNTS" | grep -o '+[0-9]*' | head -1)
                    echo -e "  ${GREEN}✓${NC} Device linked: $LINKED_NUMBER"

                    if [ "$LINKED_NUMBER" != "$PHONE_NUMBER" ] && [ -n "$LINKED_NUMBER" ]; then
                        sed_inplace "s/$PHONE_NUMBER/$LINKED_NUMBER/" "$SETTINGS_FILE" 2>/dev/null || true
                    fi
                else
                    echo -e "${YELLOW}  Could not verify link. Check: http://127.0.0.1:8080/v1/accounts${NC}"
                fi

                # Lock down to localhost if remote mode was used
                if [ "$REMOTE_MODE" = true ]; then
                    echo -e "${CYAN}  Securing Signal API to localhost only...${NC}"
                    docker stop signal-api 2>/dev/null || true
                    docker rm signal-api 2>/dev/null || true

                    docker run -d \
                        --name signal-api \
                        --restart unless-stopped \
                        -p 127.0.0.1:8080:8080 \
                        -v "$SIGNAL_DATA_DIR:/home/.local/share/signal-cli" \
                        -e MODE=native \
                        bbernhard/signal-cli-rest-api:0.80

                    sleep 3
                    if docker ps | grep -q signal-api; then
                        echo -e "  ${GREEN}✓${NC} Signal API secured (127.0.0.1 only)"
                    fi
                fi

                echo -e "  ${GREEN}✓${NC} Signal bridge configured"
            fi
            ;;

        2)
            # Native signal-cli setup instructions
            echo -e "${BLUE}Native signal-cli Setup${NC}"
            echo ""
            echo "  Install signal-cli for your platform:"
            echo ""
            if [ "$(uname)" = "Darwin" ]; then
                echo -e "    ${CYAN}brew install signal-cli${NC}"
            else
                echo -e "    Download: ${CYAN}https://github.com/AsamK/signal-cli/releases${NC}"
            fi
            echo ""
            echo "  Then start the REST API:"
            echo ""
            echo -e "    Download signal-cli-rest-api:"
            echo -e "    ${CYAN}https://github.com/bbernhard/signal-cli-rest-api${NC}"
            echo ""
            echo "  Or link your device directly with signal-cli:"
            echo ""
            echo -e "    ${CYAN}signal-cli link -n sidechannel${NC}"
            echo ""
            echo "  Make sure the REST API is accessible at the URL in your"
            echo "  settings.yaml (default: http://127.0.0.1:8080)"
            echo ""

            # Check if signal-cli is already installed and offer to link now
            if command -v signal-cli &> /dev/null; then
                echo -e "  ${GREEN}✓${NC} signal-cli found"
                echo ""
                read -p "  Link your Signal account now using signal-cli? [Y/n] " -n 1 -r
                echo ""

                if [[ ! $REPLY =~ ^[Nn]$ ]]; then
                    echo ""
                    echo -e "${CYAN}Starting device link...${NC}"
                    echo ""
                    echo "  1. Open Signal on your phone"
                    echo "  2. Settings > Linked Devices > Link New Device"
                    echo "  3. Scan the QR code that appears below:"
                    echo ""

                    # signal-cli link outputs a URI — display as QR if possible
                    LINK_URI=$(signal-cli link -n sidechannel 2>/dev/null | grep -o 'sgnl://[^"]*' || true)

                    if [ -n "$LINK_URI" ]; then
                        if command -v qrencode &> /dev/null; then
                            echo "$LINK_URI" | qrencode -t ANSIUTF8
                        else
                            echo "  Link URI: $LINK_URI"
                            echo ""
                            echo -e "  ${YELLOW}Tip:${NC} Install 'qrencode' to display as QR code"
                        fi
                        echo ""
                        echo -e "  ${GREEN}✓${NC} Device linking initiated"
                    else
                        echo -e "${YELLOW}  Could not get link URI from signal-cli.${NC}"
                        echo "  Try manually: signal-cli link -n sidechannel"
                    fi
                fi
            fi

            SIGNAL_NATIVE=true
            SKIP_SIGNAL=true
            ;;

        *)
            # Skip for now
            echo "  Skipping Signal setup. You can set it up later."
            echo ""
            echo "  See: ${CYAN}https://github.com/bbernhard/signal-cli-rest-api${NC}"
            SKIP_SIGNAL=true
            ;;
    esac
fi

# -----------------------------------------------------------------------------
# Systemd service
# -----------------------------------------------------------------------------
INSTALLED_SERVICE=false
if [ "$SKIP_SYSTEMD" = false ] && [ "$(uname)" = "Linux" ] && command -v systemctl &> /dev/null; then
    echo ""
    read -p "Install as a systemd service (auto-start on boot)? [Y/n] " -n 1 -r
    echo ""

    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        SERVICE_FILE="$HOME/.config/systemd/user/sidechannel.service"
        mkdir -p "$HOME/.config/systemd/user"

        cat > "$SERVICE_FILE" << EOF
[Unit]
Description=sidechannel - Signal Claude Bot
After=network.target docker.service

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
Environment="PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin"
EnvironmentFile=$CONFIG_DIR/.env
ExecStart=$VENV_DIR/bin/python -m sidechannel
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF

        systemctl --user daemon-reload
        systemctl --user enable sidechannel
        loginctl enable-linger "$USER" 2>/dev/null || true

        INSTALLED_SERVICE=true
        echo -e "  ${GREEN}✓${NC} Service installed and enabled"
    fi
fi

# -----------------------------------------------------------------------------
# Create run script
# -----------------------------------------------------------------------------
RUN_SCRIPT="$INSTALL_DIR/run.sh"
cat > "$RUN_SCRIPT" << EOF
#!/bin/bash
# Start sidechannel manually

cd "$INSTALL_DIR"
source "$VENV_DIR/bin/activate"
source "$CONFIG_DIR/.env"

python -m sidechannel
EOF
chmod +x "$RUN_SCRIPT"

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║              sidechannel installation complete!                ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Install dir: ${CYAN}$INSTALL_DIR${NC}"
echo -e "  Config:      ${CYAN}$CONFIG_DIR/settings.yaml${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo ""

STEP=1

# Claude CLI
if ! command -v claude &> /dev/null && ! [ -f "$HOME/.local/bin/claude" ]; then
    echo "  $STEP. Install Claude CLI: https://docs.anthropic.com/en/docs/claude-code"
    STEP=$((STEP + 1))
    echo "  $STEP. Authenticate: claude login"
    STEP=$((STEP + 1))
else
    echo "  $STEP. Authenticate Claude (if not already): claude login"
    STEP=$((STEP + 1))
fi

# Signal pairing
if [ "${SIGNAL_NATIVE:-}" = true ]; then
    echo ""
    echo "  $STEP. Start the Signal bridge (signal-cli-rest-api) on port 8080"
    STEP=$((STEP + 1))
    echo "     See: https://github.com/bbernhard/signal-cli-rest-api"
elif [ "$SKIP_SIGNAL" = true ]; then
    echo ""
    echo "  $STEP. Set up Signal protocol bridge:"
    STEP=$((STEP + 1))
    echo "     Option A: Docker"
    echo "       docker run -d --name signal-api --restart unless-stopped \\"
    echo "         -p 127.0.0.1:8080:8080 -e MODE=native \\"
    echo "         bbernhard/signal-cli-rest-api:0.80"
    echo "     Option B: Native signal-cli"
    if [ "$(uname)" = "Darwin" ]; then
        echo "       brew install signal-cli"
    else
        echo "       https://github.com/AsamK/signal-cli/releases"
    fi
    echo "     Then pair: http://127.0.0.1:8080/v1/qrcodelink?device_name=sidechannel"
fi

# How to start
echo ""
if [ "$INSTALLED_SERVICE" = true ]; then
    echo "  $STEP. Start sidechannel:"
    STEP=$((STEP + 1))
    echo "     systemctl --user start sidechannel"
    echo ""
    echo "     View logs: journalctl --user -u sidechannel -f"
else
    echo "  $STEP. Start sidechannel:"
    STEP=$((STEP + 1))
    echo "     $RUN_SCRIPT"
fi

echo ""
echo "  $STEP. Send a message to your Signal number to test: /help"
echo ""
echo -e "${CYAN}Documentation: https://github.com/hackingdave/sidechannel${NC}"
echo ""

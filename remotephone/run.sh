#!/bin/bash
# RemotePhone Linux Client launcher
# Automatically sets up a virtual environment, installs deps, and runs the client.
# For pip users: just run `remotephone` directly instead.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Check system dependencies
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 is required but not installed."
    echo "  Install with: sudo apt install python3 python3-venv python3-pip"
    exit 1
fi

# Check for PortAudio (needed by sounddevice for audio playback)
if ! ldconfig -p 2>/dev/null | grep -q libportaudio; then
    echo "NOTE: libportaudio2 not found — audio playback will be disabled."
    echo "  Install with: sudo apt install libportaudio2"
fi

# Create virtual environment if needed
cd "$SCRIPT_DIR"
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    echo "Installing dependencies..."
    source ./venv/bin/activate
    pip install --upgrade pip -q
    pip install -r requirements.txt -q
else
    source ./venv/bin/activate
fi

echo "Starting RemotePhone client..."
cd "$PROJECT_ROOT"
python -m remotephone.main "$@"

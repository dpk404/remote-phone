#!/bin/bash
# RemotePhone desktop client launcher (Linux / macOS)
# Automatically sets up a virtual environment, installs deps, and runs the client.
# For pip users: just run `remotephone` directly instead.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Check system dependencies
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 is required but not installed."
    echo "  Install with: sudo apt install python3 python3-venv python3-pip"
    exit 1
fi

# Linux only: sounddevice needs the system PortAudio library (the macOS wheel bundles it)
if command -v ldconfig >/dev/null && ! ldconfig -p | grep -q libportaudio; then
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
    pip install -e . -q
else
    source ./venv/bin/activate
fi

echo "Starting RemotePhone client..."
python -m remotephone.main "$@"

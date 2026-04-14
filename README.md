# RemotePhone

**Mirror and control your Android phone from your Linux desktop — no USB debugging required.**

RemotePhone is a two-part system: an Android app that captures and streams your phone's screen, and a Linux desktop client that displays the stream and lets you control the phone with your mouse and keyboard.

Unlike scrcpy (which requires ADB/USB debugging), RemotePhone works entirely over WiFi using standard Android APIs.

---

## Features

- **Native resolution** screen mirroring over WiFi
- **Full remote control** — tap, swipe, long press, scroll, system keys
- **Audio mirroring** with automatic phone speaker muting (Android 10+)
- **No USB debugging** — no ADB, no developer mode needed
- **Keyboard input** — type text directly into phone text fields, including PIN pad support
- **Low latency** — H.264 hardware encoding, CBR mode, direct frame routing, smart frame dropping
- **Auto-discovery** — scans local network and auto-connects to the phone
- **Auto-reconnect** — reconnects automatically on connection loss with exponential backoff
- **Wake on input** — wakes the phone screen when you interact from the desktop
- **Dark theme** — sleek UI on both phone and desktop

## Architecture

```
┌─────────────────────┐         WebSocket (WiFi)         ┌──────────────────────┐
│    Android Phone     │ <------------------------------> │   Linux Desktop      │
│                      │                                  │                      │
│  MediaProjection --> │  H.264 video frames ---------->  │  PyAV decoder        │
│  MediaCodec H.264    │  Raw PCM audio --------------->  │  sounddevice player  │
│  AudioPlaybackCapt.  │  <-- Touch/key JSON commands --  │  PyQt6 display       │
│  AccessibilityServ.  │                                  │  Mouse/keyboard      │
└─────────────────────┘                                  └──────────────────────┘
```

---

## Setup

### 1. Android App

**Requirements:** Android 7.0+ (API 24). Audio mirroring requires Android 10+.

**Option A — Install pre-built APK:**

1. Transfer `RemotePhone-debug.apk` to your phone and install it
2. Open the app and tap **"Open Accessibility Settings"** -> find **RemotePhone** -> **Enable** it

**Option B — Build from source:**

1. Open the `android/` folder in **Android Studio**
2. Let Gradle sync and download dependencies
3. Connect your phone (or use wireless install) and click **Run**
4. Enable the accessibility service as above

### 2. Linux Client

**Requirements:** Python 3.10+, FFmpeg libraries.

**Option A — Install via pip (recommended):**

```bash
# Install system dependencies (Ubuntu/Debian)
sudo apt install ffmpeg libportaudio2

# Install RemotePhone
pip install remotephone

# Run it
remotephone
```

**Option B — Run from source:**

```bash
# Install system dependencies (Ubuntu/Debian)
sudo apt install python3 python3-venv python3-pip libportaudio2 ffmpeg

# Run the client
cd remotephone
chmod +x run.sh
./run.sh
```

The launcher script automatically creates a virtual environment and installs Python dependencies.

---

## Usage

1. **On your phone:** Open RemotePhone -> tap **"Start Mirroring"** -> grant the screen capture permission
2. **On your laptop:** Run the Linux client — it auto-scans the network and connects if one phone is found
3. If auto-connect doesn't work, enter the phone's IP (shown in the app) and click **Connect**

### Controls

| Input | Action |
|-------|--------|
| Left click | Tap |
| Click + drag | Swipe |
| Hold click (> 0.5s) | Long press |
| Scroll wheel | Scroll |
| Horizontal scroll (2-finger trackpad) | Back gesture |
| Right click | Back |
| Middle click | Back |
| Escape | Back |
| Home key | Home |
| F2 | Recent apps |
| F5 | Notifications |
| F6 | Quick Settings |
| F10 | Lock screen |
| F11 | Fullscreen toggle |
| Any typing | Text input into focused field |
| Backspace | Delete character (or Back if no text field) |
| Delete | Delete forward |
| Ctrl+A / C / X / V | Select all / Copy / Cut / Paste |
| Enter | Confirm / IME action / PIN submit |

### Keyboard and Text Input

When you tap a text field on the phone, you can type directly from your desktop keyboard. The text is inserted at the cursor position with full cursor-aware editing.

**PIN/Password fields** are handled specially — keyboard digits click the on-screen PIN pad buttons via the accessibility tree, and Enter searches for the confirm/OK button.

### Audio

Toggle the **Audio** checkbox in the Linux client to stream phone audio to your laptop. When enabled, the phone speakers are automatically muted so audio only plays on the desktop. Volume is restored when audio streaming is disabled or mirroring stops.

Requires Android 10+ and `libportaudio2` on Linux.

---

## Protocol

Communication uses WebSocket on port **8765**.

- **Video:** H.264 NAL units with a 9-byte binary header (frame type, timestamp, size)
- **Audio:** Raw PCM (16-bit LE, 44100 Hz, stereo) with the same binary header
- **Control:** JSON text messages for tap, swipe, scroll, key actions, text input

### Encoding Settings

| Setting | Value |
|---------|-------|
| Codec | H.264 (AVC) hardware-accelerated |
| Profile | Main |
| Bitrate | Adaptive (8 bits/pixel, 4-40 Mbps) |
| Frame rate | 30 FPS |
| Bitrate mode | CBR |
| Keyframe interval | 1 second |
| Low latency | Enabled (Android 11+) |

---

## Known Limitations

- **Lock screen** displays as black — this is an Android OS security restriction on MediaProjection. You can still type your PIN from the keyboard (digits click the PIN pad buttons).
- **Secure screens** (banking apps, DRM content) also display as black for the same reason.
- Both devices must be on the **same WiFi network**.
- Some device manufacturers may restrict the AccessibilityService or MediaProjection behavior.
- Text input uses `ACTION_SET_TEXT` which may not work in all apps (games, custom views).

---

## Project Structure

```
remote_phone/
├── android/                    # Android app (Kotlin)
│   ├── app/src/main/java/com/remotephone/
│   │   ├── MainActivity.kt              # UI + permission flow
│   │   ├── ScreenCaptureService.kt       # MediaProjection + H.264 encoding
│   │   ├── RemoteAccessibilityService.kt # Gesture dispatch + text input
│   │   └── MirrorWebSocketServer.kt      # WebSocket server + backpressure
│   └── app/src/main/res/                 # Layouts, drawables, configs
│
├── remotephone/                # Python desktop client (pip install remotephone)
│   ├── main.py                 # Entry point
│   ├── ui/main_window.py       # PyQt6 window + video display
│   ├── network/ws_client.py    # WebSocket client + auto-reconnect
│   ├── network/scanner.py      # Network auto-discovery
│   ├── decoder/video_decoder.py # H.264 decoding (PyAV/FFmpeg)
│   ├── decoder/audio_player.py  # PCM audio playback
│   ├── input/input_handler.py   # Mouse/keyboard + gesture detection
│   ├── requirements.txt
│   └── run.sh                  # Auto-setup launcher (for running from source)
│
├── pyproject.toml              # Python package config
├── .github/workflows/          # CI/CD (APK build + PyPI publish)
└── README.md
```

## License

MIT

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="assets/logo-light.png">
    <img src="https://raw.githubusercontent.com/dpk404/remote-phone/main/assets/logo-badge.png" alt="RemotePhone" width="420">
  </picture>
</p>

<p align="center">
  <strong>Mirror and control your Android phone from your desktop, no USB debugging required.</strong>
</p>

RemotePhone is a two-part system: an Android app that captures and streams your phone's screen, and a desktop client for Linux, macOS, and Windows that displays the stream and lets you control the phone with your mouse and keyboard.

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
- **Approval on the phone** before a computer can see or control the screen, with optional remembering
- **Encrypted connection** with a certificate the phone keeps in its keystore
- **Landscape support** that turns the desktop window with the phone
- **Instant picture** on connect, even when the phone screen is static
- **Dark theme** — sleek UI on both phone and desktop

## Architecture

```
┌──────────────────────┐    WebSocket over TLS (WiFi)     ┌──────────────────────┐
│    Android Phone     │ <------------------------------> │   Desktop Client     │
│                      │                                  │                      │
│  MediaProjection --> │  H.264 video frames ---------->  │  PyAV decoder        │
│  MediaCodec H.264    │  Raw PCM audio --------------->  │  sounddevice player  │
│  AudioPlaybackCapt.  │  <-- Touch/key JSON commands --  │  PyQt6 display       │
│  AccessibilityServ.  │                                  │  Mouse/keyboard      │
└──────────────────────┘                                  └──────────────────────┘
```

---

## Setup

### 1. Android App

**Requirements:** Android 7.0+ (API 24). Audio mirroring requires Android 10+.

**Option A — Install pre-built APK:**

1. Download the latest APK from [Releases](https://github.com/dpk404/remote-phone/releases) and install it
2. **Android 13+ only:** Go to **Settings -> Apps -> RemotePhone -> ⋮ menu -> "Allow restricted settings"** (required for sideloaded apps to use accessibility)
3. Open the app and tap **"Open Accessibility Settings"** -> find **RemotePhone** -> **Enable** it

**Option B — Build from source:**

1. Open the `android/` folder in **Android Studio**
2. Let Gradle sync and download dependencies
3. Connect your phone (or use wireless install) and click **Run**
4. Enable the accessibility service as above

### 2. Desktop Client (Linux, macOS, Windows)

**Requirements:** Python 3.10+. The pip wheels bundle FFmpeg on every platform and PortAudio on macOS and Windows. Linux needs the system PortAudio library for audio playback.

**Option A: install via pip (recommended)**

```bash
# Linux only (Debian/Ubuntu): PortAudio for audio playback
sudo apt install libportaudio2

# All platforms
pip install remote-phone
remotephone
```

**Option B: run from source**

```bash
# Linux only (Debian/Ubuntu). macOS and Windows just need Python 3.10+.
sudo apt install python3 python3-venv python3-pip libportaudio2

# Linux / macOS: extract the tarball (if downloaded from Releases), then run
chmod +x run.sh
./run.sh

# Windows
python -m venv venv
venv\Scripts\activate
pip install -e .
remotephone
```

The launcher script creates a virtual environment and installs the package into it.

---

## Usage

1. **On your phone:** Open RemotePhone -> tap **"Start Mirroring"** -> grant the screen capture permission
2. **On your computer:** Run the desktop client. It auto-scans the network and connects if one phone is found
3. **Back on the phone:** a connection request appears as a notification and as a card on the app's main screen. Tap **Allow**. The picture shows up at once
4. If auto-connect doesn't work, enter the phone's IP (shown in the app) and click **Connect**

### Who may connect

Every decision is made on the phone; the desktop never asks anything.

- A computer connecting for the first time gets neither video nor control until you tap **Allow**. Deny, or no answer within a minute, closes the connection and the desktop shows why.
- Allow lasts for the current mirroring session, so reconnects are silent. Stopping and starting mirroring asks again.
- The app's **Settings** screen has three switches: **Ask before a computer connects** (on), **Remember allowed computers** (off), and **Allow several computers at once** (off). With remembering on, an allowed computer connects without asking in later sessions until you tap **Forget remembered computers**. With several off, a second computer is rejected while one is watching.
- The phone's notification and status line show which computers are watching.
- If notifications are blocked for RemotePhone, the request still appears on the app's main screen.

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
| Ctrl+A / C / X | Select all / Copy / Cut (copy and cut also land on the desktop clipboard) |
| Ctrl+V | Paste the desktop clipboard into the phone |
| Enter | Confirm / IME action / PIN submit |

### Keyboard and Text Input

When you tap a text field on the phone, you can type directly from your desktop keyboard. On Android 13+ this goes through the real input pipeline automatically, works in every app including text fields inside web pages, and your normal on-screen keyboard stays selected. On Android 12 and older, enable and select the **RemotePhone Keyboard** (the app's Remote Typing Setup card opens the settings) for the same reliability; without it, typing falls back to accessibility text actions, which work in native text fields but are unreliable in browsers.

**PIN/Password fields** are handled specially — keyboard digits click the on-screen PIN pad buttons via the accessibility tree, and Enter searches for the confirm/OK button.

### Audio

Toggle the **Audio** checkbox in the desktop client to stream phone audio to your computer. When enabled, the phone speakers are automatically muted so audio only plays on the desktop. Volume is restored when audio streaming is disabled or mirroring stops.

Requires Android 10+ and `libportaudio2` on Linux.

---

## Protocol

Communication uses WebSocket over TLS on port **8765**. The phone serves a self-signed certificate it generated once in its keystore. Desktop clients from 2.0 need the 2.0 phone app and vice versa.

- **Video:** H.264 NAL units with a 9-byte binary header (frame type, timestamp, size)
- **Audio:** Raw PCM (16-bit LE, 44100 Hz, stereo) with the same binary header
- **Control:** JSON text messages for tap, swipe, scroll, key actions, text input

### Handshake

1. The client sends `hello` with a random id it made for this phone. The phone replies with `info` (device, screen size).
2. Unknown id: the phone sends `approval: pending` and asks its owner. Allow sends `approval: granted` with a random secret, then the current group of pictures and the live stream. Deny closes the connection with code 1008.
3. Known id: the phone sends a `challenge` nonce. The client answers `auth` with an HMAC-SHA256 over the nonce and the SHA-256 fingerprint of the certificate it connected to. A wrong answer closes with 1008. Binding the answer to the certificate means a relay on the network cannot stand in for the phone.
4. Nothing but `hello` and `auth` is accepted from a client that has not been granted. The network scanner sends `hello` with `probe: true`, which only returns `info` and never prompts.

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
- Remembered computers depend on the phone's certificate and the desktop's stored secret. Clearing the app's data on either side, or reinstalling, asks for approval again.

---

## Project Structure

```
remote_phone/
├── android/                    # Android app (Kotlin)
│   ├── app/src/main/java/com/remotephone/
│   │   ├── MainActivity.kt              # UI, permission flow, connection requests
│   │   ├── SettingsActivity.kt          # Who may connect
│   │   ├── Prefs.kt                     # Settings and remembered computers
│   │   ├── ScreenCaptureService.kt       # MediaProjection + H.264 encoding, approval prompts
│   │   ├── RemoteAccessibilityService.kt # Gesture dispatch + text input
│   │   ├── MirrorWebSocketServer.kt      # WebSocket server, consent gate, picture replay
│   │   └── Tls.kt                        # Keystore certificate for the TLS server
│   └── app/src/main/res/                 # Layouts, drawables, configs
│
├── remotephone/                # Python desktop client (pip install remote-phone)
│   ├── main.py                 # Entry point
│   ├── ui/main_window.py       # PyQt6 window + video display
│   ├── network/ws_client.py    # WebSocket client + auto-reconnect
│   ├── network/scanner.py      # Network auto-discovery
│   ├── decoder/video_decoder.py # H.264 decoding (PyAV/FFmpeg)
│   ├── decoder/audio_player.py  # PCM audio playback
│   └── input/input_handler.py   # Mouse/keyboard + gesture detection
│
├── run.sh                      # Auto-setup launcher for Linux/macOS (running from source)
├── pyproject.toml              # Python package config
├── .github/workflows/          # CI/CD (APK build + PyPI publish)
└── README.md
```

## License

MIT

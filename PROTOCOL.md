# RemotePhone protocol

Communication uses WebSocket over TLS on port **8765**. The phone serves a self-signed certificate it generated once in its keystore. Desktop clients from 2.0 need the 2.0 phone app and vice versa.

- **Binary frames** start with a 9-byte big-endian header: type (1 byte), timestamp in milliseconds (4), payload size (4)
- **Video:** H.264 NAL units. Type `0x00` is the SPS/PPS config, `0x01` a keyframe, `0x02` a delta frame
- **Audio:** Raw PCM (16-bit LE, 44100 Hz, stereo), type `0x11`
- **Control:** JSON text messages: `tap`, `swipe`, `long_press`, `scroll`, `touch`, `key`, `text`, `backspace`, `delete`, `select_all`, `copy`, `cut`, `paste`, `toggle_audio`, `request_keyframe`
- **From the phone:** `info`, `approval`, `challenge`, `audio` when audio mirroring turns on or off, `control` when remote control stops or resumes, and `clipboard` with the copied text after a `copy` or `cut`

### Messages

| Message | Fields |
|---------|--------|
| `hello` | `id`, `client`, optional `probe` and `videoBitrate` |
| `auth` | `mac` |
| `tap` | `x`, `y` |
| `swipe` | `x1`, `y1`, `x2`, `y2`, `duration` in ms (300 if left out) |
| `long_press` | `x`, `y`, `duration` in ms (1000 if left out) |
| `scroll` | `x`, `y`, `dy` |
| `touch` | `action` (`down`, `move`, `up`, `cancel`), `x`, `y` |
| `key` | `action`: `back`, `home`, `recents`, `notifications`, `quick_settings`, `power` |
| `text` | `content` (`"\n"` is Enter) |
| `toggle_audio` | `enabled` |
| `backspace`, `delete`, `select_all`, `copy`, `cut`, `paste`, `request_keyframe` | none |
| `approval` (phone) | `state` (`pending` or `granted`), `secret` when granted after asking |
| `challenge` (phone) | `nonce` |
| `clipboard` (phone) | `content` |
| `audio` (phone) | `enabled`. Sent when the state changes on either side, and to a newly allowed client when audio is on |
| `control` (phone) | `enabled`. Sent when the phone's accessibility service stops or starts, and to a newly allowed client when it is off. Clients assume control is available until told otherwise; the phone drops input while it is off |

Positions are in pixels of the picture the client receives: the `screenWidth` and `screenHeight` of the latest `info`. The phone scales them to its real screen.

### Live touch and keyframes

- `info` carries `liveTouch: true` on Android 8 and later. A client then sends `touch` with `action` `down`, `move` or `up` and a position, or `cancel`, and the phone applies each one as it arrives by continuing a single stroke. A finger left down by a client that disconnects is lifted.
- Live touch is optional on both sides: a client only sends `touch` when `info` offers it, and the phone still accepts `tap`, `swipe` and `long_press`.
- Keyframes are rare (about every 10 seconds), so a client whose decoder loses its place sends `request_keyframe`. The phone answers at most twice a second. A client that falls behind is skipped ahead to a keyframe instead of being sent frames it cannot decode.
- `hello` may carry `videoBitrate`, the bitrate to aim for in bits per second, clamped to 1 to 40 Mbps. The desktop and web clients ask for 20 Mbps. With several clients the last one wins.

### Handshake

1. The client sends `hello` with a random `id` it made for this phone and a `client` name for the Allow prompt. The phone replies with `info`: `device`, `androidVersion`, `screenWidth`, `screenHeight`, `audioAvailable` and `liveTouch`. It sends `info` again whenever the stream size changes: on rotation, and when the stream steps down. `screenWidth` and `screenHeight` are the size of the stream, which can be smaller than the screen.
2. Unknown id: the phone sends `approval: pending` and asks its owner. Allow sends `approval: granted` with a random secret, then the current group of pictures and the live stream. Deny closes the connection with code 1008. With **Ask before a computer connects** off, the phone skips the question and sends `approval: granted` without a secret.
3. Known id: the phone sends a `challenge` nonce. The client answers `auth` with an HMAC-SHA256 over the nonce and the SHA-256 fingerprint of the certificate it connected to. A wrong answer closes with 1008. Binding the answer to the certificate means a relay on the network cannot stand in for the phone.
4. Nothing but `hello` and `auth` is accepted from a client that has not been granted. The network scanner sends `hello` with `probe: true`, which only returns `info` and never prompts.
5. The phone closes with code 1008 and a reason when it turns a client away (denied, no answer, another computer already connected, replaced by a newer request from the same computer, failed authentication), and with 1000 and a reason when the owner disconnects a computer or stops mirroring. Clients do not reconnect after either, and the desktop client shows the reason.

### Encoding Settings

| Setting | Value |
|---------|-------|
| Codec | H.264 (AVC) hardware-accelerated |
| Profile | Main, at the highest level the encoder offers |
| Bitrate | The rate the client asks for (20 Mbps from the desktop and web clients). Without one: 8 bits per second for each screen pixel, kept within 4-40 Mbps |
| Frame rate | 60 FPS |
| Keyframe interval | 10 seconds, plus one whenever a client asks |
| Low latency | Enabled on Android 11+ where the encoder accepts it |
| Stream size | The screen's own size, stepped down to two thirds and then half when the encoder holds frames too long |

A stream that has stepped down to a smaller size stays there until mirroring is restarted.

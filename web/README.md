# RemotePhone Web

A static browser client for the RemotePhone Android app: two files (`index.html`, `remotephone.js`),
no build step, no dependencies, no server-side code. It speaks the same protocol as the Python desktop
client, so the Android app needs no changes.

| Desktop client (Python)        | Web client (browser built-ins)              |
|--------------------------------|---------------------------------------------|
| websocket-client over TLS      | `WebSocket` (`wss://`)                     |
| PyAV / FFmpeg H.264 decoder    | WebCodecs `VideoDecoder` (hardware, Annex-B) |
| QImage / PyQt6 widget          | `<canvas>` 2D, newest frame per animation frame |
| sounddevice (PCM s16le 44.1k)  | Web Audio `AudioBufferSourceNode` scheduling |
| QSettings (id + secret)        | `localStorage`                              |
| hmac / hashlib                 | WebCrypto `crypto.subtle` HMAC-SHA256       |
| Qt mouse / wheel / key events  | Pointer, wheel, keyboard and paste events   |

No WebAssembly is needed. Video is decoded natively in one of two ways:

- **WebCodecs** (default, lowest latency): Chrome/Edge 94+, Safari 16.4+, Firefox 130+ desktop. Only available in a secure context.
- **Media Source Extensions fallback**: the H.264 stream is remuxed to fragmented MP4 in JS and played through a hidden `<video>`. Used automatically when WebCodecs is missing (e.g. Firefox for Android) or the page is served over plain `http://` on the LAN. Slightly more latency; the status line shows `(MSE)`. Force either path with `?decoder=webcodecs` or `?decoder=mse`.

## Use

1. Host the `web/` folder anywhere static (GitHub Pages, nginx, `python3 -m http.server`), or open
   `index.html` directly. Over `https://`, `http://localhost` or `file://` WebCodecs is used; elsewhere the MSE fallback.
2. Start mirroring on the phone and note its IP.
3. **Once per browser:** open `https://<phone-ip>:8765` and accept the self-signed certificate warning
   (the page itself will then show an error; that is fine). The client shows this link if the first connection fails.
4. Enter the IP, press **Connect**, tap **Allow** on the phone.

Bookmarkable: `index.html?host=192.168.1.42&port=8765&connect=1` (add `&fp=<fingerprint>`, see below).

Controls match the desktop client: click = tap, drag = swipe, hold = long press, wheel = scroll,
horizontal two-finger swipe / right or middle click / Esc = Back, F2 recents, F5 notifications, F6 quick settings,
F10 lock, F11 fullscreen, typing goes to the focused field, Ctrl/Cmd + A/C/X, Ctrl/Cmd+V pastes the computer clipboard.
Touch screens work too (pointer events).

## The certificate fingerprint (optional)

When the phone already knows a client it sends a challenge that must be answered with an HMAC over the nonce
**and the SHA-256 of the phone's TLS certificate**. Browsers cannot read the peer certificate, so:

- Without a fingerprint the web client simply drops its identity and asks again, i.e. the phone shows
  **Allow** on every new connection. Everything else works.
- With the fingerprint filled in (`Certificate fingerprint` box, stored per host), reconnects and
  "Remember allowed computers" work silently, exactly like the desktop client. Get it with:

  ```sh
  openssl s_client -connect <phone-ip>:8765 </dev/null 2>/dev/null | openssl x509 -noout -fingerprint -sha256
  ```

  (A future app change could just display `Tls.fingerprintHex()` on the phone's main screen.)

## Testing without a phone

`test/mock_phone.py` implements the phone side (approval, challenge/auth, H.264 test pattern, a 440 Hz tone
when audio is on, clipboard on copy) and prints every command it receives:

```sh
uv venv /tmp/rp-venv && uv pip install --python /tmp/rp-venv/bin/python websockets av numpy
/tmp/rp-venv/bin/python web/test/mock_phone.py           # ws://localhost:8765
/tmp/rp-venv/bin/python web/test/mock_phone.py --tls     # wss:// with a throwaway cert, prints its fingerprint
```

Type `ws://localhost` as host to use plain WebSocket against the mock. `test/e2e_cdp.mjs` drives a headless
Chromium over CDP (connect, decode, tap, swipe, long press, back, scroll, keys, copy) and saves a screenshot.

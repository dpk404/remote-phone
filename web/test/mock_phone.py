#!/usr/bin/env python3
"""
Mock RemotePhone phone for testing the web client without an Android device.

Speaks the same protocol as MirrorWebSocketServer.kt: hello -> info, approval (auto-allowed after
1 s), challenge/auth for known ids, then streams an H.264 Annex-B test pattern (ffmpeg testsrc)
and logs every control command it receives.

    python3 -m venv .venv && .venv/bin/pip install websockets av numpy
    .venv/bin/python web/test/mock_phone.py            # ws://localhost:8765  (plain, for automation)
    .venv/bin/python web/test/mock_phone.py --tls      # wss:// with a throwaway self-signed cert
"""
import argparse, asyncio, hashlib, hmac, json, os, secrets, ssl, struct, subprocess, tempfile, time

import websockets

W, H = 540, 1170
FAKE_FP = "AB" * 32

def encode_stream():
    """10 s of a moving test pattern, H.264 baseline, keyframe every second (like the phone)."""
    import av, numpy as np
    out = os.path.join(tempfile.gettempdir(), "remotephone-mock.h264")
    if not os.path.exists(out):
        with av.open(out, "w", format="h264") as c:
            s = c.add_stream("libx264", rate=30)
            s.width, s.height, s.pix_fmt = W, H, "yuv420p"
            s.options = {"profile": "baseline", "g": "30", "bf": "0", "tune": "zerolatency"}
            yy, xx = np.mgrid[0:H, 0:W]
            for i in range(300):
                img = np.zeros((H, W, 3), np.uint8)
                img[..., 0] = (xx + i * 4) % 256
                img[..., 1] = (yy + i * 2) % 256
                img[..., 2] = 128
                bar = (i * 6) % (H - 80)
                img[bar:bar + 80, :, :] = 255
                for pkt in s.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
                    c.mux(pkt)
            for pkt in s.encode():
                c.mux(pkt)
    # one packet per access unit; strip SPS/PPS/SEI/AUD so they travel as the separate config frame
    def nals(buf):
        idx, i = [], 0
        while True:
            j = buf.find(b"\x00\x00\x01", i)
            if j < 0: break
            idx.append(j - 1 if j > 0 and buf[j - 1] == 0 else j); i = j + 3
        return [buf[a:b] for a, b in zip(idx, idx[1:] + [len(buf)])]
    nal_type = lambda n: n[n.index(b"\x00\x00\x01") + 3] & 0x1F
    config, frames = b"", []
    with av.open(out) as c:
        for pkt in c.demux(video=0):
            if pkt.size == 0: continue
            parts = nals(bytes(pkt))
            if not config: config = b"".join(n for n in parts if nal_type(n) in (7, 8))
            pic = b"".join(n for n in parts if nal_type(n) in (1, 5))
            frames.append((1 if any(nal_type(n) == 5 for n in parts) else 2, pic))
    return config, frames

def frame(t, payload, ts=0):
    return struct.pack(">BII", t, ts & 0xFFFFFFFF, len(payload)) + payload

approved = {}  # id -> secret

async def handler(ws, fp):
    config, frames = STREAM
    hello = json.loads(await ws.recv())
    print("<-", hello, flush=True)
    await ws.send(json.dumps({"type": "info", "device": "Mock Pixel", "screenWidth": W, "screenHeight": H,
                              "audioAvailable": True, "androidVersion": "15"}))
    cid = hello.get("id")
    if cid in approved:
        nonce = secrets.token_hex(32)
        await ws.send(json.dumps({"type": "challenge", "nonce": nonce}))
        msg = json.loads(await ws.recv()); print("<-", msg, flush=True)
        exp = hmac.new(approved[cid].encode(), f"{nonce}|{fp}".encode(), hashlib.sha256).hexdigest()
        if msg.get("mac") != exp:
            await ws.close(1008, "Authentication failed"); return
        await ws.send(json.dumps({"type": "approval", "state": "granted"}))
    else:
        await ws.send(json.dumps({"type": "approval", "state": "pending"}))
        await asyncio.sleep(1)
        approved[cid] = secrets.token_hex(32)
        await ws.send(json.dumps({"type": "approval", "state": "granted", "secret": approved[cid]}))

    audio_on = False
    async def reader():
        nonlocal audio_on
        async for m in ws:
            print("<-", m, flush=True)
            try:
                c = json.loads(m)
                if c.get("type") == "toggle_audio": audio_on = c.get("enabled")
                if c.get("type") == "copy": await ws.send(json.dumps({"type": "clipboard", "content": "hola desde el móvil"}))
            except Exception: pass
    rt = asyncio.create_task(reader())
    try:
        await ws.send(frame(0x00, config))
        t0 = time.monotonic(); n = 0; phase = 0.0
        while True:
            t, nal = frames[n % len(frames)]
            if n and n % len(frames) == 0: await ws.send(frame(0x00, config))
            await ws.send(frame(t, nal, int((time.monotonic() - t0) * 1000)))
            if audio_on and n % 3 == 0:  # ~100 ms of a 440 Hz tone
                import math
                samples = 4410; buf = bytearray()
                for k in range(samples):
                    v = int(3000 * math.sin(phase)); phase += 2 * math.pi * 440 / 44100
                    buf += struct.pack("<hh", v, v)
                await ws.send(frame(0x11, bytes(buf)))
            n += 1
            await asyncio.sleep(max(0, t0 + n / 30 - time.monotonic()))
    except websockets.ConnectionClosed:
        pass
    finally:
        rt.cancel()

async def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--tls", action="store_true"); ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    global STREAM; STREAM = encode_stream()
    print(f"config {len(STREAM[0])}B, {len(STREAM[1])} frames", flush=True)
    sslctx, fp = None, FAKE_FP
    if a.tls:
        d = tempfile.mkdtemp(); crt, key = f"{d}/c.pem", f"{d}/k.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                        "-keyout", key, "-out", crt, "-days", "2", "-subj", "/CN=RemotePhone"], check=True, capture_output=True)
        sslctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); sslctx.load_cert_chain(crt, key)
        der = ssl.PEM_cert_to_DER_cert(open(crt).read()); fp = hashlib.sha256(der).hexdigest().upper()
    print("fingerprint", fp, flush=True)
    async with websockets.serve(lambda ws: handler(ws, fp), "0.0.0.0", a.port, ssl=sslctx, max_size=None):
        print(("wss" if a.tls else "ws") + f"://localhost:{a.port} ready", flush=True)
        await asyncio.Future()

asyncio.run(main())

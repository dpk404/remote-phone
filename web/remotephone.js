/*
 * RemotePhone Web — a static, dependency-free browser client for the RemotePhone Android app.
 *
 * Same wire protocol as the Python desktop client (remotephone/network/ws_client.py):
 *   - wss://<phone>:8765, binary frames with a 9-byte header [type u8][timestamp u32 BE][size u32 BE]
 *   - H.264 Annex-B video  -> WebCodecs VideoDecoder (hardware decode, no WASM needed)
 *   - PCM s16le 44.1 kHz stereo audio -> Web Audio
 *   - JSON text messages for hello / info / approval / challenge / auth / control commands
 */
'use strict';

const FRAME_VIDEO_CONFIG = 0x00;
const FRAME_VIDEO_KEY = 0x01;
const FRAME_VIDEO_DELTA = 0x02;
const FRAME_AUDIO_CONFIG = 0x10;
const FRAME_AUDIO_DATA = 0x11;

const $ = (id) => document.getElementById(id);
const ui = {
  host: $('host'), port: $('port'), fingerprint: $('fingerprint'), fpmsg: $('fpmsg'), connect: $('connect'),
  audio: $('audio'), status: $('status'), stage: $('stage'), canvas: $('screen'), hint: $('hint'),
  fullscreen: $('fullscreen'),
};

function setStatus(text, kind = '') {
  ui.status.textContent = text;
  ui.status.className = kind;
  ui.status.title = text;
}

function setHint(html) {
  ui.hint.style.display = html ? 'flex' : 'none';
  if (html) ui.hint.innerHTML = '<div>' + html + '</div>';
}

const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// ---------------------------------------------------------------------------------------------
// Persistent per-phone identity (localStorage), the browser's counterpart of QSettings
// ---------------------------------------------------------------------------------------------

const store = {
  get(k, d = '') { try { return localStorage.getItem('remotephone:' + k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem('remotephone:' + k, v); } catch { /* private mode */ } },
  del(k) { try { localStorage.removeItem('remotephone:' + k); } catch { /* ignore */ } },
};

function randomHex(bytes) {
  const a = crypto.getRandomValues(new Uint8Array(bytes));
  return Array.from(a, (b) => b.toString(16).padStart(2, '0')).join('');
}

function normalizeFingerprint(fp) {
  return fp.replace(/^.*=/, '').replace(/[^0-9a-fA-F]/g, '').toUpperCase();
}

// 'empty' when the box is blank, 'ok' for 64 hex characters, 'invalid' for anything else. A value
// that is silently dropped looks like a phone that keeps asking, so the box says so instead.
function fingerprintState(raw) {
  const fp = normalizeFingerprint(raw || '');
  if (!fp) return { state: 'empty', fp };
  return { state: fp.length === 64 ? 'ok' : 'invalid', fp };
}

// Red border and a line under the box while the value cannot be used
function showFingerprintState() {
  const { state } = fingerprintState(ui.fingerprint.value);
  const invalid = state === 'invalid';
  ui.fingerprint.classList.toggle('invalid', invalid);
  ui.fpmsg.style.display = invalid ? 'block' : 'none';
  ui.fpmsg.textContent = invalid
    ? 'A SHA-256 fingerprint is 64 hex characters. Fix it, or clear the box to connect without one.'
    : '';
  return state;
}

// Browsers cannot read the peer certificate, so the phone is keyed by host:port here.
// The fingerprint (needed to answer the phone's challenge) is supplied by the user.
function phoneKey(host, port) { return 'phones/' + host + ':' + port; }

async function hmacHex(secret, message) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey('raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(message));
  return Array.from(new Uint8Array(sig), (b) => b.toString(16).padStart(2, '0')).join('');
}

// ---------------------------------------------------------------------------------------------
// Video: H.264 Annex-B -> WebCodecs -> canvas
// ---------------------------------------------------------------------------------------------

function concatBytes(a, b) {
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

/** "avc1.PPCCLL" from the SPS inside an Annex-B config blob. */
function avcCodecString(config) {
  for (let i = 0; i + 4 < config.length; i++) {
    if (config[i] === 0 && config[i + 1] === 0 && config[i + 2] === 1) {
      const nal = config[i + 3];
      if ((nal & 0x1f) === 7 && i + 6 < config.length) {
        const hex = (b) => b.toString(16).padStart(2, '0');
        return 'avc1.' + hex(config[i + 4]) + hex(config[i + 5]) + hex(config[i + 6]);
      }
      i += 2;
    }
  }
  return 'avc1.42E01F'; // baseline 3.1 fallback
}

class VideoPipeline {
  constructor(canvas, onSize, onUnsupported) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d', { alpha: false, desynchronized: true });
    this.onSize = onSize;
    this.onUnsupported = onUnsupported;
    this.decoder = null;
    this.config = null;
    this.codec = null;
    this.needKey = true;
    this.pendingFrame = null;
    this.rafScheduled = false;
    this.ts = 0;
    this.decoded = 0;
  }

  static supported() { return typeof VideoDecoder === 'function'; }

  reset() {
    if (this.decoder && this.decoder.state !== 'closed') { try { this.decoder.close(); } catch { /* ignore */ } }
    this.decoder = null;
    this.config = null;
    this.codec = null;
    this.needKey = true;
    if (this.pendingFrame) { this.pendingFrame.close(); this.pendingFrame = null; }
  }

  createDecoder() {
    if (this.decoder && this.decoder.state !== 'closed') { try { this.decoder.close(); } catch { /* ignore */ } }
    this.decoder = new VideoDecoder({
      output: (frame) => this.present(frame),
      error: (e) => {
        this.decoder = null;
        this.needKey = true;
        if (e.name === 'NotSupportedError') this.onUnsupported(e);
        else console.warn('VideoDecoder error, will recreate at next keyframe:', e);
      },
    });
    this.configure();
    this.needKey = true;
  }

  /** False when this browser cannot decode the stream; the connection then switches to MSE. */
  configure() {
    try {
      // No "description" => the decoder expects Annex-B, exactly what MediaCodec emits.
      this.decoder.configure({ codec: this.codec, optimizeForLatency: true });
      return true;
    } catch (e) {
      try { this.decoder.close(); } catch { /* ignore */ }
      this.decoder = null;
      this.onUnsupported(e);
      return false;
    }
  }

  feed(type, payload) {
    if (type === FRAME_VIDEO_CONFIG) {
      this.config = payload.slice();
      this.codec = avcCodecString(this.config);
      // Encoder restarted (first connect or rotation): start a fresh decoder
      this.createDecoder();
      return;
    }
    if (!this.config) return;
    const isKey = type === FRAME_VIDEO_KEY;
    if (!this.decoder || this.decoder.state === 'closed') {
      if (!isKey) return;
      this.createDecoder();
      if (!this.decoder) return;
    } else if (isKey && this.decoder.decodeQueueSize > 4) {
      // Falling behind: a keyframe decodes on its own, so drop the backlog here rather than
      // dropping deltas, which would freeze the picture until the next keyframe
      this.decoder.reset();
      if (!this.configure()) return;
    }
    if (!isKey && this.needKey) return;
    const data = isKey ? concatBytes(this.config, payload) : payload;
    this.ts += 16_667;
    try {
      this.decoder.decode(new EncodedVideoChunk({ type: isKey ? 'key' : 'delta', timestamp: this.ts, data }));
      if (isKey) this.needKey = false;
    } catch (e) {
      console.warn('decode() failed:', e);
      this.needKey = true;
    }
  }

  present(frame) {
    // Keep only the newest decoded frame; paint once per animation frame
    if (this.pendingFrame) this.pendingFrame.close();
    this.pendingFrame = frame;
    this.decoded++;
    if (!this.rafScheduled) {
      this.rafScheduled = true;
      requestAnimationFrame(() => this.paint());
    }
  }

  paint() {
    this.rafScheduled = false;
    const frame = this.pendingFrame;
    if (!frame) return;
    this.pendingFrame = null;
    const w = frame.displayWidth, h = frame.displayHeight;
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
      this.onSize(w, h);
    }
    this.ctx.drawImage(frame, 0, 0, w, h);
    frame.close();
  }
}


// ---------------------------------------------------------------------------------------------
// Fallback video path for browsers without WebCodecs (e.g. Firefox for Android) or pages served
// over plain http: remux H.264 Annex-B into fragmented MP4 in JS and play it through Media Source
// Extensions in a hidden <video>, painted onto the same canvas.
// ---------------------------------------------------------------------------------------------

function splitNals(buf) {
  const starts = [];
  for (let i = 0; i + 2 < buf.length; i++) {
    if (buf[i] === 0 && buf[i + 1] === 0 && buf[i + 2] === 1) { starts.push(i + 3); i += 2; }
  }
  const out = [];
  for (let k = 0; k < starts.length; k++) {
    let end = k + 1 < starts.length ? starts[k + 1] - 3 : buf.length;
    while (end > starts[k] && buf[end - 1] === 0) end--; // trailing zero of a 4-byte start code
    out.push(buf.subarray(starts[k], end));
  }
  return out;
}

const u32 = (n) => [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255];
const u16 = (n) => [(n >>> 8) & 255, n & 255];
function mp4box(type, ...parts) {
  let size = 8;
  const arrs = parts.map((p) => (p instanceof Uint8Array ? p : new Uint8Array(p)));
  for (const a of arrs) size += a.length;
  const out = new Uint8Array(size);
  out.set(u32(size), 0);
  for (let i = 0; i < 4; i++) out[4 + i] = type.charCodeAt(i);
  let o = 8;
  for (const a of arrs) { out.set(a, o); o += a.length; }
  return out;
}
const MATRIX = [0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x40, 0, 0, 0];
const TIMESCALE = 90000;

function initSegment(sps, pps, w, h) {
  const avcC = mp4box('avcC', [1, sps[1], sps[2], sps[3], 0xff, 0xe1, ...u16(sps.length)], sps, [1, ...u16(pps.length)], pps);
  const avc1 = mp4box('avc1', [0, 0, 0, 0, 0, 0, 0, 1, ...new Array(16).fill(0), ...u16(w), ...u16(h),
    0, 0x48, 0, 0, 0, 0x48, 0, 0, 0, 0, 0, 0, 0, 1, ...new Array(32).fill(0), 0, 0x18, 0xff, 0xff], avcC);
  const empty = (t) => mp4box(t, [0, 0, 0, 0, 0, 0, 0, 0]);
  const stbl = mp4box('stbl', mp4box('stsd', [0, 0, 0, 0, 0, 0, 0, 1], avc1), empty('stts'), empty('stsc'),
    mp4box('stsz', new Array(12).fill(0)), empty('stco'));
  const minf = mp4box('minf', mp4box('vmhd', [0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0]),
    mp4box('dinf', mp4box('dref', [0, 0, 0, 0, 0, 0, 0, 1], mp4box('url ', [0, 0, 0, 1]))), stbl);
  const mdia = mp4box('mdia',
    mp4box('mdhd', [0, 0, 0, 0, ...u32(0), ...u32(0), ...u32(TIMESCALE), ...u32(0), 0x55, 0xc4, 0, 0]),
    mp4box('hdlr', [0, 0, 0, 0, 0, 0, 0, 0, ...[...'vide'].map((c) => c.charCodeAt(0)), ...new Array(12).fill(0), ...[...'Video'].map((c) => c.charCodeAt(0)), 0]),
    minf);
  const tkhd = mp4box('tkhd', [0, 0, 0, 3, ...u32(0), ...u32(0), ...u32(1), ...u32(0), ...u32(0), ...new Array(8).fill(0),
    0, 0, 0, 0, 0, 0, 0, 0, ...MATRIX, ...u16(w), 0, 0, ...u16(h), 0, 0]);
  const mvhd = mp4box('mvhd', [0, 0, 0, 0, ...u32(0), ...u32(0), ...u32(1000), ...u32(0), 0, 1, 0, 0, 1, 0, 0, 0,
    ...new Array(8).fill(0), ...MATRIX, ...new Array(24).fill(0), ...u32(2)]);
  const mvex = mp4box('mvex', mp4box('trex', [0, 0, 0, 0, ...u32(1), ...u32(1), ...u32(0), ...u32(0), ...u32(0)]));
  const ftyp = mp4box('ftyp', [...'isom'].map((c) => c.charCodeAt(0)), u32(0x200), [...'isomiso2avc1mp41'].map((c) => c.charCodeAt(0)));
  return concatBytes(ftyp, mp4box('moov', mvhd, mp4box('trak', tkhd, mdia), mvex));
}

function mediaSegment(seq, baseTime, duration, sample, isKey) {
  const trunLen = 8 + 12 + 12; // header, version/flags + count + data offset, one sample entry
  const mfhd = mp4box('mfhd', [0, 0, 0, 0, ...u32(seq)]);
  const tfhd = mp4box('tfhd', [0, 0x02, 0, 0, ...u32(1)]);
  const hi = Math.floor(baseTime / 0x100000000), lo = baseTime >>> 0;
  const tfdt = mp4box('tfdt', [1, 0, 0, 0, ...u32(hi), ...u32(lo)]);
  const moofSize = 8 + mfhd.length + 8 + tfhd.length + tfdt.length + trunLen;
  const trun = mp4box('trun', [0, 0, 0x07, 0x01, ...u32(1), ...u32(moofSize + 8),
    ...u32(duration), ...u32(sample.length), ...(isKey ? [0x02, 0, 0, 0] : [0x01, 0x01, 0, 0])]);
  const moof = mp4box('moof', mfhd, mp4box('traf', tfhd, tfdt, trun));
  return concatBytes(moof, mp4box('mdat', sample));
}

/** Annex-B access unit -> AVCC (4-byte lengths), dropping AUD/SPS/PPS that travel separately. */
function toAvcc(buf) {
  const nals = splitNals(buf).filter((n) => n.length && ![7, 8, 9].includes(n[0] & 0x1f));
  let size = 0;
  for (const n of nals) size += 4 + n.length;
  const out = new Uint8Array(size);
  let o = 0;
  for (const n of nals) { out.set(u32(n.length), o); out.set(n, o + 4); o += 4 + n.length; }
  return out;
}

class MsePipeline {
  constructor(canvas, onSize) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d', { alpha: false });
    this.onSize = onSize;
    this.MS = window.ManagedMediaSource || window.MediaSource;
    this.video = document.createElement('video');
    this.video.muted = true;
    this.video.playsInline = true;
    this.video.autoplay = true;
    this.video.disableRemotePlayback = true;
    this.video.style.cssText = 'position:absolute;width:1px;height:1px;opacity:0;pointer-events:none';
    document.body.appendChild(this.video);
    this.decoded = 0;
    this.codec = null;
    this.size = [0, 0];
    this.reset();
    const loop = () => { this.paint(); requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  }

  static supported() {
    const MS = window.ManagedMediaSource || window.MediaSource;
    return !!MS && MS.isTypeSupported('video/mp4; codecs="avc1.42E01F"');
  }

  setScreenSize(w, h) { this.size = [w, h]; }

  reset() {
    this.config = null; this.sps = null; this.pps = null;
    this.ms = null; this.sb = null; this.queue = []; this.started = false;
    this.seq = 1; this.time = 0;
    this.detach();
    try { this.video.load(); } catch { /* ignore */ }
  }

  detach() {
    if (this.video.src.startsWith('blob:')) URL.revokeObjectURL(this.video.src);
    this.video.removeAttribute('src');
    this.video.srcObject = null;
  }

  openSource() {
    this.detach();
    const ms = new this.MS();
    this.ms = ms; this.sb = null; this.queue = []; this.started = false; this.seq = 1; this.time = 0;
    if (window.ManagedMediaSource && ms instanceof window.ManagedMediaSource) this.video.srcObject = ms;
    else this.video.src = URL.createObjectURL(ms);
    ms.addEventListener('sourceopen', () => {
      if (ms !== this.ms) return;
      try {
        this.sb = ms.addSourceBuffer('video/mp4; codecs="' + this.codec + '"');
        this.sb.mode = 'segments';
        this.sb.addEventListener('updateend', () => this.pump());
        this.pump();
      } catch (e) { console.warn('addSourceBuffer failed', e); }
    }, { once: true });
  }

  feed(type, payload) {
    if (type === FRAME_VIDEO_CONFIG) {
      const nals = splitNals(payload);
      this.sps = nals.find((n) => (n[0] & 0x1f) === 7);
      this.pps = nals.find((n) => (n[0] & 0x1f) === 8);
      if (!this.sps || !this.pps) return;
      this.config = payload.slice();
      this.codec = avcCodecString(this.config);
      this.openSource();
      return;
    }
    if (!this.config || !this.ms) return;
    const isKey = type === FRAME_VIDEO_KEY;
    if (!this.started) {
      if (!isKey) return;
      const [w, h] = this.size[0] ? this.size : [1080, 1920];
      const init = initSegment(this.sps, this.pps, w, h);
      init.isInit = true;
      this.queue.push(init);
      this.started = true;
    } else if (this.queue.length > 6) {
      // Falling behind: drop queued media until this keyframe
      if (!isKey) return;
      this.queue = this.queue.filter((s) => s.isInit);
    }
    const dur = TIMESCALE / 30;
    this.queue.push(mediaSegment(this.seq++, this.time, dur, toAvcc(payload), isKey));
    this.time += dur;
    this.pump();
  }

  pump() {
    const sb = this.sb;
    if (!sb || sb.updating || !this.queue.length) return;
    try {
      sb.appendBuffer(this.queue.shift());
    } catch (e) {
      if (e.name === 'QuotaExceededError' && sb.buffered.length) {
        try { sb.remove(0, Math.max(0, this.video.currentTime - 2)); } catch { /* ignore */ }
      } else {
        console.warn('appendBuffer failed, restarting at next keyframe', e);
        this.openSource();
      }
    }
  }

  paint() {
    const v = this.video;
    if (!v.buffered.length) return;
    const end = v.buffered.end(v.buffered.length - 1);
    // Live edge: never let playback lag more than a few frames behind what arrived
    if (end - v.currentTime > 0.25 || v.currentTime < v.buffered.start(0)) v.currentTime = Math.max(v.buffered.start(0), end - 0.03);
    if (v.paused) v.play().catch(() => {});
    // Trim old data once more than 10 s of it has piled up, not on every frame
    if (this.sb && !this.sb.updating && v.buffered.start(0) < v.currentTime - 10) {
      try { this.sb.remove(0, v.currentTime - 5); } catch { /* ignore */ }
    }
    if (v.readyState < 2 || !v.videoWidth) return;
    const w = v.videoWidth, h = v.videoHeight;
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w; this.canvas.height = h; this.onSize(w, h);
    }
    this.ctx.drawImage(v, 0, 0, w, h);
    this.decoded++;
  }
}

// ---------------------------------------------------------------------------------------------
// Audio: raw PCM s16le, 44.1 kHz, interleaved stereo -> Web Audio
// ---------------------------------------------------------------------------------------------

class AudioPipeline {
  constructor() { this.ctx = null; this.playhead = 0; }

  start() {
    if (this.ctx) return;
    const Ctx = window.AudioContext || window.webkitAudioContext;
    this.ctx = new Ctx({ sampleRate: 44100, latencyHint: 'interactive' });
    this.ctx.resume();
    this.playhead = 0;
  }

  stop() {
    if (this.ctx) { this.ctx.close(); this.ctx = null; }
  }

  feed(payload) {
    const ctx = this.ctx;
    if (!ctx) return;
    const frames = payload.length >> 2;
    if (!frames) return;
    const buf = ctx.createBuffer(2, frames, 44100);
    const left = buf.getChannelData(0), right = buf.getChannelData(1);
    const dv = new DataView(payload.buffer, payload.byteOffset, frames * 4);
    for (let i = 0; i < frames; i++) {
      left[i] = dv.getInt16(i * 4, true) / 32768;
      right[i] = dv.getInt16(i * 4 + 2, true) / 32768;
    }
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    const now = ctx.currentTime;
    // Underrun or too much latency built up: resync with a small jitter cushion
    if (this.playhead < now || this.playhead - now > 0.35) this.playhead = now + 0.08;
    src.start(this.playhead);
    this.playhead += buf.duration;
  }
}

// ---------------------------------------------------------------------------------------------
// Input: pointer / wheel / keyboard -> phone JSON commands (ported from input_handler.py)
// ---------------------------------------------------------------------------------------------

const TAP_THRESHOLD = 10;          // px (phone space) before a press becomes a swipe
const LONG_PRESS_MIN = 500;        // ms
const HSCROLL_BACK_THRESHOLD = 400;
const VSCROLL_STEP = 60;
const VSCROLL_GAIN = 2.5;
const SCROLL_COOLDOWN = 400;       // ms: ignore taps right after scrolling (trackpad ghost taps)

const KEY_ACTIONS = { Escape: 'back', Home: 'home', F2: 'recents', F5: 'notifications', F6: 'quick_settings', F10: 'power' };
const KEY_COMMANDS = {
  Backspace: { type: 'backspace' },
  Delete: { type: 'delete' },
  Enter: { type: 'text', content: '\n' },
};
const CTRL_COMMANDS = { a: 'select_all', c: 'copy', x: 'cut' }; // v is handled through the paste event

class InputMapper {
  constructor(canvas, send) {
    this.canvas = canvas;
    this.send = send;
    this.press = null;
    this.hAccum = 0; this.hTime = 0; this.hFired = false;
    this.vAccum = 0;
    this.lastScroll = 0;

    canvas.addEventListener('pointerdown', (e) => this.onDown(e));
    canvas.addEventListener('pointermove', (e) => this.onMove(e));
    canvas.addEventListener('pointerup', (e) => this.onUp(e));
    canvas.addEventListener('pointercancel', () => { this.press = null; });
    canvas.addEventListener('contextmenu', (e) => e.preventDefault());
    canvas.addEventListener('wheel', (e) => this.onWheel(e), { passive: false });
  }

  toPhone(e) {
    const r = this.canvas.getBoundingClientRect();
    const x = ((e.clientX - r.left) / r.width) * this.canvas.width;
    const y = ((e.clientY - r.top) / r.height) * this.canvas.height;
    return { x: Math.min(Math.max(x, 0), this.canvas.width - 1), y: Math.min(Math.max(y, 0), this.canvas.height - 1) };
  }

  onDown(e) {
    ui.stage.focus({ preventScroll: true });
    if (e.button === 1 || e.button === 2) { e.preventDefault(); this.send({ type: 'key', action: 'back' }); return; }
    if (e.button !== 0) return;
    e.preventDefault();
    this.canvas.setPointerCapture(e.pointerId);
    const p = this.toPhone(e);
    const now = performance.now();
    this.press = { x: p.x, y: p.y, lx: p.x, ly: p.y, t: now, moved: false, suppressed: now - this.lastScroll < SCROLL_COOLDOWN };
  }

  onMove(e) {
    const s = this.press;
    if (!s) return;
    const p = this.toPhone(e);
    if (Math.abs(p.x - s.x) > TAP_THRESHOLD || Math.abs(p.y - s.y) > TAP_THRESHOLD) s.moved = true;
    s.lx = p.x; s.ly = p.y;
  }

  onUp(e) {
    const s = this.press;
    if (!s || e.button !== 0) return;
    this.press = null;
    const now = performance.now();
    if (s.suppressed || now - this.lastScroll < SCROLL_COOLDOWN) return;
    const elapsed = now - s.t;
    const p = this.toPhone(e);
    const r1 = (v) => Math.round(v * 10) / 10;
    if (s.moved) {
      this.send({ type: 'swipe', x1: r1(s.x), y1: r1(s.y), x2: r1(p.x), y2: r1(p.y), duration: Math.min(Math.max(Math.round(elapsed), 100), 2000) });
    } else if (elapsed >= LONG_PRESS_MIN) {
      this.send({ type: 'long_press', x: r1(s.x), y: r1(s.y), duration: Math.round(elapsed) });
    } else {
      this.send({ type: 'tap', x: r1(s.x), y: r1(s.y) });
    }
  }

  onWheel(e) {
    e.preventDefault();
    const now = performance.now();
    const unit = e.deltaMode === 1 ? 40 : e.deltaMode === 2 ? 800 : 1;
    // Qt sign convention (as in the desktop client): positive = wheel away / fingers right
    const dx = -e.deltaX * unit, dy = -e.deltaY * unit;

    if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) >= 1) {
      if (now - this.hTime > 500) { this.hAccum = 0; this.hFired = false; }
      this.hTime = now;
      this.lastScroll = now;
      if (this.hFired) return;
      this.hAccum += dx;
      if (this.hAccum > HSCROLL_BACK_THRESHOLD) {
        this.hAccum = 0; this.hFired = true;
        this.send({ type: 'key', action: 'back' });
      }
      return;
    }
    if (Math.abs(dy) < 1) return;
    if (now - this.lastScroll > 300) this.vAccum = 0;
    this.lastScroll = now;
    this.vAccum += dy;
    if (Math.abs(this.vAccum) < VSCROLL_STEP) return;
    const step = this.vAccum;
    this.vAccum = 0;
    const p = this.toPhone(e);
    this.send({ type: 'scroll', x: Math.round(p.x * 10) / 10, y: Math.round(p.y * 10) / 10, dx: Math.round(dx * 10) / 10, dy: Math.round(-step * VSCROLL_GAIN * 10) / 10 });
  }

  /** Returns true when the key was consumed. */
  onKey(e) {
    if (e.isComposing) return false;
    const mod = e.ctrlKey || e.metaKey;
    // A held system key or shortcut fires once; only text, Backspace and Delete auto-repeat (as on the desktop)
    if (e.repeat && (e.key === 'F11' || KEY_ACTIONS[e.key] || (mod && CTRL_COMMANDS[e.key.toLowerCase()]))) return true;
    if (e.key === 'F11') { e.preventDefault(); toggleFullscreen(); return true; }
    const altGr = e.getModifierState && e.getModifierState('AltGraph');
    if (KEY_ACTIONS[e.key] && !mod) { this.send({ type: 'key', action: KEY_ACTIONS[e.key] }); return true; }
    if (KEY_COMMANDS[e.key] && !mod) { this.send(KEY_COMMANDS[e.key]); return true; }
    if (mod && !altGr) {
      const cmd = CTRL_COMMANDS[e.key.toLowerCase()];
      if (cmd) { this.send({ type: cmd }); return true; }
      return false; // let Ctrl/Cmd+V reach the paste event, and other shortcuts reach the browser
    }
    // Printable text (one grapheme; dead keys arrive already composed, e.g. "á")
    if (e.key.length >= 1 && [...e.key].length === 1) { this.send({ type: 'text', content: e.key }); return true; }
    return false;
  }
}

// ---------------------------------------------------------------------------------------------
// Connection
// ---------------------------------------------------------------------------------------------

const RECONNECT_BASE = 1000, RECONNECT_MAX = 10000, RECONNECT_FACTOR = 1.5;

class PhoneConnection {
  constructor() {
    this.ws = null;
    this.wantConnected = false;
    this.everOpened = false;
    this.granted = false;
    this.delay = RECONNECT_BASE;
    this.attempt = 0;
    this.retryTimer = null;
    this.key = '';
    this.host = ''; this.port = '';
    this.info = null;
    this.fingerprint = '';
    this.video = pickVideoPipeline((e) => this.useMse(e));
    this.audio = new AudioPipeline();
    this.input = new InputMapper(ui.canvas, (cmd) => this.sendCommand(cmd));
  }

  get url() { return this.scheme + '://' + (this.host.includes(':') ? '[' + this.host + ']' : this.host) + ':' + this.port; }
  get certUrl() { return 'https://' + (this.host.includes(':') ? '[' + this.host + ']' : this.host) + ':' + this.port + '/'; }

  start(host, port, scheme = 'wss', fingerprint = '') {
    this.stop();
    this.host = host; this.port = port; this.scheme = scheme;
    this.fingerprint = fingerprint; // fixed for the session, so edits to the box do not change the answer
    this.key = phoneKey(host, port);
    this.wantConnected = true;
    this.everOpened = false;
    this.delay = RECONNECT_BASE;
    this.attempt = 0;
    this.open();
  }

  stop() {
    this.wantConnected = false;
    clearTimeout(this.retryTimer);
    if (this.ws) { try { this.ws.close(1000, 'Client disconnect'); } catch { /* ignore */ } this.ws = null; }
    this.onDisconnected();
  }

  clientId() {
    let id = store.get(this.key + '/id');
    if (!id) { id = randomHex(16); store.set(this.key + '/id', id); }
    return id;
  }

  forgetIdentity() {
    store.del(this.key + '/id');
    store.del(this.key + '/secret');
  }

  open() {
    setStatus(this.attempt ? 'Reconnecting… (attempt ' + this.attempt + ')' : 'Connecting to ' + this.host + '…');
    let ws;
    try {
      ws = new WebSocket(this.url);
    } catch (e) {
      setStatus('Invalid address: ' + e.message, 'err');
      this.wantConnected = false;
      updateButtons();
      return;
    }
    ws.binaryType = 'arraybuffer';
    this.ws = ws;
    ws.onopen = () => this.onOpen(ws);
    ws.onmessage = (ev) => { if (ws === this.ws) this.onMessage(ev.data); };
    ws.onclose = (ev) => { if (ws === this.ws) this.onClose(ev); };
    ws.onerror = () => { /* onclose follows with the details we can get */ };
  }

  onOpen(ws) {
    this.everOpened = true;
    this.attempt = 0;
    this.delay = RECONNECT_BASE;
    this.video?.reset();
    setStatus('Connected — waiting for the phone…');
    ws.send(JSON.stringify({
      type: 'hello',
      version: 1,
      client: ('RemotePhone Web on ' + browserName()).slice(0, 40),
      id: this.clientId(),
      maxWidth: 1920, maxHeight: 1920, maxFps: 60,
      videoBitrate: 8_000_000, audioBitrate: 128_000,
    }));
  }

  onMessage(data) {
    if (typeof data === 'string') { this.onText(data); return; }
    const buf = new Uint8Array(data);
    if (buf.length < 9) return;
    const dv = new DataView(data);
    const type = buf[0];
    const size = dv.getUint32(5, false);
    const payload = buf.subarray(9, 9 + size);
    if (type <= FRAME_VIDEO_DELTA) {
      this.video.feed(type, payload);
      if (type === FRAME_VIDEO_KEY && ui.hint.style.display !== 'none') setHint('');
    } else if (type === FRAME_AUDIO_DATA) {
      this.audio.feed(payload);
    } else if (type === FRAME_AUDIO_CONFIG) {
      // 44.1 kHz stereo s16le, fixed by the phone side
    }
  }

  async onText(text) {
    let msg;
    try { msg = JSON.parse(text); } catch { return; }
    switch (msg.type) {
      case 'info':
        this.info = msg;
        if (this.video.setScreenSize) this.video.setScreenSize(msg.screenWidth, msg.screenHeight);
        ui.audio.disabled = !msg.audioAvailable;
        if (!this.granted) setStatus('Connected to ' + (msg.device || 'phone') + ' — waiting for approval…');
        break;
      case 'approval':
        if (msg.secret) store.set(this.key + '/secret', msg.secret);
        if (msg.state === 'pending') {
          setStatus('Tap Allow on the phone…');
          setHint('A connection request is on the phone.<br>Tap <b>Allow</b> there.');
        } else if (msg.state === 'granted') {
          this.granted = true;
          updateButtons();
          setStatus('Connected to ' + ((this.info && this.info.device) || 'phone') + (this.video instanceof MsePipeline ? ' (MSE)' : ''), 'ok');
          setHint('Waiting for the first picture…');
          ui.stage.focus({ preventScroll: true });
          if (ui.audio.checked) this.sendCommand({ type: 'toggle_audio', enabled: true });
        }
        break;
      case 'challenge': {
        const secret = store.get(this.key + '/secret');
        if (!secret || !this.fingerprint || !crypto.subtle) {
          // Without the certificate fingerprint the challenge can't be answered from a browser.
          // Fall back to a fresh identity, which makes the phone ask its owner again.
          this.forgetIdentity();
          setStatus('Asking the phone again (no fingerprint set)…');
          this.rawReconnect();
          return;
        }
        const mac = await hmacHex(secret, (msg.nonce || '') + '|' + this.fingerprint);
        this.sendCommand({ type: 'auth', mac });
        break;
      }
      case 'clipboard':
        if (!msg.content) break;
        // No clipboard API over plain http: hand the text over in a prompt instead of losing it
        (navigator.clipboard ? navigator.clipboard.writeText(msg.content) : Promise.reject()).then(
          () => setStatus('Copied to clipboard (' + msg.content.length + ' chars)', 'ok'),
          () => { setStatus('Browser refused clipboard access', 'err'); prompt('Phone clipboard (copy it from here):', msg.content); });
        break;
      case 'error':
        setStatus(msg.message || 'Unknown error', 'err');
        break;
    }
  }

  /** Close and reopen immediately (used after rotating identity). */
  rawReconnect() {
    const old = this.ws;
    this.ws = null;
    if (old) { old.onclose = null; try { old.close(1000); } catch { /* ignore */ } }
    this.open();
  }

  /** WebCodecs is present but cannot decode this stream (no H.264 decoder, odd profile): use MSE. */
  useMse(e) {
    const old = this.video, config = old.config;
    if (!MsePipeline.supported()) { setStatus('This browser cannot decode the phone\'s video: ' + (e.message || e), 'err'); return; }
    console.warn('WebCodecs cannot decode this stream, switching to MSE:', e);
    old.reset();
    this.video = new MsePipeline(ui.canvas, fitCanvas);
    if (this.info) this.video.setScreenSize(this.info.screenWidth, this.info.screenHeight);
    if (config) this.video.feed(FRAME_VIDEO_CONFIG, config);
  }

  onClose(ev) {
    this.ws = null;
    const wasOpened = this.everOpened;
    this.onDisconnected();
    if (ev.code === 1008) {
      if (/authentication failed/i.test(ev.reason)) {
        this.forgetIdentity();
        setStatus('Authentication failed — check the certificate fingerprint. Reconnect to ask the phone again.', 'err');
      } else {
        setStatus(ev.reason || 'Rejected on the phone', 'err');
      }
      setHint(escapeHtml(ev.reason || 'Rejected on the phone'));
      this.wantConnected = false;
      updateButtons();
      return;
    }
    if (ev.code === 1000 || !this.wantConnected) {
      setStatus(ev.code === 1000 && this.wantConnected ? 'Mirroring stopped on the phone' : 'Disconnected');
      this.wantConnected = false;
      updateButtons();
      return;
    }
    if (!wasOpened && this.attempt === 0) {
      // First attempt never opened: unreachable, or (most often) the self-signed certificate
      // has not been accepted in this browser yet. Browsers give no detail, so explain both.
      const u = escapeHtml(this.certUrl);
      setHint('Could not open <code>' + escapeHtml(this.url) + '</code>.<br><br>' +
        'The phone uses a self-signed certificate. Open <a href="' + u + '" target="_blank" rel="noopener">' + u + '</a>, ' +
        'accept the warning (Advanced → Proceed), close that tab and press <b>Connect</b> again.<br><br>' +
        'Also check that mirroring is started on the phone and both devices are on the same network.');
      setStatus('Could not connect', 'err');
      this.wantConnected = false;
      updateButtons();
      return;
    }
    this.attempt++;
    setStatus('Connection lost — reconnecting in ' + (this.delay / 1000).toFixed(1) + 's', 'err');
    this.retryTimer = setTimeout(() => { if (this.wantConnected) this.open(); }, this.delay);
    this.delay = Math.min(this.delay * RECONNECT_FACTOR, RECONNECT_MAX);
  }

  onDisconnected() {
    this.granted = false;
    this.video?.reset();
    ui.audio.disabled = true;
    ui.audio.checked = false;
    this.audio.stop();
    updateButtons();
  }

  sendCommand(cmd) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN && (this.granted || cmd.type === 'auth')) {
      this.ws.send(JSON.stringify(cmd));
    }
  }
}

function browserName() {
  const ua = navigator.userAgent;
  const os = /Mac/.test(ua) ? 'Mac' : /Windows/.test(ua) ? 'Windows' : /Android/.test(ua) ? 'Android' : /Linux/.test(ua) ? 'Linux' : '';
  const br = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : 'browser';
  return br + (os ? ' / ' + os : '');
}

// ---------------------------------------------------------------------------------------------
// Layout and wiring
// ---------------------------------------------------------------------------------------------

function fitCanvas() {
  const c = ui.canvas;
  const sw = ui.stage.clientWidth - 16, sh = ui.stage.clientHeight - 16;
  const scale = Math.min(sw / c.width, sh / c.height);
  c.style.width = Math.max(1, Math.floor(c.width * scale)) + 'px';
  c.style.height = Math.max(1, Math.floor(c.height * scale)) + 'px';
}

function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen?.();
}

function pickVideoPipeline(onUnsupported) {
  const forced = new URLSearchParams(location.search).get('decoder');
  const onSize = () => fitCanvas();
  // WebCodecs is only exposed in secure contexts; MSE also works over plain http on the LAN
  if (forced !== 'mse' && window.isSecureContext && VideoPipeline.supported()) return new VideoPipeline(ui.canvas, onSize, onUnsupported);
  if (forced !== 'webcodecs' && MsePipeline.supported()) return new MsePipeline(ui.canvas, onSize);
  return null;
}

const conn = new PhoneConnection();

function updateButtons() {
  const active = conn.wantConnected;
  ui.connect.textContent = active ? 'Disconnect' : 'Connect';
  ui.connect.classList.toggle('primary', !active);
  ui.host.disabled = ui.port.disabled = active;
  for (const b of document.querySelectorAll('footer button[data-key]')) b.disabled = !conn.granted;
}

// True while the fingerprint box holds something the user entered rather than a value restored
// from storage, so changing the host afterwards must not wipe it (paste first, then type the IP)
let fpTyped = false;

ui.connect.addEventListener('click', () => {
  if (conn.wantConnected) { conn.stop(); setStatus('Disconnected'); return; }
  if (showFingerprintState() === 'invalid') { ui.fingerprint.closest('details').open = true; ui.fingerprint.focus(); return; }
  // "ws://host" forces plain WebSocket (e.g. behind a TLS-terminating proxy or for testing)
  const raw = ui.host.value.trim();
  const scheme = /^ws:\/\//i.test(raw) ? 'ws' : 'wss';
  let host = raw.replace(/^wss?:\/\//i, '').replace(/[\/\s].*$/, '');
  // "192.168.1.5:8765" typed into the host box: the port belongs in its own field
  const hostPort = host.match(/^([^:\[\]]+):(\d+)$/);
  if (hostPort) { host = hostPort[1]; ui.host.value = (scheme === 'ws' ? 'ws://' : '') + host; ui.port.value = hostPort[2]; }
  const port = (ui.port.value.trim() || '8765');
  if (!host) { ui.host.focus(); return; }
  store.set('last/host', host);
  store.set('last/port', port);
  // Saved now, keyed by the host being connected to: pasting the fingerprint before typing the
  // IP used to store it under an empty host. An emptied box forgets the stored value as well.
  const fp = fingerprintState(ui.fingerprint.value);
  if (fp.state === 'ok') store.set('fp/' + host, fp.fp); else store.del('fp/' + host);
  fpTyped = false;
  setHint('');
  conn.start(host, port, scheme, fp.fp);
  updateButtons();
});

for (const el of [ui.host, ui.port]) el.addEventListener('keydown', (e) => { if (e.key === 'Enter') ui.connect.click(); });

ui.fingerprint.addEventListener('input', () => { fpTyped = !!ui.fingerprint.value.trim(); showFingerprintState(); });
ui.host.addEventListener('change', () => {
  if (fpTyped) return;
  ui.fingerprint.value = store.get('fp/' + ui.host.value.trim());
  showFingerprintState();
});

ui.audio.addEventListener('change', () => {
  // Starting the AudioContext inside this click satisfies the browser autoplay policy
  if (ui.audio.checked) conn.audio.start(); else conn.audio.stop();
  conn.sendCommand({ type: 'toggle_audio', enabled: ui.audio.checked });
  ui.stage.focus({ preventScroll: true }); // keys go to the phone again, not to the checkbox
});

for (const b of document.querySelectorAll('footer button[data-key]')) {
  b.addEventListener('click', () => { conn.sendCommand({ type: 'key', action: b.dataset.key }); ui.stage.focus({ preventScroll: true }); });
}
ui.fullscreen.addEventListener('click', toggleFullscreen);

function typingInPageField(e) {
  const t = e.target;
  return t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || (t && t.isContentEditable);
}

document.addEventListener('keydown', (e) => {
  if (!conn.granted || typingInPageField(e)) return;
  if (conn.input.onKey(e)) e.preventDefault();
});

// Ctrl/Cmd+V: type the computer's clipboard into the phone; empty clipboard falls back to the phone's paste
document.addEventListener('paste', (e) => {
  if (!conn.granted || typingInPageField(e)) return;
  e.preventDefault();
  const text = e.clipboardData ? e.clipboardData.getData('text/plain') : '';
  conn.sendCommand(text ? { type: 'text', content: text } : { type: 'paste' });
});

new ResizeObserver(fitCanvas).observe(ui.stage);

// Restore last session; URL parameters (?host=…&port=…&fp=…&connect=1) win, handy for bookmarks
const params = new URLSearchParams(location.search);
ui.host.value = params.get('host') || store.get('last/host');
ui.port.value = params.get('port') || store.get('last/port', '8765');
ui.fingerprint.value = params.get('fp') || store.get('fp/' + ui.host.value);
fpTyped = !!params.get('fp');
showFingerprintState();
fitCanvas();
updateButtons();

if (!conn.video) {
  setHint('This browser can decode H.264 neither with WebCodecs nor with Media Source Extensions.<br>Use a recent Chrome, Edge, Firefox or Safari.');
  ui.connect.disabled = true;
} else if (params.get('connect') === '1' && ui.host.value) {
  ui.connect.click();
}

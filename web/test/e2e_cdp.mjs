// End-to-end check of the web client against mock_phone.py using a headless Chromium over CDP.
// Usage: node web/test/e2e_cdp.mjs <chrome-binary> <page-url>
import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';

const [chrome, url] = process.argv.slice(2);
const port = Number(process.env.CDP_PORT || 9333);
const proc = spawn(chrome, ['--headless=new', '--remote-debugging-port=' + port, '--user-data-dir=/tmp/rp-cdp-profile',
  '--no-first-run', '--window-size=900,1000', ...(process.env.CHROME_ARGS || '').split(' ').filter(Boolean), 'about:blank'], { stdio: 'ignore' });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let targets;
for (let i = 0; i < 50; i++) { try { targets = await (await fetch('http://127.0.0.1:' + port + '/json')).json(); break; } catch { await sleep(200); } }
const page = targets.find((t) => t.type === 'page');
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r));
ws.addEventListener('close', (e) => { console.log('CDP closed', e.code, e.reason, JSON.stringify(logs)); process.exit(2); });
const keepAlive = setInterval(() => {}, 1000);
let id = 0; const pending = new Map(); const logs = [];
ws.addEventListener('message', (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  if (m.method === 'Runtime.consoleAPICalled') logs.push(m.params.type + ': ' + m.params.args.map((a) => a.value ?? a.description).join(' '));
  if (m.method === 'Runtime.exceptionThrown') logs.push('EXC: ' + JSON.stringify(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text));
});
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const evalJs = async (expr) => (await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;

await send('Runtime.enable');
await send('Page.enable');
await send('Page.navigate', { url });
await sleep(5000);

const state = await evalJs(`({
  status: document.getElementById('status').textContent,
  canvas: [document.getElementById('screen').width, document.getElementById('screen').height],
  hint: document.getElementById('hint').style.display,
  decoded: conn.video.decoded,
  codec: conn.video.codec,
  rect: (() => { const r = document.getElementById('screen').getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })(),
})`);
console.log('STATE', JSON.stringify(state));

const [L, T, W, H] = state.rect;
const mouse = (type, x, y, extra = {}) => send('Input.dispatchMouseEvent', { type, x, y, button: 'left', buttons: type === 'mouseReleased' ? 0 : 1, clickCount: 1, ...extra });
// tap in the middle
await mouse('mousePressed', L + W / 2, T + H / 2); await sleep(60); await mouse('mouseReleased', L + W / 2, T + H / 2);
await sleep(500);
// swipe up
await mouse('mousePressed', L + W / 2, T + H * 0.8);
for (let k = 1; k <= 5; k++) { await mouse('mouseMoved', L + W / 2, T + H * (0.8 - k * 0.1)); await sleep(30); }
await mouse('mouseReleased', L + W / 2, T + H * 0.3);
await sleep(500);
// long press
await mouse('mousePressed', L + 20, T + 20); await sleep(700); await mouse('mouseReleased', L + 20, T + 20);
await sleep(500);
// right click -> back
await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: L + 30, y: T + 30, button: 'right', buttons: 2, clickCount: 1 });
await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: L + 30, y: T + 30, button: 'right', buttons: 0, clickCount: 1 });
await sleep(500);
// wheel
await send('Input.dispatchMouseEvent', { type: 'mouseWheel', x: L + W / 2, y: T + H / 2, deltaX: 0, deltaY: 120 });
await sleep(500);
// keys
for (const k of [{ key: 'h', text: 'h' }, { key: 'ñ', text: 'ñ' }, { key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 }, { key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 }, { key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 }, { key: 'F5', code: 'F5', windowsVirtualKeyCode: 116 }]) {
  await send('Input.dispatchKeyEvent', { type: 'keyDown', ...k }); await send('Input.dispatchKeyEvent', { type: 'keyUp', ...k }); await sleep(100);
}
await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'c', code: 'KeyC', modifiers: 2, windowsVirtualKeyCode: 67 });
await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'c', code: 'KeyC', modifiers: 2, windowsVirtualKeyCode: 67 });
await sleep(800);
console.log('STATUS_AFTER', await evalJs("document.getElementById('status').textContent"));
const shot = await send('Page.captureScreenshot', { format: 'png' });
writeFileSync('/tmp/rp-e2e.png', Buffer.from(shot.result.data, 'base64'));
console.log('LOGS', JSON.stringify(logs));
ws.close(); proc.kill();
process.exit(0);

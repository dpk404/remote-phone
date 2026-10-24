// Fingerprint parsing and validation, without a browser.
// Usage: node web/test/fingerprint.mjs
// The helpers are read out of the client rather than copied, so the check follows the real code.
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('../remotephone.js', import.meta.url), 'utf8');
// Each helper is a top-level function closed by a brace in column 0
function fn(name) {
  const m = src.match(new RegExp('^function ' + name + '\\(.*?^\\}', 'ms'));
  if (!m) throw new Error(name + ' not found in remotephone.js');
  return m[0];
}
const { normalizeFingerprint, fingerprintState } =
  new Function(fn('normalizeFingerprint') + fn('fingerprintState') + '\nreturn { normalizeFingerprint, fingerprintState };')();

const fp = 'A1'.repeat(32);

// Formats a phone or openssl may print
for (const raw of [fp, fp.toLowerCase(), fp.match(/../g).join(':'), 'SHA-256=' + fp, '  ' + fp + '  '])
  assert(fingerprintState(raw).state === 'ok', 'accepted: ' + raw.slice(0, 24));

// Blank means "no fingerprint", which is allowed: the phone asks its owner each time
assert(fingerprintState('').state === 'empty', 'empty');
assert(fingerprintState('   ').state === 'empty', 'blank');
assert(fingerprintState(undefined).state === 'empty', 'undefined');

// A typo must be visible, not silently dropped (an extra non-hex character is stripped, as
// copies often carry one)
for (const raw of ['A1'.repeat(31), 'A1'.repeat(33), 'not a fingerprint', fp + 'A'])
  assert(fingerprintState(raw).state === 'invalid', 'rejected: ' + raw.slice(0, 24));

// Case and separators do not change the value
assert(normalizeFingerprint(fp.toLowerCase()) === fp, 'normalised');
assert(fingerprintState('ab:cd').fp === 'ABCD', 'hex only');

function assert(cond, what) {
  if (!cond) { console.error('FAIL', what); process.exit(1); }
}
console.log('fingerprint: ok');

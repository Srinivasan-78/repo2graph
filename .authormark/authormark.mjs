#!/usr/bin/env node
/*!
 * @authormark v1 -- do not remove (authorship watermark)
 * Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
 * Author: https://github.com/Srinivasan-78
 * SPDX-License-Identifier: MIT
 * Fingerprint: AMK1.dFmQv8iPY8bY9zfOYFr8KS
 */
// authormark -- layered authorship watermarking for source code and images.
// Zero dependencies. Node >= 18.

import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import crypto from 'node:crypto';
import os from 'node:os';
import { execFileSync } from 'node:child_process';

const CWD = process.cwd();
const CONFIG_FILE = '.authormark.json';
const MANIFEST_FILE = 'AUTHORSHIP.json';
const SENTINEL = '@authormark v1';
const NOREMOVE = '-- do not remove';
// A line only counts as a header when it carries BOTH markers, so docs and
// READMEs can talk about "@authormark v1" without being mistaken for stamped files.
const isHeaderLine = l => l.includes(SENTINEL) && l.includes(NOREMOVE);
const FP_LABEL = 'Fingerprint: AMK1.';
const SKIP_DIRS = new Set(['node_modules', '.git', '.next', 'dist', 'build', 'out', 'coverage', '.turbo',
  '.vercel', 'vendor', '__pycache__', 'venv', 'site-packages', 'third_party', 'target', '.mypy_cache']);

const DEFAULT_EXTS = ['.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.css', '.scss', '.sass', '.less',
  '.py', '.go', '.rs', '.java', '.kt', '.swift', '.c', '.h', '.cpp', '.hpp', '.cs', '.php', '.rb',
  '.sh', '.bash', '.zsh', '.sql', '.lua', '.html', '.htm', '.svg', '.vue', '.svelte', '.md', '.yml', '.yaml', '.toml',
  '.bat', '.cmd', '.ps1', '.psm1', '.tf', '.tfvars', '.hcl', '.r', '.pl'];

// Extensionless files worth stamping, matched by basename.
const NAMED_FILES = new Set(['Dockerfile', 'Makefile', 'Jenkinsfile', 'Vagrantfile', 'Procfile']);

// ---------------------------------------------------------------- comment styles

const BLOCK = { open: '/*!', line: ' * ', close: ' */' };
const HTML = { open: '<!--', line: '  ', close: '-->' };
const STYLES = {
  '.js': BLOCK, '.jsx': BLOCK, '.ts': BLOCK, '.tsx': BLOCK, '.mjs': BLOCK, '.cjs': BLOCK,
  '.css': BLOCK, '.scss': BLOCK, '.sass': BLOCK, '.less': BLOCK, '.go': BLOCK, '.rs': BLOCK,
  '.java': BLOCK, '.kt': BLOCK, '.swift': BLOCK, '.c': BLOCK, '.h': BLOCK, '.cpp': BLOCK,
  '.hpp': BLOCK, '.cs': BLOCK, '.php': BLOCK, '.lua': { prefix: '-- ' }, '.sql': { prefix: '-- ' },
  '.py': { prefix: '# ' }, '.rb': { prefix: '# ' }, '.sh': { prefix: '# ' }, '.bash': { prefix: '# ' },
  '.zsh': { prefix: '# ' }, '.yml': { prefix: '# ' }, '.yaml': { prefix: '# ' }, '.toml': { prefix: '# ' },
  '.html': HTML, '.htm': HTML, '.svg': HTML, '.vue': HTML, '.svelte': HTML, '.md': HTML,
  '.bat': { prefix: 'REM ' }, '.cmd': { prefix: 'REM ' },
  '.ps1': { prefix: '# ' }, '.psm1': { prefix: '# ' }, '.pl': { prefix: '# ' }, '.r': { prefix: '# ' },
  '.tf': { prefix: '# ' }, '.tfvars': { prefix: '# ' }, '.hcl': { prefix: '# ' },
  Dockerfile: { prefix: '# ' }, Makefile: { prefix: '# ' }, Procfile: { prefix: '# ' },
  Vagrantfile: { prefix: '# ' }, Jenkinsfile: BLOCK,
};

// Basename wins over extension, so `Dockerfile` and `Makefile` are handled.
const styleFor = rel => STYLES[path.basename(rel)] || STYLES[path.extname(rel)] || BLOCK;

// ---------------------------------------------------------------- config + key

function loadConfig() {
  const p = path.join(CWD, CONFIG_FILE);
  if (!fs.existsSync(p)) die(`no ${CONFIG_FILE} here -- run:  authormark init`);
  return JSON.parse(fs.readFileSync(p, 'utf8'));
}

function keyPath(cfg) {
  return (cfg.keyFile || '~/.authormark.key').replace(/^~/, os.homedir());
}

function loadKey(cfg) {
  const p = keyPath(cfg);
  if (!fs.existsSync(p)) die(`secret key missing at ${p} -- run:  authormark init`);
  return Buffer.from(fs.readFileSync(p, 'utf8').trim(), 'hex');
}

// Canonical form: what the fingerprint is computed over. Survives CRLF churn,
// trailing whitespace and blank-line drift, so cosmetic edits don't void the mark.
function canonical(text) {
  return text.replace(/\r\n?/g, '\n').split('\n').map(l => l.replace(/[ \t]+$/, '')).join('\n').replace(/\n+$/, '') + '\n';
}

function fingerprint(key, body) {
  return crypto.createHmac('sha256', key).update(canonical(body)).digest('base64url').slice(0, 22);
}

// ---------------------------------------------------------------- header build / strip

function headerLines(cfg, fp) {
  const l = [
    `${SENTINEL} ${NOREMOVE} (authorship watermark)`,
    `Copyright (c) ${cfg.year} ${cfg.author}${cfg.email ? ` <${cfg.email}>` : ''}`,
    `Author: ${cfg.github}`,
  ];
  if (cfg.license) l.push(`SPDX-License-Identifier: ${cfg.license}`);
  l.push(`${FP_LABEL}${fp}`);
  return l;
}

function renderHeader(cfg, fp, style, zw) {
  let lines = headerLines(cfg, fp);
  if (zw) lines[0] += zwEncode(fp);
  if (style.prefix) return lines.map(l => style.prefix + l).join('\n') + '\n';
  return [style.open, ...lines.map(l => style.line + l), style.close].join('\n') + '\n';
}

// Returns {header, body} -- header is '' when the file is unstamped.
function splitHeader(text) {
  const lines = text.split('\n');
  const i = lines.findIndex(isHeaderLine);
  if (i === -1) return { header: '', body: text, at: -1 };
  let start = i, end = i;
  if (i > 0 && /^\s*(\/\*!?|<!--)\s*$/.test(lines[i - 1])) start = i - 1;
  while (end < lines.length && !lines[end].includes(FP_LABEL)) end++;
  if (end < lines.length - 1 && /^\s*(\*\/|-->)\s*$/.test(lines[end + 1])) end++;
  return { header: lines.slice(start, end + 1).join('\n'), body: lines.slice(0, start).concat(lines.slice(end + 1)).join('\n'), at: start };
}

// Where the header may legally go: after a shebang, doctype, xml prolog, or a
// YAML front-matter block (Jekyll pages and issue templates break if displaced).
function insertIndex(text) {
  const lines = text.split('\n');
  if (lines[0] === '---') {
    const close = lines.findIndex((l, i) => i > 0 && (l === '---' || l === '...'));
    if (close > 0) return lines.slice(0, close + 1).join('\n').length + 1;
  }
  if (lines[0]?.startsWith('#!')) return lines[0].length + 1;
  if (/^\s*(<\?xml|<!doctype)/i.test(lines[0] || '')) return lines[0].length + 1;
  return 0;
}

// ---------------------------------------------------------------- zero-width mark

const ZW0 = '​', ZW1 = '‌', ZWB = '⁠';

function zwEncode(s) {
  const bits = [...Buffer.from(s, 'utf8')].map(b => b.toString(2).padStart(8, '0')).join('');
  return ZWB + [...bits].map(b => (b === '1' ? ZW1 : ZW0)).join('') + ZWB;
}

function zwDecode(text) {
  const out = [];
  const re = new RegExp(`${ZWB}([${ZW0}${ZW1}]+)${ZWB}`, 'g');
  let m;
  while ((m = re.exec(text))) {
    const bits = [...m[1]].map(c => (c === ZW1 ? '1' : '0')).join('');
    const bytes = [];
    for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2));
    out.push(Buffer.from(bytes).toString('utf8'));
  }
  return out;
}

// ---------------------------------------------------------------- file walking

function walk(target, exts, acc = []) {
  const st = fs.statSync(target);
  if (st.isFile()) { if (exts.includes(path.extname(target)) || NAMED_FILES.has(path.basename(target))) acc.push(target); return acc; }
  for (const e of fs.readdirSync(target, { withFileTypes: true })) {
    if (e.name.startsWith('.') && e.name !== '.github') continue;
    if (SKIP_DIRS.has(e.name)) continue;
    walk(path.join(target, e.name), exts, acc);
  }
  return acc;
}

// Never enforce marks on generated output or vendored/third-party content.
function ignored(rel, cfg) {
  const segs = rel.split(path.sep).join('/').split('/');
  if (segs.some(s => SKIP_DIRS.has(s))) return true;
  if (segs.slice(0, -1).some(s => s.startsWith('.') && s !== '.github')) return true;
  return (cfg?.ignore || []).some(p => rel === p || rel.startsWith(p.replace(/\/+$/, '') + '/'));
}

function collect(paths, exts, cfg) {
  const targets = paths.length ? paths : ['.'];
  const files = new Set();
  for (const t of targets) {
    if (!fs.existsSync(t)) { warn(`skip (missing): ${t}`); continue; }
    for (const f of walk(t, exts)) {
      const rel = path.relative(CWD, f);
      if (!ignored(rel, cfg)) files.add(rel);
    }
  }
  return [...files].sort();
}

// ---------------------------------------------------------------- commands

function cmdInit(args) {
  const author = flag(args, '--author') || tryGit('user.name') || 'Srinivasan-78';
  const email = flag(args, '--email') || tryGit('user.email') || '';
  const github = flag(args, '--github') || 'https://github.com/Srinivasan-78';
  const license = flag(args, '--license') || 'MIT';
  const cfg = { author, email, github, year: new Date().getFullYear(), license, keyFile: '~/.authormark.key', ignore: [] };
  fs.writeFileSync(path.join(CWD, CONFIG_FILE), JSON.stringify(cfg, null, 2) + '\n');
  const kp = keyPath(cfg);
  if (fs.existsSync(kp)) {
    log(`key kept: ${kp} (already exists -- never regenerate, old fingerprints would break)`);
  } else {
    fs.writeFileSync(kp, crypto.randomBytes(32).toString('hex') + '\n', { mode: 0o600 });
    log(`key created: ${kp}  (chmod 600 -- BACK THIS UP, it is your proof of authorship)`);
  }
  log(`config written: ${CONFIG_FILE}`);
  log(`\nnext:  authormark stamp app components lib`);
}

function cmdStamp(args) {
  const cfg = loadConfig(), key = loadKey(cfg);
  const exts = flag(args, '--ext')?.split(',').map(e => (e.startsWith('.') ? e : '.' + e)) || DEFAULT_EXTS;
  const zw = args.includes('--zw');
  const dry = args.includes('--dry');
  const files = collect(positional(args), exts, cfg);
  let added = 0, refreshed = 0, same = 0;

  for (const rel of files) {
    const orig = fs.readFileSync(rel, 'utf8');
    const style = styleFor(rel);
    const { header, body } = splitHeader(orig);
    const fp = fingerprint(key, body);
    const next = renderHeader(cfg, fp, style, zw);
    if (header && header + '\n' === next.replace(/\n$/, '') + '\n') { same++; continue; }
    const at = insertIndex(body);
    const out = body.slice(0, at) + next + body.slice(at);
    if (out === orig) { same++; continue; }
    if (!dry) fs.writeFileSync(rel, out);
    header ? refreshed++ : added++;
    if (dry) log(`would ${header ? 'refresh' : 'stamp'}: ${rel}`);
  }
  log(`${dry ? '[dry] ' : ''}stamped ${added}, refreshed ${refreshed}, unchanged ${same}  (${files.length} files)`);
}

function cmdCheck(args) {
  const cfg = loadConfig();
  // CI has no access to the secret key, so fall back to presence-only checking
  // there: it still blocks a stripped header, it just can't validate the HMAC.
  const presence = args.includes('--presence') || !fs.existsSync(keyPath(cfg));
  const key = presence ? null : loadKey(cfg);
  const exts = flag(args, '--ext')?.split(',').map(e => (e.startsWith('.') ? e : '.' + e)) || DEFAULT_EXTS;
  let files;
  if (args.includes('--staged')) {
    files = execFileSync('git', ['diff', '--cached', '--name-only', '--diff-filter=ACM'], { encoding: 'utf8' })
      .split('\n')
      .filter(f => f && (exts.includes(path.extname(f)) || NAMED_FILES.has(path.basename(f))) && !ignored(f, cfg) && fs.existsSync(f));
  } else {
    files = collect(positional(args), exts, cfg);
  }
  const missing = [], tampered = [];
  for (const rel of files) {
    const text = fs.readFileSync(rel, 'utf8');
    const { header, body } = splitHeader(text);
    if (!header) { missing.push(rel); continue; }
    if (!key) continue;
    const claimed = header.match(/Fingerprint: AMK1\.([A-Za-z0-9_-]{22})/)?.[1];
    if (claimed !== fingerprint(key, body)) tampered.push(rel);
  }
  for (const f of missing) console.error(`  MISSING watermark: ${f}`);
  for (const f of tampered) console.error(`  STALE fingerprint:  ${f}  (re-run: authormark stamp ${f})`);
  if (missing.length || tampered.length) {
    console.error(`\nauthormark: ${missing.length} unmarked, ${tampered.length} stale of ${files.length}.`);
    process.exit(1);
  }
  log(`authormark: all ${files.length} files carry a watermark${presence ? ' (presence only -- no key here, fingerprints not verified).' : ' with a valid fingerprint.'}`);
}

function cmdSeal(args) {
  const cfg = loadConfig(), key = loadKey(cfg);
  const exts = flag(args, '--ext')?.split(',').map(e => (e.startsWith('.') ? e : '.' + e)) || DEFAULT_EXTS;
  const files = collect(positional(args), exts, cfg);
  const entries = files.map(rel => {
    const buf = fs.readFileSync(rel);
    return { path: rel, sha256: crypto.createHash('sha256').update(buf).digest('hex'), bytes: buf.length };
  });
  const digest = crypto.createHash('sha256')
    .update(entries.map(e => `${e.sha256}  ${e.path}`).join('\n')).digest('hex');
  const manifest = {
    schema: 'authormark/manifest/1',
    author: cfg.author, email: cfg.email, github: cfg.github,
    sealedAt: new Date().toISOString(),
    fileCount: entries.length,
    digest,
    proof: crypto.createHmac('sha256', key).update(digest).digest('hex'),
    files: entries,
  };
  fs.writeFileSync(path.join(CWD, MANIFEST_FILE), JSON.stringify(manifest, null, 2) + '\n');
  log(`sealed ${entries.length} files -> ${MANIFEST_FILE}`);
  log(`digest: ${digest}`);
  log(`\nnotarize it (free, public, timestamped):`);
  log(`  gpg --armor --detach-sign ${MANIFEST_FILE}      # if you have a GPG key`);
  log(`  ots stamp ${MANIFEST_FILE}                      # OpenTimestamps -> bitcoin-anchored proof`);
}

function cmdVerify(args) {
  const cfg = loadConfig(), key = loadKey(cfg);
  const file = positional(args)[0] || MANIFEST_FILE;
  const m = JSON.parse(fs.readFileSync(file, 'utf8'));
  const digest = crypto.createHash('sha256')
    .update(m.files.map(e => `${e.sha256}  ${e.path}`).join('\n')).digest('hex');
  const proofOk = m.proof === crypto.createHmac('sha256', key).update(digest).digest('hex');
  log(`manifest digest: ${digest === m.digest ? 'OK' : 'MISMATCH'}`);
  log(`your HMAC proof: ${proofOk ? 'OK -- this manifest was sealed with your key' : 'FAIL -- not sealed by your key'}`);
  let changed = 0, missing = 0;
  for (const e of m.files) {
    if (!fs.existsSync(e.path)) { missing++; console.error(`  gone:    ${e.path}`); continue; }
    const h = crypto.createHash('sha256').update(fs.readFileSync(e.path)).digest('hex');
    if (h !== e.sha256) { changed++; console.error(`  changed: ${e.path}`); }
  }
  log(`${m.fileCount} sealed, ${changed} changed, ${missing} gone since ${m.sealedAt}`);
  if (!proofOk || digest !== m.digest) process.exit(1);
}

function cmdScan(args) {
  const cfg = fs.existsSync(path.join(CWD, CONFIG_FILE)) ? loadConfig() : null;
  const key = cfg && fs.existsSync(keyPath(cfg)) ? loadKey(cfg) : null;
  for (const f of positional(args)) {
    log(`\n=== ${f}`);
    const buf = fs.readFileSync(f);
    const ext = path.extname(f).toLowerCase();
    if (ext === '.png') { scanPng(buf, key); continue; }
    if (ext === '.jpg' || ext === '.jpeg') { scanJpeg(buf); continue; }
    const text = buf.toString('utf8');
    const { header, body } = splitHeader(text);
    if (header) {
      log(header.split('\n').map(l => '  ' + l.trim()).join('\n'));
      const claimed = header.match(/Fingerprint: AMK1\.([A-Za-z0-9_-]{22})/)?.[1];
      if (key) log(`  -> fingerprint ${claimed === fingerprint(key, body) ? 'VALID for your key' : 'does NOT match current content'}`);
    } else log('  no visible header');
    const zw = zwDecode(text);
    if (zw.length) log(`  hidden zero-width mark(s): ${zw.join(', ')}`);
    else log('  no zero-width mark');
  }
}

// ---------------------------------------------------------------- PNG codec

const CRC_TABLE = (() => {
  const t = new Int32Array(256);
  for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c; }
  return t;
})();

function crc32(buf) {
  let c = -1;
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ -1) >>> 0;
}

function pngChunks(buf) {
  if (buf.readUInt32BE(0) !== 0x89504e47) die('not a PNG');
  const out = [];
  let p = 8;
  while (p < buf.length) {
    const len = buf.readUInt32BE(p);
    const type = buf.toString('ascii', p + 4, p + 8);
    out.push({ type, data: buf.subarray(p + 8, p + 8 + len) });
    p += 12 + len;
  }
  return out;
}

function pngSerialize(chunks) {
  const parts = [Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])];
  for (const c of chunks) {
    const len = Buffer.alloc(4); len.writeUInt32BE(c.data.length);
    const body = Buffer.concat([Buffer.from(c.type, 'ascii'), c.data]);
    const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(body));
    parts.push(len, body, crc);
  }
  return Buffer.concat(parts);
}

function unfilter(raw, width, height, bpp, rowBytes) {
  const out = Buffer.alloc(height * rowBytes);
  let pos = 0;
  for (let y = 0; y < height; y++) {
    const ft = raw[pos++];
    const line = raw.subarray(pos, pos + rowBytes); pos += rowBytes;
    const cur = out.subarray(y * rowBytes, (y + 1) * rowBytes);
    const prev = y > 0 ? out.subarray((y - 1) * rowBytes, y * rowBytes) : null;
    for (let x = 0; x < rowBytes; x++) {
      const a = x >= bpp ? cur[x - bpp] : 0;
      const b = prev ? prev[x] : 0;
      const c = prev && x >= bpp ? prev[x - bpp] : 0;
      let v = line[x];
      if (ft === 1) v += a;
      else if (ft === 2) v += b;
      else if (ft === 3) v += (a + b) >> 1;
      else if (ft === 4) {
        const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
        v += pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
      } else if (ft !== 0) die(`bad PNG filter ${ft}`);
      cur[x] = v & 0xff;
    }
  }
  return out;
}

function unpackBits(row, width, channels, depth) {
  if (depth === 8) return row;
  const out = Buffer.alloc(width * channels);
  const max = (1 << depth) - 1;
  let bit = 0;
  for (let i = 0; i < width * channels; i++) {
    const byte = row[bit >> 3];
    const shift = 8 - depth - (bit & 7);
    out[i] = (byte >> shift) & max;
    bit += depth;
  }
  return out;
}

function decodePng(buf) {
  const chunks = pngChunks(buf);
  const ihdr = chunks.find(c => c.type === 'IHDR').data;
  const width = ihdr.readUInt32BE(0), height = ihdr.readUInt32BE(4);
  const depth = ihdr[8], colorType = ihdr[9], interlace = ihdr[12];
  if (interlace) die('interlaced PNG not supported -- re-save without interlacing');
  if (depth === 16) die('16-bit PNG not supported -- convert to 8-bit');
  const channels = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 }[colorType];
  if (!channels) die(`unsupported PNG color type ${colorType}`);
  if (depth !== 8 && colorType !== 0 && colorType !== 3) die(`unsupported bit depth ${depth} for color type ${colorType}`);

  const idat = zlib.inflateSync(Buffer.concat(chunks.filter(c => c.type === 'IDAT').map(c => c.data)));
  const bitsPerPixel = channels * depth;
  const rowBytes = Math.ceil((width * bitsPerPixel) / 8);
  const bpp = Math.max(1, Math.ceil(bitsPerPixel / 8));
  const raw = unfilter(idat, width, height, bpp, rowBytes);

  const plte = chunks.find(c => c.type === 'PLTE')?.data;
  const trns = chunks.find(c => c.type === 'tRNS')?.data;
  const rgba = Buffer.alloc(width * height * 4, 255);
  const grayMax = (1 << depth) - 1;

  for (let y = 0; y < height; y++) {
    const row = unpackBits(raw.subarray(y * rowBytes, (y + 1) * rowBytes), width, channels, depth);
    for (let x = 0; x < width; x++) {
      const o = (y * width + x) * 4, s = x * channels;
      if (colorType === 0) { const v = Math.round((row[s] / grayMax) * 255); rgba[o] = rgba[o + 1] = rgba[o + 2] = v; }
      else if (colorType === 4) { rgba[o] = rgba[o + 1] = rgba[o + 2] = row[s]; rgba[o + 3] = row[s + 1]; }
      else if (colorType === 2) { rgba[o] = row[s]; rgba[o + 1] = row[s + 1]; rgba[o + 2] = row[s + 2]; }
      else if (colorType === 6) { rgba[o] = row[s]; rgba[o + 1] = row[s + 1]; rgba[o + 2] = row[s + 2]; rgba[o + 3] = row[s + 3]; }
      else { const i = row[s]; rgba[o] = plte[i * 3]; rgba[o + 1] = plte[i * 3 + 1]; rgba[o + 2] = plte[i * 3 + 2]; rgba[o + 3] = trns && i < trns.length ? trns[i] : 255; }
    }
  }
  const hasAlpha = colorType === 4 || colorType === 6 || !!trns;
  return { width, height, rgba, hasAlpha, chunks };
}

function encodePng({ width, height, rgba, hasAlpha, texts }) {
  const ch = hasAlpha ? 4 : 3;
  const rowBytes = width * ch;
  const raw = Buffer.alloc(height * (rowBytes + 1));
  for (let y = 0; y < height; y++) {
    raw[y * (rowBytes + 1)] = 0; // filter: None (keeps LSBs byte-addressable)
    for (let x = 0; x < width; x++) {
      const s = (y * width + x) * 4, d = y * (rowBytes + 1) + 1 + x * ch;
      raw[d] = rgba[s]; raw[d + 1] = rgba[s + 1]; raw[d + 2] = rgba[s + 2];
      if (ch === 4) raw[d + 3] = rgba[s + 3];
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0); ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; ihdr[9] = hasAlpha ? 6 : 2; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  const chunks = [{ type: 'IHDR', data: ihdr }];
  for (const [k, v] of texts) chunks.push({ type: 'tEXt', data: Buffer.concat([Buffer.from(k, 'latin1'), Buffer.from([0]), Buffer.from(v, 'latin1')]) });
  chunks.push({ type: 'IDAT', data: zlib.deflateSync(raw, { level: 9 }) });
  chunks.push({ type: 'IEND', data: Buffer.alloc(0) });
  return pngSerialize(chunks);
}

// ---------------------------------------------------------------- LSB steganography

// Payload is written repeatedly across the RGB LSBs so a partial crop still
// leaves whole copies behind. Each copy: "AMK1" + u16 length + bytes + u32 crc.
function lsbEmbed(rgba, width, height, payload) {
  const rec = Buffer.concat([
    Buffer.from('AMK1', 'ascii'),
    (() => { const b = Buffer.alloc(2); b.writeUInt16BE(payload.length); return b; })(),
    payload,
    (() => { const b = Buffer.alloc(4); b.writeUInt32BE(crc32(payload)); return b; })(),
  ]);
  const slots = width * height * 3;
  const need = rec.length * 8;
  if (need > slots) die(`image too small for the hidden mark (needs ${Math.ceil(need / 3)} px, has ${width * height})`);
  let bit = 0;
  for (let i = 0; i < slots; i++) {
    const px = Math.floor(i / 3), ch = i % 3;
    const byte = rec[Math.floor(bit / 8) % rec.length];
    const b = (byte >> (7 - (bit % 8))) & 1;
    const o = px * 4 + ch;
    rgba[o] = (rgba[o] & 0xfe) | b;
    bit++;
  }
  return Math.floor(slots / need);
}

function lsbExtract(rgba, width, height) {
  const slots = width * height * 3;
  const bytes = Buffer.alloc(Math.floor(slots / 8));
  for (let i = 0; i < bytes.length; i++) {
    let v = 0;
    for (let b = 0; b < 8; b++) {
      const idx = i * 8 + b, px = Math.floor(idx / 3), ch = idx % 3;
      v = (v << 1) | (rgba[px * 4 + ch] & 1);
    }
    bytes[i] = v;
  }
  const found = new Set();
  for (let i = 0; i + 10 <= bytes.length; i++) {
    if (bytes.toString('ascii', i, i + 4) !== 'AMK1') continue;
    const len = bytes.readUInt16BE(i + 4);
    if (i + 6 + len + 4 > bytes.length) continue;
    const payload = bytes.subarray(i + 6, i + 6 + len);
    if (bytes.readUInt32BE(i + 6 + len) !== crc32(payload)) continue;
    found.add(payload.toString('utf8'));
    i += 6 + len + 3;
  }
  return [...found];
}

// ---------------------------------------------------------------- 5x7 bitmap font

const GLYPHS = {
  A: '.###.|#...#|#...#|#####|#...#|#...#|#...#', B: '####.|#...#|#...#|####.|#...#|#...#|####.',
  C: '.###.|#...#|#....|#....|#....|#...#|.###.', D: '####.|#...#|#...#|#...#|#...#|#...#|####.',
  E: '#####|#....|#....|####.|#....|#....|#####', F: '#####|#....|#....|####.|#....|#....|#....',
  G: '.###.|#...#|#....|#.###|#...#|#...#|.###.', H: '#...#|#...#|#...#|#####|#...#|#...#|#...#',
  I: '#####|..#..|..#..|..#..|..#..|..#..|#####', J: '..###|...#.|...#.|...#.|...#.|#..#.|.##..',
  K: '#...#|#..#.|#.#..|##...|#.#..|#..#.|#...#', L: '#....|#....|#....|#....|#....|#....|#####',
  M: '#...#|##.##|#.#.#|#...#|#...#|#...#|#...#', N: '#...#|##..#|#.#.#|#..##|#...#|#...#|#...#',
  O: '.###.|#...#|#...#|#...#|#...#|#...#|.###.', P: '####.|#...#|#...#|####.|#....|#....|#....',
  Q: '.###.|#...#|#...#|#...#|#.#.#|#..#.|.##.#', R: '####.|#...#|#...#|####.|#.#..|#..#.|#...#',
  S: '.####|#....|#....|.###.|....#|....#|####.', T: '#####|..#..|..#..|..#..|..#..|..#..|..#..',
  U: '#...#|#...#|#...#|#...#|#...#|#...#|.###.', V: '#...#|#...#|#...#|#...#|#...#|.#.#.|..#..',
  W: '#...#|#...#|#...#|#.#.#|#.#.#|##.##|#...#', X: '#...#|#...#|.#.#.|..#..|.#.#.|#...#|#...#',
  Y: '#...#|#...#|.#.#.|..#..|..#..|..#..|..#..', Z: '#####|....#|...#.|..#..|.#...|#....|#####',
  0: '.###.|#...#|#..##|#.#.#|##..#|#...#|.###.', 1: '..#..|.##..|..#..|..#..|..#..|..#..|.###.',
  2: '.###.|#...#|....#|...#.|..#..|.#...|#####', 3: '#####|...#.|..#..|...#.|....#|#...#|.###.',
  4: '...#.|..##.|.#.#.|#..#.|#####|...#.|...#.', 5: '#####|#....|####.|....#|....#|#...#|.###.',
  6: '..##.|.#...|#....|####.|#...#|#...#|.###.', 7: '#####|....#|...#.|..#..|.#...|.#...|.#...',
  8: '.###.|#...#|#...#|.###.|#...#|#...#|.###.', 9: '.###.|#...#|#...#|.####|....#|...#.|.##..',
  ' ': '.....|.....|.....|.....|.....|.....|.....', '.': '.....|.....|.....|.....|.....|.##..|.##..',
  '-': '.....|.....|.....|#####|.....|.....|.....', _: '.....|.....|.....|.....|.....|.....|#####',
  '/': '....#|...#.|...#.|..#..|.#...|.#...|#....', '@': '.###.|#...#|#.###|#.#.#|#.###|#....|.###.',
  '(': '...#.|..#..|.#...|.#...|.#...|..#..|...#.', ')': '.#...|..#..|...#.|...#.|...#.|..#..|.#...',
  ':': '.....|.##..|.##..|.....|.##..|.##..|.....', '#': '.#.#.|.#.#.|#####|.#.#.|#####|.#.#.|.#.#.',
  '+': '.....|..#..|..#..|#####|..#..|..#..|.....', ',': '.....|.....|.....|.....|.##..|.##..|.#...',
  "'": '..#..|..#..|.....|.....|.....|.....|.....', '©': '.###.|#...#|#.##.|#.#..|#.##.|#...#|.###.',
};

function textMask(text) {
  const chars = [...text.toUpperCase().replace(/\(C\)/g, '©')];
  const w = chars.length * 6 - 1, h = 7;
  const mask = new Uint8Array(w * h);
  chars.forEach((c, i) => {
    const g = (GLYPHS[c] || GLYPHS['#']).split('|');
    for (let y = 0; y < 7; y++) for (let x = 0; x < 5; x++) if (g[y][x] === '#') mask[y * w + i * 6 + x] = 1;
  });
  return { mask, w, h };
}

function drawText(rgba, W, H, text, { scale, opacity, x0, y0 }) {
  const { mask, w, h } = textMask(text);
  const at = (mx, my) => (mx >= 0 && my >= 0 && mx < w && my < h ? mask[my * w + mx] : 0);
  const blend = (px, py, rgb, a) => {
    if (px < 0 || py < 0 || px >= W || py >= H) return;
    const o = (py * W + px) * 4;
    for (let k = 0; k < 3; k++) rgba[o + k] = Math.round(rgba[o + k] * (1 - a) + rgb[k] * a);
    if (rgba[o + 3] < 255) rgba[o + 3] = Math.max(rgba[o + 3], Math.round(255 * a));
  };
  for (let py = 0; py < h * scale; py++) {
    for (let px = 0; px < w * scale; px++) {
      const mx = Math.floor(px / scale), my = Math.floor(py / scale);
      let on = at(mx, my), near = 0;
      if (!on) for (let dy = -1; dy <= 1 && !near; dy++) for (let dx = -1; dx <= 1; dx++) if (at(mx + dx, my + dy)) { near = 1; break; }
      if (on) blend(x0 + px, y0 + py, [255, 255, 255], opacity);
      else if (near) blend(x0 + px, y0 + py, [0, 0, 0], opacity * 0.75); // outline: readable on any background
    }
  }
  return { w: w * scale, h: h * scale };
}

// ---------------------------------------------------------------- JPEG metadata

function buildExif(fields) {
  const n = fields.length;
  const dataStart = 8 + 2 + n * 12 + 4;
  const head = Buffer.alloc(dataStart);
  head.write('II', 0, 'ascii'); head.writeUInt16LE(42, 2); head.writeUInt32LE(8, 4);
  head.writeUInt16LE(n, 8);
  const blobs = [];
  let off = dataStart;
  fields.forEach((f, i) => {
    const val = Buffer.from(f.value + '\0', 'latin1');
    const p = 10 + i * 12;
    head.writeUInt16LE(f.tag, p); head.writeUInt16LE(2, p + 2); head.writeUInt32LE(val.length, p + 4);
    if (val.length <= 4) val.copy(head, p + 8);
    else { head.writeUInt32LE(off, p + 8); blobs.push(val); off += val.length; }
  });
  head.writeUInt32LE(0, 10 + n * 12);
  return Buffer.concat([Buffer.from('Exif\0\0', 'latin1'), head, ...blobs]);
}

function app(marker, payload) {
  const len = Buffer.alloc(2); len.writeUInt16BE(payload.length + 2);
  return Buffer.concat([Buffer.from([0xff, marker]), len, payload]);
}

function jpegStamp(buf, cfg, note) {
  if (buf.readUInt16BE(0) !== 0xffd8) die('not a JPEG');
  const kept = [Buffer.from([0xff, 0xd8])];
  let p = 2;
  // Copy leading APPn segments except any existing Exif/XMP/COM we are replacing.
  while (p < buf.length - 1 && buf[p] === 0xff) {
    const m = buf[p + 1];
    if (m === 0xda || m === 0xd9 || (m >= 0xd0 && m <= 0xd9)) break;
    const len = buf.readUInt16BE(p + 2);
    const seg = buf.subarray(p, p + 2 + len);
    const tag = seg.toString('latin1', 4, 40);
    const drop = (m === 0xe1 && (tag.startsWith('Exif') || tag.startsWith('http://ns.adobe.com/xap'))) || m === 0xfe;
    if (!drop) kept.push(seg);
    p += 2 + len;
  }
  const rights = `Copyright (c) ${cfg.year} ${cfg.author}. ${cfg.github}`;
  const exif = buildExif([
    { tag: 0x010e, value: note || rights },          // ImageDescription
    { tag: 0x0131, value: 'authormark/1' },          // Software
    { tag: 0x013b, value: cfg.author },              // Artist
    { tag: 0x8298, value: rights },                  // Copyright
  ]);
  const xmp = Buffer.from(
    'http://ns.adobe.com/xap/1.0/\0<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>' +
    '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">' +
    `<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:xmpRights="http://ns.adobe.com/xap/1.0/rights/">` +
    `<dc:creator><rdf:Seq><rdf:li>${esc(cfg.author)}</rdf:li></rdf:Seq></dc:creator>` +
    `<dc:rights><rdf:Alt><rdf:li xml:lang="x-default">${esc(rights)}</rdf:li></rdf:Alt></dc:rights>` +
    `<dc:identifier>${esc(cfg.github)}</dc:identifier>` +
    `<xmpRights:WebStatement>${esc(cfg.github)}</xmpRights:WebStatement>` +
    `</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>`, 'latin1');
  const com = Buffer.from(`${SENTINEL} ${NOREMOVE}. ${rights}`, 'latin1');
  return Buffer.concat([...kept, app(0xe1, exif), app(0xe1, xmp), app(0xfe, com), buf.subarray(p)]);
}

function esc(s) { return String(s).replace(/[<>&]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c])); }

// ---------------------------------------------------------------- image command

function cmdImage(args) {
  const cfg = loadConfig(), key = loadKey(cfg);
  const files = positional(args);
  if (!files.length) die('usage: authormark image <file.png|file.jpg> [-o out] [--visible "text"] [--tile]');
  const outFlag = flag(args, '-o') || flag(args, '--out');
  const visible = args.includes('--visible') ? (flag(args, '--visible') || `(c) ${cfg.year} ${cfg.author}`) : null;
  const tile = args.includes('--tile');
  const opacity = Number(flag(args, '--opacity') ?? 0.55);
  const scaleFlag = flag(args, '--scale');
  const noStego = args.includes('--no-stego');

  for (const f of files) {
    const ext = path.extname(f).toLowerCase();
    const out = outFlag || (args.includes('--inplace') ? f : f.replace(/(\.[^.]+)$/, '.marked$1'));
    const rights = `Copyright (c) ${cfg.year} ${cfg.author}. All rights reserved. ${cfg.github}`;

    if (ext === '.jpg' || ext === '.jpeg') {
      fs.writeFileSync(out, jpegStamp(fs.readFileSync(f), cfg));
      log(`${f} -> ${out}  [EXIF Artist/Copyright + XMP + COM]  (no pixel marks: JPEG is lossy -- convert to PNG for those)`);
      continue;
    }
    if (ext !== '.png') { warn(`skip ${f} (only .png and .jpg supported)`); continue; }

    const img = decodePng(fs.readFileSync(f));
    const { width: W, height: H, rgba } = img;

    if (visible) {
      const scale = Number(scaleFlag ?? Math.max(1, Math.round(Math.min(W, H) / 220)));
      const { w: tw, h: th } = textMask(visible);
      const bw = tw * scale, bh = th * scale, pad = Math.max(6, 4 * scale);
      if (tile) {
        const stepX = bw + pad * 6, stepY = bh + pad * 5;
        for (let y = pad, r = 0; y < H; y += stepY, r++)
          for (let x = pad + (r % 2 ? stepX / 2 : 0); x < W; x += stepX)
            drawText(rgba, W, H, visible, { scale, opacity: opacity * 0.6, x0: Math.round(x), y0: Math.round(y) });
      } else {
        drawText(rgba, W, H, visible, { scale, opacity, x0: W - bw - pad, y0: H - bh - pad });
      }
    }

    let copies = 0;
    if (!noStego) {
      const payload = Buffer.from(JSON.stringify({
        a: cfg.author, u: cfg.github, y: cfg.year, t: new Date().toISOString().slice(0, 10),
        f: crypto.createHmac('sha256', key).update(path.basename(f)).digest('base64url').slice(0, 16),
      }));
      copies = lsbEmbed(rgba, W, H, payload);
    }

    const texts = [
      ['Title', path.basename(f)], ['Author', cfg.author], ['Copyright', rights],
      ['Source', cfg.github], ['Software', 'authormark/1'], ['Comment', `${SENTINEL} ${NOREMOVE}`],
    ];
    fs.writeFileSync(out, encodePng({ width: W, height: H, rgba, hasAlpha: img.hasAlpha, texts }));
    log(`${f} -> ${out}  [${W}x${H}] metadata${visible ? ' + visible' : ''}${noStego ? '' : ` + hidden x${copies}`}`);
  }
}

function scanPng(buf, key) {
  const img = decodePng(buf);
  for (const c of pngChunks(buf)) {
    if (c.type === 'tEXt' || c.type === 'iTXt') {
      const s = c.data.toString('latin1');
      const i = s.indexOf('\0');
      log(`  meta ${s.slice(0, i)}: ${s.slice(i + 1).replace(/\0/g, ' ').trim()}`);
    }
  }
  const hidden = lsbExtract(img.rgba, img.width, img.height);
  if (hidden.length) hidden.forEach(h => log(`  HIDDEN (LSB): ${h}`));
  else log('  no hidden LSB mark (or the image was re-encoded/resized)');
}

function scanJpeg(buf) {
  let p = 2, found = 0;
  while (p < buf.length - 1 && buf[p] === 0xff) {
    const m = buf[p + 1];
    if (m === 0xda || m === 0xd9) break;
    const len = buf.readUInt16BE(p + 2);
    const seg = buf.subarray(p + 4, p + 2 + len);
    if (m === 0xfe) { log(`  COM: ${seg.toString('latin1').trim()}`); found++; }
    if (m === 0xe1) {
      const s = seg.toString('latin1');
      if (s.startsWith('Exif')) { for (const t of s.match(/[\x20-\x7e]{6,}/g) || []) if (!/^Exif/.test(t)) log(`  EXIF: ${t}`); found++; }
      if (s.includes('xmpmeta')) { for (const t of s.match(/<rdf:li[^>]*>[^<]+/g) || []) log(`  XMP: ${t.replace(/<[^>]*>/g, '')}`); found++; }
    }
    p += 2 + len;
  }
  if (!found) log('  no authorship metadata found');
}

// ---------------------------------------------------------------- hook

function cmdHook() {
  const dir = path.join(CWD, '.git', 'hooks');
  if (!fs.existsSync(dir)) die('no .git/hooks here -- run inside a git repo');
  const self = path.resolve(process.argv[1]);
  const p = path.join(dir, 'pre-commit');
  const body = `#!/bin/sh\n# authormark: block commits that strip the authorship watermark\nnode "${self}" check --staged || {\n  echo ""\n  echo "Commit blocked: watermark missing or stale. Fix with:  node ${self} stamp <files>"\n  exit 1\n}\n`;
  if (fs.existsSync(p) && !fs.readFileSync(p, 'utf8').includes('authormark')) {
    fs.appendFileSync(p, '\n' + body.replace(/^#!.*\n/, ''));
    log(`appended authormark check to existing ${p}`);
  } else {
    fs.writeFileSync(p, body, { mode: 0o755 });
    log(`installed ${p}`);
  }
  log('any commit -- yours or an AI agent\'s -- now fails if a watermark was removed.');
}

// ---------------------------------------------------------------- utils + main

function flag(args, name) {
  const i = args.indexOf(name);
  if (i === -1) return undefined;
  const v = args[i + 1];
  return v && !v.startsWith('-') ? v : '';
}
function positional(args) {
  const out = [];
  const valued = ['--ext', '-o', '--out', '--visible', '--opacity', '--scale', '--author', '--email', '--github', '--license'];
  for (let i = 0; i < args.length; i++) {
    if (args[i].startsWith('-')) { if (valued.includes(args[i]) && args[i + 1] && !args[i + 1].startsWith('-')) i++; continue; }
    out.push(args[i]);
  }
  return out;
}
function tryGit(k) { try { return execFileSync('git', ['config', k], { encoding: 'utf8' }).trim(); } catch { return ''; } }
function log(...a) { console.log(...a); }
function warn(...a) { console.error(...a); }
function die(m) { console.error(`authormark: ${m}`); process.exit(1); }

const USAGE = `authormark -- layered authorship watermarking

  init [--author N] [--email E] [--github U] [--license L]
       create .authormark.json + your secret HMAC key (~/.authormark.key)

  stamp <paths...> [--ext .ts,.tsx] [--zw] [--dry]
       insert/refresh the copyright header + keyed fingerprint in source files
       --zw also plants an invisible zero-width mark that survives copy-paste

  check [paths...] | check --staged
       exit 1 if any file is unmarked or its fingerprint is stale (for CI/hooks)

  seal [paths...]        write AUTHORSHIP.json: per-file hashes + keyed proof
  verify [manifest]      re-verify a manifest against the working tree
  scan <files...>        show every mark found in a source file or image
  image <files...> [-o out] [--inplace] [--visible "txt"] [--tile]
                   [--opacity 0.55] [--scale N] [--no-stego]
       PNG: text chunks + optional visible watermark + hidden LSB payload
       JPEG: EXIF Artist/Copyright + XMP + COM comment (metadata only)
  hook install           git pre-commit hook that blocks de-watermarked commits`;

const [cmd, ...rest] = process.argv.slice(2);
try {
  switch (cmd) {
    case 'init': cmdInit(rest); break;
    case 'stamp': cmdStamp(rest); break;
    case 'check': cmdCheck(rest); break;
    case 'seal': cmdSeal(rest); break;
    case 'verify': cmdVerify(rest); break;
    case 'scan': cmdScan(rest); break;
    case 'image': cmdImage(rest); break;
    case 'hook': cmdHook(rest); break;
    default: log(USAGE); process.exit(cmd ? 1 : 0);
  }
} catch (e) {
  die(e.message);
}

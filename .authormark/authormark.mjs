#!/usr/bin/env node
/*!
 * @authormark v1 -- do not remove (authorship watermark)
 * Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
 * Author: https://github.com/Srinivasan-78
 * SPDX-License-Identifier: MIT
 * Fingerprint: AMK1.i5LPmK4UGjXpIXJSJdDjIu
 */
// authormark -- layered authorship watermarking for source code and images.
// Zero dependencies. Node >= 18.

import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import crypto from 'node:crypto';
import os from 'node:os';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const CWD = process.cwd();
const CONFIG_FILE = '.authormark.json';
const MANIFEST_FILE = 'AUTHORSHIP.json';
const LOG_FILE = 'AUTHORSHIP.log';
const ATTEST_FILE = 'AUTHORSHIP.intoto.jsonl';
const SENTINEL = '@authormark v1';
const NOREMOVE = '-- do not remove';
// A line only counts as a header when it carries BOTH markers, so docs and
// READMEs can talk about "@authormark v1" without being mistaken for stamped files.
const isHeaderLine = l => l.includes(SENTINEL) && l.includes(NOREMOVE);
const FP_LABEL = 'Fingerprint: AMK1.';
const SKIP_DIRS = new Set(['node_modules', '.git', '.next', 'dist', 'build', 'out', 'coverage', '.turbo',
  '.vercel', 'vendor', '__pycache__', 'venv', 'site-packages', 'third_party', 'target', '.mypy_cache']);

const DEFAULT_EXTS = ['.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.mts', '.cts', '.css', '.scss', '.sass', '.less',
  '.py', '.pyi', '.go', '.rs', '.java', '.kt', '.kts', '.swift', '.c', '.h', '.cpp', '.hpp', '.cc', '.cxx', '.mm',
  '.cs', '.php', '.rb', '.sh', '.bash', '.zsh', '.fish', '.sql', '.lua', '.html', '.htm', '.svg', '.vue', '.svelte',
  '.astro', '.md', '.mdx', '.yml', '.yaml', '.toml', '.bat', '.cmd', '.ps1', '.psm1', '.tf', '.tfvars', '.hcl',
  '.r', '.pl', '.pm', '.dart', '.scala', '.sc', '.groovy', '.gradle', '.d', '.cr', '.nim', '.jl', '.ex', '.exs',
  '.erl', '.hrl', '.clj', '.cljs', '.cljc', '.edn', '.hs', '.elm', '.sol', '.proto', '.graphql', '.gql',
  '.fs', '.fsx', '.fsi', '.ml', '.mli', '.res', '.resi', '.zig', '.sv', '.svh', '.vhd', '.vhdl',
  '.cmake', '.tcl', '.rkt'];
// Deliberately excluded -- the extension names two languages with incompatible
// comment syntax: .v (Verilog vs Coq), .m (Objective-C vs MATLAB). Opt in per
// repo via the config `ext` list if you know which one you mean.

// Extensionless files worth stamping, matched by basename.
const NAMED_FILES = new Set(['Dockerfile', 'Containerfile', 'Makefile', 'GNUmakefile', 'Jenkinsfile',
  'Vagrantfile', 'Procfile', 'Rakefile', 'Gemfile', 'Guardfile', 'Brewfile', 'Berksfile',
  'Fastfile', 'Appfile', 'Podfile', 'CMakeLists.txt']);

// ---------------------------------------------------------------- comment styles

const BLOCK = { open: '/*!', line: ' * ', close: ' */' };  // C-family; valid wherever // works too
const HTML = { open: '<!--', line: '  ', close: '-->' };
const PAREN = { open: '(*', line: ' * ', close: ' *)' };   // OCaml, F#, Pascal
const HASH = { prefix: '# ' };
const DASH = { prefix: '-- ' };                            // Haskell, Elm, Ada, SQL, Lua
const SLASH = { prefix: '// ' };                           // Zig -- no block comment
const SEMI = { prefix: '; ' };                             // Lisp / Clojure / Scheme
const PCT = { prefix: '% ' };                              // Erlang
const REM = { prefix: 'REM ' };
const STYLES = {
  '.js': BLOCK, '.jsx': BLOCK, '.ts': BLOCK, '.tsx': BLOCK, '.mjs': BLOCK, '.cjs': BLOCK, '.mts': BLOCK, '.cts': BLOCK,
  '.css': BLOCK, '.scss': BLOCK, '.sass': BLOCK, '.less': BLOCK, '.go': BLOCK, '.rs': BLOCK,
  '.java': BLOCK, '.kt': BLOCK, '.kts': BLOCK, '.swift': BLOCK, '.c': BLOCK, '.h': BLOCK, '.cpp': BLOCK,
  '.hpp': BLOCK, '.cc': BLOCK, '.cxx': BLOCK, '.mm': BLOCK, '.cs': BLOCK, '.php': BLOCK,
  '.dart': BLOCK, '.scala': BLOCK, '.sc': BLOCK, '.groovy': BLOCK, '.gradle': BLOCK, '.d': BLOCK,
  '.sol': BLOCK, '.proto': BLOCK, '.res': BLOCK, '.resi': BLOCK, '.sv': BLOCK, '.svh': BLOCK,
  '.lua': DASH, '.sql': DASH, '.hs': DASH, '.elm': DASH, '.vhd': DASH, '.vhdl': DASH,
  '.py': HASH, '.pyi': HASH, '.rb': HASH, '.sh': HASH, '.bash': HASH, '.zsh': HASH, '.fish': HASH,
  '.yml': HASH, '.yaml': HASH, '.toml': HASH, '.pl': HASH, '.pm': HASH, '.r': HASH, '.cr': HASH,
  '.nim': HASH, '.jl': HASH, '.ex': HASH, '.exs': HASH, '.tf': HASH, '.tfvars': HASH, '.hcl': HASH,
  '.graphql': HASH, '.gql': HASH, '.cmake': HASH, '.tcl': HASH, '.ps1': HASH, '.psm1': HASH,
  '.html': HTML, '.htm': HTML, '.svg': HTML, '.vue': HTML, '.svelte': HTML, '.astro': HTML,
  '.md': HTML, '.mdx': HTML,
  '.fs': PAREN, '.fsx': PAREN, '.fsi': PAREN, '.ml': PAREN, '.mli': PAREN,
  '.zig': SLASH,
  '.clj': SEMI, '.cljs': SEMI, '.cljc': SEMI, '.edn': SEMI, '.rkt': SEMI,
  '.erl': PCT, '.hrl': PCT,
  '.bat': REM, '.cmd': REM,
  Dockerfile: HASH, Containerfile: HASH, Makefile: HASH, GNUmakefile: HASH, Procfile: HASH,
  Vagrantfile: HASH, Rakefile: HASH, Gemfile: HASH, Guardfile: HASH, Brewfile: HASH, Berksfile: HASH,
  Fastfile: HASH, Appfile: HASH, Podfile: HASH, 'CMakeLists.txt': HASH, Jenkinsfile: BLOCK,
};

// Basename wins over extension, so `Dockerfile` and `Makefile` are handled.
const styleFor = rel => STYLES[path.basename(rel)] || STYLES[path.extname(rel)] || BLOCK;

// ---------------------------------------------------------------- config + key

const IGNORE_FILE = '.authormarkignore';

// gitignore-lite: drop blank lines and #comments; each remaining line is an
// ignore pattern understood by ignored() (exact path, dir prefix, or glob).
function readIgnoreFile() {
  const p = path.join(CWD, IGNORE_FILE);
  if (!fs.existsSync(p)) return [];
  return fs.readFileSync(p, 'utf8').split('\n')
    .map(l => l.trim()).filter(l => l && !l.startsWith('#'));
}

function loadConfig() {
  const p = path.join(CWD, CONFIG_FILE);
  if (!fs.existsSync(p)) die(`no ${CONFIG_FILE} here -- run:  authormark init`);
  const g = globalDefaults();
  const cfg = JSON.parse(fs.readFileSync(p, 'utf8'));
  cfg.ignore = [...(cfg.ignore || []), ...readIgnoreFile()];
  cfg.include = cfg.include || [];              // if non-empty, a file must match one glob
  if (cfg.reuse === undefined) cfg.reuse = g.reuse ?? false;
  if (cfg.maxBytes === undefined) cfg.maxBytes = g.maxBytes ?? 2 * 1024 * 1024;
  return cfg;
}

// Machine-wide defaults, so every new repo gets the same identity without flags.
function globalDefaults() {
  try { return JSON.parse(fs.readFileSync(path.join(os.homedir(), CONFIG_FILE), 'utf8')); }
  catch { return {}; }
}

// Glob: `*` matches a run of non-slash characters, `**` matches a run that may
// include slashes, `?` matches one non-slash character. A trailing slash is
// dropped. Anchored at the start; matches the path itself or a parent dir.
function matchGlob(rel, pattern) {
  const norm = rel.split(path.sep).join('/');
  const pat = pattern.split(path.sep).join('/').replace(/\/+$/, '');
  let rx = '';
  for (let i = 0; i < pat.length; i++) {
    const c = pat[i];
    if (c === '*') {
      if (pat[i + 1] === '*') {
        // `**/` = zero or more leading path segments; bare `**` = anything.
        if (pat[i + 2] === '/') { rx += '(?:.*/)?'; i += 2; } else { rx += '.*'; i += 1; }
      } else {
        rx += '[^/]*';
      }
    } else if (c === '?') {
      rx += '[^/]';
    } else if ('.+^${}()|[]\\'.includes(c)) {
      rx += '\\' + c;
    } else {
      rx += c;
    }
  }
  return new RegExp('^' + rx + '(/|$)').test(norm);
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

// Unkeyed content digest for the Fingerprint: line in ed25519 mode -- it is only
// a cheap "did the body change" marker; the Signature: line is the real proof.
function contentDigest(body) {
  return crypto.createHash('sha256').update(canonical(body)).digest('base64url').slice(0, 22);
}

const SIG_LABEL = 'Signature: AMK2.';

// One object that hides the hmac-vs-ed25519 split from every command.
//   fpFor(body)        -> string for the `Fingerprint:` line
//   sigFor(body)       -> string for the `Signature:` line, or null (hmac / no key)
//   verify(header,body)-> true | false | null   (null = can only presence-check)
//   macFor(str)        -> proof string for a manifest / log entry, or null
//   macVerify(str,mac) -> true | false | null
function signer(cfg) {
  const algo = cfg.algo === 'ed25519' ? 'ed25519' : 'hmac';

  if (algo === 'ed25519') {
    const pubs = [];
    if (cfg.publicKey) {
      try { pubs.push(crypto.createPublicKey({ key: Buffer.from(cfg.publicKey, 'base64'), format: 'der', type: 'spki' })); } catch {}
    }
    pubs.push(...loadArchivedPubs(cfg));
    let priv = null;
    const kp = keyPath(cfg);
    if (fs.existsSync(kp)) {
      try { priv = crypto.createPrivateKey(fs.readFileSync(kp, 'utf8')); } catch {}
    }
    const sign = str => crypto.sign(null, Buffer.from(str), priv).toString('base64url');
    const anyPub = (str, macB64) => {
      const m = Buffer.from(macB64, 'base64url');
      return pubs.some(pk => { try { return crypto.verify(null, Buffer.from(str), pk, m); } catch { return false; } });
    };
    return {
      algo,
      fpFor: body => contentDigest(body),
      sigFor: priv ? body => sign(canonical(body)) : null,
      verify(header, body) {
        const sig = header.match(/Signature: AMK2\.([A-Za-z0-9_-]+)/)?.[1];
        const fp = header.match(/Fingerprint: AMK1\.([A-Za-z0-9_-]{22})/)?.[1];
        if (!pubs.length || !sig) return fp ? (fp === contentDigest(body) ? null : false) : null;
        return anyPub(canonical(body), sig);
      },
      macFor: priv ? sign : null,
      macVerify: (str, mac) => (pubs.length ? anyPub(str, mac) : null),
    };
  }

  const keys = fs.existsSync(keyPath(cfg)) ? loadAllKeys(cfg) : [];
  const hmac = (k, s) => crypto.createHmac('sha256', k).update(s).digest('hex');
  return {
    algo,
    fpFor: body => keys.length ? fingerprint(keys[0], body) : contentDigest(body),
    sigFor: null,
    verify(header, body) {
      if (!keys.length) return null;
      const fp = header.match(/Fingerprint: AMK1\.([A-Za-z0-9_-]{22})/)?.[1];
      return keys.some(k => fp === fingerprint(k, body));
    },
    macFor: keys.length ? str => hmac(keys[0], str) : null,
    macVerify(str, mac) {
      if (!keys.length) return null;
      return keys.some(k => hmac(k, str) === mac);
    },
  };
}

// ---------------------------------------------------------------- header build / strip

function headerLines(cfg, fp, sig) {
  const who = `${cfg.year} ${cfg.author}${cfg.email ? ` <${cfg.email}>` : ''}`;
  const l = [
    `${SENTINEL} ${NOREMOVE} (authorship watermark)`,
    `Copyright (c) ${who}`,
    `Author: ${cfg.github}`,
  ];
  // REUSE-spec (reuse.software) machine-readable copyright line, opt-in via config.
  if (cfg.reuse) l.push(`SPDX-FileCopyrightText: ${who}`);
  if (cfg.license) l.push(`SPDX-License-Identifier: ${cfg.license}`);
  l.push(`${FP_LABEL}${fp}`);
  if (sig) l.push(`${SIG_LABEL}${sig}`);   // ed25519: verifiable with the public key alone
  return l;
}

function renderHeader(cfg, fp, style, zw, sig) {
  let lines = headerLines(cfg, fp, sig);
  if (zw) lines[0] += zwEncode(fp);
  if (style.prefix) return lines.map(l => style.prefix + l).join('\n') + '\n';
  return [style.open, ...lines.map(l => style.line + l), style.close].join('\n') + '\n';
}

// A header we wrote is at most 7 lines plus its delimiters; never scan further.
// Without this bound a sentinel line whose `Fingerprint:` was deleted would make
// the search run to EOF and stamp would then overwrite the whole file with a header.
const HEADER_MAX_LINES = 12;
const HEADER_FIELD = /(Copyright \(c\)|Author:|SPDX-File(?:CopyrightText|Contributor):|SPDX-License-Identifier:|Fingerprint: |Signature: )/;

// Returns {header, body} -- header is '' when the file is unstamped.
function splitHeader(text) {
  const lines = text.split('\n');
  const i = lines.findIndex(isHeaderLine);
  if (i === -1) return { header: '', body: text, at: -1 };
  let start = i, end = i;
  if (i > 0 && /^\s*(\/\*!?|<!--)\s*$/.test(lines[i - 1])) start = i - 1;
  const limit = Math.min(lines.length, i + HEADER_MAX_LINES);
  while (end < limit && !lines[end].includes(FP_LABEL)) end++;
  if (end >= limit) {
    // No fingerprint in range -- fall back to the block's closing delimiter so a
    // hand-mangled header is still replaced rather than duplicated.
    let close = i;
    while (close < limit && !/^\s*(\*\/|-->)\s*$/.test(lines[close])) close++;
    if (close < limit) end = close;
    else {
      // Prefix comment styles have no delimiter: take only the lines that are
      // recognisably header fields, so a neighbouring comment is not swallowed.
      end = i;
      while (end + 1 < limit && HEADER_FIELD.test(lines[end + 1])) end++;
    }
  } else {
    // An ed25519 Signature: line sits just below Fingerprint: -- keep it with the header.
    if (end + 1 < limit && lines[end + 1].includes(SIG_LABEL)) end++;
    if (end < lines.length - 1 && /^\s*(\*\/|-->)\s*$/.test(lines[end + 1])) end++;
  }
  return { header: lines.slice(start, end + 1).join('\n'), body: lines.slice(0, start).concat(lines.slice(end + 1)).join('\n'), at: start };
}

// Where the header may legally go: after a shebang, doctype, xml prolog, or a
// YAML front-matter block (Jekyll pages and issue templates break if displaced).
function insertIndex(text) {
  const lines = text.split('\n');
  // CRLF files keep a trailing \r on every line, so compare without it -- but keep
  // using the untrimmed length, since that \r is a real byte in the offset.
  const bare = l => (l || '').replace(/\r$/, '');
  if (bare(lines[0]) === '---') {
    const close = lines.findIndex((l, i) => i > 0 && (bare(l) === '---' || bare(l) === '...'));
    if (close > 0) return lines.slice(0, close + 1).join('\n').length + 1;
  }
  if (bare(lines[0]).startsWith('#!')) return lines[0].length + 1;
  if (/^\s*(<\?xml|<!doctype)/i.test(bare(lines[0]))) return lines[0].length + 1;
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
  // A dangling symlink or a race with a deleted file must not abort the whole run.
  let st;
  try { st = fs.statSync(target); } catch { return acc; }
  if (st.isFile()) { if (exts.includes(path.extname(target)) || NAMED_FILES.has(path.basename(target))) acc.push(target); return acc; }
  if (!st.isDirectory()) return acc;
  for (const e of fs.readdirSync(target, { withFileTypes: true })) {
    if (e.name.startsWith('.') && e.name !== '.github') continue;
    if (SKIP_DIRS.has(e.name)) continue;
    if (e.isSymbolicLink()) continue; // a linked directory can loop back on itself
    walk(path.join(target, e.name), exts, acc);
  }
  return acc;
}

// Never enforce marks on generated output or vendored/third-party content.
function ignored(rel, cfg) {
  const norm = rel.split(path.sep).join('/');
  const segs = norm.split('/');
  if (segs.some(s => SKIP_DIRS.has(s))) return true;
  if (segs.slice(0, -1).some(s => s.startsWith('.') && s !== '.github')) return true;
  return (cfg?.ignore || []).some(p =>
    rel === p ||
    norm === p ||
    norm.startsWith(p.replace(/\/+$/, '') + '/') ||
    ((p.includes('*') || p.includes('?')) && matchGlob(rel, p)));
}

// An `include` list (globs) turns collection into an allowlist: a file must
// match at least one pattern to be stamped/checked. Empty list = stamp all.
function includedBy(rel, cfg) {
  const inc = cfg?.include || [];
  if (inc.length === 0) return true;
  return inc.some(p => matchGlob(rel, p) || rel === p || rel.split(path.sep).join('/') === p);
}

function collect(paths, exts, cfg) {
  const targets = paths.length ? paths : ['.'];
  const files = new Set();
  for (const t of targets) {
    if (!fs.existsSync(t)) { warn(`skip (missing): ${t}`); continue; }
    for (const f of walk(t, exts)) {
      const rel = path.relative(CWD, f);
      if (!ignored(rel, cfg) && includedBy(rel, cfg)) files.add(rel);
    }
  }
  return [...files].sort();
}

// ---------------------------------------------------------------- commands

function cmdInit(args) {
  const g = globalDefaults();
  const author = flag(args, '--author') || g.author || tryGit('user.name') || 'unknown';
  const email = flag(args, '--email') || g.email || tryGit('user.email') || '';
  const github = flag(args, '--github') || g.github || '';
  const license = flag(args, '--license') || g.license || 'MIT';
  const ed25519 = args.includes('--ed25519') || g.algo === 'ed25519';
  const cfg = {
    author, email, github, year: new Date().getFullYear(), license,
    keyFile: '~/.authormark.key',
    algo: ed25519 ? 'ed25519' : 'hmac',
    ignore: [], include: [],
    reuse: flag(args, '--reuse') === 'true' || args.includes('--reuse') || g.reuse || false,
    maxBytes: g.maxBytes ?? 2 * 1024 * 1024,
  };
  const kp = keyPath(cfg);
  if (ed25519) {
    if (fs.existsSync(kp)) {
      log(`key kept: ${kp} (already exists -- never regenerate, old signatures would break)`);
      try {
        const pub = crypto.createPublicKey(crypto.createPrivateKey(fs.readFileSync(kp, 'utf8')));
        cfg.publicKey = pub.export({ type: 'spki', format: 'der' }).toString('base64');
      } catch { die(`existing ${kp} is not an ed25519 private key -- move it aside first`); }
    } else {
      const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519');
      fs.writeFileSync(kp, privateKey.export({ type: 'pkcs8', format: 'pem' }), { mode: 0o600 });
      cfg.publicKey = publicKey.export({ type: 'spki', format: 'der' }).toString('base64');
      log(`ed25519 keypair created: private ${kp} (chmod 600 -- BACK THIS UP), public key embedded in ${CONFIG_FILE}`);
    }
  } else if (fs.existsSync(kp)) {
    log(`key kept: ${kp} (already exists -- never regenerate, old fingerprints would break)`);
  } else {
    fs.writeFileSync(kp, crypto.randomBytes(32).toString('hex') + '\n', { mode: 0o600 });
    log(`key created: ${kp}  (chmod 600 -- BACK THIS UP, it is your proof of authorship)`);
  }
  fs.writeFileSync(path.join(CWD, CONFIG_FILE), JSON.stringify(cfg, null, 2) + '\n');
  log(`config written: ${CONFIG_FILE}${ed25519 ? '  (algo: ed25519 -- CI verifies with the public key, no secret needed)' : ''}`);
  if (!args.includes('--quiet')) log(`\nnext:  authormark stamp app components lib`);
}

function cmdStamp(args) {
  const cfg = loadConfig();
  const s = signer(cfg);
  if (cfg.algo === 'ed25519' && !s.sigFor) die(`ed25519 mode but no usable private key at ${keyPath(cfg)}`);
  if (cfg.algo !== 'ed25519') loadKey(cfg);  // fail early with the familiar message if the hmac key is gone
  const exts = flag(args, '--ext')?.split(',').map(e => (e.startsWith('.') ? e : '.' + e)) || DEFAULT_EXTS;
  const zw = args.includes('--zw');
  const dry = args.includes('--dry');
  const files = collect(positional(args), exts, cfg);
  let added = 0, refreshed = 0, same = 0, skipped = 0;

  for (const rel of files) {
    if (tooBig(rel, cfg)) { skipped++; continue; }
    const orig = fs.readFileSync(rel, 'utf8');
    const style = styleFor(rel);
    const { header, body } = splitHeader(orig);
    const fp = s.fpFor(body);
    const sig = s.sigFor ? s.sigFor(body) : null;
    const next = renderHeader(cfg, fp, style, zw, sig);
    if (header && header + '\n' === next.replace(/\n$/, '') + '\n') { same++; continue; }
    const at = insertIndex(body);
    const out = body.slice(0, at) + next + body.slice(at);
    if (out === orig) { same++; continue; }
    if (!dry) fs.writeFileSync(rel, out);
    header ? refreshed++ : added++;
    if (dry) log(`would ${header ? 'refresh' : 'stamp'}: ${rel}`);
  }
  log(`${dry ? '[dry] ' : ''}stamped ${added}, refreshed ${refreshed}, unchanged ${same}` +
    `${skipped ? `, skipped ${skipped} (too large)` : ''}  (${files.length} files)`);
}

// Files above cfg.maxBytes are left alone -- a watermark in a giant generated
// blob or a checked-in asset is noise, and reading it wastes memory.
function tooBig(rel, cfg) {
  try { return fs.statSync(rel).size > (cfg?.maxBytes ?? 2 * 1024 * 1024); }
  catch { return false; }
}

// Streamed SHA-256 so `seal` can cover files of any size.
function hashFile(rel) {
  const h = crypto.createHash('sha256');
  const fd = fs.openSync(rel, 'r');
  const buf = Buffer.alloc(1 << 20);
  let bytes = 0, n;
  try {
    while ((n = fs.readSync(fd, buf, 0, buf.length, null)) > 0) { h.update(buf.subarray(0, n)); bytes += n; }
  } finally {
    fs.closeSync(fd);
  }
  return { hash: h.digest('hex'), bytes };
}

// Deliberate, auditable removal -- for a licence change or upstreaming a file.
// Never call this to "fix" a stale fingerprint; re-stamp instead.
function cmdUnstamp(args) {
  const cfg = fs.existsSync(path.join(CWD, CONFIG_FILE)) ? loadConfig() : { ignore: [] };
  const exts = flag(args, '--ext')?.split(',').map(e => (e.startsWith('.') ? e : '.' + e)) || DEFAULT_EXTS;
  const dry = !args.includes('--force');
  const files = collect(positional(args), exts, cfg);
  let removed = 0, clean = 0;
  for (const rel of files) {
    const orig = fs.readFileSync(rel, 'utf8');
    const { header, body } = splitHeader(orig);
    if (!header) { clean++; continue; }
    // splitHeader already carries the zero-width mark off with the header block;
    // just tidy the blank line it leaves at the top of the file.
    const out = body.replace(/^\n/, '');
    if (!dry) fs.writeFileSync(rel, out);
    removed++;
    log(`${dry ? 'would remove' : 'removed'} watermark: ${rel}`);
  }
  log(`${dry ? '[dry] ' : ''}removed ${removed}, already clean ${clean}  (${files.length} files)`);
  if (dry && removed) log(`\nre-run with --force to write.`);
}

function cmdCheck(args) {
  const cfg = loadConfig();
  // ed25519 verifies from the public key in config (works in CI with no secret).
  // hmac needs the local key; without it we can only presence-check.
  const forcePresence = args.includes('--presence') ||
    (cfg.algo !== 'ed25519' && !fs.existsSync(keyPath(cfg)));
  const s = forcePresence ? null : signer(cfg);
  const mode = forcePresence ? 'presence' : (cfg.algo === 'ed25519' ? 'ed25519' : 'hmac');
  const exts = flag(args, '--ext')?.split(',').map(e => (e.startsWith('.') ? e : '.' + e)) || DEFAULT_EXTS;
  let files;
  if (args.includes('--staged')) {
    // git prints paths from the repo root, which is not necessarily CWD.
    const root = execFileSync('git', ['rev-parse', '--show-toplevel'], { encoding: 'utf8' }).trim();
    files = execFileSync('git', ['diff', '--cached', '--name-only', '--diff-filter=ACM'], { encoding: 'utf8' })
      .split('\n')
      .filter(f => f && (exts.includes(path.extname(f)) || NAMED_FILES.has(path.basename(f))))
      .map(f => path.relative(CWD, path.join(root, f)))
      .filter(f => !ignored(f, cfg) && fs.existsSync(f));
  } else {
    files = collect(positional(args), exts, cfg);
  }
  const missing = [], tampered = [];
  let skipped = 0;
  for (const rel of files) {
    // Stay consistent with stamp: it never marks files this large, so check
    // must not demand a mark on them either.
    if (tooBig(rel, cfg)) { skipped++; continue; }
    const text = fs.readFileSync(rel, 'utf8');
    const { header, body } = splitHeader(text);
    if (!header) { missing.push(rel); continue; }
    if (!s) continue;                       // presence-only
    if (s.verify(header, body) === false) tampered.push(rel);
  }
  const ok = missing.length === 0 && tampered.length === 0;

  if (args.includes('--json')) {
    process.stdout.write(JSON.stringify({
      ok, mode,
      total: files.length, skipped, missing, stale: tampered,
    }, null, 2) + '\n');
    if (!ok) process.exit(1);
    return;
  }

  for (const f of missing) console.error(`  MISSING watermark: ${f}`);
  for (const f of tampered) console.error(`  ${mode === 'ed25519' ? 'BAD SIGNATURE' : 'STALE fingerprint'}: ${f}  (re-run: authormark stamp ${f})`);
  if (!ok) {
    console.error(`\nauthormark: ${missing.length} unmarked, ${tampered.length} ${mode === 'ed25519' ? 'unverifiable' : 'stale'} of ${files.length}.`);
    process.exit(1);
  }
  const tail = mode === 'presence' ? ' (presence only -- fingerprints not verified).'
    : mode === 'ed25519' ? ' with a valid ed25519 signature.'
    : ' with a valid fingerprint.';
  log(`authormark: all ${files.length} files carry a watermark${tail}`);
}

function cmdSeal(args) {
  const cfg = loadConfig();
  const s = signer(cfg);
  if (!s.macFor) die(cfg.algo === 'ed25519'
    ? `ed25519 mode but no usable private key at ${keyPath(cfg)}`
    : `secret key missing at ${keyPath(cfg)} -- run:  authormark init`);
  const exts = flag(args, '--ext')?.split(',').map(e => (e.startsWith('.') ? e : '.' + e)) || DEFAULT_EXTS;
  const files = collect(positional(args), exts, cfg);
  const entries = files.map(rel => {
    // Seal every collected file regardless of size, but hash big ones in
    // chunks so a large asset can't blow the heap.
    const { hash, bytes } = hashFile(rel);
    return { path: rel, sha256: hash, bytes };
  });
  const digest = crypto.createHash('sha256')
    .update(entries.map(e => `${e.sha256}  ${e.path}`).join('\n')).digest('hex');
  const manifest = {
    schema: 'authormark/manifest/1',
    algo: s.algo,
    author: cfg.author, email: cfg.email, github: cfg.github,
    sealedAt: new Date().toISOString(),
    fileCount: entries.length,
    digest,
    proof: s.macFor(digest),
    files: entries,
  };
  fs.writeFileSync(path.join(CWD, MANIFEST_FILE), JSON.stringify(manifest, null, 2) + '\n');
  const chain = appendChain(s, digest);
  log(`sealed ${entries.length} files -> ${MANIFEST_FILE}`);
  log(`digest: ${digest}`);
  log(`chain:  ${LOG_FILE} seq ${chain.seq} (prev ${chain.prev.slice(0, 12)}…)`);
  log(`\nnotarize it (free, public, timestamped):`);
  log(`  authormark timestamp                            # RFC 3161 TSA token (needs openssl)`);
  log(`  authormark attest                               # SLSA provenance statement`);
  log(`  ots stamp ${MANIFEST_FILE}                      # OpenTimestamps -> bitcoin-anchored proof`);
}

// ---------------------------------------------------------------- tamper-evident log

// Every seal appends one line to AUTHORSHIP.log. `prev` is the SHA-256 of the
// entire file as it stood before the append, so altering or dropping any past
// line breaks `prev` on every line after it. `mac` binds the entry to the key
// (HMAC in hmac mode, an ed25519 signature otherwise).
function appendChain(s, digest) {
  const p = path.join(CWD, LOG_FILE);
  const before = fs.existsSync(p) ? fs.readFileSync(p) : Buffer.alloc(0);
  const prev = before.length ? crypto.createHash('sha256').update(before).digest('hex') : '0'.repeat(64);
  const seq = before.length ? before.toString('utf8').split('\n').filter(Boolean).length : 0;
  const rec = { seq, ts: new Date().toISOString(), digest, prev };
  rec.mac = s.macFor(`${rec.seq}\n${rec.ts}\n${rec.digest}\n${rec.prev}`);
  fs.appendFileSync(p, JSON.stringify(rec) + '\n');
  return rec;
}

function cmdChain() {
  const cfg = loadConfig();
  const s = signer(cfg);
  const p = path.join(CWD, LOG_FILE);
  if (!fs.existsSync(p)) die(`no ${LOG_FILE} here -- run:  authormark seal`);
  const lines = fs.readFileSync(p, 'utf8').split('\n').filter(Boolean);
  let broken = 0, unsigned = 0;
  for (let i = 0; i < lines.length; i++) {
    const rec = JSON.parse(lines[i]);
    const want = i === 0 ? '0'.repeat(64)
      : crypto.createHash('sha256').update(lines.slice(0, i).join('\n') + '\n').digest('hex');
    const linkOk = rec.prev === want;
    const macRes = s.macVerify(`${rec.seq}\n${rec.ts}\n${rec.digest}\n${rec.prev}`, rec.mac);
    if (!linkOk) broken++;
    if (macRes === false) unsigned++;
    log(`  #${rec.seq}  ${rec.ts}  ${rec.digest.slice(0, 16)}…  ${linkOk ? 'link OK' : 'LINK BROKEN'}${macRes === null ? '' : (macRes ? ' / mac OK' : ' / MAC BAD')}`);
  }
  log(`\n${lines.length} entries, ${broken} broken link(s), ${unsigned} bad mac(s).`);
  if (broken || unsigned) process.exit(1);
}

// ---------------------------------------------------------------- RFC 3161 timestamp

async function cmdTimestamp(args) {
  const file = positional(args)[0] || MANIFEST_FILE;
  if (!fs.existsSync(file)) die(`no ${file} -- run:  authormark seal`);
  const tsa = flag(args, '--tsa') || 'http://timestamp.digicert.com';
  try { execFileSync('openssl', ['version'], { stdio: 'ignore' }); }
  catch { die('openssl not found -- it builds and verifies the RFC 3161 request'); }

  const tsq = `${file}.tsq`, tsr = `${file}.tsr`;
  execFileSync('openssl', ['ts', '-query', '-data', file, '-sha256', '-cert', '-no_nonce', '-out', tsq]);
  const res = await fetch(tsa, {
    method: 'POST',
    headers: { 'Content-Type': 'application/timestamp-query' },
    body: fs.readFileSync(tsq),
  });
  if (!res.ok) die(`TSA ${tsa} returned HTTP ${res.status}`);
  fs.writeFileSync(tsr, Buffer.from(await res.arrayBuffer()));
  fs.rmSync(tsq, { force: true });
  log(`timestamp token -> ${tsr}  (from ${tsa})`);
  log(`verify:  openssl ts -reply -in ${tsr} -text | grep -E 'Time stamp|Hash'`);
}

// ---------------------------------------------------------------- SLSA attestation

function cmdAttest(args) {
  const cfg = loadConfig();
  const exts = flag(args, '--ext')?.split(',').map(e => (e.startsWith('.') ? e : '.' + e)) || DEFAULT_EXTS;
  const files = collect(positional(args), exts, cfg);
  const subject = files.map(rel => ({ name: rel, digest: { sha256: hashFile(rel).hash } }));
  const now = new Date().toISOString();
  const statement = {
    _type: 'https://in-toto.io/Statement/v1',
    subject,
    predicateType: 'https://slsa.dev/provenance/v1',
    predicate: {
      buildDefinition: {
        buildType: 'https://github.com/Srinivasan-78/authormark-watch/attest/v1',
        externalParameters: { author: cfg.author, github: cfg.github, license: cfg.license || null },
        internalParameters: {},
        resolvedDependencies: [],
      },
      runDetails: {
        builder: { id: cfg.github || 'https://github.com/Srinivasan-78/authormark-watch' },
        metadata: { invocationId: crypto.randomUUID(), startedOn: now, finishedOn: now },
      },
    },
  };
  const out = flag(args, '-o') || ATTEST_FILE;
  fs.writeFileSync(path.join(CWD, out), JSON.stringify(statement) + '\n');
  log(`wrote SLSA provenance for ${subject.length} file(s) -> ${out}`);
  if (args.includes('--sign')) {
    try {
      execFileSync('cosign', ['sign-blob', '--yes', '--output-signature', `${out}.sig`, path.join(CWD, out)], { stdio: 'inherit' });
      log(`cosign signature -> ${out}.sig`);
    } catch {
      warn('cosign not available or sign failed -- statement written unsigned');
    }
  } else {
    log(`sign it:  cosign sign-blob --yes --output-signature ${out}.sig ${out}`);
  }
}

// ---------------------------------------------------------------- key rotation

function cmdRotate(args) {
  const cfg = loadConfig();
  const kp = keyPath(cfg);
  if (!fs.existsSync(kp)) die(`no key at ${kp} -- run:  authormark init`);
  const dir = kp + '.d';
  fs.mkdirSync(dir, { recursive: true });
  const tag = new Date().toISOString().replace(/[:.]/g, '-');
  const archived = path.join(dir, `key-${tag}`);
  fs.copyFileSync(kp, archived);

  if (args.includes('--keep')) {
    log(`archived a copy of the current key to ${archived} (key unchanged)`);
    return;
  }

  if (cfg.algo === 'ed25519') {
    // Stash the retiring public key so signatures made under it still verify.
    if (cfg.publicKey) fs.writeFileSync(path.join(dir, `pub-${tag}.b64`), cfg.publicKey + '\n');
    const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519');
    fs.writeFileSync(kp, privateKey.export({ type: 'pkcs8', format: 'pem' }), { mode: 0o600 });
    cfg.publicKey = publicKey.export({ type: 'spki', format: 'der' }).toString('base64');
    // Rewrite config, preserving key order but with the fresh public key.
    fs.writeFileSync(path.join(CWD, CONFIG_FILE), JSON.stringify(cfg, null, 2) + '\n');
    log(`rotated ed25519 keypair: new private ${kp}, public key updated in ${CONFIG_FILE}`);
  } else {
    fs.writeFileSync(kp, crypto.randomBytes(32).toString('hex') + '\n', { mode: 0o600 });
    log(`rotated: new key at ${kp}, previous archived to ${archived}`);
  }
  log(`old marks still verify (archived keys/pubkeys are tried on check/scan/chain).`);
  log(`re-stamp to move everything to the new key:  authormark stamp .`);
}

// Archived ed25519 public keys (base64 DER), newest first.
function loadArchivedPubs(cfg) {
  const dir = keyPath(cfg) + '.d';
  if (!fs.existsSync(dir)) return [];
  const out = [];
  for (const f of fs.readdirSync(dir).sort().reverse()) {
    if (!f.startsWith('pub-')) continue;
    try {
      out.push(crypto.createPublicKey({
        key: Buffer.from(fs.readFileSync(path.join(dir, f), 'utf8').trim(), 'base64'),
        format: 'der', type: 'spki',
      }));
    } catch {}
  }
  return out;
}

// Current key first, then any archived under ~/.authormark.key.d/, newest first.
function loadAllKeys(cfg) {
  const keys = [loadKey(cfg)];
  const dir = keyPath(cfg) + '.d';
  if (fs.existsSync(dir)) {
    for (const f of fs.readdirSync(dir).sort().reverse()) {
      if (!f.startsWith('key-')) continue;
      try { keys.push(Buffer.from(fs.readFileSync(path.join(dir, f), 'utf8').trim(), 'hex')); } catch {}
    }
  }
  return keys;
}

function cmdVerify(args) {
  const cfg = loadConfig();
  const s = signer(cfg);
  const file = positional(args)[0] || MANIFEST_FILE;
  const m = JSON.parse(fs.readFileSync(file, 'utf8'));
  const digest = crypto.createHash('sha256')
    .update(m.files.map(e => `${e.sha256}  ${e.path}`).join('\n')).digest('hex');
  const proofRes = s.macVerify(m.digest, m.proof);
  log(`manifest digest: ${digest === m.digest ? 'OK' : 'MISMATCH'}`);
  log(`${m.algo === 'ed25519' ? 'ed25519 proof' : 'HMAC proof'}: ${
    proofRes === null ? 'SKIPPED -- no key/pubkey available'
    : proofRes ? 'OK -- sealed with your key' : 'FAIL -- not sealed by your key'}`);
  let changed = 0, missing = 0;
  for (const e of m.files) {
    if (!fs.existsSync(e.path)) { missing++; console.error(`  gone:    ${e.path}`); continue; }
    if (hashFile(e.path).hash !== e.sha256) { changed++; console.error(`  changed: ${e.path}`); }
  }
  log(`${m.fileCount} sealed, ${changed} changed, ${missing} gone since ${m.sealedAt}`);
  if (proofRes === false || digest !== m.digest || changed > 0) process.exit(1);
}

function cmdScan(args) {
  const cfg = fs.existsSync(path.join(CWD, CONFIG_FILE)) ? loadConfig() : null;
  const keys = cfg && fs.existsSync(keyPath(cfg)) ? loadAllKeys(cfg) : [];
  for (const f of positional(args)) {
    log(`\n=== ${f}`);
    const buf = fs.readFileSync(f);
    const ext = path.extname(f).toLowerCase();
    if (ext === '.png') { scanPng(buf, keys[0] || null); continue; }
    if (ext === '.jpg' || ext === '.jpeg') { scanJpeg(buf); continue; }
    if (MEDIA_EXTS.has(ext)) {
      const raw = buf.toString('latin1');
      const hit = ext === '.svg' ? /<metadata[^>]*id="authormark"/.test(raw) : raw.includes(SENTINEL);
      log(`  ${ext.slice(1).toUpperCase()} metadata mark: ${hit ? 'present' : 'not found'}`);
      continue;
    }
    const text = buf.toString('utf8');
    const { header, body } = splitHeader(text);
    if (header) {
      log(header.split('\n').map(l => '  ' + l.trim()).join('\n'));
      const claimed = header.match(/Fingerprint: AMK1\.([A-Za-z0-9_-]{22})/)?.[1];
      if (keys.length) {
        const hit = keys.findIndex(k => claimed === fingerprint(k, body));
        log(`  -> fingerprint ${hit === 0 ? 'VALID for your current key' : hit > 0 ? `VALID for archived key #${hit}` : 'does NOT match current content'}`);
      }
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
  if (buf.length < 8 || buf.readUInt32BE(0) !== 0x89504e47) die('not a PNG');
  const out = [];
  let p = 8;
  // p + 8 so a truncated tail stops the walk instead of throwing a range error.
  while (p + 8 <= buf.length) {
    const len = buf.readUInt32BE(p);
    const type = buf.toString('ascii', p + 4, p + 8);
    if (p + 12 + len > buf.length) die('truncated PNG chunk');
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
  const ihdr = chunks.find(c => c.type === 'IHDR')?.data;
  if (!ihdr || ihdr.length < 13) die('PNG has no usable IHDR');
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
  if (colorType === 3 && !plte) die('palette PNG has no PLTE chunk');
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
  if (outFlag && files.length > 1) die('-o takes a single input file -- use --inplace for a batch');
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
    if (MEDIA_EXTS.has(ext)) { markMedia(f, out, cfg); continue; }
    if (ext !== '.png') { warn(`skip ${f} (unsupported: ${ext || 'no extension'})`); continue; }

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

// ---------------------------------------------------------------- other media carriers
// Metadata-level marks only (no pixel/DCT stego). Enough to assert authorship
// and survive a plain copy; a re-encode by an editor can still strip them --
// `authormark attack` shows exactly what survives what.

const MEDIA_EXTS = new Set(['.gif', '.svg', '.mp3', '.webp', '.mp4', '.m4v', '.m4a', '.mov', '.pdf']);

function markMedia(f, out, cfg) {
  const ext = path.extname(f).toLowerCase();
  const buf = fs.readFileSync(f);
  const rights = `${SENTINEL} ${NOREMOVE}. Copyright (c) ${cfg.year} ${cfg.author}. ${cfg.github}`;
  let marked;
  if (ext === '.gif') marked = gifMark(buf, rights);
  else if (ext === '.svg') marked = svgMark(buf, cfg, rights);
  else if (ext === '.mp3') marked = mp3Mark(buf, cfg, rights);
  else if (ext === '.webp') marked = webpMark(buf, cfg, rights);
  else if (['.mp4', '.m4v', '.m4a', '.mov'].includes(ext)) marked = mp4Mark(buf, cfg);
  else if (ext === '.pdf') marked = pdfMark(buf, cfg, rights);
  else return false;
  fs.writeFileSync(out, marked);
  log(`${f} -> ${out}  [${ext.slice(1).toUpperCase()} metadata mark]`);
  return true;
}

// GIF89a: a Comment Extension (0x21 0xFE ... 0x00) right after the header +
// Logical Screen Descriptor + optional Global Color Table.
function gifMark(buf, text) {
  if (buf.toString('ascii', 0, 3) !== 'GIF') die('not a GIF');
  let p = 13;
  const packed = buf[10];
  if (packed & 0x80) p += 3 * (2 ** ((packed & 7) + 1));   // skip GCT
  const clean = [];
  // Drop any comment extension we previously wrote, keep everything else.
  let q = p;
  while (q < buf.length && buf[q] === 0x21 && buf[q + 1] === 0xFE) {
    let r = q + 2;
    while (buf[r] && r < buf.length) r += 1 + buf[r];
    r++;
    if (buf.toString('latin1', q + 3, q + 3 + SENTINEL.length) !== SENTINEL) { clean.push(buf.subarray(q, r)); }
    q = r;
  }
  const body = Buffer.from(text, 'latin1');
  const subs = [];
  for (let i = 0; i < body.length; i += 255) {
    const chunk = body.subarray(i, i + 255);
    subs.push(Buffer.from([chunk.length]), chunk);
  }
  const ext = Buffer.concat([Buffer.from([0x21, 0xFE]), ...subs, Buffer.from([0x00])]);
  return Buffer.concat([buf.subarray(0, p), ext, ...clean, buf.subarray(q)]);
}

// SVG: a real <metadata> element with Dublin Core, just inside <svg …>.
function svgMark(buf, cfg, rights) {
  // Only ever replace our own <metadata id="authormark">; leave the author's alone.
  let s = buf.toString('utf8').replace(/\s*<metadata\b[^>]*\bid="authormark"[\s\S]*?<\/metadata>\s*/i, '');
  const md = `<metadata id="authormark">` +
    `<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:dc="http://purl.org/dc/elements/1.1/">` +
    `<rdf:Description><dc:creator>${esc(cfg.author)}</dc:creator>` +
    `<dc:rights>${esc(rights)}</dc:rights><dc:identifier>${esc(cfg.github)}</dc:identifier></rdf:Description>` +
    `</rdf:RDF></metadata>`;
  const m = s.match(/<svg\b[^>]*>/i);
  if (!m) die('no <svg> root element');
  const at = m.index + m[0].length;
  return Buffer.from(s.slice(0, at) + '\n  ' + md + s.slice(at), 'utf8');
}

// ID3v2.4 tag prepended to the MP3 (skips/replaces an existing leading ID3 tag).
function mp3Mark(buf, cfg, rights) {
  let start = 0;
  if (buf.toString('latin1', 0, 3) === 'ID3') {
    const sz = (buf[6] << 21) | (buf[7] << 14) | (buf[8] << 7) | buf[9];
    start = 10 + sz + ((buf[5] & 0x10) ? 10 : 0);
  }
  const synch = n => Buffer.from([(n >> 21) & 0x7f, (n >> 14) & 0x7f, (n >> 7) & 0x7f, n & 0x7f]);
  const textFrame = (id, val) => {
    const data = Buffer.concat([Buffer.from([0x03]), Buffer.from(val, 'utf8')]);   // 0x03 = UTF-8
    return Buffer.concat([Buffer.from(id, 'latin1'), synch(data.length), Buffer.from([0, 0]), data]);
  };
  const frames = Buffer.concat([
    textFrame('TCOP', `${cfg.year} ${cfg.author}`),
    textFrame('TPE1', cfg.author),
    textFrame('TENC', 'authormark/1'),
    textFrame('TXXX', `authormark\x00${rights}`),
  ]);
  const tag = Buffer.concat([Buffer.from('ID3\x04\x00\x00', 'latin1'), synch(frames.length), frames]);
  return Buffer.concat([tag, buf.subarray(start)]);
}

// WebP: append an "XMP " chunk (and flip the VP8X XMP flag when the file is
// already extended). Simple VP8/VP8L files are left with a trailing chunk that
// compliant readers still pick up.
function webpMark(buf, cfg, rights) {
  if (buf.toString('ascii', 0, 4) !== 'RIFF' || buf.toString('ascii', 8, 12) !== 'WEBP') die('not a WebP');
  const xmp = Buffer.from(
    `<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">` +
    `<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/">` +
    `<dc:creator>${esc(cfg.author)}</dc:creator><dc:rights>${esc(rights)}</dc:rights>` +
    `<dc:identifier>${esc(cfg.github)}</dc:identifier></rdf:Description></rdf:RDF></x:xmpmeta>`, 'utf8');
  const chunks = [];
  let p = 12;
  while (p + 8 <= buf.length) {
    const fourcc = buf.toString('ascii', p, p + 4);
    const size = buf.readUInt32LE(p + 4);
    const end = p + 8 + size + (size & 1);
    if (fourcc !== 'XMP ') chunks.push(buf.subarray(p, Math.min(end, buf.length)));
    p = end;
  }
  const xmpChunk = Buffer.concat([
    Buffer.from('XMP '), (() => { const b = Buffer.alloc(4); b.writeUInt32LE(xmp.length); return b; })(),
    xmp, xmp.length & 1 ? Buffer.from([0]) : Buffer.alloc(0),
  ]);
  if (chunks[0] && chunks[0].toString('ascii', 0, 4) === 'VP8X') chunks[0][8 + 0] |= 0x04;   // XMP flag
  const bodyBuf = Buffer.concat([...chunks, xmpChunk]);
  const riff = Buffer.alloc(12);
  riff.write('RIFF', 0, 'ascii'); riff.writeUInt32LE(4 + bodyBuf.length, 4); riff.write('WEBP', 8, 'ascii');
  return Buffer.concat([riff, bodyBuf]);
}

// ISO-BMFF / QuickTime: a moov/udta with ©cpy ©ART ©nam ©cmt atoms.
function mp4Mark(buf, cfg) {
  const box = (type, payload) => {
    const b = Buffer.alloc(8); b.writeUInt32BE(payload.length + 8, 0); b.write(type, 4, 'latin1');
    return Buffer.concat([b, payload]);
  };
  const cAtom = (type, text) => {
    const t = Buffer.from(text, 'utf8');
    const d = Buffer.alloc(4); d.writeUInt16BE(t.length, 0); d.writeUInt16BE(0x55c4, 2);   // len, lang(und)
    return box(type, Buffer.concat([d, t]));
  };
  // locate top-level moov
  let p = 0, moovStart = -1, moovSize = 0;
  while (p + 8 <= buf.length) {
    let size = buf.readUInt32BE(p);
    const type = buf.toString('latin1', p + 4, p + 8);
    if (size === 1) size = Number(buf.readBigUInt64BE(p + 8));
    if (size < 8) break;
    if (type === 'moov') { moovStart = p; moovSize = size; break; }
    p += size;
  }
  if (moovStart === -1) die('no moov box -- streamed/fragmented MP4 not supported');
  const udta = box('udta', Buffer.concat([
    cAtom('\xa9cpy', `${cfg.year} ${cfg.author}`),
    cAtom('\xa9ART', cfg.author),
    cAtom('\xa9nam', `${SENTINEL} ${NOREMOVE}`),
    cAtom('\xa9cmt', `${cfg.github}`),
  ]));
  const oldMoov = buf.subarray(moovStart, moovStart + moovSize);
  const newMoovInner = Buffer.concat([oldMoov.subarray(8), udta]);
  const newMoov = box('moov', newMoovInner);
  return Buffer.concat([buf.subarray(0, moovStart), newMoov, buf.subarray(moovStart + moovSize)]);
}

// PDF incremental update: append an /Info dict + XMP stream, a catalog override
// pointing at the XMP, a fresh xref section and a trailer chaining to /Prev.
function pdfMark(buf, cfg, rights) {
  const s = buf.toString('latin1');
  if (!s.startsWith('%PDF-')) die('not a PDF');
  const lastXref = s.lastIndexOf('startxref');
  if (lastXref === -1) die('no startxref -- linearised/broken PDF');
  const prev = parseInt(s.slice(lastXref + 9).trim(), 10);
  const sizeM = s.match(/\/Size\s+(\d+)/g);
  let size = sizeM ? Math.max(...sizeM.map(x => parseInt(x.replace(/\D/g, ''), 10))) : 0;
  const rootM = s.match(/\/Root\s+(\d+)\s+(\d+)\s+R/);
  if (!rootM || !size) die('cannot locate /Root or /Size in trailer');
  const rootNum = rootM[1];

  let out = buf.length && buf[buf.length - 1] === 0x0a ? Buffer.from(buf) : Buffer.concat([buf, Buffer.from('\n')]);
  const off = {};
  const infoNum = ++size, xmpNum = ++size;

  const xmp = `<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>` +
    `<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">` +
    `<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator><rdf:Seq><rdf:li>${esc(cfg.author)}</rdf:li></rdf:Seq></dc:creator>` +
    `<dc:rights><rdf:Alt><rdf:li xml:lang="x-default">${esc(rights)}</rdf:li></rdf:Alt></dc:rights></rdf:Description>` +
    `</rdf:RDF></x:xmpmeta><?xpacket end="w"?>`;

  const append = str => { out = Buffer.concat([out, Buffer.from(str, 'latin1')]); };
  off[infoNum] = out.length;
  append(`${infoNum} 0 obj\n<< /Producer (authormark/1) /Author (${esc(cfg.author)}) ` +
    `/Copyright (${esc(`${cfg.year} ${cfg.author}`)}) >>\nendobj\n`);
  off[xmpNum] = out.length;
  append(`${xmpNum} 0 obj\n<< /Type /Metadata /Subtype /XML /Length ${xmp.length} >>\nstream\n${xmp}\nendstream\nendobj\n`);
  off[rootNum] = out.length;
  append(`${rootNum} 0 obj\n<< /Type /Catalog /Metadata ${xmpNum} 0 R >>\nendobj\n`);

  const xrefStart = out.length;
  const nums = [Number(rootNum), infoNum, xmpNum].sort((a, b) => a - b);
  let xref = `xref\n`;
  for (const n of nums) xref += `${n} 1\n${String(off[n]).padStart(10, '0')} 00000 n \n`;
  xref += `trailer\n<< /Size ${size + 1} /Root ${rootNum} 0 R /Info ${infoNum} 0 R /Prev ${prev} >>\n` +
    `startxref\n${xrefStart}\n%%EOF\n`;
  return Buffer.concat([out, Buffer.from(xref, 'latin1')]);
}

// ---------------------------------------------------------------- robustness harness

async function cmdAttack(args) {
  const file = positional(args)[0];
  if (!file || !fs.existsSync(file)) die('usage: authormark attack <marked-image>');
  const have = t => { try { execFileSync(t, ['-version'], { stdio: 'ignore' }); return true; } catch { return false; } };
  const magick = have('magick') ? 'magick' : have('convert') ? 'convert' : null;
  if (!magick) die('needs ImageMagick (`magick` or `convert`) on PATH');

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'am-attack-'));
  const ext = path.extname(file).toLowerCase();
  const attacks = [
    ['re-encode (q80)', ['-quality', '80']],
    ['resize 50%', ['-resize', '50%']],
    ['resize 150%', ['-resize', '150%']],
    ['crop 90%', ['-gravity', 'center', '-crop', '90%x90%+0+0', '+repage']],
    ['rotate 90', ['-rotate', '90']],
    ['grayscale', ['-colorspace', 'Gray']],
    ['strip metadata', ['-strip']],
  ];
  const probe = p => {
    const buf = fs.readFileSync(p);
    let visibleMeta = false, lsb = false;
    try {
      if (path.extname(p).toLowerCase() === '.png') {
        const chunks = pngChunks(buf);
        visibleMeta = chunks.some(c => (c.type === 'tEXt' || c.type === 'iTXt') && c.data.toString('latin1').includes('authormark'));
        const img = decodePng(buf);
        lsb = lsbExtract(img.rgba, img.width, img.height).length > 0;
      } else {
        visibleMeta = buf.toString('latin1').includes('authormark');
      }
    } catch {}
    return { visibleMeta, lsb };
  };

  log(`baseline ${file}:`);
  const base = probe(file);
  log(`  metadata mark: ${base.visibleMeta ? 'present' : 'ABSENT'} | LSB payload: ${base.lsb ? 'present' : 'n/a'}`);
  log(`\nafter each attack (ImageMagick):`);
  for (const [name, ops] of attacks) {
    const outP = path.join(tmp, `a${ext || '.png'}`);
    try {
      execFileSync(magick, [file, ...ops, outP], { stdio: 'ignore' });
      const r = probe(outP);
      log(`  ${name.padEnd(20)}  metadata ${r.visibleMeta ? 'survived' : 'lost   '}   LSB ${r.lsb ? 'survived' : 'lost'}`);
    } catch {
      log(`  ${name.padEnd(20)}  (attack failed to run)`);
    }
  }
  fs.rmSync(tmp, { recursive: true, force: true });
}

// ---------------------------------------------------------------- theft crawl

// Pull the distinctive strings out of this repo's stamped files: the keyed
// fingerprints and (for ed25519) signatures. A verbatim copy carries them too.
function localMarks(cfg) {
  const files = collect([], DEFAULT_EXTS, cfg);
  const fps = new Set(), sigs = new Set();
  for (const rel of files) {
    if (tooBig(rel, cfg)) continue;
    const { header } = splitHeader(fs.readFileSync(rel, 'utf8'));
    if (!header) continue;
    const fp = header.match(/Fingerprint: AMK1\.([A-Za-z0-9_-]{22})/)?.[1];
    const sg = header.match(/Signature: AMK2\.([A-Za-z0-9_-]{40,})/)?.[1];
    if (fp) fps.add(fp);
    if (sg) sigs.add(sg);
  }
  return { fps: [...fps], sigs: [...sigs] };
}

async function ghSearchCode(q, token) {
  const res = await fetch(`https://api.github.com/search/code?per_page=20&q=${encodeURIComponent(q)}`, {
    headers: {
      Accept: 'application/vnd.github.v3+json',
      'User-Agent': 'authormark-crawl',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });
  if (res.status === 403 && res.headers.get('x-ratelimit-remaining') === '0') {
    const wait = Math.max(1, (Number(res.headers.get('x-ratelimit-reset')) * 1000 - Date.now()) / 1000);
    warn(`GitHub search rate-limited; sleeping ${Math.ceil(wait)}s`);
    await new Promise(r => setTimeout(r, wait * 1000 + 500));
    return ghSearchCode(q, token);
  }
  if (!res.ok) { warn(`GitHub search "${q}" -> HTTP ${res.status}`); return []; }
  return (await res.json()).items || [];
}

async function sgSearch(literal) {
  // Sourcegraph public instance -- no auth needed for public code.
  const q = `context:global content:${JSON.stringify(literal)} count:20`;
  try {
    const res = await fetch(`https://sourcegraph.com/.api/search/stream?q=${encodeURIComponent(q)}`, {
      headers: { Accept: 'text/event-stream', 'User-Agent': 'authormark-crawl' },
    });
    if (!res.ok) return [];
    const text = await res.text();
    const hits = [];
    for (const line of text.split('\n')) {
      if (!line.startsWith('data:')) continue;
      try {
        const evt = JSON.parse(line.slice(5));
        if (!Array.isArray(evt)) continue;
        for (const m of evt) {
          if (m.type === 'content' || m.repository) {
            hits.push({ repo: m.repository, path: m.path, url: m.repository ? `https://${m.repository}` : null });
          }
        }
      } catch {}
    }
    return hits;
  } catch { return []; }
}

async function cmdCrawl(args) {
  const cfg = loadConfig();
  const owner = (cfg.github || '').replace(/^https?:\/\/github\.com\//, '').split('/')[0].toLowerCase();
  const token = flag(args, '--token') || process.env.GITHUB_TOKEN || process.env.GH_TOKEN ||
    (() => { try { return execFileSync('gh', ['auth', 'token'], { encoding: 'utf8' }).trim(); } catch { return null; } })();
  const asJson = args.includes('--json');
  const ghOnly = args.includes('--github-only');
  const sgOnly = args.includes('--sourcegraph-only');

  const { fps, sigs } = localMarks(cfg);
  // Each needle: the literal to search plus which label prefixes it.
  const needles = [
    ...sigs.slice(0, 3).map(v => ({ literal: `AMK2.${v}`, kind: 'sig' })),
    ...fps.map(v => ({ literal: `AMK1.${v}`, kind: 'fp' })),
  ].slice(0, 8);
  if (!needles.length) die('no stamped files here -- nothing to search for');

  const foreign = [];
  const seen = new Set();
  // "owner/repo" or "host/owner/repo" -> owner segment.
  const ownerOf = repo => {
    const segs = repo.toLowerCase().replace(/^https?:\/\//, '').split('/').filter(Boolean);
    return segs.length >= 2 ? segs[segs.length - 2] : '';
  };
  const add = (src, repo, filePath, url) => {
    if (!repo || (owner && ownerOf(repo) === owner)) return;   // skip your own repos
    const k = `${repo}::${filePath}`;
    if (seen.has(k)) return;
    seen.add(k);
    foreign.push({ via: src, repo, path: filePath || null, url: url || null });
  };

  if (!sgOnly) {
    if (!token) warn('no GitHub token (env GITHUB_TOKEN/GH_TOKEN or --token) -- code search needs auth; skipping GitHub');
    else {
      for (const n of needles) {
        for (const it of await ghSearchCode(`"${n.literal}"`, token)) {
          add('github', it.repository?.full_name, it.path, it.html_url);
        }
        await new Promise(r => setTimeout(r, 6500));   // stay under 10 search req/min
      }
    }
  }
  if (!ghOnly) {
    for (const n of needles.slice(0, 4)) {
      for (const h of await sgSearch(n.literal)) add('sourcegraph', h.repo, h.path, h.url);
    }
  }

  if (asJson) { process.stdout.write(JSON.stringify({ owner, searched: needles.length, foreign }, null, 2) + '\n'); }
  else if (!foreign.length) log(`\ncrawl: no copies of ${needles.length} mark(s) found outside @${owner}.`);
  else {
    log(`\ncrawl: ${foreign.length} possible copy/copies outside @${owner}:`);
    for (const f of foreign) log(`  [${f.via}] ${f.repo}${f.path ? ' / ' + f.path : ''}${f.url ? '  ' + f.url : ''}`);
  }
  if (foreign.length) process.exitCode = 2;
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
  while (p + 3 < buf.length && buf[p] === 0xff) {
    const m = buf[p + 1];
    if (m === 0xda || m === 0xd9) break;
    if (m === 0xff) { p++; continue; }                       // fill byte
    if (m === 0x01 || (m >= 0xd0 && m <= 0xd7)) { p += 2; continue; } // no payload
    const len = buf.readUInt16BE(p + 2);
    if (len < 2 || p + 2 + len > buf.length) break;
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
  const self = fs.realpathSync(process.argv[1]);
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

// ---------------------------------------------------------------- setup (new repo)

const WORKFLOW = `name: authormark
on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262  # v4.2.2
      - uses: actions/setup-node@39370e3970a6d050c480ffad4ff0ed4d3fdee5af  # v4.1.0
        with:
          node-version: '20'
      - name: Verify authorship watermarks
        # A pull_request gets a presence-only check: an edited file's fingerprint
        # is legitimately stale until it is re-stamped, so a full check there
        # fails every PR that touches a marked file. Push to a protected branch
        # runs the full ed25519/HMAC verification.
        run: |
          if [ "\${{ github.event_name }}" = "pull_request" ]; then
            node .authormark/authormark.mjs check --presence .
          else
            node .authormark/authormark.mjs check .
          fi
`;

const AGENT_RULE = `
## Authorship headers — never remove

Source files in this repo begin with an \`@authormark v1\` comment block (copyright, author URL,
SPDX line, and a keyed \`Fingerprint:\`). It is a watermark, not boilerplate.

- **Never delete, edit, reorder, or relocate that block.** Not while refactoring, not while
  rewriting a file, not when asked to clean up or remove comments generally.
- When you move or rename a file, the header moves with it.
- Editing a file makes its fingerprint stale. That is expected. Refresh it with
  \`node .authormark/authormark.mjs stamp <file>\` — never resolve staleness by deleting the header.
- CI runs a presence check on every PR (a missing header fails it) and the full
  \`authormark check\` on push to the default branch.
`;

function mitLicense(cfg) {
  return `MIT License

Copyright (c) ${cfg.year} ${cfg.author}${cfg.email ? ` <${cfg.email}>` : ''}
${cfg.github}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
`;
}

function findImages(dir = '.', acc = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isSymbolicLink()) continue;
    if (e.isDirectory()) {
      if (e.name === '.git' || SKIP_DIRS.has(e.name)) continue;
      findImages(p, acc);
    } else if (/\.(png|jpe?g)$/i.test(e.name)) {
      try { if (fs.statSync(p).size <= 5 * 1024 * 1024) acc.push(p); } catch { /* vanished */ }
    }
  }
  return acc;
}

// Everything a fresh repo needs, in one command. Every step is idempotent, so
// rerunning it after adding files just tops the repo up.
function cmdSetup(args) {
  const self = fs.realpathSync(process.argv[1]);
  const steps = [];

  if (!fs.existsSync(path.join(CWD, CONFIG_FILE))) cmdInit([...args, '--quiet']);
  else log(`config kept: ${CONFIG_FILE}`);
  const cfg = loadConfig();

  const vendorDir = path.join(CWD, '.authormark');
  const vendored = path.join(vendorDir, 'authormark.mjs');
  if (path.dirname(self) !== vendorDir && self !== vendored) {
    fs.mkdirSync(vendorDir, { recursive: true });
    fs.copyFileSync(self, vendored);
    steps.push('vendored .authormark/authormark.mjs');
  }

  fs.mkdirSync(path.join(CWD, '.github', 'workflows'), { recursive: true });
  fs.writeFileSync(path.join(CWD, '.github', 'workflows', 'authormark.yml'), WORKFLOW);
  steps.push('CI workflow');

  const has = f => fs.existsSync(path.join(CWD, f));
  const reads = f => (has(f) ? fs.readFileSync(path.join(CWD, f), 'utf8') : '');
  if (!/^## Authorship headers/m.test(reads('CLAUDE.md') + reads('AGENTS.md'))) {
    if (has('CLAUDE.md')) fs.appendFileSync(path.join(CWD, 'CLAUDE.md'), AGENT_RULE);
    else if (has('AGENTS.md')) fs.appendFileSync(path.join(CWD, 'AGENTS.md'), AGENT_RULE);
    else {
      fs.writeFileSync(path.join(CWD, 'AGENTS.md'), '# Repo rules\n' + AGENT_RULE);
      fs.writeFileSync(path.join(CWD, 'CLAUDE.md'), '# Repo rules\n\n@AGENTS.md\n');
    }
    steps.push('agent rules');
  }

  const licenses = ['LICENSE', 'LICENSE.md', 'LICENSE.txt', 'LICENCE', 'LICENCE.md', 'COPYING', 'COPYING.txt'];
  if (cfg.license === 'MIT' && !licenses.some(has)) {
    fs.writeFileSync(path.join(CWD, 'LICENSE'), mitLicense(cfg));
    steps.push('LICENSE (MIT)');
  }

  const extArg = flag(args, '--ext');
  cmdStamp(['.', '--zw', ...(extArg ? ['--ext', extArg] : [])]);

  if (!args.includes('--no-images')) {
    let ok = 0, bad = 0, already = 0;
    for (const img of findImages()) {
      // Re-marking rewrites the file for no gain, so a rerun leaves marked images alone.
      if (fs.readFileSync(img).includes('authormark/1')) { already++; continue; }
      // Child process per image: an unsupported PNG must not abort the run.
      try { execFileSync(process.execPath, [self, 'image', img, '--inplace'], { stdio: 'ignore' }); ok++; }
      catch { bad++; }
    }
    if (ok || bad || already) {
      steps.push(`${ok} image(s) marked` +
        (already ? `, ${already} already marked` : '') +
        (bad ? `, ${bad} skipped (unsupported)` : ''));
    }
  }

  cmdSeal([]);

  if (!args.includes('--no-hook') && fs.existsSync(path.join(CWD, '.git', 'hooks'))) cmdHook([]);

  log(`\nsetup complete: ${steps.join(', ')}`);
  log('commit these, and CI will reject any future PR that strips a watermark.');
}

// ---------------------------------------------------------------- utils + main

// undefined when the flag is absent OR carries no value, so every `?? default`
// and `|| default` downstream falls back instead of seeing an empty string
// (Number('') is 0 -- that silently made --opacity/--scale zero).
function flag(args, name) {
  const i = args.indexOf(name);
  if (i === -1) return undefined;
  const v = args[i + 1];
  return v && !v.startsWith('-') ? v : undefined;
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

  setup [--author N] [--email E] [--github U] [--license L] [--no-images] [--no-hook]
       ONE COMMAND for a new repo: init + vendor + CI + agent rules + LICENSE
       + stamp + images + seal + pre-commit hook. Idempotent -- rerun anytime.

  init [--author N] [--email E] [--github U] [--license L] [--reuse] [--ed25519]
       create .authormark.json + your secret key (~/.authormark.key)
       --reuse   also emits a REUSE-spec SPDX-FileCopyrightText line
       --ed25519 asymmetric mode: a Signature: line CI verifies from the public
                 key in config -- no secret needed to check authenticity

  stamp <paths...> [--ext .ts,.tsx] [--zw] [--dry]
       insert/refresh the copyright header + keyed fingerprint in source files
       --zw also plants an invisible zero-width mark that survives copy-paste
       honours .authormarkignore plus the config include / maxBytes settings

  unstamp <paths...> [--ext ...] [--force]
       remove the header block (licence change / upstreaming). Dry unless --force.

  check [paths...] | check --staged | check --json
       exit 1 if any file is unmarked or its fingerprint is stale (for CI/hooks)

  seal [paths...]        write AUTHORSHIP.json + append a hash-chained AUTHORSHIP.log entry
  verify [manifest]      re-verify a manifest against the working tree
  chain                  walk AUTHORSHIP.log: check every prev-hash link and keyed mac
  timestamp [file] [--tsa URL]
                        get an RFC 3161 token for AUTHORSHIP.json (needs openssl)
  attest [paths...] [-o file] [--sign]
                        write a SLSA provenance statement; --sign calls cosign
  rotate [--keep]       new secret key; old one archived and still tried on check/scan
  scan <files...>        show every mark found in a source file or image
  image <files...> [-o out] [--inplace] [--visible "txt"] [--tile]
                   [--opacity 0.55] [--scale N] [--no-stego]
       PNG: text chunks + optional visible watermark + hidden LSB payload
       JPEG: EXIF Artist/Copyright + XMP + COM comment (metadata only)
       GIF/SVG/WebP/MP3/MP4/PDF: metadata-level authorship marks
  attack <marked-image>  re-encode/resize/crop/rotate/strip via ImageMagick and
                        report which marks survive each (needs magick or convert)
  crawl [--token T] [--github-only|--sourcegraph-only] [--json]
                        search GitHub code + Sourcegraph for this repo's
                        fingerprints/signatures in repos you do not own
  hook install           git pre-commit hook that blocks de-watermarked commits`;

async function runCli(argv) {
  const [cmd, ...rest] = argv;
  try {
    switch (cmd) {
      case 'init': cmdInit(rest); break;
      case 'setup': cmdSetup(rest); break;
      case 'stamp': cmdStamp(rest); break;
      case 'unstamp': cmdUnstamp(rest); break;
      case 'check': cmdCheck(rest); break;
      case 'seal': cmdSeal(rest); break;
      case 'verify': cmdVerify(rest); break;
      case 'chain': cmdChain(rest); break;
      case 'timestamp': await cmdTimestamp(rest); break;
      case 'attest': cmdAttest(rest); break;
      case 'rotate': cmdRotate(rest); break;
      case 'scan': cmdScan(rest); break;
      case 'image': cmdImage(rest); break;
      case 'attack': await cmdAttack(rest); break;
      case 'crawl': await cmdCrawl(rest); break;
      case 'hook': cmdHook(rest); break;
      default: log(USAGE); process.exit(cmd ? 1 : 0);
    }
  } catch (e) {
    die(e.message);
  }
}

// Only dispatch when run directly, so tests can import the pure helpers.
const isMain = (() => {
  try { return fileURLToPath(import.meta.url) === fs.realpathSync(process.argv[1]); }
  catch { return false; }
})();
if (isMain) runCli(process.argv.slice(2)).catch(e => die(e.message));

export {
  canonical, fingerprint, contentDigest, signer, zwEncode, zwDecode, splitHeader, insertIndex,
  styleFor, renderHeader, headerLines, isHeaderLine, crc32, textMask,
  lsbEmbed, lsbExtract, buildExif, collect, ignored, includedBy, matchGlob,
  tooBig, hashFile, appendChain, loadAllKeys, localMarks,
  gifMark, svgMark, mp3Mark, webpMark, mp4Mark, pdfMark, runCli,
};

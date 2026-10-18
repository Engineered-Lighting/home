import fs from 'node:fs';
import path from 'node:path';
import { createHash, randomBytes } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import Core from '../app/src/home-equipment-registration.js';
import { readPackIndex, validateManifest } from '../tools/content-packs.mjs';

export const REGISTRATION_ROUTE = '/api/apartment/v1/sites/victoria/maps/victoria-den/rooms/den/registration';
export const MAX_REGISTRATION_BYTES = 64 * 1024;
const DEFAULT_PACKS = fileURLToPath(new URL('../app/data/home-content', import.meta.url));
const STATIC_ROOT = fileURLToPath(new URL('../app/src', import.meta.url));
const LEGACY_DATA = fileURLToPath(new URL('../app/data/apartment', import.meta.url));
const HASH = /^[a-f0-9]{64}$/;
const REVISION = /^(0|[1-9][0-9]{0,14})$/;
const ID = /^[a-z0-9][a-z0-9-]{0,63}$/;
const locks = new Map();
const PRIVATE_HEADERS = {
  'Content-Type': 'application/json; charset=utf-8',
  'Cache-Control': 'private, no-store, max-age=0',
  'CDN-Cache-Control': 'no-store', Pragma: 'no-cache',
  'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
  'Cross-Origin-Resource-Policy': 'same-origin',
};
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const same = (a, b) => Core.canonicalJson(a) === Core.canonicalJson(b);
const fault = (code, status = 503) => Object.assign(new Error(code), { code, status });
function contained(parent, child) {
  const relative = path.relative(parent, child);
  return relative === '' || (!relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative));
}
function absolute(value) {
  if (typeof value !== 'string' || !path.isAbsolute(value) || value.includes('\0')) throw fault('equipment_configuration_invalid');
  return path.resolve(value);
}
// Refuse links in every existing path component, including parents of the root.
// The service-owned private directory is the filesystem authority, not any URL.
function noLinks(file, { missing = false } = {}) {
  let cursor = path.parse(file).root;
  for (const part of file.slice(cursor.length).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part);
    let stat;
    try { stat = fs.lstatSync(cursor); } catch (error) {
      if (missing && error.code === 'ENOENT') return;
      throw fault('equipment_store_unavailable');
    }
    if (stat.isSymbolicLink()) throw fault('equipment_link_refused');
  }
}
function privateMode(stat) {
  if (process.platform !== 'win32' && ((stat.mode & 0o077) || stat.uid !== process.getuid())) throw fault('equipment_store_not_private');
}
function rootPath(root, packs, { initialize = false } = {}) {
  root = absolute(root); packs = absolute(packs);
  for (const forbidden of [packs, STATIC_ROOT, LEGACY_DATA]) {
    if (contained(forbidden, root) || contained(root, forbidden)) throw fault('equipment_private_root_overlap');
  }
  noLinks(root, { missing: initialize });
  if (initialize && !fs.existsSync(root)) fs.mkdirSync(root, { recursive: true, mode: 0o700 });
  noLinks(root);
  const stat = fs.lstatSync(root);
  if (!stat.isDirectory()) throw fault('equipment_store_unavailable');
  privateMode(stat);
  return root;
}
function readFile(file, limit, { privateFile = false } = {}) {
  noLinks(file);
  const before = fs.lstatSync(file);
  if (!before.isFile() || (privateFile && before.nlink !== 1) || before.size < 1 || before.size > limit) throw fault('equipment_file_invalid');
  if (privateFile) privateMode(before);
  const fd = fs.openSync(file, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
  try {
    const opened = fs.fstatSync(fd);
    if (opened.dev !== before.dev || opened.ino !== before.ino || opened.size !== before.size) throw fault('equipment_file_changed');
    const bytes = fs.readFileSync(fd);
    if (bytes.length !== before.size) throw fault('equipment_file_changed');
    return bytes;
  } finally { fs.closeSync(fd); }
}
function jsonFile(file, limit, options) {
  try { return JSON.parse(readFile(file, limit, options).toString('utf8')); }
  catch (error) { if (error.status) throw error; throw fault('equipment_store_corrupt'); }
}

/** Read only the active manifest and the exact canonical frame/collision bytes.
 * Display assets remain immutable; this API cannot select arbitrary files. */
export function readActiveBinding(contentPacksDir = DEFAULT_PACKS) {
  try {
    const packs = absolute(contentPacksDir);
    const site = path.join(packs, 'victoria');
    const revision = readFile(path.join(site, 'ACTIVE'), 128).toString('utf8').trim();
    if (!ID.test(revision)) throw fault('equipment_active_pack_invalid');
    const directory = path.join(site, revision);
    const manifest = validateManifest(jsonFile(path.join(directory, 'content-pack.json'), 256 * 1024));
    if (manifest.siteId !== 'victoria' || manifest.revision !== revision || !manifest.sceneAvailable) throw fault('equipment_active_pack_invalid');
    const active = readPackIndex(packs).packs.filter(item => item.active && item.manifest.siteId === 'victoria');
    if (active.length !== 1 || !same(active[0].manifest, manifest)) throw fault('equipment_catalog_active_mismatch');
    const pinned = (name, maximum) => {
      const asset = manifest.assets[name];
      if (!asset || asset.bytes > maximum) throw fault('equipment_binding_asset_invalid');
      const bytes = readFile(path.join(directory, ...asset.path.split('/')), maximum);
      if (bytes.length !== asset.bytes || hash(bytes) !== asset.sha256) throw fault('equipment_binding_integrity_failed');
      return asset.sha256;
    };
    return {
      content_revision: revision,
      registration_version: manifest.registrationVersion,
      manifest_digest: hash(Core.canonicalJson(manifest)),
      frame_digest: pinned('frame.json', 1024 * 1024),
      canonical_collision_digest: pinned('collision.glb', 32 * 1024 * 1024),
    };
  } catch (error) { if (error.status) throw error; throw fault('equipment_active_pack_unavailable'); }
}
function validated(doc, binding, options = {}) {
  const result = Core.validateDocument(doc, { binding, ...options });
  if (!result.ok) throw fault('equipment_document_invalid', 422);
  if (Buffer.byteLength(JSON.stringify(doc)) > MAX_REGISTRATION_BYTES) throw fault('equipment_document_too_large', 413);
  return doc;
}
function revisionsDirectory(root) {
  const directory = path.join(root, 'revisions');
  noLinks(directory);
  const stat = fs.lstatSync(directory);
  if (!stat.isDirectory()) throw fault('equipment_store_corrupt');
  privateMode(stat);
  return directory;
}
function storedDocument(file, binding) {
  try { return validated(jsonFile(file, MAX_REGISTRATION_BYTES, { privateFile: true }), binding); }
  catch (error) {
    // A damaged or differently framed authority must never look like an empty model.
    if (error.code === 'equipment_document_invalid') throw fault('equipment_store_binding_or_document_invalid');
    throw error;
  }
}
function readStore(root, packs, { revision, digest } = {}) {
  root = rootPath(root, packs);
  const binding = readActiveBinding(packs);
  const head = storedDocument(path.join(root, 'registration.json'), binding);
  const directory = revisionsDirectory(root);
  const committed = storedDocument(path.join(directory, head.revision + '.json'), binding);
  if (!same(head, committed)) throw fault('equipment_store_corrupt');
  let result = head;
  if (revision !== undefined) {
    if (!Number.isSafeInteger(revision) || revision < 0 || revision > head.revision) throw fault('equipment_revision_not_found', 404);
    result = storedDocument(path.join(directory, revision + '.json'), binding);
    if (result.revision !== revision) throw fault('equipment_store_corrupt');
  }
  if (digest !== undefined && (!HASH.test(digest) || result.digest !== digest)) throw fault('equipment_digest_mismatch', 409);
  return result;
}
export function readRegistration(root, { contentPacksDir = DEFAULT_PACKS, ...options } = {}) {
  return readStore(root, absolute(contentPacksDir), options);
}
function writePrivate(file, bytes) {
  const fd = fs.openSync(file, 'wx', 0o600);
  try { fs.writeFileSync(fd, bytes); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
}
function syncDirectory(directory) {
  // Windows does not expose directory fsync through this API. Atomic rename is
  // still used there; the deployed Linux authority also flushes its directory.
  if (process.platform === 'win32') return;
  const fd = fs.openSync(directory, fs.constants.O_RDONLY);
  try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
}
function publish(root, doc, { initialize = false } = {}) {
  const directory = revisionsDirectory(root);
  const bytes = JSON.stringify(doc) + '\n';
  const snapshot = path.join(directory, doc.revision + '.json');
  if (fs.existsSync(snapshot)) throw fault('equipment_revision_already_exists');
  const temporary = path.join(root, '.registration-' + randomBytes(12).toString('hex') + '.tmp');
  try {
    writePrivate(temporary, bytes);
    // Revisions are never replaced. A partial filesystem failure is visible and
    // refused on the next save, rather than overwriting an immutable revision.
    writePrivate(snapshot, bytes);
    syncDirectory(directory);
    if (initialize && fs.existsSync(path.join(root, 'registration.json'))) throw fault('equipment_already_initialized');
    fs.renameSync(temporary, path.join(root, 'registration.json'));
    syncDirectory(root);
  } finally { if (fs.existsSync(temporary)) fs.unlinkSync(temporary); }
}
/** Explicit deployment bootstrap, never called by an HTTP request. The caller
 * supplies owner-reviewed references and an empty or authored initial document. */
export function initRegistration(root, document, { contentPacksDir = DEFAULT_PACKS } = {}) {
  const packs = absolute(contentPacksDir);
  const binding = readActiveBinding(packs);
  const doc = validated(Core.sealDocument(Core.normalizeDocument(document)), binding);
  if (doc.revision !== 0) throw fault('equipment_initial_revision_invalid', 422);
  root = rootPath(root, packs, { initialize: true });
  if (fs.existsSync(path.join(root, 'registration.json')) || fs.existsSync(path.join(root, 'revisions'))) throw fault('equipment_already_initialized');
  fs.mkdirSync(path.join(root, 'revisions'), { mode: 0o700 });
  publish(root, doc, { initialize: true });
  return doc;
}
async function serial(root, operation) {
  const previous = locks.get(root) || Promise.resolve();
  let release;
  const next = new Promise(resolve => { release = resolve; });
  const tail = previous.then(() => next);
  locks.set(root, tail);
  await previous;
  try { return operation(); }
  finally { release(); if (locks.get(root) === tail) locks.delete(root); }
}
export function createRegistrationStore({ root, contentPacksDir = DEFAULT_PACKS } = {}) {
  root = absolute(root); const packs = absolute(contentPacksDir);
  return {
    read: options => readStore(root, packs, options),
    save: (candidate, { authorize = () => true } = {}) => serial(root, () => {
      if (authorize() !== true) throw fault('equipment_authentication_required', 401);
      const stored = readStore(root, packs);
      if (!candidate || candidate.revision !== stored.revision || candidate.digest !== stored.digest) {
        throw Object.assign(fault('equipment_revision_conflict', 409), { registration: stored });
      }
      validated(candidate, stored.binding, { verifyDigest: false, verifyPositions: false });
      const locked = references => { const { confirmed, ...geometry } = references; return geometry; };
      if (!same(locked(candidate.references), locked(stored.references))) throw fault('equipment_references_locked', 422);
      const doc = Core.normalizeDocument(candidate);
      doc.revision = stored.revision + 1;
      const sealed = validated(Core.sealDocument(doc), readActiveBinding(packs));
      rootPath(root, packs); revisionsDirectory(root);
      if (authorize() !== true) throw fault('equipment_authentication_required', 401);
      publish(root, sealed);
      return sealed;
    }),
  };
}

function send(res, status, value, headers = {}) {
  if (res.destroyed || res.writableEnded) return;
  res.writeHead(status, { ...PRIVATE_HEADERS, ...headers });
  res.end(JSON.stringify(value));
}
function routeOf(method, url) {
  const raw = String(url || '');
  const [pathname, query = ''] = raw.split('?');
  if (raw.includes('#') || raw.split('?').length > 2) throw fault('equipment_route_invalid', 400);
  if (pathname === REGISTRATION_ROUTE && !query) {
    if (!['GET', 'PUT'].includes(method)) throw fault('equipment_method_not_allowed', 405);
    return {};
  }
  const suffix = pathname.slice(REGISTRATION_ROUTE.length);
  if (!pathname.startsWith(REGISTRATION_ROUTE) || !/^\/revisions\/(0|[1-9][0-9]{0,14})$/.test(suffix)) throw fault('equipment_route_not_found', 404);
  if (method !== 'GET') throw fault('equipment_method_not_allowed', 405);
  const revision = Number(suffix.slice('/revisions/'.length));
  if (!REVISION.test(String(revision))) throw fault('equipment_route_invalid', 400);
  const params = new URLSearchParams(query);
  const entries = [...params.entries()];
  if (entries.length && (entries.length !== 1 || entries[0][0] !== 'digest' || !HASH.test(entries[0][1]))) throw fault('equipment_route_invalid', 400);
  return { revision, ...(entries.length ? { digest: entries[0][1] } : {}) };
}
function readBody(req, timeoutMs) {
  return new Promise((resolve, reject) => {
    let size = 0; const chunks = [];
    const finish = (error, body) => {
      clearTimeout(timer); req.off('data', data); req.off('end', end); req.off('aborted', aborted); req.off('error', aborted);
      if (error) { req.pause(); reject(error); } else resolve(body);
    };
    const data = chunk => { size += chunk.length; if (size > MAX_REGISTRATION_BYTES) finish(fault('equipment_body_too_large', 413)); else chunks.push(chunk); };
    const end = () => { try { finish(null, JSON.parse(Buffer.concat(chunks).toString('utf8'))); } catch { finish(fault('equipment_json_invalid', 400)); } };
    const aborted = () => finish(fault('equipment_request_aborted', 400));
    const timer = setTimeout(() => finish(fault('equipment_request_timeout', 408)), timeoutMs);
    req.on('data', data); req.once('end', end); req.once('aborted', aborted); req.once('error', aborted);
  });
}
export function createEquipmentRegistrationGateway({ env = process.env, authenticate, requestTimeoutMs = 5000 } = {}) {
  if (!Number.isInteger(requestTimeoutMs) || requestTimeoutMs < 1 || requestTimeoutMs > 5000) throw fault('equipment_timeout_invalid');
  let store, origin, configured = false, generation = 0, active = 0;
  try {
    origin = new URL(env.HOME_WEB_EQUIPMENT_REGISTRATION_ORIGIN);
    if (env.HOME_WEB_EQUIPMENT_REGISTRATION_ENABLED !== '1' || origin.origin !== env.HOME_WEB_EQUIPMENT_REGISTRATION_ORIGIN ||
        !['https:', ...(env.NODE_ENV === 'test' ? ['http:'] : [])].includes(origin.protocol) || origin.username || origin.password) throw fault('equipment_unconfigured');
    store = createRegistrationStore({ root: env.HOME_WEB_EQUIPMENT_REGISTRATION_ROOT, contentPacksDir: env.HOME_CONTENT_PACKS_DIR || DEFAULT_PACKS });
    configured = true;
  } catch { /* Remain visibly unavailable without touching the filesystem. */ }
  const allowed = req => typeof authenticate === 'function' && authenticate(req) === true;
  const handler = async function (req, res) {
    if (!String(req.url || '').startsWith('/api/apartment/v1/')) return false;
    let charged = false;
    try {
      if (!allowed(req)) throw fault('equipment_authentication_required', 401);
      if (!configured) throw fault('equipment_unconfigured');
      if (req.headers.host !== origin.host || (req.headers.origin && req.headers.origin !== origin.origin) ||
          ['cross-site', 'same-site'].includes(req.headers['sec-fetch-site'])) throw fault('equipment_origin_denied', 403);
      const options = routeOf(req.method, req.url);
      if (active >= 8) throw fault('equipment_busy', 429);
      active++; charged = true;
      const epoch = generation, deadline = Date.now() + requestTimeoutMs;
      const authorize = () => epoch === generation && Date.now() <= deadline && !req.aborted && !res.destroyed && allowed(req);
      let registration;
      if (req.method === 'PUT') {
        if (req.headers.origin !== origin.origin || req.headers['x-home-equipment-csrf'] !== '1' ||
            !/^application\/json(?:\s*;.*)?$/i.test(req.headers['content-type'] || '')) throw fault('equipment_csrf_denied', 403);
        const length = req.headers['content-length'];
        if (length && (!/^\d+$/.test(length) || Number(length) > MAX_REGISTRATION_BYTES)) throw fault('equipment_body_too_large', 413);
        const body = await readBody(req, requestTimeoutMs);
        if (!authorize()) throw fault('equipment_authentication_required', 401);
        registration = await store.save(body, { authorize });
      } else {
        if ((req.headers['content-length'] && req.headers['content-length'] !== '0') || req.headers['transfer-encoding']) throw fault('equipment_unexpected_body', 400);
        registration = store.read(options);
      }
      if (!authorize()) throw fault('equipment_authentication_required', 401);
      send(res, 200, { ok: true, registration, capabilities: { read: true, write: options.revision === undefined } }, { ETag: '"' + registration.digest + '"' });
    } catch (error) {
      send(res, error.status || 503, { ok: false, error: error.code || 'equipment_store_unavailable', ...(error.registration ? { registration: error.registration } : {}) },
        error.status === 405 ? { Allow: 'GET, PUT' } : {});
    } finally { if (charged) active--; }
    return true;
  };
  handler.invalidate = req => {
    if (req && (!allowed(req) || !origin || req.headers.origin !== origin.origin)) return false;
    generation++; return true;
  };
  return handler;
}

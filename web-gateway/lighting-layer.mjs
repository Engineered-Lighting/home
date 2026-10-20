// Read-only L3 receiver sidecars beside the active Den pack. The caller keeps
// its existing authentication/origin boundary and supplies that authorization
// explicitly. This module never changes a pack, registration or layer.
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import Core from '../app/src/home-equipment-registration.js';
import { readPackIndex } from '../tools/content-packs.mjs';
import { readActiveBinding } from './equipment-registration.mjs';

export const MAX_LIGHTING_MANIFEST_BYTES = 4 * 1024 * 1024;
// Bound each buffered response and keep at most two filesystem reads in flight.
// The current L3 receiver fields fit independently within this limit.
export const MAX_LIGHTING_FILE_BYTES = 64 * 1024 * 1024;
const MAX_RECEIVERS = 16 * 1024 * 1024, MAX_CONCURRENT = 2;
const ID = '[a-z0-9][a-z0-9-]{0,63}', HASH = /^[a-f0-9]{64}$/;
const ROUTE = new RegExp('^/content-packs/victoria/(' + ID + ')\\.lighting/(lighting-manifest\\.json|(?:splat|splat-mobile|points)-(?:albedo|normal-oct|basis-u8|basis-scale|bounce-tint|flags)\\.bin)$');
const SOURCES = { splat: 'apartment.ply', 'splat-mobile': 'apartment.mobile.ply', points: 'points.ply' };
const FIELDS = { albedo: 3, 'normal-oct': 2, 'basis-u8': null, 'basis-scale': 2, 'bounce-tint': 3, flags: 1 };
const PRIVATE_HEADERS = {
  'Cache-Control': 'private, no-store, max-age=0', 'CDN-Cache-Control': 'no-store', Pragma: 'no-cache',
  'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer', 'Cross-Origin-Resource-Policy': 'same-origin',
};
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const same = (a, b) => Core.canonicalJson(a) === Core.canonicalJson(b);
const fault = (code, status = 503) => Object.assign(new Error(code), { code, status });
const need = (condition, code) => { if (!condition) throw fault(code); };

function noLinks(filename) {
  let cursor = path.parse(filename).root;
  for (const part of filename.slice(cursor.length).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part);
    need(!fs.lstatSync(cursor).isSymbolicLink(), 'lighting_layer_link_refused');
  }
}
function safeFile(root, filename) {
  noLinks(root); noLinks(filename);
  const relative = path.relative(fs.realpathSync(root), fs.realpathSync(filename));
  need(relative !== '' && relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative),
    'lighting_layer_path_invalid');
  return filename;
}
async function readBounded(root, filename, maximum) {
  safeFile(root, filename);
  const before = fs.lstatSync(filename);
  need(before.isFile() && before.nlink === 1 && before.size > 0 && before.size <= maximum, 'lighting_layer_file_invalid');
  const file = await fs.promises.open(filename, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
  try {
    const opened = await file.stat();
    need(opened.dev === before.dev && opened.ino === before.ino && opened.size === before.size && opened.nlink === 1,
      'lighting_layer_file_changed');
    // Read the stat-bounded length rather than reading until EOF if a producer
    // replaces or grows a file while a request is running.
    const bytes = Buffer.alloc(opened.size);
    let position = 0;
    while (position < bytes.length) {
      const read = await file.read(bytes, position, bytes.length - position, position);
      need(read.bytesRead > 0, 'lighting_layer_file_changed'); position += read.bytesRead;
    }
    const after = await file.stat();
    need(after.size === opened.size && after.mtimeMs === opened.mtimeMs && after.ctimeMs === opened.ctimeMs && after.nlink === 1,
      'lighting_layer_file_changed');
    return bytes;
  } finally { await file.close(); }
}
function validateLayer(manifest, binding, pack) {
  need(object(manifest) && manifest.schema === 'home-lighting-layer-1' && manifest.version === 1
    && manifest.site_id === 'victoria' && manifest.provenance?.status === 'modeled' && manifest.provenance?.measured === false,
  'lighting_layer_schema');
  need(same(manifest.binding, binding), 'lighting_layer_binding_mismatch');
  need(object(manifest.pack_assets) && object(manifest.files) && object(manifest.receivers), 'lighting_layer_manifest_invalid');
  for (const [name, asset] of Object.entries(manifest.pack_assets)) {
    need(HASH.test(asset?.sha256 || '') && pack.assets[name]?.sha256 === asset.sha256, 'lighting_layer_pack_asset_mismatch');
  }
  const columns = manifest.columns;
  need(Array.isArray(columns) && columns.length > 0 && columns.length <= 32, 'lighting_layer_columns_invalid');
  const entries = new Map();
  for (const [set, source] of Object.entries(SOURCES)) {
    const receivers = manifest.receivers[set]; if (!receivers) continue;
    need(object(receivers) && Number.isSafeInteger(receivers.count) && receivers.count > 0 && receivers.count <= MAX_RECEIVERS,
      'lighting_layer_receivers_invalid');
    need(receivers.source === source && HASH.test(receivers.source_sha256 || '')
      && receivers.source_sha256 === pack.assets[source]?.sha256 && receivers.source_sha256 === manifest.pack_assets[source]?.sha256,
    'lighting_layer_source_mismatch');
    need(Array.isArray(receivers.column_scale) && receivers.column_scale.length === columns.length
      && receivers.column_scale.every(value => Number.isFinite(value) && value > 0) && Array.isArray(receivers.files),
    'lighting_layer_columns_invalid');
    for (const [field, width] of Object.entries(FIELDS)) {
      const name = set + '-' + field + '.bin', entry = manifest.files[name], bytes = receivers.count * (width ?? columns.length);
      need(entry && entry.bytes === bytes && bytes <= MAX_LIGHTING_FILE_BYTES && HASH.test(entry.sha256 || '')
        && receivers.files.includes(name), 'lighting_layer_file_mismatch');
      entries.set(name, entry);
    }
  }
  need(entries.size > 0, 'lighting_layer_receivers_invalid');
  return entries;
}

function send(req, res, status, bytes, type, headers = {}) {
  if (res.destroyed || res.writableEnded) return;
  res.writeHead(status, { ...PRIVATE_HEADERS, 'Content-Type': type, 'Content-Length': bytes.length, ...headers });
  res.end(req.method === 'HEAD' ? undefined : bytes);
}

/** Returns true when the sidecar namespace was handled, false otherwise.
 * Authentication is fail-closed unless the caller supplies an explicit check;
 * the caller must retain its existing Host/Origin policy before invoking it. */
export function createLightingLayerHandler({ contentPacksDir, authenticate } = {}) {
  const configured = typeof contentPacksDir === 'string' && path.isAbsolute(contentPacksDir) && !contentPacksDir.includes('\0');
  const root = configured ? path.resolve(contentPacksDir) : null;
  const allowed = req => typeof authenticate === 'function' && authenticate(req) === true;
  let active = 0;
  async function serve(req, res) {
    let charged = false;
    try {
      if (!allowed(req)) throw fault('lighting_layer_authentication_required', 401);
      const route = ROUTE.exec(String(req.url || ''));
      if (!route) throw fault('lighting_layer_not_found', 404);
      if (!['GET', 'HEAD'].includes(req.method)) { req.resume?.(); throw fault('lighting_layer_method_not_allowed', 405); }
      if ((req.headers?.['content-length'] && req.headers['content-length'] !== '0') || req.headers?.['transfer-encoding'])
        throw fault('lighting_layer_unexpected_body', 400);
      if (!configured) throw fault('lighting_layer_unconfigured');
      if (active >= MAX_CONCURRENT) throw fault('lighting_layer_busy', 429);
      active++; charged = true;
      const revision = route[1], name = route[2];
      const current = (await readBounded(root, path.join(root, 'victoria', 'ACTIVE'), 128)).toString('utf8').trim();
      if (current !== revision) throw fault('lighting_layer_not_found', 404);
      const binding = readActiveBinding(root);
      need(binding.content_revision === revision, 'lighting_layer_binding_mismatch');
      const pack = readPackIndex(root).packs.find(item => item.active && item.manifest.siteId === 'victoria'
        && item.manifest.revision === revision)?.manifest;
      need(pack && hash(Core.canonicalJson(pack)) === binding.manifest_digest, 'lighting_layer_binding_mismatch');
      const directory = path.join(root, 'victoria', revision + '.lighting');
      let bytes = await readBounded(root, path.join(directory, 'lighting-manifest.json'), MAX_LIGHTING_MANIFEST_BYTES);
      let manifest;
      try { manifest = JSON.parse(bytes.toString('utf8')); } catch { throw fault('lighting_layer_manifest_invalid'); }
      const entries = validateLayer(manifest, binding, pack);
      if (name !== 'lighting-manifest.json') {
        const entry = entries.get(name);
        if (!entry) throw fault('lighting_layer_not_found', 404);
        bytes = await readBounded(root, path.join(directory, name), Math.min(entry.bytes, MAX_LIGHTING_FILE_BYTES));
        need(bytes.length === entry.bytes && hash(bytes) === entry.sha256, 'lighting_layer_file_mismatch');
      }
      // Do not finish an old-pack read after a pack switch or session revocation.
      need(same(binding, readActiveBinding(root)), 'lighting_layer_binding_mismatch');
      if (!allowed(req)) throw fault('lighting_layer_authentication_required', 401);
      if (req.aborted || res.destroyed) return;
      send(req, res, 200, bytes, name.endsWith('.json') ? 'application/json; charset=utf-8' : 'application/octet-stream',
        { ETag: '"' + hash(bytes) + '"' });
    } catch (error) {
      const status = error.status || 503;
      send(req, res, status, Buffer.from(JSON.stringify({ ok: false, error: error.code || 'lighting_layer_unavailable' })),
        'application/json; charset=utf-8', status === 405 ? { Allow: 'GET, HEAD' } : {});
    } finally { if (charged) active--; }
  }
  return (req, res) => {
    const url = String(req.url || '');
    if (!url.startsWith('/content-packs/') || !url.includes('.lighting')) return false;
    void serve(req, res); return true;
  };
}

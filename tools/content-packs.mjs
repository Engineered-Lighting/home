import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

export const MAX_PACK_BYTES = 8 * 1024 ** 3;
const ID = /^[a-z0-9][a-z0-9-]{0,63}$/;
const NAME = /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/;
const RESERVED = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i;
const EXTENSION = /\.(json|jpe?g|png|webp|glb|ply|spz)$/i;
export function validPackPath(value) {
  return typeof value === 'string' && value.length <= 240 && EXTENSION.test(value)
    && value.split('/').every(part => NAME.test(part) && !part.endsWith('.') && !RESERVED.test(part));
}
export function validateManifest(manifest) {
  const fail = () => { throw new Error('content_pack_manifest_invalid'); };
  if (!manifest || manifest.schemaVersion !== 1 || !ID.test(manifest.siteId) || !ID.test(manifest.revision)
      || RESERVED.test(manifest.siteId) || RESERVED.test(manifest.revision)
      || !Number.isInteger(manifest.registrationVersion) || manifest.registrationVersion < 0 || manifest.registrationVersion > 4294967295
      || typeof manifest.sceneAvailable !== 'boolean' || !manifest.assets || Array.isArray(manifest.assets)) fail();
  const entries = Object.entries(manifest.assets);
  if (!entries.length || entries.length > 128) fail();
  let bytes = 0;
  const paths = new Set();
  for (const [name, asset] of entries) {
    if (!NAME.test(name) || !asset || !validPackPath(asset.path) || paths.has(asset.path.toLowerCase())
        || !Number.isSafeInteger(asset.bytes) || asset.bytes <= 0 || asset.bytes > 2 * 1024 ** 3
        || !/^[a-f0-9]{64}$/.test(asset.sha256)) fail();
    paths.add(asset.path.toLowerCase()); bytes += asset.bytes;
  }
  if (bytes > MAX_PACK_BYTES || !Array.isArray(manifest.heroStills) || manifest.heroStills.length > 16) fail();
  for (const hero of manifest.heroStills) {
    const projection = hero.projection;
    if (!hero || !manifest.assets[hero.asset] || !/\.(jpe?g|png|webp)$/i.test(manifest.assets[hero.asset].path)
        || !Number.isFinite(hero.rangeM) || hero.rangeM <= 0
        || !Number.isInteger(hero.widthPx) || hero.widthPx < 1 || hero.widthPx > 32768
        || !Number.isInteger(hero.heightPx) || hero.heightPx < 1 || hero.heightPx > 32768
        || !Number.isFinite(hero.headingDeg) || hero.headingDeg < 0 || hero.headingDeg >= 360
        || !Number.isFinite(hero.pitchDeg) || hero.pitchDeg < -90 || hero.pitchDeg > 0
        || !projection || projection.type !== 'perspective' || !Number.isFinite(projection.aspect)
        || Math.abs(projection.aspect - hero.widthPx / hero.heightPx) > 0.001
        || typeof projection.verified !== 'boolean') fail();
    if (projection.fovDeg !== null && (!Number.isFinite(projection.fovDeg) || projection.fovDeg <= 0 || projection.fovDeg >= 180)) fail();
    if (![null, 'horizontal', 'vertical'].includes(projection.fovAxis)) fail();
    if (projection.verified && (projection.fovDeg === null || projection.fovAxis === null)) fail();
  }
  if (manifest.sceneAvailable && (!manifest.assets['frame.json'] || !manifest.assets['points.ply']
      || !(manifest.assets['apartment.ply'] || manifest.assets['apartment.spz']))) fail();
  if (!manifest.review || !['research-only', 'approved'].includes(manifest.review.status)) fail();
  if (manifest.review.attributionReviewed !== undefined && typeof manifest.review.attributionReviewed !== 'boolean') fail();
  if (manifest.attributions !== undefined && (!Array.isArray(manifest.attributions) || manifest.attributions.length > 8)) fail();
  for (const credit of manifest.attributions || []) {
    if (!credit || typeof credit.text !== 'string' || !credit.text.trim() || credit.text.length > 512
        || /[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/.test(credit.text)
        || Object.keys(credit).some(key => !['text', 'url'].includes(key))) fail();
    if (credit.url !== undefined && credit.url !== null) {
      if (typeof credit.url !== 'string' || Buffer.byteLength(credit.url, 'utf8') > 2048 || /[\u0000-\u0020\u007f-\u009f]/.test(credit.url)) fail();
      let url; try { url = new URL(credit.url); } catch { fail(); }
      if (url.protocol !== 'https:' || !url.hostname || url.username || url.password) fail();
    }
  }
  return manifest;
}
export function safePackFile(root, relative) {
  if (!validPackPath(relative)) throw new Error('content_pack_path_invalid');
  if (fs.lstatSync(root).isSymbolicLink()) throw new Error('content_pack_link_refused');
  const canonicalRoot = fs.realpathSync(root);
  let current = canonicalRoot;
  for (const part of relative.split('/')) {
    current = path.join(current, part);
    if (fs.lstatSync(current).isSymbolicLink()) throw new Error('content_pack_link_refused');
  }
  const canonical = fs.realpathSync(current);
  const fromRoot = path.relative(canonicalRoot, canonical);
  if (!fromRoot || fromRoot.startsWith('..') || path.isAbsolute(fromRoot) || !fs.statSync(canonical).isFile()) {
    throw new Error('content_pack_path_invalid');
  }
  return canonical;
}
export function verifyPack(directory) {
  const manifestFile = safePackFile(directory, 'content-pack.json');
  if (fs.statSync(manifestFile).size > 256 * 1024) throw new Error('content_pack_manifest_invalid');
  const manifest = validateManifest(JSON.parse(fs.readFileSync(manifestFile, 'utf8')));
  for (const asset of Object.values(manifest.assets)) {
    const file = safePackFile(directory, asset.path);
    if (fs.statSync(file).size !== asset.bytes || createHash('sha256').update(fs.readFileSync(file)).digest('hex') !== asset.sha256) {
      throw new Error('content_pack_integrity_failed');
    }
  }
  return manifest;
}

// Only declared pack assets can be exposed by a development/gateway route.
// The caller must retain its existing authentication gate before calling this.
export function resolveServedPackFile(root, requestPath) {
  if (typeof requestPath !== 'string') return null;
  let decoded;
  try { decoded = decodeURIComponent(requestPath); } catch { return null; }
  const [siteId, revision, ...parts] = decoded.split('/');
  if (!ID.test(siteId) || !ID.test(revision) || !validPackPath(parts.join('/'))) return null;
  try {
    const directory = path.join(root, siteId, revision);
    if (fs.lstatSync(path.join(root, siteId)).isSymbolicLink() || fs.lstatSync(directory).isSymbolicLink()) return null;
    const relativeRoot = path.relative(fs.realpathSync(root), fs.realpathSync(directory));
    if (relativeRoot.startsWith('..') || path.isAbsolute(relativeRoot)) return null;
    const manifest = validateManifest(JSON.parse(fs.readFileSync(safePackFile(directory, 'content-pack.json'), 'utf8')));
    if (manifest.siteId !== siteId || manifest.revision !== revision) return null;
    const relative = parts.join('/');
    if (relative !== 'content-pack.json' && !Object.values(manifest.assets).some(asset => asset.path === relative)) return null;
    return safePackFile(directory, relative);
  } catch { return null; }
}

export function readPackIndex(root) {
  try {
    const file = path.join(root, 'index.json');
    if (fs.lstatSync(file).isSymbolicLink() || fs.statSync(file).size > 8 * 1024 * 1024) return { schemaVersion: 1, packs: [] };
    const index = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (index.schemaVersion !== 1 || !Array.isArray(index.packs) || index.packs.length > 512) throw new Error('invalid');
    return { schemaVersion: 1, packs: index.packs.map(item => {
      const manifest = validateManifest(item.manifest);
      // Re-read the installed manifest so an index cannot select missing or
      // partially prepared content, nor provide arbitrary URLs to the client.
      const relative = manifest.siteId + '/' + manifest.revision + '/content-pack.json';
      const installed = resolveServedPackFile(root, relative);
      if (!installed || JSON.stringify(JSON.parse(fs.readFileSync(installed, 'utf8'))) !== JSON.stringify(manifest)) throw new Error('invalid');
      return { manifest, active: item.active === true, baseUrl: '/content-packs/' + manifest.siteId + '/' + manifest.revision + '/' };
    }) };
  } catch { return { schemaVersion: 1, packs: [] }; }
}

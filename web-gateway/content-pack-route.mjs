// Authenticated, read-only access to installed, manifest-declared room assets.
import fs from 'node:fs';
import path from 'node:path';
import { readPackIndex, resolveServedPackFile } from '../tools/content-packs.mjs';

const PRIVATE = { 'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
  'Cross-Origin-Resource-Policy': 'same-origin', 'Referrer-Policy': 'no-referrer' };
const TYPES = { '.json': 'application/json; charset=utf-8', '.glb': 'model/gltf-binary',
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp' };

function unlinkedRoot(root) {
  let cursor = path.parse(root).root;
  for (const part of root.slice(cursor.length).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part);
    if (fs.lstatSync(cursor).isSymbolicLink()) throw new Error('content_pack_link_refused');
  }
}

export function createContentPackHandler({ contentPacksDir, authenticate } = {}) {
  if (!path.isAbsolute(contentPacksDir || '')) throw new Error('content_pack_root_invalid');
  const root = path.resolve(contentPacksDir);
  return function handleContentPack(req, res) {
    const pathname = String(req.url || '').split('?')[0];
    if (!pathname.startsWith('/content-packs/')) return false;
    const json = (status, value, extra = {}) => {
      const body = Buffer.from(JSON.stringify(value));
      res.writeHead(status, { ...PRIVATE, 'Content-Type': 'application/json; charset=utf-8', 'Content-Length': body.length, ...extra });
      res.end(req.method === 'HEAD' ? undefined : body);
    };
    if (typeof authenticate !== 'function' || authenticate(req) !== true) { json(401, { ok: false, error: 'authentication_required' }); return true; }
    if (!['GET', 'HEAD'].includes(req.method)) {
      req.resume?.(); json(405, { ok: false, error: 'content_pack_read_only' }, { Allow: 'GET, HEAD' }); return true;
    }
    if (req.headers['transfer-encoding'] || (req.headers['content-length'] && req.headers['content-length'] !== '0')) {
      req.resume?.(); json(400, { ok: false, error: 'unexpected_body' }); return true;
    }
    try {
      unlinkedRoot(root);
      if (pathname === '/content-packs/index.json') { json(200, readPackIndex(root)); return true; }
      const file = resolveServedPackFile(root, pathname.slice('/content-packs/'.length));
      if (!file) { json(404, { ok: false, error: 'content_pack_not_found' }); return true; }
      const before = fs.statSync(file);
      const fd = fs.openSync(file, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
      const opened = fs.fstatSync(fd);
      if (!opened.isFile() || before.dev !== opened.dev || before.ino !== opened.ino || before.size !== opened.size) {
        fs.closeSync(fd); throw new Error('content_pack_changed');
      }
      if (authenticate(req) !== true || req.aborted || res.destroyed) { fs.closeSync(fd); json(401, { ok: false, error: 'authentication_required' }); return true; }
      const metadata = path.extname(file).toLowerCase() === '.json';
      res.writeHead(200, { ...PRIVATE, 'Content-Type': TYPES[path.extname(file).toLowerCase()] || 'application/octet-stream',
        'Content-Length': opened.size, ...(!metadata ? { 'Cache-Control': 'private, max-age=31536000, immutable' } : {}) });
      if (req.method === 'HEAD') { fs.closeSync(fd); res.end(); return true; }
      const stream = fs.createReadStream(file, { fd, autoClose: true });
      stream.on('error', () => res.destroy());
      res.once('close', () => stream.destroy());
      stream.pipe(res);
    } catch {
      if (!res.headersSent) json(503, { ok: false, error: 'content_pack_unavailable' });
      else res.destroy();
    }
    return true;
  };
}

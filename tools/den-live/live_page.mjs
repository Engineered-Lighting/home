// Step 3's local page: Home on your scan, with the live people layer drawing den-live's people stream.
//
//   node tools\den-live\live_page.mjs --app <Home checkout holding the people layer> [--port 18190] [--stream-port 8111]
//                                     [--room real|authored] [--tmp <folder>]
//
// --app is the claude/den-live-home worktree: its app/src holds home-3d/apartment-people-layer.mjs, the module step 4
// releases into your deployed Home. This page serves that Home unchanged, through home_harness.mjs (127.0.0.1 only, its
// own Host only, GET only), and adds two things:
// - /api/den-live/v1/homes/den/{stream,state,body}, passed to the loop's stream server on 127.0.0.1:<stream-port>. They're
//   the same paths step 4's gateway route serves, so the page's code doesn't change between the steps.
// - /api/den-live/v1/homes/den/labels, GET and PUT (a JSON body of at most 64 KB), passed to the same server, and
//   window.HomeDenLabels naming it, so Home's Labels tool (home-den-labels-view.mjs) reads and saves the den's labels.
//   --app defaults to the claude/den-labels-home worktree when it holds that tool.
// - its panel, /__live/panel.mjs, which mounts the layer on Home's engine and feeds it the stream.
// It prints `ready <url>` once it listens, and nothing with a coordinate in it.
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { HOST, createHarness, fail, loadHome, option, packRoot, send } from './home_harness.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url)), ROOT = path.resolve(HERE, '../..');
const LABELS_APP = path.join(ROOT, '..', 'den-labels-home');
const APP = path.resolve(option('app', fs.existsSync(path.join(LABELS_APP, 'app', 'src', 'home-den-labels-view.mjs'))
  ? LABELS_APP : path.join(ROOT, '..', 'den-live-home')));
const LABELS_MAX_BYTES = 65536;
const PORT = Number(option('port', '18190')), STREAM_PORT = Number(option('stream-port', '8111')), ROOM = option('room', 'real');
const STREAM = '/api/den-live/v1/homes/den/';
if (!['real', 'authored'].includes(ROOM)) fail('--room is real or authored');
if (!fs.existsSync(path.join(APP, 'app', 'src', 'home-3d', 'apartment-people-layer.mjs'))) {
  fail('--app must be a Home checkout holding the people layer (the claude/den-live-home worktree)');
}
const home = await loadHome(APP);
const PACKS = packRoot(home, { room: ROOM, packs: option('packs'), tmp: option('tmp'), prefix: 'live-page-' });

/* The loop's stream, passed through as it comes: the page reaches it on its own origin. */
function pass(req, res, pathname) {
  const body = pathname === STREAM + 'body';
  const upstream = http.request({ host: HOST, port: STREAM_PORT, path: body ? `${pathname}-v2` : pathname, method: 'GET',
    headers: { Host: `${HOST}:${STREAM_PORT}`, Accept: req.headers.accept || '*/*' } }, up => {
    const mime = up.headers['content-type']?.split(';', 1)[0].trim().toLowerCase();
    const schema = up.headers['x-home-den-body-schema'];
    if (body && (up.statusCode !== 200 || mime !== 'text/event-stream' || schema !== 'den-live-body-2')) {
      up.destroy();
      return send(res, 503, { ok: false, error: 'body_unavailable' });
    }
    res.writeHead(up.statusCode, { 'Content-Type': up.headers['content-type'] || 'application/json', 'Cache-Control': 'no-store',
      ...(body ? { 'X-Home-Den-Body-Schema': schema } : {}) });
    res.flushHeaders();
    up.pipe(res);
  });
  upstream.on('error', () => { if (!res.headersSent) send(res, 503, { ok: false, error: 'stream_stopped' }); else res.destroy(); });
  res.on('close', () => upstream.destroy());
  upstream.end();
}
/* The den's labels, GET and PUT, to the same server. A save's body is read here first (at most 64 KB) so it goes on with
 * its real length and type only. Nothing answering there is a 503 the Labels tool reads as "den-live isn't running". */
function labels(req, res, pathname) {
  const forward = body => {
    const headers = { Host: `${HOST}:${STREAM_PORT}`, Accept: 'application/json' };
    if (body) Object.assign(headers, { 'Content-Type': 'application/json', 'Content-Length': body.length });
    const upstream = http.request({ host: HOST, port: STREAM_PORT, path: pathname, method: req.method, headers }, up => {
      res.writeHead(up.statusCode, { 'Content-Type': up.headers['content-type'] || 'application/json', 'Cache-Control': 'no-store' });
      up.pipe(res);
    });
    upstream.on('error', () => { if (!res.headersSent) send(res, 503, { ok: false, error: 'labels_unavailable' }); else res.destroy(); });
    upstream.end(body || undefined);
  };
  if (req.method === 'GET') return forward(null);
  if (!/^application\/json(?:\s*;|$)/i.test(String(req.headers['content-type'] || ''))) return send(res, 415, { ok: false, error: 'content_type' });
  const chunks = []; let size = 0, over = false;
  req.on('data', chunk => { size += chunk.length; if (size > LABELS_MAX_BYTES) over = true; else chunks.push(chunk); });
  req.on('end', () => (over ? send(res, 413, { ok: false, error: 'too_large' }) : forward(Buffer.concat(chunks))));
  return undefined;
}
async function routes(req, res, url) {
  if (req.method === 'GET' && ['stream', 'state', 'body'].some(name => url.pathname === STREAM + name)) {
    pass(req, res, url.pathname);
    return true;
  }
  if ((req.method === 'GET' || req.method === 'PUT') && url.pathname === STREAM + 'labels' && !url.search) {
    labels(req, res, url.pathname);
    return true;
  }
  return false;
}

const PAGE_INIT = `<script>window.HG_DISABLE_SERVICE_WORKER=true;window.__denLive={stream:${JSON.stringify(STREAM + 'stream')},site:${JSON.stringify(home.site)}};`
  + (ROOM === 'real' ? `window.HomeDenLive=Object.freeze({...window.__denLive,home:'den',body:${JSON.stringify(STREAM + 'body')},bodySchema:'den-live-body-2'});` : '')
  + `window.HomeDenLabels=Object.freeze({site:${JSON.stringify(home.site)},labels:${JSON.stringify(STREAM + 'labels')}});`
  + `try{localStorage.setItem('apartment3d.site',${JSON.stringify(home.site)});localStorage.setItem('home.perf.backgroundWarmup','off');localStorage.setItem('apartment3d.prewarm','off');}catch(_){}</script>`;
// --labels-only (run_local.py's): no camera loop runs, so the markers' panel would only say the stream stopped.
const LABELS_ONLY = process.argv.includes('--labels-only');
const made = createHarness({ home, packs: PACKS, port: PORT, pageInit: PAGE_INIT,
  panel: path.join(HERE, LABELS_ONLY ? 'labels_only_panel.mjs' : 'live_page_panel.mjs'),
  panelRoute: '/__live/panel.mjs', panelModule: true, authoredRoom: ROOM === 'authored', routes });
if (made.error) fail(`no active pack for the den in ${ROOM === 'real' ? 'your content packs' : 'the authored room'} (${made.error})`);
const { server, origin } = made;
server.on('error', error => fail(error.code === 'EADDRINUSE' ? `port ${PORT} is busy; is another live page still open?` : error.message));
server.listen(PORT, HOST, () => console.log(`ready ${origin}/`));
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => { server.closeAllConnections(); server.close(() => process.exit(0)); });

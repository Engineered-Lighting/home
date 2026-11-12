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
// - its panel, /__live/panel.mjs, which mounts the layer on Home's engine and feeds it the stream.
// It prints `ready <url>` once it listens, and nothing with a coordinate in it.
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { HOST, createHarness, fail, loadHome, option, packRoot, send } from './home_harness.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url)), ROOT = path.resolve(HERE, '../..');
const APP = path.resolve(option('app', path.join(ROOT, '..', 'den-live-home')));
const PORT = Number(option('port', '18190')), STREAM_PORT = Number(option('stream-port', '8111')), ROOM = option('room', 'real');
const STREAM = '/api/den-live/v1/homes/den/';
if (!['real', 'authored'].includes(ROOM)) fail('--room is real or authored');
if (!fs.existsSync(path.join(APP, 'app', 'src', 'home-3d', 'apartment-people-layer.mjs'))) {
  fail('--app must be a Home checkout holding the people layer (the claude/den-live-home worktree)');
}
const home = await loadHome(APP);
const PACKS = packRoot(home, { room: ROOM, refsRoot: ROOT, packs: option('packs'), tmp: option('tmp'), prefix: 'live-page-' });

/* The loop's stream, passed through as it comes: the page reaches it on its own origin. */
function pass(req, res, pathname) {
  const upstream = http.request({ host: HOST, port: STREAM_PORT, path: pathname, method: 'GET',
    headers: { Host: `${HOST}:${STREAM_PORT}`, Accept: req.headers.accept || '*/*' } }, up => {
    res.writeHead(up.statusCode, { 'Content-Type': up.headers['content-type'] || 'application/json', 'Cache-Control': 'no-store' });
    res.flushHeaders();
    up.pipe(res);
  });
  upstream.on('error', () => { if (!res.headersSent) send(res, 503, { ok: false, error: 'stream_stopped' }); else res.destroy(); });
  res.on('close', () => upstream.destroy());
  upstream.end();
}
async function routes(req, res, url) {
  if (req.method === 'GET' && ['stream', 'state', 'body'].some(name => url.pathname === STREAM + name)) {
    pass(req, res, url.pathname);
    return true;
  }
  return false;
}

const PAGE_INIT = `<script>window.HG_DISABLE_SERVICE_WORKER=true;window.__denLive={stream:${JSON.stringify(STREAM + 'stream')},body:${JSON.stringify(STREAM + 'body')},site:${JSON.stringify(home.site)}};`
  + `try{localStorage.setItem('apartment3d.site',${JSON.stringify(home.site)});localStorage.setItem('home.perf.backgroundWarmup','off');localStorage.setItem('apartment3d.prewarm','off');}catch(_){}</script>`;
const made = createHarness({ home, packs: PACKS, port: PORT, pageInit: PAGE_INIT, panel: path.join(HERE, 'live_page_panel.mjs'),
  panelRoute: '/__live/panel.mjs', panelModule: true, authoredRoom: ROOM === 'authored', routes });
if (made.error) fail(`no active pack for the den in ${ROOM === 'real' ? 'your content packs' : 'the authored room'} (${made.error})`);
const { server, origin } = made;
server.on('error', error => fail(error.code === 'EADDRINUSE' ? `port ${PORT} is busy; is another live page still open?` : error.message));
server.listen(PORT, HOST, () => console.log(`ready ${origin}/`));
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => { server.closeAllConnections(); server.close(() => process.exit(0)); });

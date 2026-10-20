// Browser observation of one server-configured Victoria Lab. No caller-selected
// origins/credentials, recording/model writes, redirects, or generic proxying.
import https from 'node:https';

export const PERCEPTION_LAB_PREFIX = '/api/perception-lab/v1';
const MAX_JSON = 4 * 1024 * 1024, MAX_GRAPH = 1024 * 1024, MAX_MEDIA = 4 * 1024 * 1024;
const ID = '[A-Za-z0-9][A-Za-z0-9_-]{0,127}', SESSION = '/sessions/' + ID;
const PRIVATE = { 'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
  'Cross-Origin-Resource-Policy': 'same-origin', 'Referrer-Policy': 'no-referrer',
  'X-Home-Lab-Access': 'read-only' };
const numeric = (min, max) => value => /^(0|[1-9][0-9]*)$/.test(value)
  && Number.isSafeInteger(Number(value)) && Number(value) >= min && Number(value) <= max;
const instant = numeric(0, 1800000), generation = numeric(0, Number.MAX_SAFE_INTEGER);
const routes = [
  [/^\/(capabilities|activity-loop|sessions)$/, {}],
  [/^\/cameras\/den\/(preview|context|structured-context)$/, {}],
  [/^\/cameras\/den\/video$/, { after_sequence: numeric(0, 999999999999999), generation: v => /^[a-f0-9]{32}$/.test(v) }],
  [new RegExp('^' + SESSION + '$'), { at_ms: instant }],
  [new RegExp('^' + SESSION + '/live$'), {}],
  [new RegExp('^' + SESSION + '/graph$'), { at_ms: instant }, MAX_GRAPH],
  [new RegExp('^' + SESSION + '/events$'), { after_id: generation, limit: numeric(1, 200) }, MAX_GRAPH],
  [new RegExp('^' + SESSION + '/findings$'), {}, MAX_GRAPH],
  [new RegExp('^' + SESSION + '/timeline$'), { from_ms: instant, to_ms: instant }, MAX_GRAPH],
  [new RegExp('^' + SESSION + '/replay$'), { at_ms: instant }, MAX_GRAPH, ['at_ms']],
  [new RegExp('^' + SESSION + '/media$'), { cursor: generation, limit: numeric(1, 200),
    kind: v => ['video', 'depth', 'image'].includes(v), at_ms: instant, encoded_at_ms: instant, radius_ms: numeric(0, 120000) }],
  [new RegExp('^' + SESSION + '/media/' + ID + '/descriptor$'), { generation }, MAX_JSON, ['generation']],
  [new RegExp('^' + SESSION + '/media/' + ID + '$'), { generation }, MAX_MEDIA, ['generation'], true],
];

export function describeLabRead(url, range) {
  if (typeof url !== 'string' || !url.startsWith(PERCEPTION_LAB_PREFIX + '/') || /[%#\\\s]/.test(url)) return null;
  const [pathname, query = '', extra] = url.slice(PERCEPTION_LAB_PREFIX.length).split('?');
  if (extra !== undefined || query.length > 512) return null;
  for (const [pattern, allowed, maximum = MAX_JSON, required = [], binary = false] of routes) {
    if (!pattern.test(pathname)) continue;
    const params = new URLSearchParams(query), pairs = [...params];
    if (pairs.length !== new Set(pairs.map(([key]) => key)).size
      || required.some(key => !params.has(key))
      || pairs.some(([key, value]) => !Object.hasOwn(allowed, key) || !allowed[key](value))) return null;
    if (params.has('at_ms') && params.has('encoded_at_ms')) return null;
    if (params.has('from_ms') && params.has('to_ms') && Number(params.get('from_ms')) > Number(params.get('to_ms'))) return null;
    if (params.has('encoded_at_ms') && params.get('kind') !== 'video') return null;
    let span = null;
    if (binary) {
      const match = /^bytes=(0|[1-9][0-9]*)-(0|[1-9][0-9]*)$/.exec(String(range || ''));
      if (!match) return null;
      const start = Number(match[1]), end = Number(match[2]);
      if (![start, end].every(Number.isSafeInteger) || start > end || end - start + 1 > MAX_MEDIA) return null;
      span = { start, end };
    } else if (range !== undefined) return null;
    return { path: PERCEPTION_LAB_PREFIX + pathname + (query ? '?' + query : ''), maximum, binary, span };
  }
  return null;
}

function json(res, status, code) {
  if (res.destroyed || res.writableEnded) return;
  const bytes = Buffer.from(JSON.stringify({ error: { code } }));
  res.writeHead(status, { ...PRIVATE, 'Content-Type': 'application/json; charset=utf-8', 'Content-Length': bytes.length });
  res.end(bytes);
}

function parseOrigin(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || !url.hostname || url.username || url.password || url.search || url.hash || url.pathname !== '/') return null;
    return url;
  } catch { return null; }
}

/** Called only after the gateway's existing Host/Origin policy. authenticate
 * must validate the current Home session on each invocation, including after
 * upstream completion. credential and CA are server-side configuration only.
 * The upstream credential must be provisioned with read capability alone. */
export function createPerceptionLabReadHandler({ origin, ca, credential, authenticate,
  request = https.request, timeoutMs = 5000, maxConcurrent = 4 } = {}) {
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 5000
    || !Number.isInteger(maxConcurrent) || maxConcurrent < 1 || maxConcurrent > 4) throw new Error('lab_read_limits_invalid');
  const upstream = parseOrigin(origin);
  const configured = upstream && (typeof ca === 'string' || Buffer.isBuffer(ca)) && ca.length > 0 && ca.length <= 65536
    && typeof credential === 'function';
  const authorized = req => typeof authenticate === 'function' && authenticate(req) === true;
  let active = 0;
  return function handlePerceptionLabRead(req, res) {
    const url = String(req.url || '');
    if (url !== PERCEPTION_LAB_PREFIX && !url.startsWith(PERCEPTION_LAB_PREFIX + '/')) return false;
    if (!authorized(req)) { json(res, 401, 'lab_session_required'); return true; }
    if (req.headers?.['x-home-lab-site'] !== 'victoria') { json(res, 403, 'lab_site_mismatch'); return true; }
    if (req.method !== 'GET') { req.resume?.(); json(res, 405, 'lab_read_only'); return true; }
    if (req.headers?.['transfer-encoding'] || req.headers?.['content-length'] && req.headers['content-length'] !== '0') {
      req.resume?.(); json(res, 400, 'lab_unexpected_body'); return true;
    }
    const route = describeLabRead(url, req.headers?.range);
    if (!route) { json(res, 404, 'lab_read_route_denied'); return true; }
    if (!configured) { json(res, 503, 'lab_read_unconfigured'); return true; }
    if (active >= maxConcurrent) { json(res, 429, 'lab_read_busy'); return true; }
    let token;
    try { token = credential(); } catch { json(res, 503, 'lab_credential_unavailable'); return true; }
    if (typeof token !== 'string' || !/^[A-Za-z0-9._~+/=-]{32,512}$/.test(token)) {
      json(res, 503, 'lab_credential_unavailable'); return true;
    }
    active++;
    let upstreamRequest = null, incoming = null, done = false;
    const finish = (status, code, body, headers) => {
      if (done) return;
      done = true; active--; clearTimeout(timer);
      req.off?.('aborted', abort); res.off?.('close', abort);
      upstreamRequest?.destroy(); incoming?.destroy();
      if (req.aborted || res.destroyed || res.writableEnded) return;
      if (!authorized(req)) { json(res, 401, 'lab_session_required'); return; }
      if (!body) { json(res, status, code); return; }
      res.writeHead(status, { ...PRIVATE, ...headers, 'Content-Length': body.length }); res.end(body);
    };
    const abort = () => finish(499, 'lab_read_cancelled');
    const timer = setTimeout(() => finish(504, 'lab_read_timeout'), timeoutMs); timer.unref?.();
    req.once?.('aborted', abort); res.once?.('close', abort);
    try {
      upstreamRequest = request({ protocol: 'https:', hostname: upstream.hostname, port: upstream.port || 443,
        method: 'GET', path: route.path, ca, rejectUnauthorized: true, agent: false,
        headers: { Authorization: 'Bearer ' + token, 'X-Home-Lab-Site': 'victoria',
          Accept: route.binary ? 'application/octet-stream' : 'application/json', 'Accept-Encoding': 'identity',
          ...(route.binary ? { Range: req.headers.range } : {}) } }, response => {
        incoming = response;
        if (done) { response.destroy(); return; }
        const status = response.statusCode, type = String(response.headers['content-type'] || '').split(';')[0].toLowerCase();
        if (response.headers['content-encoding'] && response.headers['content-encoding'] !== 'identity'
          || status >= 300 && status < 400) { finish(502, 'lab_upstream_invalid_response'); return; }
        if (![200, 206, 400, 401, 403, 404, 409, 410, 412, 416, 422, 429, 503].includes(status)) {
          finish(502, 'lab_upstream_invalid_response'); return;
        }
        if (status === 401 || status === 403) { finish(503, 'lab_upstream_authorization_unavailable'); return; }
        const success = status === 200 || status === 206;
        const binary = route.binary && success;
        if (binary ? status !== 206 || !['image/jpeg', 'image/png', 'video/mp4', 'application/octet-stream'].includes(type)
          : type !== 'application/json' || status === 206) { finish(502, 'lab_upstream_invalid_response'); return; }
        const declared = response.headers['content-length'];
        if (declared !== undefined && (!/^(0|[1-9][0-9]*)$/.test(String(declared)) || Number(declared) > route.maximum)) {
          finish(502, 'lab_upstream_response_too_large'); return;
        }
        let range = null;
        if (binary) {
          range = /^bytes (0|[1-9][0-9]*)-(0|[1-9][0-9]*)\/(0|[1-9][0-9]*)$/.exec(String(response.headers['content-range'] || ''));
          if (!range || !range.slice(1).map(Number).every(Number.isSafeInteger) || Number(range[1]) !== route.span.start
            || Number(range[2]) < route.span.start || Number(range[2]) > route.span.end || Number(range[3]) <= Number(range[2])) {
            finish(502, 'lab_upstream_range_invalid'); return;
          }
        }
        const chunks = []; let bytes = 0;
        response.on('data', chunk => {
          if (done) return;
          bytes += chunk.length;
          if (bytes > route.maximum) { finish(502, 'lab_upstream_response_too_large'); return; }
          chunks.push(chunk);
        });
        response.once('aborted', () => finish(502, 'lab_upstream_interrupted'));
        response.once('error', () => finish(502, 'lab_upstream_unavailable'));
        response.once('end', () => {
          if (done) return;
          if (declared !== undefined && bytes !== Number(declared) || range && bytes !== Number(range[2]) - Number(range[1]) + 1) {
            finish(502, 'lab_upstream_interrupted'); return;
          }
          const body = Buffer.concat(chunks, bytes);
          if (!binary) {
            try { const value = JSON.parse(body.toString('utf8')); if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error(); }
            catch { finish(502, 'lab_upstream_invalid_response'); return; }
          }
          finish(status, null, body, { 'Content-Type': type,
            ...(range ? { 'Content-Range': response.headers['content-range'], 'Accept-Ranges': 'bytes' } : {}) });
        });
      });
      upstreamRequest.once('error', () => finish(502, 'lab_upstream_unavailable'));
      upstreamRequest.end();
    } catch { finish(502, 'lab_upstream_unavailable'); }
    return true;
  };
}

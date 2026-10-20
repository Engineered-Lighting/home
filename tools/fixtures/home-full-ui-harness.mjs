// The production Home shell and lazy components, using authored HTTP/HA
// responses only. No household assets, credentials or external services.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createHash } from 'node:crypto';
import { runM2Shell } from './apartment-m2-shell-harness.mjs';

export const OWNER = '0190a0b0-0000-7000-8000-000000000001';
export const FRIEND = '0190a0b0-0000-7000-8000-000000000002';
const PACKET = Object.freeze({ id: 'authored-full-ui-packet', event_ts: '2026-10-19T12:00:00Z',
  zone: 'authored_study', frame_count: 0, trigger: 'authored_event', qwen_status: 'valid',
  latest_labels: { primary_activity: { label: 'reading', confidence: .92 },
    privacy_sensitivity: { label: 'normal', confidence: .99 }, image_quality: { label: 'good', confidence: .99 } } });
const SESSION = Object.freeze({ id: 'authored-full-ui-session', name: 'Authored reading session',
  site_id: 'victoria', camera_id: 'authored-camera', experiment_id: 'detection', state: 'stopped',
  started_at: 1792411200, stopped_at: 1792411260, duration_s: 60, bookmark_count: 2, generation: 1 });

export async function runFullUiShell(name, steps) {
  return runM2Shell(name, async shell => {
    const { page, origin, receipt, checks } = shell, context = page.context();
    const state = { people: 'populated', intelligence: 'populated', metrics: 'populated', lab: 'populated', detailError: false };
    const calls = [], writes = [], sockets = [];
    const sourceFiles = ['home-app.jsx', 'home-overlay.js', 'home-lights.jsx', 'home-video-labeler.jsx', 'home-people.jsx', 'home-intelligence.jsx', 'home-perception-lab.jsx',
      'home-perception-lab-data.js', 'home-perception-lab-transport.js', 'home-tokens.css', 'simulation.jsx',
      'home-video-labeler-data.js', 'home-video-labeler-timeline.jsx',
      'home-apartment-camera.jsx', 'home-apartment-camera-source.js', 'home-apartment-den-perception.jsx',
      'home-apartment-perception-source.js', 'home-apartment-perception-player.js', 'home-apartment-perception-view.js',
      'home-apartment-perception-dock-model.js', 'home-den-lab-stream.js', 'home-lighting-brain-client.js', 'home-lighting-brain-follower.js'];
    const sourceHashes = () => Object.fromEntries(sourceFiles.map(name => [name,
      createHash('sha256').update(fs.readFileSync(new URL('../../app/src/' + name, import.meta.url))).digest('hex')]));
    receipt.full_ui = { authored_services: true, source_components_replaced: false, source_sha256: sourceHashes(), calls, writes, sockets };
    await context.addInitScript(({ origin }) => {
      window.HG_WEB_MODE = true;
      const defaults = { HA: 'ha', METRICS: 'metrics', INTELLIGENCE: 'intelligence', VISION: 'vision',
        SUPERVISOR: 'supervisor', S2S: 's2s', TRACKER: 'tracker', VIDEO_LABELER: 'video-labeler', FRIGATE: 'frigate', VLLM: 'vllm' };
      for (const [key, value] of Object.entries(defaults)) window['HG_DEFAULT_' + key + '_BASE'] = '/proxy/' + value;
      window.__HOME_WEB_HA_TOKEN_PROXY = true;
      window.__HOME_WEB_HA_TOKEN = 'authored-proxy-marker';
      localStorage.setItem('hg-prefs', JSON.stringify({ endpoint: '/proxy/ha', metricsBase: '/proxy/metrics', theme: 'dark' }));
      window.HG_DISABLE_SERVICE_WORKER = true;
    }, { origin });

    await context.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url());
      if (url.origin !== origin) return route.fallback(); // Existing harness denies external network.
      const p = url.pathname, method = req.method();
      if (!p.startsWith('/api/') && !p.startsWith('/proxy/')) return route.fallback();
      const family = p.includes('/intelligence/') ? 'intelligence' : p.startsWith('/api/perception-lab/') ? 'lab'
        : p.includes('/identit') || p.includes('/agent_profile') || p.startsWith('/api/agent/') ? 'people'
          : p === '/proxy/metrics/metrics' ? 'metrics' : 'other';
      calls.push({ path: p, method, family, state: state[family] || null });
      const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body),
        ...(p.startsWith('/api/perception-lab/') ? { headers: { 'X-Home-Lab-Access': 'read-only' } } : {}) });
      if (!['GET', 'HEAD'].includes(method)) {
        writes.push({ path: p, method });
        return json({ error: 'authored_read_only_fixture' }, 403);
      }
      if (p.startsWith('/api/apartment/')) return route.fallback();
      if (p === '/api/agent/v1/household') {
        if (state.people === 'error') return json({ error: 'authored_household_unavailable' }, 503);
        return json({ people: state.people === 'empty' ? [] : [
          { person_id: OWNER, display_name: 'QA Owner', is_self: true, pronouns: null },
          { person_id: FRIEND, display_name: 'QA Ada', is_self: false, pronouns: 'she/her' }] });
      }
      if (p === '/api/agent/v1/relationships') return json({ relationships: state.people === 'empty' ? [] : [
        { fact_id: 'authored-friend', subject_person_id: OWNER, object_person_id: FRIEND, predicate: 'friend_of' }] });
      if (p.endsWith('/api/extended_openai_conversation/identities')) return json({ ready: false,
        setup_error: 'legacy_identity_semantics_frozen', legacy_identity_boundary: { read_only: true } });
      if (p.endsWith('/api/extended_openai_conversation/agent_profiles')) return json({ profiles: {}, frigate_faces_available: false });
      if (p.includes('/api/extended_openai_conversation/agent_profile/')) return json({ profile: { display_name: 'QA Ada', notes: '' } });
      if (p.endsWith('/frigate_proxy/faces')) return json({});
      if (p.includes('/frigate_proxy/events')) return json([]);
      if (p === '/proxy/metrics/metrics') return state.metrics === 'error' ? json({ error: 'authored_metrics_unavailable' }, 503)
        : json({ model: 'Authored full UI model', ttft_ms: 147, tps: 62, gpu_util_pct: 34,
          vram_used_gb: 12, vram_total_gb: 24, cpu_pct: 21, ram_used_gb: 8, ram_total_gb: 32, gpu_temp_c: 48 });
      if (p.startsWith('/proxy/intelligence/api/intelligence/')) {
        const routeName = p.slice('/proxy/intelligence/api/intelligence/'.length);
        if (state.intelligence === 'error') return json({ error: 'authored_intelligence_unavailable' }, 503);
        if (routeName === 'observation-packets') return json({ observation_packets: state.intelligence === 'empty' ? [] : [PACKET] });
        if (routeName === 'observation-packets/map') return json({ points: [] });
        if (routeName === 'observation-packets/' + PACKET.id) return state.detailError ? json({ error: 'authored_packet_unavailable' }, 503)
          : json({ packet: PACKET, frames: [], labels: [], evidence: [] });
        if (routeName === 'today') return json({ generated_at: '2026-10-19T12:00:00Z', status: 'fresh_idle' });
        if (routeName === 'jobs') return json({ jobs: [] });
        if (routeName === 'scheduler') return json({ scheduler: null });
        if (routeName === 'candidates') return json({ candidates: [] });
        if (routeName === 'training-clips/manifests') return json({ manifests: [] });
        if (routeName === 'label-evals/queue') return json({ items: [] });
        return json({});
      }
      if (p.startsWith('/api/perception-lab/v1/')) {
        const routeName = p.slice('/api/perception-lab/v1/'.length), site = req.headers()['x-home-lab-site'] || 'victoria';
        if (state.lab === 'error') return json({ error: { code: 'authored_lab_unavailable', message: 'Authored Lab is unavailable' } }, 503);
        if (routeName === 'capabilities') return json({ site_id: site, cameras: [], live_previews: {}, live_videos: {},
          recording_allowed: true, experiments: [], apartment_viewing: { provisioned: false },
          reasoning: { available: false, reason: 'Authored read-only fixture' }, storage: { policy: 'Authored fixture only' } });
        if (routeName === 'sessions') return json({ site_id: site, version: 1, items: state.lab === 'empty' ? [] : [{ ...SESSION, site_id: site }] });
        if (routeName === 'sessions/' + SESSION.id) return json({ session: { ...SESSION, site_id: site },
          annotations: [{ id: 'authored-reading-label', start_s: 0, end_s: 60, activity: 'reading', reviewed: false,
            origin: 'human', note: 'Authored interval', revision: 1, time_basis: 'session' }], events: [], runs: [], frames: {} });
        if (routeName === 'sessions/' + SESSION.id + '/media') return json({ generation: 1, items: [] });
        if (routeName === 'sessions/' + SESSION.id + '/findings') return json({ runs: [] });
        if (routeName === 'recordings') return json({ items: [] });
        return json({ items: [], annotations: [], bookmarks: [] });
      }
      if (p.endsWith('/api/states')) return json([]);
      if (p.endsWith('/api/config')) return json({ unit_system: { length: 'm' }, components: [] });
      if (p.endsWith('/api/extended_openai_conversation/model') || p.endsWith('/api/extended_openai_conversation/apartment_model')) return route.fallback();
      if (p.endsWith('/healthz') || p.endsWith('/health')) return json({ status: 'ok', service: 'authored-full-ui', online: true });
      if (p.endsWith('/stream') || p.endsWith('/events') || p.endsWith('/rooms')) return json({ rooms: [], events: [] });
      return route.fallback();
    });
    await context.routeWebSocket('**/*', socket => {
      const url = new URL(socket.url());
      if (url.origin.replace(/^ws/, 'http') !== origin || url.pathname !== '/proxy/ha/api/websocket') return socket.close();
      socket.onMessage(bytes => {
        const msg = JSON.parse(String(bytes));
        sockets.push({ type: msg.type });
        if (msg.type === 'auth') return socket.send(JSON.stringify({ type: 'auth_ok', ha_version: 'authored' }));
        if (msg.type === 'ping') return socket.send(JSON.stringify({ type: 'pong', id: msg.id }));
        const allowed = ['get_states', 'get_config', 'subscribe_events', 'unsubscribe_events',
          'config/entity_registry/list', 'config/device_registry/list', 'config/area_registry/list', 'conversation/agent/list'];
        if (!allowed.includes(msg.type)) {
          writes.push({ path: url.pathname, method: 'WEBSOCKET', type: msg.type });
          return socket.send(JSON.stringify({ type: 'result', id: msg.id, success: false, error: { code: 'authored_read_only_fixture' } }));
        }
        const result = msg.type === 'get_config' ? { unit_system: { length: 'm' }, components: [] } : [];
        socket.send(JSON.stringify({ type: 'result', id: msg.id, success: true, result }));
      });
      socket.send(JSON.stringify({ type: 'auth_required', ha_version: 'authored' }));
    });
    const waitText = async (locator, pattern) => {
      await locator.filter({ hasText: pattern }).first().waitFor({ timeout: 15000 });
    };
    const viewports = [{ width: 1366, height: 768 }, { width: 390, height: 844 }];
    const inViewport = async locator => {
      const box = await locator.boundingBox(), viewport = page.viewportSize();
      assert.ok(box && box.width > 0 && box.height > 0, 'action is rendered');
      assert.ok(box.x >= -1 && box.y >= -1 && box.x + box.width <= viewport.width + 1 && box.y + box.height <= viewport.height + 1,
        'primary action fits viewport: ' + JSON.stringify({ box, viewport }));
    };
    try { await steps({ ...shell, state, calls, writes, sockets, waitText, viewports, inViewport }); }
    catch (error) {
      await page.screenshot({ path: receipt.run + '/full-ui-failure.png' }).catch(() => {});
      receipt.full_ui.failure_dom = await page.locator('body').innerText().catch(() => 'page unavailable');
      receipt.full_ui.runtime = await page.evaluate(() => ({ web: window.HG_WEB_MODE, ha: window.HG_DEFAULT_HA_BASE,
        configured_ha: window.HomeServices?.get('ha'), ha_client_url: window.__hav_haClient?.url })).catch(() => null);
      receipt.full_ui.layout = await page.evaluate(() => [...document.querySelectorAll('[aria-label="Intelligence Atlas"], [aria-label="People — relationships and identities"], section[aria-label="Perception Lab"], .apt-home-stage')]
        .map(node => { const b = node.getBoundingClientRect(), s = getComputedStyle(node); return { label: node.getAttribute('aria-label') || node.className,
          box: { x: b.x, y: b.y, width: b.width, height: b.height }, position: s.position, zIndex: s.zIndex, overflow: s.overflow,
          actions: [...node.querySelectorAll('button')].slice(0, 8).map(button => { const r = button.getBoundingClientRect(); return {
            name: button.getAttribute('aria-label') || button.textContent, box: { x: r.x, y: r.y, width: r.width, height: r.height },
            hit: document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)?.outerHTML.slice(0, 200) }; }) }; })).catch(() => null);
      throw error;
    }
    assert.deepEqual(sourceHashes(), receipt.full_ui.source_sha256, 'production source is stable for the entire mounted proof');
    assert.deepEqual(writes, [], 'the default read-only fixture transport receives no writes; dedicated fixture writes are recorded separately');
    checks.push({ stage: 'all_default_service_requests_authored_and_no_writes', count: calls.length });
  });
}

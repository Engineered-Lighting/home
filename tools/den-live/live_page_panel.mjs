// The live page's panel (live_page.mjs serves it): it mounts Home's people layer on Home's own engine, feeds it the
// people stream, and says in words what the stream says: the camera, the mode, how many people, how far behind.
// It reaches nothing but this page's own origin. Step 4 moves the mounting into Home itself; the layer stays as it is.
// Current Home mounts the figure and markers through its regular live view.
import { attachApartmentPeopleLayer } from '/home-3d/apartment-people-layer.mjs';

const { stream, site } = window.__denLive || {};
const style = document.createElement('style');
style.textContent = `
  #den-live { position: fixed; top: 12px; right: 12px; width: 260px; z-index: 2147483000; background: rgba(18, 20, 24, .92);
    color: #e8e8e8; font: 13px/1.45 system-ui, sans-serif; border-radius: 8px; padding: 10px 12px; box-shadow: 0 6px 24px rgba(0,0,0,.4); }
  #den-live h2 { font-size: 13px; margin: 0 0 6px; }
  #den-live .banner { margin: 6px 0; padding: 4px 8px; border-radius: 4px; background: #7a4a12; color: #fff; }
  #den-live .banner:empty { display: none; }
  #den-live dl { display: grid; grid-template-columns: auto 1fr; gap: 2px 10px; margin: 0; }
  #den-live dt { color: #9aa3ad; }
  #den-live dd { margin: 0; }`;
document.head.appendChild(style);
const panel = document.createElement('div');
panel.id = 'den-live';
panel.innerHTML = '<h2>Live den view, on this PC</h2><div class="banner"></div><dl></dl>';
document.body.appendChild(panel);
const banner = panel.querySelector('.banner'), list = panel.querySelector('dl');

let layer = null, engine = null, latest = null, lastAt = 0;
// The markers, read-only, as deployed Home's den view offers them: Home's labels overlay draws each one's near phrase.
const fallback = Object.freeze({ markers: () => (layer ? layer.markers() : []) });
if (!window.HomeDenLiveView) window.HomeDenLiveView = fallback;
function mount() {
  if (window.HomeDenLiveView !== fallback) {
    layer?.dispose();
    layer = null;
    return;
  }
  const current = window.__APT_ENGINE;
  if (!current?.apartmentRoot || current.siteId !== site) return;
  if (current === engine) return;
  layer?.dispose();
  engine = current;
  layer = attachApartmentPeopleLayer(current);
}
setInterval(mount, 500);

// The stream names the scan revision its positions are in; markers are drawn only on that scan.
const otherScan = () => !!latest?.content_revision && !!engine && engine.contentRevision !== latest.content_revision;
const source = new EventSource(stream);
source.addEventListener('people', message => {
  try { latest = JSON.parse(message.data); } catch (_) { return; }
  lastAt = performance.now();
  mount();
  if (!otherScan()) layer?.update(latest);
});

const words = value => value == null ? '-' : String(value);
function render() {
  const stopped = !latest || performance.now() - lastAt > 3000;
  const camera = latest?.camera || {};
  banner.textContent = stopped ? 'Stream stopped: is run_local.py still running?'
    : otherScan() ? 'This page shows another scan revision than the camera is registered to: no markers'
    : camera.status !== 'ok' ? `Camera ${camera.status}${camera.why ? `: ${camera.why}` : ''}`
      : camera.why ? camera.why[0].toUpperCase() + camera.why.slice(1)
        : latest.mode === 'infrared' ? 'Infrared' : '';
  const health = latest?.health || {};
  const rows = [['Camera', stopped ? '-' : camera.status], ['People', stopped ? '-' : words(latest.tracks.length)],
    ['Behind', health.lag_s_median == null ? '-' : `${health.lag_s_median.toFixed(2)} s`],
    ['Frames', health.fps_in == null ? '-' : `${health.fps_in.toFixed(1)} a second`],
    ['Dropped', words(health.dropped_per_min) + ' a minute'], ['Markers', words(window.HomeDenLiveView?.markers().length)],
    ['Body', words(window.HomeDenLiveView?.status?.().body?.layer?.points)]];
  list.replaceChildren(...rows.flatMap(([name, value]) => {
    const dt = document.createElement('dt'), dd = document.createElement('dd');
    dt.textContent = name; dd.textContent = value;
    return [dt, dd];
  }));
}
setInterval(render, 500);
render();

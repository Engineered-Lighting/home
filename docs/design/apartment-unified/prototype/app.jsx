/* Apartment unified prototype — state, scene wiring, keyboard, responsive sheet. */
const { useState, useEffect, useRef, useMemo, useCallback } = React;
const SITES = window.APT_DATA.sites; const HOMES = [SITES.echo, SITES.victoria];
const clone = (o) => JSON.parse(JSON.stringify(o));
const STORE = "apt-unified-proto-v1";

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "dark",
  "echoScenario": "connected",
  "victoriaScenario": "current"
}/*EDITMODE-END*/;

const camRec = (c) => { let yaw = c.yaw, pitch = c.pitch; if (c.look) { const d = [c.look[0] - c.pos[0], c.look[1] - c.pos[1], c.look[2] - c.pos[2]]; yaw = Math.atan2(d[1], d[0]); pitch = Math.atan2(d[2], Math.hypot(d[0], d[1])); }
  return { id: c.id, kind: "camera", name: c.name, pos: c.pos.slice(), yaw, pitch, vfov: c.vfov, hfov: Math.round((2 * Math.atan(Math.tan((c.vfov * Math.PI) / 360) * 16 / 9) * 180) / Math.PI), range: 5, mount: "wall", status: c.standIn ? "authored" : "approximate" }; };
function initHome(site) {
  const fixtures = {};
  site.fixtures.forEach((f) => {
    if (f.kind === "cylinder") fixtures[f.id] = { channels: clone(f.channels), aim: clone(f.aim) };
    else if (f.kind === "ceiling") fixtures[f.id] = { ...f.state, manual: f.id === "office" ? "at 20:41" : null, ...(f.engineered ? { channels: clone(f.channels), aim: clone(f.aim) } : {}) };
    else fixtures[f.id] = { ...f.state };
  });
  return { fixtures, proposed: {}, history: {}, targets: site.targets.map((t) => ({ ...t, state: t.id === "aim-painting" ? "changed" : "saved" })),
    cams: site.cameras.map(camRec), selected: null, tab: "light", camId: site.cameras[0].id, cameraView: null, trace: null, dirty: false, mode: "cloud" };
}
const Q = new URLSearchParams(location.search);
const NAMES_KEY = "apt-unified.names";
Object.values(SITES).forEach((s) => s.fixtures.forEach((f) => { if (!f.orig) f.orig = { name: f.name, short: f.short }; }));
try { const N = JSON.parse(localStorage.getItem(NAMES_KEY) || "{}"); Object.entries(N).forEach(([sid, m]) => { const s = SITES[sid]; if (s) s.fixtures.forEach((f) => { if (m[f.id]) Object.assign(f, m[f.id]); }); }); } catch (e) { /* */ }
const loadPrefs = () => { let p = {}; try { p = JSON.parse(localStorage.getItem(STORE) || "{}"); } catch (e) { /* */ }
  if (Q.get("home")) p.siteId = Q.get("home"); if (Q.get("dock")) p.dock = Q.get("dock"); if (Q.get("detail")) p.detail = Q.get("detail"); return p; };

function App() {
  const prefs = useMemo(loadPrefs, []);
  const [tw, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const t = { ...tw, theme: Q.get("theme") || tw.theme, echoScenario: Q.get("echo") || tw.echoScenario, victoriaScenario: Q.get("vic") || tw.victoriaScenario };
  const [siteId, setSiteId] = useState(prefs.siteId || "victoria");
  const [homes, setHomes] = useState(() => { const h = { echo: initHome(SITES.echo), victoria: initHome(SITES.victoria) }; const k = prefs.siteId || "victoria";
    if (Q.get("sel")) h[k] = { ...h[k], selected: { kind: Q.get("sel").startsWith("aim-") || Q.get("sel").startsWith("target-") ? "target" : "fixture", id: Q.get("sel") }, tab: Q.get("tab") || "light" }; return h; });
  const [dock, setDock] = useState(prefs.dock || "default"); const [detail, setDetail] = useState(prefs.detail || "simple");
  const [layers, setLayers] = useState({ pools: true, beams: "selected", targetsAlways: false, labelsAlways: false, cameras: false, bodyObserved: true, bodyInferred: true, bodyVolume: false, bodyUncertainty: false, framePose: true, ...(prefs.layers || {}) });
  const [setup, setSetup] = useState(false); const [tdraft, setTdraft] = useState(null); const [ddraft, setDdraft] = useState(null); const [setupTool, setSetupTool] = useState("position"); const dockBeforeSetup = useRef(null);
  const [hover, setHover] = useState(null); const [tapAim, setTapAim] = useState(false); const [modal, setModal] = useState(null);
  const [toast, setToast] = useState(null); const [frames, setFrames] = useState({}); const [sceneInfo, setSceneInfo] = useState({}); const [detent, setDetent] = useState(0);
  const [photo, setPhoto] = useState(null); const [vw, setVw] = useState(document.getElementById("root").clientWidth || window.innerWidth); const [vh, setVh] = useState(document.getElementById("root").clientHeight || window.innerHeight);
  const [sheet, setSheet] = useState(Q.get("sheet") || "half"); const [sheetTab, setSheetTab] = useState(Q.get("panel") || "inspect");
  const [colTab, setColTab] = useState(Q.get("col") || "controls"); const [chats, setChats] = useState(() => ({ echo: window.chatSeed(SITES.echo), victoria: window.chatSeed(SITES.victoria) }));
  const hostRef = useRef(null); const sceneRef = useRef(null); const H = useRef({});
  const site = SITES[siteId]; const hs = homes[siteId]; const scen = siteId === "echo" ? t.echoScenario : t.victoriaScenario;
  const narrow = vw < 900; const phone = vw < 600;
  const selFixture = hs.selected && hs.selected.kind === "fixture" ? site.fixtures.find((f) => f.id === hs.selected.id) : null;

  useEffect(() => { document.documentElement.setAttribute("data-theme", t.theme); }, [t.theme]);
  useEffect(() => { try { localStorage.setItem(STORE, JSON.stringify({ siteId, dock, detail, layers })); } catch (e) { /* */ } }, [siteId, dock, detail, layers]);
  useEffect(() => { const root = document.getElementById("root"); const r = () => { setVw(root.clientWidth); setVh(root.clientHeight); }; const ro = new ResizeObserver(r); ro.observe(root); r(); return () => ro.disconnect(); }, []);
  useEffect(() => { if (!toast) return; const id = setTimeout(() => setToast(null), toast.ms || 3600); return () => clearTimeout(id); }, [toast]);

  const upd = useCallback((fn, id) => setHomes((h) => { const k = id || H.current.siteId; return { ...h, [k]: fn(h[k]) }; }), []);
  const say = (text, ms, action) => setToast({ text, ms: ms || (action ? 6000 : undefined), action, n: Math.random() });
  const snap = (h, fid) => ({ aim: h.fixtures[fid] && h.fixtures[fid].aim ? clone(h.fixtures[fid].aim) : null, proposed: h.proposed[fid] ? clone(h.proposed[fid]) : null });
  const pushHist = (h, fid) => { const hist = h.history[fid] || { past: [], future: [] }; return { ...h.history, [fid]: { past: [...hist.past.slice(-30), snap(h, fid)], future: [] } }; };

  const act = {
    setSite: (id) => { if (id === siteId) return; sceneRef.current && sceneRef.current.cancelDrag(); setTapAim(false); setTdraft(null); setDdraft(null); setPhoto(null); setHover(null); setSiteId(id);
      say(`${SITES[id].name}${homes[id].dirty ? " · your unsaved work here is kept" : ""}`); },
    select: (sel, tab) => { setTapAim(false); upd((h) => {
      const f = sel && sel.kind === "fixture" && site.fixtures.find((x) => x.id === sel.id);
      const nextTab = tab || (f ? (h.tab === "aim" && !f.engineered ? "light" : h.selected && h.selected.kind === "fixture" ? h.tab : "light") : "light");
      return { ...h, selected: sel, tab: nextTab, trace: null }; }); },
    hover: setHover,
    setTab: (tab) => upd((h) => ({ ...h, tab })),
    setChannel: (fid, ch, patch) => upd((h) => { const st = h.fixtures[fid]; return { ...h, fixtures: { ...h.fixtures, [fid]: { ...st, channels: { ...st.channels, [ch]: { ...st.channels[ch], ...patch } } } } }; }),
    setLight: (fid, patch) => upd((h) => ({ ...h, fixtures: { ...h.fixtures, [fid]: { ...h.fixtures[fid], ...patch, manual: "just now" } } })),
    returnToAuto: (fid) => { upd((h) => ({ ...h, fixtures: { ...h.fixtures, [fid]: { ...h.fixtures[fid], manual: null } } })); say("Returned to Auto · adaptive lighting resumes · prototype, nothing sent"); },
    propose: (fid, hit, origin) => { if (!hit || !hit.point) return; upd((h) => ({ ...h, history: pushHist(h, fid), proposed: { ...h.proposed, [fid]: { ...hit, origin: origin || "drag" } } })); },
    applyAim: (fid) => { const pr = hs.proposed[fid]; const f = site.fixtures.find((x) => x.id === fid); if (!pr || pr.valid === false) return;
      upd((h) => { const p = { ...h.proposed }; delete p[fid]; return { ...h, didPerception: h.didPerception || pr.origin === "perception", history: pushHist(h, fid), proposed: p, fixtures: { ...h.fixtures, [fid]: { ...h.fixtures[fid], aim: { targetId: pr.snapId || null, point: pr.point, normal: pr.normal, surface: pr.snapName || pr.surface } } } }; });
      say(`${f.short} ${f.kind === "cylinder" ? "modeled" : "simulated"} aim set to ${pr.snapName || pr.surface} · no hardware command sent`); },
    discard: (fid) => upd((h) => { const p = { ...h.proposed }; delete p[fid]; return { ...h, history: pushHist(h, fid), proposed: p }; }),
    undo: (fid) => upd((h) => { const hist = h.history[fid]; if (!hist || !hist.past.length) return h; const prev = hist.past[hist.past.length - 1]; const cur = snap(h, fid); const p = { ...h.proposed }; if (prev.proposed) p[fid] = prev.proposed; else delete p[fid];
      return { ...h, proposed: p, fixtures: { ...h.fixtures, [fid]: { ...h.fixtures[fid], aim: prev.aim } }, history: { ...h.history, [fid]: { past: hist.past.slice(0, -1), future: [cur, ...hist.future] } } }; }),
    redo: (fid) => upd((h) => { const hist = h.history[fid]; if (!hist || !hist.future.length) return h; const nx = hist.future[0]; const cur = snap(h, fid); const p = { ...h.proposed }; if (nx.proposed) p[fid] = nx.proposed; else delete p[fid];
      return { ...h, proposed: p, fixtures: { ...h.fixtures, [fid]: { ...h.fixtures[fid], aim: nx.aim } }, history: { ...h.history, [fid]: { past: [...hist.past, cur], future: hist.future.slice(1) } } }; }),
    recallTarget: (fid, tid) => { const tg = hs.targets.find((x) => x.id === tid); const sc = sceneRef.current; if (!tg || !sc) return; const chk = sc.checkAim(fid, tg.pos);
      upd((h) => ({ ...h, selected: { kind: "fixture", id: fid }, tab: "aim" })); act.propose(fid, { ...chk, point: tg.pos.slice(), normal: tg.normal.slice(), surface: tg.name, snapId: tg.id, snapName: tg.name }, H.current.origin || "recall"); },
    saveTarget: (fid, name) => { const pr = hs.proposed[fid]; if (!pr || !name) return; const id = "aim-" + name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") + "-" + Math.random().toString(36).slice(2, 5);
      const clash = hs.targets.find((x) => x.name.toLowerCase() === name.toLowerCase()); const nt = { id, name, category: "custom", pos: pr.point, normal: pr.normal, size: [0.5, 0.5], source: "aimed", state: "saving" };
      upd((h) => ({ ...h, dirty: true, targets: [...h.targets, nt], proposed: { ...h.proposed, [fid]: { ...h.proposed[fid], snapId: id, snapName: name } } }));
      setTimeout(() => upd((h) => ({ ...h, targets: h.targets.map((x) => x.id !== id ? x : clash ? { ...x, state: "conflict", conflict: `“${name}” already names another aim. Replace it, or keep both?`, clash: clash.id } : { ...x, state: scen === "draft" ? "local" : "saved" }), dirty: !!clash || scen === "draft" }), siteId), 900); },
    resolveTarget: (tid, how) => upd((h) => { const tg = h.targets.find((x) => x.id === tid); let ts = h.targets;
      if (how === "replace") ts = ts.filter((x) => x.id !== tg.clash).map((x) => x.id === tid ? { ...x, state: "saved", conflict: null } : x);
      else ts = ts.map((x) => x.id === tid ? { ...x, name: x.name + " 2", state: "saved", conflict: null } : x); return { ...h, targets: ts, dirty: false }; }),
    confirmTarget: (tid) => upd((h) => ({ ...h, targets: h.targets.map((x) => x.id === tid ? { ...x, state: "saved" } : x) })),
    startTapAim: () => setTapAim(true), cancelTapAim: () => setTapAim(false),
    trace: (k) => { upd((h) => ({ ...h, trace: h.trace === k ? null : k }));
      if (k === "proposed" && site.perception.sample) { const P = site.perception.sample.proposal; H.current.origin = "perception"; act.recallTarget(P.fixtureId, P.targetId); H.current.origin = null; }
      if (k === "did") say("Nothing has been executed. Proposals wait for you, and these fixtures aren't commissioned.");
      if (k === "seen" && site.capabilities.body) setLayers((l) => ({ ...l, bodyObserved: true, bodyInferred: true })); },
    setCamera: (id) => upd((h) => ({ ...h, camId: id })),
    cameraView: (id) => upd((h) => ({ ...h, cameraView: id })), returnToRoom: () => upd((h) => ({ ...h, cameraView: null })),
    setMode: (m) => { if (m === "photo") { setPhoto({ state: "loading", pct: 8 }); return; } setPhoto(null); upd((h) => ({ ...h, mode: m })); },
    retryPhoto: () => setPhoto({ state: "loading", pct: 8 }),
    orbit: (d) => { sceneRef.current && sceneRef.current.orbitStep(d); }, fit: () => sceneRef.current && sceneRef.current.fit(),
    setLayer: (k, v) => setLayers((l) => ({ ...l, [k]: v })), setDock: (d) => setDock(d), setDetail,
    toggleSetup: () => { if (setup) { setTdraft(null); setDdraft(null); } if (!setup) { dockBeforeSetup.current = dock; setDock(dock === "hidden" ? "hidden" : "collapsed"); setTapAim(false); } else if (dockBeforeSetup.current) setDock(dockBeforeSetup.current); setSetup(!setup); },
    newTarget: () => { if (!setup) act.toggleSetup(); setSetupTool("targets"); setTdraft({ mode: "placing", isNew: true }); },
    editTarget: (id) => { const tg = hs.targets.find((x) => x.id === id); if (!tg) return; if (!setup) act.toggleSetup(); setSetupTool("targets");
      setTdraft({ mode: "editing", id: tg.id, name: tg.name, category: tg.category, shape: tg.shape === "point" || !(tg.size && tg.size[0]) ? "point" : "surface", pos: tg.pos.slice(), normal: tg.normal.slice(), size: (tg.size && tg.size[0] ? tg.size : [0.5, 0.5]).slice(), rot: tg.rot || 0, surface: tg.surface || tg.name, horizontal: Math.abs(tg.normal[2]) > 0.8, fit: null }); },
    placeTarget: (hit) => setTdraft((d) => { const horiz = hit.horizontal, nm = (hit.name || "").toLowerCase();
      const cat = d && d.category && !d.isNew ? d.category : horiz ? (hit.kind === "furniture" ? (nm.includes("island") ? "island" : nm.includes("table") || nm.includes("desk") ? "table" : "custom") : "custom") : hit.kind === "art" ? "art" : "custom";
      const preset = { table: [1.2, 0.75], island: [1.2, 0.65], art: [0.9, 0.65], custom: [0.6, 0.6] }[cat];
      let name = d && d.name ? d.name : hit.name || ""; if (name && hs.targets.some((t) => t.name.toLowerCase() === name.toLowerCase() && t.id !== (d && d.id))) name += " 2";
      return { ...(d || {}), isNew: false, mode: "editing", name, category: cat, shape: (d && d.shape) || "surface", pos: hit.fit ? hit.fit.center.slice() : hit.point.map((v) => Math.round(v * 100) / 100), normal: hit.normal,
        size: hit.fit ? hit.fit.size.map((v) => Math.round(v * 100) / 100) : (d && d.size) || preset.slice(), rot: 0, surface: hit.surface, face: hit.face, horizontal: horiz, fit: hit.fit, fitName: hit.name, test: null }; }),
    updateDraft: (patch) => setTdraft((d) => ({ ...d, ...patch })), pickAgain: () => setTdraft((d) => ({ ...d, mode: "placing" })), cancelDraft: () => setTdraft(null),
    testDraft: (fid) => setTdraft((d) => ({ ...d, test: d.test === fid ? null : fid })),
    saveDraft: () => { const d = tdraft; if (!d || !d.pos || !(d.name || "").trim()) return; const nm = d.name.trim();
      const id = d.id || "aim-" + nm.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") + "-" + Math.random().toString(36).slice(2, 5);
      const prev = d.id && hs.targets.find((t) => t.id === d.id);
      const rec = { id, name: nm, category: d.category, shape: d.shape, pos: d.pos, normal: d.normal, size: d.shape === "point" ? [0, 0] : d.size, rot: d.rot || 0, surface: d.surface, source: prev ? prev.source : "mapped", state: "saving" };
      upd((h) => ({ ...h, targets: d.id ? h.targets.map((t) => (t.id === d.id ? rec : t)) : [...h.targets, rec] }));
      setTimeout(() => upd((h) => ({ ...h, targets: h.targets.map((t) => (t.id === id ? { ...t, state: scen === "draft" ? "local" : "saved" } : t)) }), siteId), 800);
      setTdraft(null); say(`“${nm}” ${d.id ? "updated" : "saved"} · ready in every light's Aim tab`); },
    deleteTarget: (id) => { const tg = hs.targets.find((t) => t.id === id);
      upd((h) => ({ ...h, targets: h.targets.filter((t) => t.id !== id), fixtures: Object.fromEntries(Object.entries(h.fixtures).map(([k, v]) => [k, v.aim && v.aim.targetId === id ? { ...v, aim: { ...v.aim, targetId: null } } : v])) }));
      setTdraft(null); say(`“${tg ? tg.name : "Named aim"}” deleted · lights aimed there keep their aim`); },
    applyScene: (sc) => { const before = clone(hs.fixtures), sid = siteId;
      upd((h) => { const fx = { ...h.fixtures }; site.fixtures.forEach((f) => { const st = fx[f.id];
        if (f.kind === "cylinder") { const ch = { ...st.channels }; ["spot", "down", "radial"].forEach((k) => { const p = { ...((sc.all || {})[k] || {}), ...(((sc.per || {})[f.id] || {})[k] || {}) }; if (!Object.keys(p).length) return;
            if (k === "radial") ch.radial = { ...ch.radial, on: p.on, k: p.k || ch.radial.k, levels: p.level != null ? ch.radial.levels.map(() => p.level) : ch.radial.levels }; else ch[k] = { ...ch[k], ...p }; }); fx[f.id] = { ...st, channels: ch }; }
        else { const p = { ...(sc.lights || {}), ...((sc.rooms || {})[f.room] || {}) }; fx[f.id] = f.kind === "plug" ? { ...st, on: p.on } : { ...st, ...p, manual: null }; } });
        return { ...h, fixtures: fx, scene: sc.id }; });
      say(`${sc.name} ${sc.auto ? "started" : "applied"}${sc.note ? " · " + sc.note : ""} · prototype, nothing sent`, 6000, { label: "Undo", fn: () => upd((h) => ({ ...h, fixtures: before, scene: null }), sid) }); },
    setAll: (on, roomId) => { const before = clone(hs.fixtures), sid = siteId;
      upd((h) => { const fx = { ...h.fixtures }; site.fixtures.forEach((f) => { if (roomId && f.room !== roomId) return; const st = fx[f.id];
        if (f.kind === "cylinder") { const ch = { ...st.channels }; ["spot", "down"].forEach((k) => { ch[k] = { ...ch[k], on }; }); ch.radial = { ...ch.radial, on, levels: on && ch.radial.levels.every((v) => !v) ? ch.radial.levels.map(() => 50) : ch.radial.levels }; fx[f.id] = { ...st, channels: ch }; }
        else fx[f.id] = { ...st, on }; }); return { ...h, fixtures: fx, scene: null }; });
      say(`${roomId ? site.rooms.find((r) => r.id === roomId).name : "All lights"} ${on ? "on" : "off"} · prototype, nothing sent`, 6000, { label: "Undo", fn: () => upd((h) => ({ ...h, fixtures: before }), sid) }); },
    setFixtureAll: (fid, on) => upd((h) => { const f = site.fixtures.find((x) => x.id === fid), st = h.fixtures[fid]; const nx = { ...st }; if (f.kind !== "cylinder") nx.on = on;
      if (st.channels) { const ch = { ...st.channels }; ["spot", "down"].forEach((k) => { if (ch[k]) ch[k] = { ...ch[k], on }; }); if (ch.radial) ch.radial = { ...ch.radial, on, levels: on && ch.radial.levels.every((v) => !v) ? ch.radial.levels.map(() => 50) : ch.radial.levels }; nx.channels = ch; }
      return { ...h, scene: null, fixtures: { ...h.fixtures, [fid]: nx } }; }),
    toggleZone: (fid, i, solo) => upd((h) => { const st = h.fixtures[fid], r = st.channels.radial, base = r.on ? r.levels.slice() : r.levels.map(() => 0); const lit = base.filter(Boolean), avg = lit.length ? Math.round(lit.reduce((a, b) => a + b, 0) / lit.length) : 60;
      const levels = solo ? base.map((v, j) => (j === i ? v || avg : 0)) : base.map((v, j) => (j === i ? (v > 0 ? 0 : Math.max(40, avg)) : v));
      return { ...h, scene: null, fixtures: { ...h.fixtures, [fid]: { ...st, channels: { ...st.channels, radial: { ...r, on: levels.some((v) => v > 0), levels } } } } }; }),
    zonesToward: (fid, mode) => { const fc = sceneRef.current ? sceneRef.current._zoneFacingCached(fid) : []; upd((h) => { const st = h.fixtures[fid], r = st.channels.radial, lv = Math.min(100, Math.max(50, ...r.levels));
      const levels = fc.map((z) => { const wall = z.kind === "wall" && z.dist != null && z.dist < 2.2; return (mode === "walls" ? wall : !wall) ? lv : 0; });
      return { ...h, scene: null, fixtures: { ...h.fixtures, [fid]: { ...st, channels: { ...st.channels, radial: { ...r, on: levels.some(Boolean), levels } } } } }; }); },
    focusFixture: (fid) => { const f = site.fixtures.find((x) => x.id === fid); if (f && sceneRef.current) sceneRef.current.focus(f.pos); },
    proposeOn: (fid, pr) => { const chk = sceneRef.current ? sceneRef.current.checkAim(fid, pr.point) : {}; upd((h) => ({ ...h, selected: { kind: "fixture", id: fid }, tab: "aim" })); act.propose(fid, { ...chk, point: pr.point, normal: pr.normal, surface: pr.surface, snapId: pr.snapId, snapName: pr.snapName }, "suggested"); },
    setLink: (fid, entity) => upd((h) => ({ ...h, links: { ...(h.links || {}), [fid]: entity } })),
    flashLight: (fid, entity) => { const f = site.fixtures.find((x) => x.id === fid); act.focusFixture(fid); say(`Flashing ${f ? f.name : "light"} (${entity}) twice · prototype, nothing sent`); },
    renameFixture: (fid, patch) => { const f = site.fixtures.find((x) => x.id === fid); if (!f) return; Object.assign(f, patch);
      try { const N = JSON.parse(localStorage.getItem(NAMES_KEY) || "{}"), m = N[site.id] || (N[site.id] = {}); if (f.name === f.orig.name && f.short === f.orig.short) delete m[fid]; else m[fid] = { name: f.name, short: f.short }; localStorage.setItem(NAMES_KEY, JSON.stringify(N)); } catch (e) { /* */ }
      upd((h) => ({ ...h, namesRev: (h.namesRev || 0) + 1 })); },
    hoverFixture: (fid) => { const sc = sceneRef.current; if (sc && sc._hover) sc._hover(fid ? { kind: "fixture", id: fid } : null); },
    composeZones: (fid, patch) => { if (!patch) return; upd((h) => { const st = h.fixtures[fid], r = st.channels.radial, lv = r.on ? r.levels.slice() : r.levels.map(() => 0);
      Object.entries(patch).forEach(([i, v]) => { lv[+i] = v; }); return { ...h, scene: null, fixtures: { ...h.fixtures, [fid]: { ...st, channels: { ...st.channels, radial: { ...r, on: lv.some((v) => v > 0), levels: lv } } } } }; }); },
    restoreFixtures: (before) => upd((h) => ({ ...h, fixtures: before, scene: null })), notify: (text) => say(text),
    newDevice: (kind) => { if (!setup) act.toggleSetup(); setSetupTool("cameras"); setTdraft(null); const n = hs.cams.filter((c) => c.kind === kind).length + 1;
      setDdraft({ mode: "placing", kind, mount: "wall", name: kind === "camera" ? `Camera ${n}` : `mmWave sensor ${n}`, vfov: kind === "camera" ? 58 : 90, hfov: kind === "camera" ? 92 : 120, range: kind === "camera" ? 5 : 6 }); },
    editDevice: (id) => { const c = hs.cams.find((x) => x.id === id); if (!c) return; if (!setup) act.toggleSetup(); setSetupTool("cameras"); setTdraft(null); setDdraft({ ...c, mode: "editing" }); },
    placeDevice: (hit) => setDdraft((d) => { if (!d) return d; const D = (a, b) => [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
      if (d.mode === "looking") { const v = D(d.pos, hit.point); return { ...d, mode: "editing", yaw: Math.atan2(v[1], v[0]), pitch: Math.atan2(v[2], Math.hypot(v[0], v[1])) }; }
      const inR = (r) => { const xs = r.poly.map((p) => p[0]), ys = r.poly.map((p) => p[1]); return hit.point[0] >= Math.min(...xs) - 0.05 && hit.point[0] <= Math.max(...xs) + 0.05 && hit.point[1] >= Math.min(...ys) - 0.05 && hit.point[1] <= Math.max(...ys) + 0.05; };
      const room = site.rooms.find(inR) || site.rooms[0]; const cx = room.poly.reduce((a, p) => a + p[0], 0) / room.poly.length, cy = room.poly.reduce((a, p) => a + p[1], 0) / room.poly.length; let pos, yaw, pitch;
      if (d.mount === "ceiling") { pos = [hit.point[0], hit.point[1], room.h - 0.04]; yaw = Math.atan2(cy - pos[1], cx - pos[0]); pitch = d.kind === "camera" ? -0.9 : -1.5707; }
      else { const n = hit.normal; pos = [hit.point[0] + n[0] * 0.04, hit.point[1] + n[1] * 0.04, d.mount === "wall" ? Math.max(hit.point[2], 0.3) : hit.point[2] + 0.04]; yaw = Math.abs(n[2]) > 0.8 ? Math.atan2(cy - pos[1], cx - pos[0]) : Math.atan2(n[1], n[0]); pitch = d.kind === "camera" ? -0.5 : -0.35; }
      return { ...d, mode: "editing", pos: pos.map((v) => Math.round(v * 100) / 100), yaw, pitch, surface: d.mount === "ceiling" ? `${room.name} ceiling` : hit.surface, roomH: room.h }; }),
    updateDdraft: (patch) => setDdraft((d) => ({ ...d, ...patch })), cancelDdraft: () => setDdraft(null),
    saveDevice: () => { const d = ddraft; if (!d || !d.pos) return; const id = d.id || `${d.kind}-${Math.random().toString(36).slice(2, 6)}`; const nm = (d.name || "").trim() || (d.kind === "camera" ? "Camera" : "mmWave sensor");
      const rec = { id, kind: d.kind, name: nm, pos: d.pos, yaw: d.yaw, pitch: d.pitch, vfov: d.vfov, hfov: d.hfov, range: d.range, mount: d.mount, surface: d.surface, roomH: d.roomH, status: d.kind === "camera" ? "placed" : "planned" };
      upd((h) => ({ ...h, cams: d.id ? h.cams.map((c) => (c.id === d.id ? { ...c, ...rec } : c)) : [...h.cams, rec] })); setDdraft(null); say(`${nm} ${d.id ? "moved" : "placed"} · saved to the ${site.name} Apartment model`); },
    deleteDevice: (id) => { const c = hs.cams.find((x) => x.id === id); upd((h) => ({ ...h, cams: h.cams.filter((x) => x.id !== id) })); setDdraft(null); say(`${c ? c.name : "Device"} removed`); },
    setSetupTool: (t2) => { setTdraft(null); setDdraft(null); setSetupTool(t2); }, openSetup: (tool) => { if (!setup) act.toggleSetup(); setSetupTool(tool); },
    markDirty: () => upd((h) => (h.dirty ? h : { ...h, dirty: true })),
    closeApp: () => { const dirty = HOMES.filter((x) => homes[x.id].dirty); if (dirty.length) setModal("close"); else say("Close returns to Home. This prototype stays open."); },
    about: () => setModal("about"), help: () => setModal("help"), openLab: () => say(`Perception Lab would open at ${site.id === "victoria" ? "frame #18234 with this session selected" : "this camera"}. Not part of this prototype.`),
    copy: (txt) => { try { navigator.clipboard.writeText(txt); } catch (e) { /* */ } say("Copied"); }, toast: say,
  };
  H.current.siteId = siteId; H.current.tdraft = tdraft; H.current.ddraft = ddraft; H.current.setupTargets = setup && setupTool === "targets"; H.current.act = act; H.current.hs = hs; H.current.site = site; H.current.tapAim = tapAim;

  useEffect(() => { if (!photo || photo.state !== "loading") return; const id = setTimeout(() => setPhoto(photo.pct >= 100 ? { state: "unavailable" } : { state: "loading", pct: Math.min(100, photo.pct + 23) }), 260); return () => clearTimeout(id); }, [photo]);

  /* scene */
  useEffect(() => {
    const sc = new window.AptScene(hostRef.current, {
      onHover: (h) => setHover(h),
      onSelect: (sel) => { const { hs: h, site: s, act: a } = H.current; if (H.current.setupTargets && sel && sel.kind === "target") { a.editTarget(sel.id); return; } const sf = h.selected && h.selected.kind === "fixture" && s.fixtures.find((x) => x.id === h.selected.id);
        if (sel && sel.kind === "target" && sf && sf.engineered && h.tab === "aim") { a.recallTarget(sf.id, sel.id); return; }
        if (!sel) { if (h.selected) a.select(null); return; } a.select(sel); },
      onAim: (e) => { const { hs: h, act: a } = H.current; const fid = h.selected && h.selected.id; if (!fid) return;
        if ((e.phase === "start" || (e.phase === "end" && e.hit)) && h.tab !== "aim") { a.setTab("aim"); setSheetTab((t) => (t === "chat" ? t : "inspect")); setSheet((s) => (s === "peek" ? "half" : s)); }
        if (e.phase === "end" && e.hit) { a.propose(fid, e.hit, e.via); if (e.via === "tap") setTapAim(false); } },
      onZone: (fid, i, solo) => H.current.act.toggleZone(fid, i, solo),
      onCompose: (fid, patch, phase) => H.current.act.composeZones(fid, patch, phase),
      onRoomTool: (t) => H.current.act.setTab(t === "aim" ? "aim" : "light"),
      onAimConfirm: (fid, what) => { const A = H.current.act; if (what === "set") A.applyAim(fid); else A.discard(fid); },
      onDraftEdit: (patch) => H.current.act.updateDraft(patch),
      onPlace: (hit) => { const A = H.current.act; if (H.current.ddraft) A.placeDevice(hit); else A.placeTarget(hit); },
      onScene: (info) => setSceneInfo((m) => ({ ...m, [info.site]: info })),
    });
    sceneRef.current = sc; window.__aptScene = sc; const id = setInterval(() => setDetent(sc.detent()), 300);
    return () => { clearInterval(id); sc.dispose(); };
  }, []);

  const pAct = scen !== "none"; const dimBody = scen === "stale" || scen === "held";
  const bodyVisible = site.capabilities.body && pAct && (layers.bodyObserved || layers.bodyInferred || layers.bodyVolume || layers.bodyUncertainty);
  const sheetPx = narrow ? Math.round(sheet === "peek" ? 150 : sheet === "full" ? vh - 60 : vh * 0.5) : 0;
  useEffect(() => {
    const sc = sceneRef.current; if (!sc) return; const B = site.perception.body; const tchk = tdraft && tdraft.test && tdraft.pos ? sc.checkAim(tdraft.test, tdraft.pos) : null;
    sc.update({ site, mode: hs.mode, selected: hs.selected, hover, fixtures: hs.fixtures, proposed: tchk ? { point: tdraft.pos, normal: tdraft.normal, valid: tchk.valid, blockerPoint: tchk.blockerPoint } : selFixture ? hs.proposed[selFixture.id] : null,
      aimFixture: tchk ? tdraft.test : selFixture && selFixture.engineered && !setup && (selFixture.kind !== "cylinder" || hs.tab === "aim") ? selFixture.id : null, tapAim,
      setup, placing: !!(tdraft && tdraft.mode === "placing") || !!(ddraft && (ddraft.mode === "placing" || ddraft.mode === "looking")), cams: hs.cams, devDraft: ddraft && ddraft.pos ? ddraft : null, showDevices: setup && setupTool === "cameras", zoneFixture: selFixture && selFixture.kind === "cylinder" && hs.tab !== "aim" && !setup ? selFixture.id : null, roomTool: selFixture && selFixture.kind === "cylinder" && !setup ? (hs.tab === "aim" ? "aim" : "zones") : null, pendingAim: !!(selFixture && hs.proposed[selFixture.id]), targetDraft: tdraft && tdraft.pos ? tdraft : null, showTargetMap: setup && setupTool === "targets", targets: hs.targets,
      layers: { pools: layers.pools, beams: layers.beams, targets: layers.targetsAlways || (setup && setupTool === "targets") ? "always" : "auto", labels: layers.labelsAlways ? "always" : "auto", cameras: layers.cameras || hs.trace === "seen" || !!hs.cameraView },
      body: bodyVisible ? { joints: B.joints, inferred: B.inferred, visible: true, dim: dimBody, layers: { observed: layers.bodyObserved, inferred: layers.bodyInferred, volume: layers.bodyVolume, uncertainty: layers.bodyUncertainty },
        label: scen === "replay" ? "Person · replay #18234" : dimBody ? `Person · ${scen === "stale" ? "6 min old" : "held 38 s"}` : "Person · frame #18234", title: "One analyzed frame, not tracked. Inferred joints are dashed; body volume is estimated from the joints." } : null,
      cameraView: hs.cameraView, insets: { top: 0, bottom: narrow ? sheetPx : 64 } });
  });
  useEffect(() => { // Victoria's analyzed frame: rendered once from the Den camera pose, then fixed like a real capture.
    if (siteId !== "victoria" || frames.victoria) return; const id = setTimeout(() => { const sc = sceneRef.current; if (!sc || !sc.site || sc.site.id !== "victoria") return;
      const B = site.perception.body; setFrames((m) => ({ ...m, victoria: { url: sc.renderFromCamera("den", 960, 540), kp: sc.projectFromCamera("den", window.APT_DATA.COCO.map((k) => B.joints[k])) } })); }, 450);
    return () => clearTimeout(id);
  }, [siteId, frames.victoria]);

  /* keyboard */
  useEffect(() => {
    const onKey = (e) => {
      const tg = e.target; if (tg && (/^(INPUT|TEXTAREA|SELECT)$/.test(tg.tagName) || tg.isContentEditable)) return; if (modal) return;
      const { hs: h, act: a, site: s } = H.current; const sc = sceneRef.current; const fid = h.selected && h.selected.kind === "fixture" ? h.selected.id : null;
      if (e.key === "Escape") {
        if (sc && sc.isDragging()) { sc.cancelDrag(); return; } if (H.current.tapAim) { setTapAim(false); return; }
        if (H.current.tdraft) { const d = H.current.tdraft; if (d.mode === "placing" && d.pos) setTdraft({ ...d, mode: "editing" }); else setTdraft(null); return; }
        if (H.current.ddraft) { const d = H.current.ddraft; if ((d.mode === "placing" || d.mode === "looking") && d.pos) setDdraft({ ...d, mode: "editing" }); else setDdraft(null); return; }
        if (h.cameraView) { a.returnToRoom(); return; } if (fid && h.proposed[fid]) { a.discard(fid); say("Proposal discarded · ⌘Z restores it"); return; }
        if (h.selected) { a.select(null); return; } a.closeApp(); return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z" && fid) { e.preventDefault(); e.shiftKey ? a.redo(fid) : a.undo(fid); return; }
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (tg && tg.classList && tg.classList.contains("apt-reticle") && /^Arrow/.test(e.key)) return;
      if (tg && tg.closest && tg.closest(".apt-col") && /^Arrow/.test(e.key)) return;
      const k = e.key;
      if (k === "ArrowLeft") sc.orbitStep(-1); else if (k === "ArrowRight") sc.orbitStep(1); else if (k === "ArrowUp") sc.tilt(1); else if (k === "ArrowDown") sc.tilt(-1);
      else if (k === "+" || k === "=") sc.zoomStep(1); else if (k === "-") sc.zoomStep(-1); else if (k === "h" || k === "f") sc.fit(); else if (k === "?") setModal("help");
      else if (k === "a" && fid) a.setTab("aim"); else if (k === "z" && fid) a.setTab("light");
      else if (k === "Enter" && fid && !(tg && tg.tagName === "BUTTON") && h.proposed[fid] && h.proposed[fid].valid !== false) a.applyAim(fid);
      else if (k === "n" || k === "p") { const L = s.fixtures; const i = fid ? L.findIndex((x) => x.id === fid) : -1; const j = (i + (k === "n" ? 1 : -1) + L.length) % L.length; a.select({ kind: "fixture", id: L[j].id }); }
      else return; e.preventDefault();
    };
    window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey);
  }, [modal]);

  const grab = useRef(null);
  const onGrabDown = (e) => { grab.current = { y: e.clientY }; e.currentTarget.setPointerCapture(e.pointerId); };
  const onGrabUp = (e) => { if (!grab.current) return; const dy = e.clientY - grab.current.y; grab.current = null; const order = ["peek", "half", "full"]; const i = order.indexOf(sheet);
    setSheet(Math.abs(dy) < 8 ? order[(i + 1) % 3] : dy < 0 ? order[Math.min(2, i + 1)] : order[Math.max(0, i - 1)]); };

  const chatOn = !setup && (phone ? sheetTab === "chat" : colTab === "chat");
  useEffect(() => { if (chatOn && chats[siteId].unread) setChats((c) => ({ ...c, [siteId]: { ...c[siteId], unread: false } })); }, [chatOn, siteId]);
  const ctx = { site, hs, scen, act, colTab, setColTab, chat: chats[siteId], setChat: (fn) => setChats((c) => ({ ...c, [siteId]: fn(c[siteId]) })), openCamera: () => { setColTab("controls"); setDock("default"); if (phone) setSheetTab("camera"); }, layers, dock, detail, setup, setupTool, tapAim, narrow, mode: hs.mode, tdraft, ddraft, zoneFacing: (fid) => (sceneRef.current && sceneRef.current.site ? sceneRef.current._zoneFacingCached(fid) : []), coverage: (d) => (sceneRef.current ? sceneRef.current.coverage(d) : []), checkAim: (fid, p) => (sceneRef.current ? sceneRef.current.checkAim(fid, p) : null), homes: HOMES, sceneInfo: sceneInfo[siteId], bodyVisible, detent,
    frameUrl: frames.victoria && frames.victoria.url, kp2d: frames.victoria && frames.victoria.kp, scenOf: (id) => (id === "echo" ? t.echoScenario : t.victoriaScenario), dirtyOf: (id) => homes[id].dirty };
  const camName = hs.cameraView && (site.cameras.find((c) => c.id === hs.cameraView) || {}).name;
  const showInspect = setup || (phone ? sheetTab === "inspect" : !chatOn); const showDock = phone ? sheetTab === "camera" : !chatOn;
  const echoBlocked = siteId === "echo" && (scen === "loading" || scen === "failed");

  return <div className={`apt-app ${dock === "expanded" && !narrow ? "is-wide" : ""}`} style={{ "--sheet-h": sheetPx + "px", "--sheet-px": sheetPx + "px" }}>
    <TopBar ctx={ctx} />
    <main className="apt-stage" aria-label={`${site.name} room`}>
      <div className="apt-host" ref={hostRef} style={{ opacity: echoBlocked ? 0.18 : 1, transition: "opacity .3s" }} />
      {tapAim && <div className="apt-hint" role="status"><Icon name="target" size={14} />Tap a surface to aim {selFixture && selFixture.short}<Btn sm onClick={() => setTapAim(false)}>Cancel <span className="kbd">esc</span></Btn></div>}
      {hs.cameraView && <div className="apt-hint" role="status" style={{ borderColor: "var(--hg-border)" }}><Icon name="camera" size={14} />Viewing from {camName}<Btn sm onClick={act.returnToRoom}>Return to room</Btn></div>}
      {photo && <div className="apt-notice" role="status">{photo.state === "loading" ? <><h3>Loading photo · {photo.pct}%</h3><div className="apt-progress"><i style={{ width: photo.pct + "%" }} /></div><p>Requesting the {site.name} photo scan. The current view stays as it is.</p></>
        : <><h3>Photo isn't available here</h3><p>The {site.name} photo scan isn't packaged with this prototype. In Home it streams from the apartment asset service. You're still looking at {site.name}.</p>
          <div className="apt-actions"><Btn sm onClick={act.retryPhoto}>Retry</Btn><Btn sm kind="primary" onClick={() => setPhoto(null)}>Back to Cloud</Btn></div></>}</div>}
      {echoBlocked && <div className="apt-notice" role="status">{scen === "loading" ? <><h3>Loading Echo Park · 42%</h3><div className="apt-progress"><i style={{ width: "42%" }} /></div><p>Fetching the model and point cloud. Victoria stays untouched.</p></>
        : <><h3>Echo Park's scan didn't load</h3><p>The apartment asset service didn't answer. Nothing else changed, and Apartment won't switch homes on its own.</p><div className="apt-actions"><Btn sm kind="primary" onClick={() => say("Retrying… (prototype)")}>Retry</Btn><Btn sm onClick={() => act.setSite("victoria")}>Open Victoria</Btn></div></>}</div>}
      {setup && <SetupRail ctx={ctx} />}
      {!narrow && !setup && <Legend ctx={ctx} />}
      {!(narrow && sheet === "full") && <SceneToolbar ctx={ctx} />}
      {!narrow && <div className="apt-zoom"><Btn sm icon="plus" label="Zoom in" onClick={() => sceneRef.current.zoomStep(1)} /><Btn sm icon="minus" label="Zoom out" onClick={() => sceneRef.current.zoomStep(-1)} /></div>}
      {toast && <div className="apt-toast" role="status">{toast.text}{toast.action && <Btn sm onClick={() => { toast.action.fn(); setToast(null); }}>{toast.action.label}</Btn>}<Btn sm icon="close" kind="ghost" label="Dismiss" onClick={() => setToast(null)} /></div>}
    </main>
    <aside className="apt-col" aria-label="Inspector">
      <button type="button" className="apt-grab" aria-label="Resize panel" onPointerDown={onGrabDown} onPointerUp={onGrabUp} />
      {phone && <div className="apt-sheettabs"><Seg full value={sheetTab} label="Panel" onChange={(v) => { setSheetTab(v); if (sheet === "peek") setSheet("half"); }} options={[{ value: "inspect", label: selFixture ? selFixture.short || "Light" : "Room" }, { value: "camera", label: site.capabilities.jev ? "Camera · JEV" : "Camera" }, { value: "chat", label: "Chat" }]} /></div>}
      {!phone && !setup && <div className="apt-coltabs" role="tablist" aria-label="Panel"><button type="button" role="tab" aria-selected={!chatOn} onClick={() => setColTab("controls")}>Controls</button><button type="button" role="tab" aria-selected={chatOn} onClick={() => setColTab("chat")}>Chat{chats[siteId].unread && !chatOn && <span className="dot" aria-label="New message" />}</button></div>}
      {showInspect && <div className="apt-insp">{setup ? <SetupInspector ctx={ctx} /> : <Inspector ctx={ctx} />}</div>}
      {showDock && !setup && <PerceptionDock ctx={narrow ? { ...ctx, dock: dock === "hidden" || dock === "collapsed" ? "default" : dock } : ctx} />}
      {!setup && <ChatPanel ctx={ctx} active={chatOn} />}
      {!narrow && !setup && !chatOn && dock === "hidden" && <div style={{ borderTop: "1px solid var(--hg-border)", padding: "8px 12px" }}><Btn sm icon="camera" onClick={() => setDock("default")}>Show camera</Btn></div>}
    </aside>
    {modal === "help" && <HelpModal onClose={() => setModal(null)} />}
    {modal === "about" && <AboutModal onClose={() => setModal(null)} sceneInfo={ctx.sceneInfo} />}
    {modal === "close" && <Modal title="Leave Apartment?" onClose={() => setModal(null)}><p className="apt-p">{HOMES.filter((x) => homes[x.id].dirty).map((x) => x.name).join(" and ")} has unsaved changes. They stay in this browser as a local draft if you leave.</p>
      <div className="apt-actions"><Btn kind="primary" onClick={() => { setModal(null); say("Prototype: Home would open; the draft is kept."); }}>Leave, keep draft</Btn><Btn onClick={() => setModal(null)}>Stay</Btn></div></Modal>}
    <TweaksPanel>
      <TweakSection label="Look" />
      <TweakRadio label="Theme" value={t.theme} options={["dark", "light"]} onChange={(v) => setTweak("theme", v)} />
      <TweakSection label="Echo Park state" />
      <TweakSelect label="Model and lights" value={t.echoScenario} options={[{ value: "connected", label: "Live model, HA connected" }, { value: "draft", label: "Local draft, HA offline" }, { value: "loading", label: "Loading" }, { value: "failed", label: "Scan failed" }]} onChange={(v) => setTweak("echoScenario", v)} />
      <TweakSection label="Victoria perception" />
      <TweakSelect label="Den camera" value={t.victoriaScenario} options={[{ value: "current", label: "Current (4 s)" }, { value: "held", label: "Held (38 s)" }, { value: "stale", label: "Stale (6 min)" }, { value: "abstain", label: "JEV abstains" }, { value: "replay", label: "Replay" }, { value: "none", label: "No session" }]} onChange={(v) => setTweak("victoriaScenario", v)} />
    </TweaksPanel>
  </div>;
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);

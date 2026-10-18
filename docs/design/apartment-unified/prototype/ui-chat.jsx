/* Chat tab: the Home chat inside the Apartment view. Prototype: replies are scripted and nothing is sent. */
const { useState: useStateC, useEffect: useEffectC, useRef: useRefC } = React;

const CHAT_CMDS = [["/look", "Ask what a camera sees"], ["/world-state", "Rooms, people and lights right now"], ["/explain", "Why it did that: tools from the last answer"],
  ["/lights", "Open the light controls"], ["/ask", "Ask an external model"], ["/travel", "Travel Mode status"], ["/clear", "Start a new conversation"]];
const CHAT_SUGG = { victoria: ["Make the Den cozy", "Light the painting", "What's happening in the Den?", "/world-state"], echo: ["Dim the living room to 30%", "Turn everything off", "Who's home?", "/explain"] };
const CHAT_SCENES = {
  victoria: { evening: { id: "evening", name: "Evening", all: { spot: { on: true, bri: 35, k: 2700 }, down: { on: true, bri: 20, k: 2700 }, radial: { on: true, level: 30, k: 2400 } } },
    bright: { id: "bright", name: "Bright", all: { spot: { on: true, bri: 80, k: 4000 }, down: { on: true, bri: 70, k: 4000 }, radial: { on: true, level: 70, k: 4000 } } } },
  echo: { evening: { id: "evening", name: "Evening", lights: { on: true, bri: 40, k: 2700 } }, bright: { id: "bright", name: "Bright", lights: { on: true, bri: 100, k: 4000 } } },
};
let chatSeq = 0; const chatId = () => "c" + ++chatSeq + Date.now().toString(36);
const partsLen = (parts) => parts.reduce((a, p) => a + (typeof p === "string" ? p.length : 4), 0);
const chatLit = (st) => !!st && (st.channels ? Object.values(st.channels).some((c) => c && c.on && (!c.levels || c.levels.some(Boolean))) : !!st.on);
const fmtT = (t) => new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(t));

function chatSeed(site) {
  const vic = site.id === "victoria", f = (vic ? site.fixtures.find((x) => x.id === "vic-s4") : site.fixtures.find((x) => /office|living/i.test(x.name))) || site.fixtures[0];
  const parts = vic ? ["You sat down at the desk, so I brought ", { fid: f.id }, " up to 55% at 2700 K."] : ["Welcome home. I turned on ", { fid: f.id }, " for you."];
  return { unread: true, msgs: [{ id: chatId(), role: "home", proactive: true, at: Date.now() - 4 * 60000, parts, shown: partsLen(parts), cards: [{ type: "action", title: f.name, detail: vic ? "Spot 55% · 2700 K" : "On", fid: f.id }], steps: ["proactive · " + (vic ? "seated at desk" : "arrival"), "light.turn_on · " + (f.entity || f.id)] }] };
}

function chatRespond(text, ctx, focus) {
  const { site, hs, act } = ctx, raw = text.trim(), low = raw.toLowerCase(), vic = site.id === "victoria", before = JSON.parse(JSON.stringify(hs.fixtures));
  const word = (w) => new RegExp("\\b" + w.replace(/[^a-z0-9]/g, "") + "\\b").test(low);
  const named = site.fixtures.find((f) => low.includes(f.name.toLowerCase()) || (f.short && word(f.short.toLowerCase())));
  const fx = named || (focus && focus.kind === "fixture" ? site.fixtures.find((f) => f.id === focus.id) : null);
  const tgt = hs.targets.find((t) => low.includes(t.name.toLowerCase())) || (vic && /\bread/.test(low) ? hs.targets.find((t) => /read/i.test(t.name)) : null);
  const room = site.rooms.find((r) => low.includes(r.name.toLowerCase())), pct = +((low.match(/(\d{1,3})\s*%/) || [])[1] || 0) || null;
  if (low.startsWith("/")) {
    const cmd = low.split(/\s+/)[0], q = raw.slice(cmd.length).trim(), cam = site.cameras[0] || {};
    if (cmd === "/look") return { parts: vic ? ["One person at the desk, seated and facing the monitor. ", { fid: "vic-s4" }, " is lighting the desk."] : ["Someone is in the living room, near the sofa. No one else is in view."], cards: [{ type: "look" }], steps: ["/look" + (q ? " " + q : ""), "vision · " + (cam.name || "camera"), "one analyzed frame"] };
    if (cmd === "/world-state") return { parts: ["Here's the home right now."], cards: [{ type: "world" }], steps: ["/world-state", "world_state"] };
    if (cmd === "/explain") { const last = [...ctx.chat.msgs].reverse().find((m) => m.role !== "user" && m.steps && m.steps.length && !m.isExplain);
      return { isExplain: true, parts: last ? ["Here's how I handled that."] : ["There's nothing to explain yet."], cards: last ? [{ type: "explain", q: last.q || "proactive", steps: last.steps }] : [] }; }
    if (cmd === "/lights") return { parts: ["Opened the light controls."], run: () => ctx.setColTab("controls"), steps: ["/lights"] };
    if (cmd === "/travel") return { parts: ["Travel Mode is off."], cards: [{ type: "travel" }], steps: ["/travel status"] };
    if (cmd === "/ask") return { role: "external", parts: [q ? "In general, warm light around 2700 K at low brightness is easier on sleep in the evening than cool light. Your home already shifts warmer after sunset." : "Add a question after /ask."], note: "Sent your question and a room summary. No camera images.", steps: ["/ask", "route · external", "answered"] };
    return { role: "system", parts: ["Unknown command. Type / to see the list."] };
  }
  if (/\b(i like|i prefer|i'd rather|remember that)\b/.test(low)) return { parts: ["Want me to keep that as a preference? It's reviewed before it changes anything."], cards: [{ type: "pref", text: raw, state: "open" }], steps: ["preference review · private"] };
  if (!named && /\boff\b/.test(low) && /\b(all|everything|every light|lights)\b/.test(low)) { const fs = site.fixtures.filter((f) => !room || f.room === room.id);
    return { run: () => act.setAll(false, room ? room.id : undefined), parts: [room ? `Done. The ${room.name.toLowerCase()} is off.` : "Done. Every light is off."], cards: [{ type: "action", title: room ? room.name + " off" : "All lights off", detail: fs.length + " lights", before }], steps: [`light.turn_off × ${fs.length}`] }; }
  if (!named && /\bon\b/.test(low) && /\b(all|everything|every light)\b/.test(low)) return { run: () => act.setAll(true), parts: ["Every light is on."], cards: [{ type: "action", title: "All lights on", detail: site.fixtures.length + " lights", before }], steps: ["light.turn_on × " + site.fixtures.length] };
  if (tgt && /\b(aim|point|light|spot|shine|read|reading)\b/.test(low)) {
    const cand = (fx && fx.engineered ? [fx] : []).concat(site.fixtures.filter((f) => f.engineered && f !== fx));
    const ok = cand.map((f) => ({ f, r: ctx.checkAim(f.id, tgt.pos) })).filter((x) => x.r && x.r.valid), pick = ok.find((x) => !x.r.shadow) || ok[0];
    if (!pick) return { parts: ["No light can reach ", { tid: tgt.id }, " from where it's mounted."], steps: ["aim · solve " + cand.length + " fixtures", "none reachable"] };
    return { run: () => { act.propose(pick.f.id, { ...pick.r, point: tgt.pos.slice(), normal: tgt.normal.slice(), surface: tgt.name, snapId: tgt.id, snapName: tgt.name }, "chat"); act.select({ kind: "fixture", id: pick.f.id }, "aim"); },
      parts: ["I'd light ", { tid: tgt.id }, " from ", { fid: pick.f.id }, pick.r.shadow ? `. Your ${pick.r.shadow.part} is in that beam right now, so expect a shadow while you're there. It's previewed in the room.` : ", clear of you. It's previewed in the room."],
      cards: [{ type: "proposal", fid: pick.f.id, tid: tgt.id, state: "open" }], steps: ["aim · solve " + cand.length + " fixtures", pick.f.short + " → " + tgt.name + (pick.r.shadow ? " · body in beam" : " · clear")] };
  }
  if (fx && (pct || /\b(bright|brighter|dim|dimmer|warm|warmer|cool|cooler|on|off)\b/.test(low))) {
    const st = hs.fixtures[fx.id] || {}, cyl = fx.kind === "cylinder", chn = cyl ? (/\bdown\b/.test(low) ? "down" : "spot") : null, cur = (cyl ? st.channels[chn] : st) || {};
    let bri = cur.bri || 50, k = cur.k || 2700, on = true;
    if (pct) bri = Math.max(1, Math.min(100, pct)); else if (/\bbright(er)?\b/.test(low)) bri = Math.min(100, bri + 20); else if (/\bdim(mer)?\b/.test(low)) bri = Math.max(1, bri - 20);
    if (/\bwarm(er)?\b/.test(low)) k = Math.max(1800, k - 400); if (/\bcool(er)?\b/.test(low)) k = Math.min(6500, k + 400); if (/\boff\b/.test(low) && !pct) on = false;
    const lbl = cyl ? (chn === "down" ? "Down " : "Spot ") : "", desc = on ? (fx.kind === "plug" ? "on" : `${bri}% · ${k} K`) : "off";
    return { run: () => (cyl ? act.setChannel(fx.id, chn, { on, bri, k }) : act.setLight(fx.id, fx.kind === "plug" ? { on } : { on, bri, k })),
      parts: [on ? "Set " : "Turned off ", { fid: fx.id }, on ? ` ${lbl.toLowerCase()}to ${desc}.` : "."], cards: [{ type: "action", title: fx.name, detail: lbl + desc, fid: fx.id, before }], steps: [`light.turn_${on ? "on" : "off"} · ${fx.entity || fx.id}${cyl ? " · " + chn : ""}`] };
  }
  if (room && pct) { const fs = site.fixtures.filter((f) => f.room === room.id && f.kind !== "plug");
    return { run: () => fs.forEach((f) => (f.kind === "cylinder" ? act.setChannel(f.id, "spot", { on: true, bri: pct }) : act.setLight(f.id, { on: true, bri: pct }))), parts: [`The ${room.name.toLowerCase()} is at ${pct}%.`], cards: [{ type: "action", title: room.name, detail: `${fs.length} lights · ${pct}%`, before }], steps: [`light.turn_on × ${fs.length} · ${pct}%`] }; }
  const sc = /\b(cozy|cosy|evening|relax|chill|wind down)\b/.test(low) ? CHAT_SCENES[site.id].evening : /\b(bright|work|focus)\b/.test(low) ? CHAT_SCENES[site.id].bright : null;
  if (sc) return { run: () => act.applyScene(sc), parts: [sc.id === "evening" ? "Evening it is: warm and low" : "Bright it is", vic ? " across the Den." : " through the home."], cards: [{ type: "action", title: sc.name, detail: "Scene · " + site.fixtures.length + " lights", before }], steps: ["scene · " + sc.name, "light.turn_on × " + site.fixtures.length] };
  if (/\b(who|anyone|someone|happening|going on|see)\b/.test(low)) return chatRespond("/look", ctx, focus);
  return { parts: ["I can change lights, aim the spots, look through the cameras and explain what I did. Try “", vic ? "Make the Den cozy" : "Dim the living room to 30%", "”, or type / for commands."] };
}

function ChatEnt({ p, ctx }) {
  const { site, hs, act } = ctx;
  if (p.fid) { const f = site.fixtures.find((x) => x.id === p.fid); if (!f) return null;
    return <button type="button" className="apt-ent" title="Show in the room" onMouseEnter={() => act.hoverFixture(f.id)} onMouseLeave={() => act.hoverFixture(null)} onClick={() => { act.select({ kind: "fixture", id: f.id }); act.focusFixture(f.id); }}>{f.name}</button>; }
  const t = p.tid && hs.targets.find((x) => x.id === p.tid);
  return t ? <button type="button" className="apt-ent t" title="Show in the room" onClick={() => act.select({ kind: "target", id: t.id })}>{t.name}</button> : null;
}
function ChatParts({ parts, shown, ctx }) {
  let left = shown == null ? Infinity : shown; const out = [];
  parts.forEach((p, i) => { if (left <= 0) return; if (typeof p === "string") { out.push(p.length <= left ? p : p.slice(0, left)); left -= p.length; } else { out.push(<ChatEnt key={i} p={p} ctx={ctx} />); left -= 4; } });
  return out;
}
const plainParts = (parts, ctx) => parts.map((p) => (typeof p === "string" ? p : p.fid ? (ctx.site.fixtures.find((f) => f.id === p.fid) || {}).name : (ctx.hs.targets.find((t) => t.id === p.tid) || {}).name)).join("");

function ChatCard({ m, cd, ci, ctx }) {
  const { act, site, hs } = ctx, fx = cd.fid && site.fixtures.find((x) => x.id === cd.fid);
  const set = (patch) => ctx.setChat((c) => ({ ...c, msgs: c.msgs.map((x) => (x.id === m.id ? { ...x, cards: x.cards.map((y, j) => (j === ci ? { ...y, ...patch } : y)) } : x)) }));
  const show = () => { act.select({ kind: "fixture", id: fx.id }); act.focusFixture(fx.id); };
  if (cd.type === "action") return <div className="apt-card">
    <div className="ttl"><Icon name={cd.undone ? "undo" : "check"} size={13} style={{ color: cd.undone ? "var(--hg-fg-3)" : "var(--apt-ok)" }} /><span className="grow">{cd.title}</span><span className="apt-meta">{cd.undone ? "Undone" : cd.detail}</span></div>
    {(fx || (cd.before && !cd.undone)) && <div className="apt-actions">{fx && <Btn sm onClick={show}>Show</Btn>}{fx && <Btn sm onClick={() => { act.select({ kind: "fixture", id: fx.id }, "light"); ctx.setColTab("controls"); }}>Adjust</Btn>}
      {cd.before && !cd.undone && <Btn sm kind="ghost" onClick={() => { act.restoreFixtures(cd.before); set({ undone: true }); }}>Undo</Btn>}</div>}
  </div>;
  if (cd.type === "proposal") { const t = hs.targets.find((x) => x.id === cd.tid), st = cd.state === "open" && !hs.proposed[cd.fid] ? "gone" : cd.state;
    return <div className="apt-card prop">
      <div className="ttl"><span className="mk" aria-hidden="true" /><span className="grow">{fx.short} → {t ? t.name : "aim"}</span><span className="apt-meta">{st === "set" ? "Aim set" : st === "discarded" ? "Discarded" : st === "gone" ? "No longer proposed" : "Proposed · modeled"}</span></div>
      {st === "open" && <div className="apt-actions"><Btn sm kind="primary" onClick={() => { act.applyAim(fx.id); set({ state: "set" }); }}>Set aim</Btn><Btn sm onClick={() => { act.select({ kind: "fixture", id: fx.id }, "aim"); act.focusFixture(fx.id); }}>Preview</Btn><Btn sm kind="ghost" onClick={() => { act.discard(fx.id); set({ state: "discarded" }); }}>Discard</Btn></div>}
    </div>; }
  if (cd.type === "look") { const cam = site.cameras[0] || {}; return <div className="apt-card"><div className="ttl"><Icon name="camera" size={13} /><span className="grow">{cam.name || "Camera"}</span><span className="apt-meta">one frame · just now</span></div><div className="apt-actions"><Btn sm icon="camera" onClick={ctx.openCamera}>Open camera</Btn></div></div>; }
  if (cd.type === "world") return <div className="apt-card"><div className="ttl"><span className="grow">World state</span><span className="apt-meta">live</span></div>
    <ul className="apt-wl">{site.rooms.map((r, i) => { const fs = site.fixtures.filter((f) => f.room === r.id); if (!fs.length) return null; const on = fs.filter((f) => chatLit(hs.fixtures[f.id])).length;
      return <li key={r.id}><span className="grow">{r.name}</span><span className="apt-meta">{i === 0 ? "1 person · " : ""}{on} of {fs.length} lights on</span></li>; })}</ul></div>;
  if (cd.type === "explain") return <div className="apt-card"><div className="ttl"><Icon name="help" size={13} /><span className="grow">Why: “{cd.q}”</span></div><ol className="apt-steps">{cd.steps.map((s, i) => <li key={i}>{s}</li>)}</ol><p className="apt-meta">In Home this comes from the routing log and tool-call events.</p></div>;
  if (cd.type === "pref") return <div className="apt-card"><div className="ttl"><Icon name="lock" size={13} /><span className="grow">Preference review</span><span className="apt-meta">private · not saved to chat</span></div><p className="apt-p">“{cd.text}”</p>
    {cd.state === "open" ? <div className="apt-actions"><Btn sm kind="primary" onClick={() => set({ state: "saved" })}>Save preference</Btn><Btn sm kind="ghost" onClick={() => set({ state: "dismissed" })}>Not now</Btn></div> : <p className="apt-meta">{cd.state === "saved" ? "Saved as a shared preference. It applies after review." : "Not saved."}</p>}</div>;
  if (cd.type === "travel") return <div className="apt-card"><div className="ttl"><span className="grow">Travel Mode</span><Badge state="none" label="Off" /></div><p className="apt-meta">When it's on, every light stays off and light cards in chat lock. It's the last guard before any light output.</p><div className="apt-actions"><Btn sm onClick={() => act.notify("Travel Mode turns on from Home Assistant · prototype, nothing sent")}>Turn on</Btn></div></div>;
  return null;
}

function ChatPanel({ ctx, active }) {
  const { site, hs, act, chat } = ctx; const [draft, setDraft] = useStateC(""); const [mi, setMi] = useStateC(0); const [ctxOff, setCtxOff] = useStateC(false); const [listening, setListening] = useStateC(false);
  const feedRef = useRefC(null), taRef = useRefC(null), pending = useRefC(null), micT = useRefC(null);
  const busy = chat.msgs.some((m) => m.thinking || m.streaming), sel = hs.selected;
  const selLabel = !sel ? null : sel.kind === "fixture" ? (site.fixtures.find((x) => x.id === sel.id) || {}).name : sel.kind === "target" ? (hs.targets.find((x) => x.id === sel.id) || {}).name : null;
  const items = draft.startsWith("/") && !/\s/.test(draft) ? CHAT_CMDS.filter(([c]) => c.startsWith(draft.toLowerCase())) : [];
  useEffectC(() => setCtxOff(false), [sel && sel.id]);
  useEffectC(() => { const m = chat.msgs.find((x) => x.streaming); if (!m) return; const id = setTimeout(() => ctx.setChat((c) => ({ ...c, msgs: c.msgs.map((x) => { if (x.id !== m.id) return x; const n = Math.min(x.full, x.shown + 5); return { ...x, shown: n, streaming: n < x.full }; }) })), 22); return () => clearTimeout(id); }, [chat]);
  useEffectC(() => { const f = feedRef.current; if (f && active) f.scrollTop = f.scrollHeight; }, [chat, active]);
  useEffectC(() => { const t = taRef.current; if (t) { t.style.height = "auto"; t.style.height = Math.min(120, t.scrollHeight) + "px"; } }, [draft, active]);
  useEffectC(() => () => { if (pending.current) clearTimeout(pending.current.timer); clearTimeout(micT.current); }, []);
  const send = (txt) => { const text = (txt != null ? txt : draft).trim(); if (!text || busy) return; setDraft(""); setMi(0);
    if (text.toLowerCase() === "/clear") { ctx.setChat((c) => ({ ...c, msgs: [] })); return; }
    const focus = ctxOff ? null : sel, r = chatRespond(text, ctx, focus);
    const um = { id: chatId(), role: "user", at: Date.now(), parts: [text], shown: text.length, about: focus ? selLabel : null };
    const hm = { id: chatId(), role: r.role || "home", at: Date.now(), parts: [], shown: 0, full: 0, thinking: true, steps: r.steps || [], q: text, cards: [], note: r.note, isExplain: r.isExplain };
    ctx.setChat((c) => ({ ...c, msgs: [...c.msgs, um, hm] }));
    pending.current = { id: hm.id, timer: setTimeout(() => { pending.current = null; if (r.run) r.run(); const full = partsLen(r.parts);
      ctx.setChat((c) => ({ ...c, msgs: c.msgs.map((m) => (m.id === hm.id ? { ...m, thinking: false, parts: r.parts, full, shown: 0, streaming: true, cards: r.cards || [] } : m)) })); }, 650) }; };
  const stop = () => { if (pending.current) { clearTimeout(pending.current.timer); const id = pending.current.id; pending.current = null; const p = ["Stopped before doing anything."];
      ctx.setChat((c) => ({ ...c, msgs: c.msgs.map((m) => (m.id === id ? { ...m, thinking: false, parts: p, full: partsLen(p), shown: partsLen(p), steps: [], cards: [] } : m)) })); return; }
    ctx.setChat((c) => ({ ...c, msgs: c.msgs.map((m) => (m.streaming ? { ...m, streaming: false, stopped: true } : m)) })); };
  const mic = () => { if (listening) { clearTimeout(micT.current); setListening(false); return; } setListening(true);
    micT.current = setTimeout(() => { setListening(false); setDraft(site.id === "victoria" ? "Make the Den a little cozier" : "Dim the living room to 30%"); setTimeout(() => taRef.current && taRef.current.focus(), 0); }, 1800); };
  const onKey = (e) => { e.stopPropagation();
    if (items.length) { if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); setMi((mi + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length); return; }
      if (e.key === "Tab" || (e.key === "Enter" && items[mi] && items[mi][0] !== draft.toLowerCase())) { e.preventDefault(); setDraft(items[mi][0] + " "); return; }
      if (e.key === "Escape") { e.preventDefault(); setDraft(""); return; } }
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } };
  const copyAll = () => { const t = chat.msgs.map((m) => (m.role === "user" ? "You" : m.role === "external" ? "External" : m.role === "system" ? "System" : "Home") + ": " + plainParts(m.parts, ctx)).join("\n");
    try { navigator.clipboard.writeText(t); } catch (err) { /* */ } act.notify("Transcript copied"); };
  return <section className="apt-chat" hidden={!active} aria-label="Chat">
    <div className="apt-chat-head"><span className="apt-meta grow">{site.id === "victoria" ? "Local model · Den camera" : "Local model · Home Assistant"}</span>
      <Btn sm kind="ghost" icon="help" label="Explain the last answer" title="Why did it do that?" disabled={busy} onClick={() => send("/explain")} />
      <Btn sm kind="ghost" icon="copy" label="Copy transcript" title="Copy transcript" onClick={copyAll} />
      <Btn sm kind="ghost" icon="plus" label="New conversation" title="New conversation" disabled={busy} onClick={() => ctx.setChat((c) => ({ ...c, msgs: [] }))} /></div>
    <div className="apt-chat-feed" ref={feedRef} aria-live="polite">
      {!chat.msgs.length && <p className="apt-p apt-chat-empty">Ask your home anything, or type / for commands.</p>}
      {chat.msgs.map((m, i) => { const prev = chat.msgs[i - 1], head = m.role !== "user" && m.role !== "system" && (!prev || prev.role !== m.role || m.proactive);
        return <div key={m.id} className={`apt-msg ${m.role}`}>
          {head && <div className="who"><b>{m.role === "external" ? "External model" : "Home"}</b>{m.proactive && <span>proactive</span>}<span>{fmtT(m.at)}</span></div>}
          {m.about && <div className="apt-meta">about {m.about}</div>}
          {m.thinking ? <div className="apt-progress"><span className="apt-typing" aria-label="Working"><i /><i /><i /></span>{m.steps[0] && <span>{m.steps[0]}</span>}</div>
            : <div className="txt"><ChatParts parts={m.parts} shown={m.streaming ? m.shown : null} ctx={ctx} />{m.stopped && <span className="apt-meta"> · stopped</span>}</div>}
          {m.note && !m.thinking && <div className="apt-meta">{m.note}</div>}
          {!m.thinking && !m.streaming && (m.cards || []).map((cd, ci) => <ChatCard key={ci} m={m} cd={cd} ci={ci} ctx={ctx} />)}
        </div>; })}
      {!busy && <div className="apt-sugg">{CHAT_SUGG[site.id].map((s) => <button key={s} type="button" className="apt-chip" onClick={() => send(s)}>{s}</button>)}</div>}
    </div>
    <div className="apt-compose">
      {items.length > 0 && <div className="apt-slash" role="listbox" aria-label="Commands">{items.map(([c, d], i) => <button key={c} type="button" role="option" aria-selected={i === mi} onMouseDown={(e) => { e.preventDefault(); setDraft(c + " "); if (taRef.current) taRef.current.focus(); }}><span className="c">{c}</span><span className="d">{d}</span></button>)}</div>}
      {selLabel && !ctxOff && <span className="apt-ctxchip">About {selLabel}<Btn sm kind="ghost" icon="close" label="Don't include the selection" onClick={() => setCtxOff(true)} /></span>}
      <div className="box">
        {listening ? <div className="apt-listen grow" aria-live="polite"><i /><i /><i /><i /><i /><span className="apt-meta">Listening… tap the mic to stop</span></div>
          : <textarea ref={taRef} rows={1} value={draft} placeholder="Ask your home, or type /" aria-label="Message" onChange={(e) => { setDraft(e.target.value); setMi(0); }} onKeyDown={onKey} />}
        <Btn sm kind="ghost" icon="mic" label={listening ? "Stop listening" : "Voice"} title="Voice" pressed={listening} onClick={mic} />
        {busy ? <Btn sm icon="stop" label="Stop" title="Stop" onClick={stop} /> : <Btn sm kind="primary" icon="send" label="Send" title="Send (Enter)" disabled={!draft.trim()} onClick={() => send()} />}
      </div>
      <p className="apt-meta">Prototype · replies are scripted and nothing is sent.</p>
    </div>
  </section>;
}

Object.assign(window, { ChatPanel, chatSeed });

/* Apartment unified prototype — shared primitives (Badge, buttons, controls, icons). */
const { useState: useStateK, useEffect: useEffectK, useRef: useRefK } = React;

const ICONS = {
  close: "M4 4l8 8M12 4l-8 8", back: "M10 3L5 8l5 5", chevL: "M10 3L5 8l5 5", chevR: "M6 3l5 5-5 5", chevD: "M3 6l5 5 5-5", chevU: "M3 10l5-5 5 5", pencil: "M10.5 2.5l3 3L6 13H3v-3z", send: "M8 13V3.5M3.8 7.7L8 3.5l4.2 4.2", stop: "M5 5h6v6H5z", mic: "M8 2.2a2 2 0 0 1 2 2v3.6a2 2 0 0 1-4 0V4.2a2 2 0 0 1 2-2zM4.2 7.8a3.8 3.8 0 0 0 7.6 0M8 11.6v2.2",
  layers: "M8 2l6 3-6 3-6-3 6-3zM2 8l6 3 6-3M2 11l6 3 6-3", help: "M6 6a2 2 0 1 1 2.8 1.8c-.5.3-.8.7-.8 1.2V10M8 12.5v.5",
  camera: "M2 5h3l1-2h4l1 2h3v8H2zM8 11.2a2.2 2.2 0 1 0 0-4.4 2.2 2.2 0 0 0 0 4.4z", expand: "M9 3h4v4M7 13H3V9M13 3L9 7M3 13l4-4",
  collapse: "M13 7H9V3M3 9h4v4M9 7l4-4M7 9l-4 4", hide: "M2 8s2.2-4 6-4 6 4 6 4-2.2 4-6 4-6-4-6-4zM3 13L13 3",
  external: "M6 3H3v10h10v-3M9 3h4v4M13 3L7 9", copy: "M5 5h8v8H5zM3 11V3h8", undo: "M5 4L2 7l3 3M2 7h7a4 4 0 0 1 0 8H7",
  redo: "M11 4l3 3-3 3M14 7H7a4 4 0 0 0 0 8h2", target: "M8 1v3M8 12v3M1 8h3M12 8h3M8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  ruler: "M2 11L11 2l3 3-9 9zM5 8l1.5 1.5M7 6l1.5 1.5M9 4l1.5 1.5", info: "M8 7v5M8 4.5V5M8 15A7 7 0 1 0 8 1a7 7 0 0 0 0 14z",
  check: "M3 8.5l3 3 7-7", fit: "M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4", plus: "M8 3v10M3 8h10", minus: "M3 8h10",
  setup: "M3 4h10M3 8h10M3 12h10M6 2.5v3M10 6.5v3M5 10.5v3", eye: "M2 8s2.2-4 6-4 6 4 6 4-2.2 4-6 4-6-4-6-4zM8 10a2 2 0 1 0 0-4 2 2 0 0 0 0 4z",
  lock: "M4 7h8v7H4zM6 7V5a2 2 0 0 1 4 0v2", warn: "M8 2l6.5 11.5h-13zM8 6.5v3.5M8 12v.2", spark: "M8 2v3M8 11v3M2 8h3M11 8h3M4 4l2 2M10 10l2 2M12 4l-2 2M6 10l-2 2",
  bookmark: "M4 2h8v12l-4-3-4 3z", play: "M5 3l8 5-8 5z", person: "M8 7a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM3 14c.6-3 2.6-4.5 5-4.5s4.4 1.5 5 4.5",
};
function Icon({ name, size = 16, style }) {
  return <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flex: "0 0 auto", ...style }}><path d={ICONS[name] || ""} /></svg>;
}

/* Status vocabulary: one shape per meaning, never colour alone. */
const TONE_FOR = {
  live: "live", connected: "ok", current: "ok", saved: "ok", applied: "ok", reported: "ok",
  proposed: "proposed", draft: "proposed", modeled: "neutral", authored: "authored", simulated: "authored",
  recorded: "recorded", replay: "recorded", held: "attn", stale: "attn", unsaved: "attn", changed: "attn", loading: "neutral", saving: "neutral",
  expired: "neutral", unavailable: "neutral", none: "neutral", disconnected: "attn", blocked: "blocked", conflict: "blocked", invalid: "blocked",
};
function Badge({ state, label, title, spin, className = "" }) {
  const tone = TONE_FOR[state] || state || "neutral";
  return <span className={`apt-badge ${tone} ${className}`} title={title}>{spin ? <span className="spin" /> : <span className="g" />}<span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{label}</span></span>;
}

function Btn({ children, icon, kind = "", sm, onClick, disabled, title, pressed, label, style, type = "button", className = "", ...rest }) {
  const cls = ["apt-btn", kind, sm ? "sm" : "", !children && icon ? "icon" : "", className].filter(Boolean).join(" ");
  return <button type={type} className={cls} onClick={onClick} disabled={disabled} title={title} aria-label={label} aria-pressed={pressed} style={style} {...rest}>{icon && <Icon name={icon} size={sm ? 14 : 16} />}{children}</button>;
}

function Seg({ value, options, onChange, full, label, className = "" }) {
  return <div className={`apt-seg ${full ? "full" : ""} ${className}`} role="group" aria-label={label}>
    {options.map((o) => <button type="button" key={o.value} aria-pressed={value === o.value} disabled={o.disabled} title={o.title} onClick={() => onChange(o.value)}>{o.icon && <Icon name={o.icon} size={14} />}{o.label}{o.extra}</button>)}
  </div>;
}

function Switch({ checked, onChange, label, disabled }) {
  return <button type="button" role="switch" className="apt-switch" aria-checked={!!checked} aria-label={label} disabled={disabled} onClick={() => onChange(!checked)} />;
}

function Slider({ label, value, min, max, step = 1, unit, onChange, temp, disabled, fmt }) {
  return <label className="apt-slider">
    <span>{label}</span>
    <input type="range" className={`apt-range ${temp ? "temp" : ""}`} min={min} max={max} step={step} value={value} disabled={disabled} aria-label={label} onChange={(e) => onChange(+e.target.value)} />
    <span className="apt-val">{fmt ? fmt(value) : `${value}${unit || ""}`}</span>
  </label>;
}

function Disc({ summary, children, open, onToggle }) {
  return <details className="apt-disc" open={open} onToggle={onToggle ? (e) => onToggle(e.currentTarget.open) : undefined}><summary>{summary}</summary><div className="body">{children}</div></details>;
}

function Popover({ anchor, onClose, children, align = "left", width }) {
  const [pos, setPos] = useStateK(null);
  useEffectK(() => {
    const el = anchor && anchor.current; if (!el) return;
    const r = el.getBoundingClientRect(); const vw = window.innerWidth;
    const w = Math.min(width || 320, vw - 24);
    let left = align === "right" ? r.right - w : r.left; left = Math.max(12, Math.min(left, vw - w - 12));
    setPos({ left, top: r.bottom + 6, width: w, up: false });
  }, [anchor]);
  useEffectK(() => {
    const k = (e) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } };
    window.addEventListener("keydown", k, true); return () => window.removeEventListener("keydown", k, true);
  }, [onClose]);
  if (!pos) return null;
  return <><div className="apt-scrim" onClick={onClose} /><div className="apt-pop" role="dialog" style={{ position: "fixed", left: pos.left, top: pos.top, width: pos.width }}>{children}</div></>;
}

function Modal({ title, onClose, children }) {
  const ref = useRefK(null);
  useEffectK(() => {
    const prev = document.activeElement; ref.current && ref.current.focus();
    const k = (e) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } };
    window.addEventListener("keydown", k, true); document.documentElement.setAttribute("data-overlay-open", "1");
    return () => { window.removeEventListener("keydown", k, true); document.documentElement.removeAttribute("data-overlay-open"); prev && prev.focus && prev.focus(); };
  }, []);
  return <div className="apt-modal" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}><div role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref}>
    <div className="apt-row"><h2 className="grow">{title}</h2><Btn icon="close" label="Close" kind="ghost" onClick={onClose} /></div>{children}</div></div>;
}

const fmtK = (k) => `${k} K`;
const fmtPct = (v) => `${v}%`;
const fmtAge = (s) => s == null ? "—" : s < 60 ? `${Math.round(s)} s` : s < 3600 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`;
const kelvinCss = (k) => { const c = window.aptKelvin ? window.aptKelvin(k) : [1, .85, .7]; return `rgb(${Math.round(c[0] * 255)},${Math.round(c[1] * 255)},${Math.round(c[2] * 255)})`; };

Object.assign(window, { Icon, Badge, Btn, Seg, Switch, Slider, Disc, Popover, Modal, fmtK, fmtPct, fmtAge, kelvinCss, TONE_FOR });

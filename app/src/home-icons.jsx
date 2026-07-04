/* Home icons — small inline SVGs, 1.5px stroke, 24x24 grid (Geist-style) */

function IconMic({ size = 16, stroke = "currentColor" }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={stroke} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="3" width="6" height="12" rx="3" />
      <path d="M6 11a6 6 0 0 0 12 0" />
      <line x1="12" y1="17" x2="12" y2="21" />
    </svg>
  );
}

function IconSend({ size = 14, stroke = "currentColor" }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={stroke} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <line x1="5" y1="12" x2="19" y2="12" />
      <polyline points="13 6 19 12 13 18" />
    </svg>
  );
}

function IconSun({ size = 14, stroke = "currentColor" }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={stroke} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3.5" />
      <line x1="12" y1="3" x2="12" y2="5" /><line x1="12" y1="19" x2="12" y2="21" />
      <line x1="3" y1="12" x2="5" y2="12" /><line x1="19" y1="12" x2="21" y2="12" />
      <line x1="5.6" y1="5.6" x2="6.9" y2="6.9" /><line x1="17.1" y1="17.1" x2="18.4" y2="18.4" />
      <line x1="5.6" y1="18.4" x2="6.9" y2="17.1" /><line x1="17.1" y1="6.9" x2="18.4" y2="5.6" />
    </svg>
  );
}

function IconMoon({ size = 14, stroke = "currentColor" }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={stroke} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />
    </svg>
  );
}

function IconStop({ size = 12, fill = "currentColor" }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" fill={fill} /></svg>
  );
}

/* The EL logo mark — pill + dot in current foreground */
function IconLogo({ size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 400 400" fill="currentColor">
      <circle cx="130" cy="230" r="38" />
      <rect x="225" y="100" width="76" height="200" rx="38" />
    </svg>
  );
}

/* Tiny waveform — for voice transcripts (static) */
function IconWaveStatic({ size = 28, stroke = "currentColor" }) {
  return (
    <svg width={size} height={Math.round(size * 0.5)} viewBox="0 0 56 14" fill="none" stroke={stroke} strokeWidth="1.25" strokeLinecap="round">
      <line x1="2"  y1="7" x2="2"  y2="7"  /><line x1="6"  y1="5" x2="6"  y2="9" />
      <line x1="10" y1="3" x2="10" y2="11" /><line x1="14" y1="4" x2="14" y2="10" />
      <line x1="18" y1="2" x2="18" y2="12" /><line x1="22" y1="5" x2="22" y2="9" />
      <line x1="26" y1="3" x2="26" y2="11" /><line x1="30" y1="4" x2="30" y2="10" />
      <line x1="34" y1="2" x2="34" y2="12" /><line x1="38" y1="5" x2="38" y2="9" />
      <line x1="42" y1="3" x2="42" y2="11" /><line x1="46" y1="4" x2="46" y2="10" />
      <line x1="50" y1="6" x2="50" y2="8"  /><line x1="54" y1="7" x2="54" y2="7" />
    </svg>
  );
}

/* Live animated waveform — for listening / speaking state */
function IconWaveLive({ bars = 14, height = 16, color = "currentColor" }) {
  const arr = Array.from({ length: bars });
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 2, height }}>
      {arr.map((_, i) => {
        const anim = `hg-wave-${(i % 5) + 1} ${0.7 + (i % 3) * 0.15}s ease-in-out infinite`;
        const delay = `${(i * 0.07) % 0.6}s`;
        return (
          <span key={i} style={{
            display: "inline-block",
            width: 1.5, height,
            background: color,
            transformOrigin: "center",
            animation: anim,
            animationDelay: delay,
            opacity: 0.92,
          }} />
        );
      })}
    </span>
  );
}

/* Status indicator: small filled circle with optional pulse */
function StatusDot({ tone = "live", size = 6 }) {
  const colorMap = {
    live: "var(--hg-ice-bright)",
    success: "var(--hg-fg-0)",
    pending: "var(--hg-ice)",
    error: "var(--hg-warn)",
    warn: "var(--hg-warn)",
    crit: "var(--hg-crit)",
    idle: "var(--hg-fg-3)",
  };
  const isPulse = tone === "live" || tone === "pending";
  return (
    <span
      style={{
        display: "inline-block",
        width: size, height: size,
        borderRadius: "50%",
        background: colorMap[tone],
        boxShadow: tone === "live" ? `0 0 8px var(--hg-ice-glow)` : "none",
        animation: isPulse ? "hg-pulse-dot 1.6s ease-in-out infinite" : "none",
        verticalAlign: "middle",
      }}
    />
  );
}

/* ── Control-card icons (lights + media) ─────────────────────────────── */
function IconBulb({ size = 14, stroke = "currentColor" }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={stroke} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 18h6" />
      <path d="M10 21h4" />
      <path d="M12 3a6 6 0 0 0-3.6 10.8c.5.4.9.9 1 1.6l.1.6h5l.1-.6c.1-.7.5-1.2 1-1.6A6 6 0 0 0 12 3z" />
    </svg>
  );
}

function IconPlay({ size = 14, fill = "currentColor" }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={fill}>
      <path d="M8 5v14l11-7z" />
    </svg>
  );
}

function IconPause({ size = 14, fill = "currentColor" }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={fill}>
      <rect x="6" y="5" width="4" height="14" rx="1" />
      <rect x="14" y="5" width="4" height="14" rx="1" />
    </svg>
  );
}

function IconSkipNext({ size = 14, fill = "currentColor" }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={fill}>
      <path d="M6 5v14l9-7z" />
      <rect x="16" y="5" width="2.5" height="14" rx="1" />
    </svg>
  );
}

function IconSkipPrev({ size = 14, fill = "currentColor" }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={fill}>
      <rect x="5.5" y="5" width="2.5" height="14" rx="1" />
      <path d="M18 5v14l-9-7z" />
    </svg>
  );
}

function IconVolume({ size = 14, stroke = "currentColor" }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={stroke} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 9v6h4l5 4V5L8 9H4z" />
      <path d="M16.5 8.5a5 5 0 0 1 0 7" />
    </svg>
  );
}

function IconSpeaker({ size = 14, stroke = "currentColor" }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={stroke} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="6" y="3" width="12" height="18" rx="2" />
      <circle cx="12" cy="14" r="3.5" />
    </svg>
  );
}

function IconApartment({ size = 15, stroke = "currentColor" }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={stroke} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 10.5 12 4l8 6.5" />
      <path d="M6.5 9.2V20h11V9.2" />
      <path d="M9 20v-6h6v6" />
      <path d="M9.2 10.5h.1M14.7 10.5h.1" />
    </svg>
  );
}

function IconNeuralNetwork({ size = 16, stroke = "currentColor" }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M6.2 7.2 12 5.4l5.8 2.7M6.2 7.2l2.1 7.2M17.8 8.1l-2.1 6.8M8.3 14.4l7.4.5M12 5.4l3.7 9.5M12 5.4 8.3 14.4"
        stroke={stroke}
        strokeWidth="1.05"
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity="0.44"
      />
      <circle cx="12" cy="5.4" r="2.05" fill={stroke} opacity="0.95" />
      <circle cx="6.2" cy="7.2" r="1.45" fill={stroke} opacity="0.68" />
      <circle cx="17.8" cy="8.1" r="1.45" fill={stroke} opacity="0.68" />
      <circle cx="8.3" cy="14.4" r="1.65" fill={stroke} opacity="0.8" />
      <circle cx="15.7" cy="14.9" r="1.65" fill={stroke} opacity="0.8" />
    </svg>
  );
}

Object.assign(window, {
  IconMic, IconSend, IconSun, IconMoon, IconStop, IconLogo,
  IconWaveStatic, IconWaveLive, StatusDot,
  IconBulb, IconPlay, IconPause, IconSkipNext, IconSkipPrev, IconVolume, IconSpeaker,
  IconApartment,
  IconNeuralNetwork,
});

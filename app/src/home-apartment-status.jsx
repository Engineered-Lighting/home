/* APT-S02: the top bar's status popover, one row per system for the home
 * being shown. Every state comes from HomeApartmentStates (APT-F06); this
 * file only presents it and computes no state of its own. */
(function () {
  "use strict";
  const { useEffect, useState } = React;

  const STYLE_ID = "apt-home-status-rows-style";
  const CSS = `
.apt-home-shell .apt-home-status-rows { display: grid; gap: 2px; max-height: min(70vh, 560px); overflow: auto; }
.apt-home-shell .apt-home-status-heading { margin: 0; padding: 6px 10px 4px; font-size: 12.5px; font-weight: 560; color: var(--apt-text); }
.apt-home-shell .apt-home-status-row { display: grid; grid-template-columns: minmax(92px, auto) 1fr; gap: 2px 10px; padding: 8px 10px; border-radius: 8px; font-size: 12.5px; }
.apt-home-shell .apt-home-status-row + .apt-home-status-row { border-top: 1px solid var(--hg-border, rgba(127,127,127,.18)); border-radius: 0; }
.apt-home-shell .apt-home-status-system { color: var(--hg-fg-2); }
.apt-home-shell .apt-home-status-value { min-width: 0; color: var(--apt-text); }
.apt-home-shell .apt-home-status-detail { grid-column: 2; color: var(--hg-fg-2); }
.apt-home-shell .apt-home-status-codes { grid-column: 2; color: var(--hg-fg-2); }
/* The top bar styles its own summary; the nested Details summary opts out. */
.apt-home-shell .apt-home-status .apt-home-status-codes > summary { display: inline-flex; align-items: center; justify-content: flex-start;
  gap: 0; width: auto; min-width: 0; height: auto; min-height: 24px; padding: 0; border: 0; overflow: visible; color: var(--hg-fg-2); cursor: pointer; }
.apt-home-shell .apt-home-status-codes ul { margin: 2px 0 0; padding-left: 16px; }
.apt-home-shell .apt-home-status-codes code { font-family: var(--hg-mono, ui-monospace, monospace); font-size: 11.5px; }
@media (pointer: coarse) { .apt-home-shell .apt-home-status .apt-home-status-codes > summary { min-width: 44px; min-height: 44px; } }
`;
  function ensureStyles() {
    if (typeof document === "undefined" || document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID; style.textContent = CSS;
    document.head.appendChild(style);
  }

  // Re-render when a home's producer report or the vocabulary changes.
  function useReportVersion() {
    const store = window.HomeApartmentStates?.reports;
    const [version, setVersion] = useState(() => store?.version?.() || 0);
    useEffect(() => store?.subscribe?.(setVersion), [store]);
    return version;
  }

  function HomeApartmentStatusRows({ state, homeName }) {
    ensureStyles();
    useReportVersion();
    const States = window.HomeApartmentStates, Badge = window.HomeApartmentUI?.Badge;
    if (!States) return <div role="status"><p>{state?.sourceLabel || "Loading room"}</p></div>;
    const rows = States.statusRows(state);
    const name = homeName || (state?.sites || []).find(site => site.id === state?.siteId)?.shortName || "This home";
    return <div role="status" aria-label={`${name} · what is connected`} data-apt-status-site={state?.siteId || ""}>
      <h2 className="apt-home-status-heading">{name} · what is connected</h2>
      <div className="apt-home-status-rows">
        {rows.map(row => <div className="apt-home-status-row" key={row.key} data-system={row.key}
          data-group={row.group || undefined} data-state={row.state || undefined}>
          <span className="apt-home-status-system">{row.system}</span>
          <span className="apt-home-status-value">{Badge && row.state ? <Badge state={row.state}>{row.title}</Badge> : row.title}</span>
          {row.detail && <span className="apt-home-status-detail">{row.detail}</span>}
          {row.details?.length > 0 && <details className="apt-home-status-codes">
            <summary>Details</summary>
            <ul>{row.details.map(reason => <li key={`${reason.group}:${reason.code}`}>
              {reason.shown} · <code>{reason.code}</code></li>)}</ul>
          </details>}
        </div>)}
      </div>
    </div>;
  }

  window.HomeApartmentStatusRows = HomeApartmentStatusRows;
})();

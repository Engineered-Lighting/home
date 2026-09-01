/* eslint-disable */
/**
 * home-worldstate.jsx — Addendum 27 / F-32: world-state debug drawer.
 *
 * Same right-anchored slide-in pattern as the M5 explain drawer
 * (home-explain.jsx). Reads from the existing
 *   GET /api/extended_openai_conversation/world_state[?room=<name>]
 * endpoint — no new server-side dependencies. Replaces the /world-state
 * slash command's raw-JSON dump with a navigable panel that shows:
 *
 *   • System — frigate online + tracked entity count
 *   • Rooms — per-room: occupied flag, identified persons, perception
 *     summary + age, freshness badge
 *   • People — per-person: currently-seen flag, last room + age, HA
 *     presence location
 *   • Raw JSON — paste-back surface, collapsed by default
 *
 * Refresh button re-fetches. Escape closes. The drawer is read-only
 * — every mutation already has a dedicated path (people overlay
 * for identities, agent tools for actions). This is purely a
 * "what does the agent see RIGHT NOW" inspector.
 */

const { useState, useEffect, useRef, useMemo, useCallback } = React;

const WS_FONT_MONO = '"Geist Mono", "JetBrains Mono", monospace';
const WS_FONT_SANS = '"Geist", "Inter", sans-serif';

// ── Helpers (delegated to home-worldstate-helpers.js, resolved at
//    call time so Babel-async load order doesn't matter — same fix
//    as home-explain.jsx).
function _ageOf(tsIso, nowMs) {
  if (window.wsAgeOf) return window.wsAgeOf(tsIso, nowMs);
  return null;
}
function _fmtAge(sec) {
  if (window.wsFmtAge) return window.wsFmtAge(sec);
  return "—";
}
function _freshnessBand(sec) {
  if (window.wsFreshnessBand) return window.wsFreshnessBand(sec);
  return { label: "—", color: "var(--hg-fg-4)" };
}

// ── Section primitives (mirrors home-explain.jsx) ───────────────────

function WsSection({ title, count, defaultOpen = true, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{
      borderBottom: "1px solid var(--hg-border-soft)",
      padding: "14px 24px",
    }}>
      <button
        onClick={() => setOpen(o => !o)}
        className="hg-focusable"
        style={{
          background: "transparent", border: "none", padding: 0,
          color: "var(--hg-fg-2)",
          fontFamily: WS_FONT_MONO, fontSize: 10,
          letterSpacing: "0.18em", textTransform: "uppercase",
          cursor: "pointer", display: "flex", alignItems: "center", gap: 6,
        }}
      >
        <span style={{ width: "1ch", display: "inline-block" }}>{open ? "▾" : "▸"}</span>
        <span>{title}</span>
        {count !== undefined && (
          <span style={{ color: "var(--hg-fg-4)", marginLeft: 6 }}>· {count}</span>
        )}
      </button>
      {open && <div style={{ marginTop: 12 }}>{children}</div>}
    </div>
  );
}

function _Dot({ color, title }) {
  return (
    <span title={title} style={{
      width: 8, height: 8, borderRadius: 8,
      background: color, display: "inline-block",
      flex: "0 0 auto",
    }} />
  );
}

// ── Per-room row ─────────────────────────────────────────────────────

function RoomRow({ name, room, now }) {
  const occupied = !!room?.occupied;
  const persons = Array.isArray(room?.persons) ? room.persons : [];
  const identifiedPersons = persons.filter(
    (p) => p?.identity && p.identity.name
  );
  const perceptionAge = room?.perception_age_seconds;
  const perceptionBand = _freshnessBand(perceptionAge);
  return (
    <div style={{
      padding: "10px 0",
      borderTop: "1px solid var(--hg-border-soft)",
      fontFamily: WS_FONT_MONO, fontSize: 11.5,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <_Dot
          color={occupied ? "var(--hg-ice)" : "var(--hg-fg-5)"}
          title={occupied ? "occupied" : "empty"}
        />
        <span style={{ color: "var(--hg-fg-0)", fontWeight: 500 }}>{name}</span>
        <span style={{ marginLeft: "auto", color: perceptionBand.color, fontSize: 9, letterSpacing: "0.12em" }}>
          {perceptionAge != null ? `${perceptionBand.label} · ${_fmtAge(perceptionAge)} ago` : ""}
        </span>
      </div>
      {identifiedPersons.length > 0 && (
        <div style={{ marginLeft: 16, marginBottom: 4, display: "flex", gap: 6, flexWrap: "wrap" }}>
          {identifiedPersons.map((p, i) => (
            <span key={i} style={{
              fontFamily: WS_FONT_MONO, fontSize: 10,
              padding: "2px 7px",
              border: "1px solid var(--hg-border-soft)",
              color: "var(--hg-fg-1)",
            }}>
              {p.identity.name}
              {p.identity.confidence != null && (
                <span style={{ color: "var(--hg-fg-4)", marginLeft: 4 }}>
                  · {(p.identity.confidence * 100).toFixed(0)}%
                </span>
              )}
            </span>
          ))}
        </div>
      )}
      {room?.perception_summary && (
        <div style={{
          marginLeft: 16, marginTop: 6,
          fontFamily: WS_FONT_SANS, fontSize: 12, fontStyle: "italic",
          color: "var(--hg-fg-2)",
        }}>
          “{room.perception_summary}”
        </div>
      )}
      {Array.isArray(room?.frigate_labels) && room.frigate_labels.length > 0 && (
        <div style={{ marginLeft: 16, marginTop: 4, color: "var(--hg-fg-4)", fontSize: 10 }}>
          labels: {room.frigate_labels.join(", ")}
        </div>
      )}
    </div>
  );
}

// ── Per-person row ──────────────────────────────────────────────────

function PersonRow({ name, person, now }) {
  const seen = !!person?.currently_seen;
  const ago = _ageOf(person?.last_visual_at, now);
  const band = _freshnessBand(ago);
  return (
    <div style={{
      padding: "8px 0",
      borderTop: "1px solid var(--hg-border-soft)",
      fontFamily: WS_FONT_MONO, fontSize: 11.5,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <_Dot
          color={seen ? "var(--hg-ice)" : "var(--hg-fg-5)"}
          title={seen ? "currently seen" : "not currently seen"}
        />
        <span style={{ color: "var(--hg-fg-0)", fontWeight: 500 }}>{name}</span>
        <span style={{ marginLeft: "auto", color: band.color, fontSize: 9, letterSpacing: "0.12em" }}>
          {ago != null ? `${band.label} · ${_fmtAge(ago)} ago` : ""}
        </span>
      </div>
      <div style={{ marginLeft: 16, marginTop: 3, color: "var(--hg-fg-3)", fontSize: 10.5 }}>
        {person?.last_visual_room && <span>last seen in <span style={{ color: "var(--hg-fg-1)" }}>{person.last_visual_room}</span></span>}
        {person?.last_visual_confidence != null && (
          <span style={{ marginLeft: 8 }}>
            · conf <span style={{ color: "var(--hg-fg-1)" }}>{(person.last_visual_confidence * 100).toFixed(0)}%</span>
          </span>
        )}
        {person?.ha_location && (
          <span style={{ marginLeft: 8 }}>
            · ha <span style={{ color: "var(--hg-fg-1)" }}>{person.ha_location}</span>
          </span>
        )}
      </div>
    </div>
  );
}

// ── Main drawer ─────────────────────────────────────────────────────

// F-32+ (Addendum 27): auto-refresh cadence. 5s feels live without
// hammering the integration; per-fetch is ~50ms typical so cost is
// negligible. Paused when document is hidden (no point polling when
// the user isn't looking).
const WS_AUTO_REFRESH_MS = 5000;

function HomeWorldStateDrawer({ open, onClose, endpoint, token, sim, refreshIntervalMs, initialRoom }) {
  const [data, setData] = useState(null);    // null = loading, {} = response
  const [error, setError] = useState(null);
  // Attempt number while fetchWithRetry is still reconnecting (null = idle).
  const [reconnecting, setReconnecting] = useState(null);
  const [fetchMs, setFetchMs] = useState(null);
  const [now, setNow] = useState(Date.now());
  // F-32+ auto-refresh state: when did the LAST successful fetch
  // complete? When was it last STARTED (for the "refreshing…" badge)?
  // Visibility flag flips on document visibilitychange.
  const [lastFetchAt, setLastFetchAt] = useState(0);
  const [fetchingSince, setFetchingSince] = useState(0);
  const [pageVisible, setPageVisible] = useState(
    typeof document !== "undefined" ? document.visibilityState === "visible" : true
  );
  const fetchAbortRef = useRef(null);
  const intervalMs = refreshIntervalMs || WS_AUTO_REFRESH_MS;
  // Per-room filter (Addendum 27+). When set, the drawer renders only
  // the matching room + the people whose last_visual_room matches.
  // Header shows a chip with × to clear. Initialized from the
  // initialRoom prop on open; cleared when the user clicks ×.
  const [roomFilter, setRoomFilter] = useState(initialRoom || null);
  // Re-sync roomFilter when the drawer is re-opened with a new initialRoom
  // (e.g., /world-state living_room after a prior /world-state kitchen).
  useEffect(() => {
    if (open) setRoomFilter(initialRoom || null);
  }, [open, initialRoom]);

  // Sim fixture path — useful for visual review without HA
  const simFixture = useCallback(() => {
    if (!sim?.active || typeof window.__SIM_WORLD_STATE_FIXTURE !== "function") return null;
    try { return window.__SIM_WORLD_STATE_FIXTURE(); } catch { return null; }
  }, [sim?.active]);

  // F-32+: `silent` mode skips the "null = loading" flash so auto-
  // refreshes don't blank-out the panel every 5 seconds. Manual
  // refresh + initial open keep the loading state for visible feedback.
  const fetchSnapshot = useCallback(async ({ silent = false } = {}) => {
    if (!silent) {
      setData(null);
      setFetchMs(null);
    }
    setError(null);
    setReconnecting(null);
    setNow(Date.now());
    setFetchingSince(Date.now());

    const fixture = simFixture();
    if (fixture) {
      setData(fixture);
      setFetchMs(0);
      setLastFetchAt(Date.now());
      setFetchingSince(0);
      return;
    }

    if (!endpoint || !token) {
      setError("not connected — /connect <url> <token>");
      setData({});
      setFetchingSince(0);
      return;
    }

    const controller = new AbortController();
    fetchAbortRef.current = controller;
    const t0 = Date.now();
    try {
      const base = endpoint.replace(/\/+$/, "");
      const url = `${base}/api/extended_openai_conversation/world_state`;
      // Visible loads (initial/manual) retry through the box-reboot window with
      // a reconnecting banner. Silent auto-refresh ticks stay single-shot so a
      // sustained outage can't stack overlapping retry fans behind the poll.
      const r = await window.fetchWithRetry({
        url,
        options: {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store",
          signal: controller.signal,
        },
        maxAttempts: silent ? 1 : 4,
        onAttempt: ({ attempt, nextDelay }) => {
          if (!silent && nextDelay != null) setReconnecting(attempt);
        },
      });
      setReconnecting(null);
      if (!r.ok) {
        setError(`HTTP ${r.status}`);
        if (!silent) setData({});
        setFetchMs(Date.now() - t0);
        setFetchingSince(0);
        return;
      }
      const payload = await r.json();
      setData(payload);
      setFetchMs(Date.now() - t0);
      setLastFetchAt(Date.now());
      setFetchingSince(0);
    } catch (e) {
      // Our own controller only aborts on unmount cleanup — bail silently.
      // A per-attempt timeout inside fetchWithRetry does NOT abort it, so a
      // genuine outage still surfaces below.
      if (controller.signal.aborted) return;
      setReconnecting(null);
      const status = e && e.lastStatus;
      if (e && e.reason === "http" && status) setError(`HTTP ${status}`);
      else if (e && e.attempts) setError(`world state unreachable after ${e.attempts} ${e.attempts === 1 ? "attempt" : "attempts"}`);
      else setError(e?.message || String(e));
      if (!silent) setData({});
      setFetchMs(Date.now() - t0);
      setFetchingSince(0);
    }
  }, [endpoint, token, simFixture]);

  // Wrapper for the manual refresh button — always non-silent so the
  // user sees the loading state.
  const refreshManual = useCallback(() => fetchSnapshot({ silent: false }), [fetchSnapshot]);

  // Initial fetch on open
  useEffect(() => {
    if (!open) return undefined;
    fetchSnapshot();
    return () => {
      try { fetchAbortRef.current?.abort(); } catch {}
    };
  }, [open, fetchSnapshot]);

  // Overlay layer: topmost-only Escape + focus management via HomeOverlay.
  const wsRootRef = React.useRef(null);
  window.HomeOverlay.useOverlayLayer({
    key: "world",
    active: !!open,
    onEscape: () => { onClose && onClose(); },
    rootRef: wsRootRef,
  });

  // Tick "now" every second so the age badges decay live (cheap;
  // only one timer, only when drawer open).
  useEffect(() => {
    if (!open) return undefined;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [open]);

  // F-32+ (Addendum 27): page-visibility listener — pause auto-refresh
  // when the user tabs away (the data they're not looking at doesn't
  // need to be fresh). Resume on visibility return + force one fetch
  // so the panel is current the moment they tab back in.
  useEffect(() => {
    if (typeof document === "undefined") return undefined;
    const handler = () => {
      const visible = document.visibilityState === "visible";
      setPageVisible(visible);
      if (visible && open) {
        // Force a silent refresh on tab-return so the user doesn't
        // wait up to (intervalMs) for the next normal tick.
        fetchSnapshot({ silent: true });
      }
    };
    document.addEventListener("visibilitychange", handler);
    return () => document.removeEventListener("visibilitychange", handler);
  }, [open, fetchSnapshot]);

  // F-32+: auto-refresh loop. Fires every intervalMs while drawer is
  // open + page is visible. Silent fetch (no loading flash). Uses
  // lastFetchAt to schedule precisely (avoid double-firing when the
  // initial fetch + the interval tick race).
  useEffect(() => {
    if (!open || !pageVisible) return undefined;
    const t = setInterval(() => {
      fetchSnapshot({ silent: true });
    }, intervalMs);
    return () => clearInterval(t);
  }, [open, pageVisible, intervalMs, fetchSnapshot]);

  if (!open) return null;

  const enabled = data?.enabled !== false;
  const rawRooms = data?.rooms || {};
  const rawPeople = data?.people || {};
  const system = data?.system || {};
  // Apply roomFilter: if set, restrict rooms + people. Case-insensitive
  // match against both the exact key and the slug-of-name (so
  // /world-state "Living Room" matches `living_room`). When no match,
  // render empty + a "no match" hint in the section bodies.
  let rooms = rawRooms;
  let people = rawPeople;
  let filterApplied = false;
  let filterMatched = false;
  if (roomFilter) {
    filterApplied = true;
    const normalize = (s) => String(s).toLowerCase().replace(/\s+/g, "_");
    const wanted = normalize(roomFilter);
    const roomKey = Object.keys(rawRooms).find((k) =>
      normalize(k) === wanted || normalize(rawRooms[k]?.name || "") === wanted
    );
    if (roomKey) {
      filterMatched = true;
      rooms = { [roomKey]: rawRooms[roomKey] };
      // Filter people to those whose last_visual_room matches
      people = Object.fromEntries(
        Object.entries(rawPeople).filter(([, p]) =>
          p && normalize(p.last_visual_room || "") === normalize(roomKey)
        )
      );
    } else {
      rooms = {};
      people = {};
    }
  }
  const roomCount = Object.keys(rooms).length;
  const peopleCount = Object.keys(people).length;
  const totalRoomCount = Object.keys(rawRooms).length;
  const totalPeopleCount = Object.keys(rawPeople).length;
  const updatedAge = _ageOf(data?.updated_at, now);

  return (
    <div
      ref={wsRootRef}
      role="dialog"
      aria-modal="false"
      aria-label="World state inspector"
      style={{
        position: "fixed",
        top: 0, right: 0, bottom: 0,
        width: "min(560px, 100vw)",
        background: "var(--hg-bg-0)",
        borderLeft: "1px solid var(--hg-border)",
        boxShadow: "-12px 0 32px rgba(0,0,0,0.3)",
        zIndex: 1100,
        display: "flex", flexDirection: "column",
        fontFamily: WS_FONT_MONO,
        animation: "worldstate-slide-in 220ms cubic-bezier(0.16,1,0.3,1)",
      }}
    >
      <style>{`
        @keyframes worldstate-slide-in {
          from { transform: translateX(100%); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
      `}</style>

      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "14px 24px 12px",
        borderBottom: "1px solid var(--hg-border-soft)",
      }}>
        <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.05 }}>
          <span style={{
            fontFamily: WS_FONT_SANS, fontSize: 15, fontWeight: 500,
            color: "var(--hg-fg-0)", letterSpacing: "-0.02em",
          }}>world state</span>
          <span style={{
            fontSize: 8.5, letterSpacing: "0.24em", fontWeight: 500,
            color: "var(--hg-fg-4)", marginTop: 3,
          }}>rooms · people · perception</span>
        </div>
        <span style={{ marginLeft: "auto", display: "inline-flex", gap: 6, alignItems: "center" }}>
          {/* Per-room filter chip — visible when roomFilter is set.
              × clears the filter back to all-rooms view. Color is ice
              (cool) when the filter matched a real room; warn when
              the typed room doesn't exist in the data (lets the user
              know to widen). */}
          {filterApplied && (
            <span style={{
              display: "inline-flex", alignItems: "center", gap: 4,
              border: `1px solid ${filterMatched ? "var(--hg-ice)" : "var(--hg-warn)"}`,
              color: filterMatched ? "var(--hg-ice)" : "var(--hg-warn)",
              padding: "2px 7px",
              fontSize: 9, letterSpacing: "0.12em",
              textTransform: "lowercase",
              marginRight: 4,
            }}>
              <span>room: {roomFilter}</span>
              <button
                onClick={() => setRoomFilter(null)}
                title="clear room filter"
                style={{
                  background: "transparent", border: "none", padding: "0 2px",
                  color: "inherit", fontFamily: WS_FONT_MONO, fontSize: 11,
                  cursor: "pointer", lineHeight: 1,
                }}
                aria-label="Clear room filter"
              >×</button>
            </span>
          )}
          {/* F-32+ countdown badge — derived from now - lastFetchAt.
              Updates every second via the existing "now" tick. Three
              states (delegated to helper):
                refreshing → "refreshing…"
                paused (tab hidden) → "paused (tab hidden)"
                otherwise → "next in Ns"
              Subtle styling — same fg-4 as fetchMs. */}
          {(() => {
            const refreshing = fetchingSince > 0;
            const paused = !pageVisible;
            const secsLeft = lastFetchAt > 0
              ? Math.max(0, (lastFetchAt + intervalMs - now) / 1000)
              : null;
            const text = window.wsFormatRefreshCountdown
              ? window.wsFormatRefreshCountdown(secsLeft, paused, refreshing)
              : "";
            if (!text || text === "—") return null;
            return (
              <span
                title={`auto-refresh every ${Math.round(intervalMs / 1000)}s while drawer is open + tab visible`}
                style={{
                  color: paused ? "var(--hg-warn)" : "var(--hg-fg-4)",
                  fontSize: 9, letterSpacing: "0.06em", marginRight: 4,
                }}
              >
                {text}
              </span>
            );
          })()}
          {fetchMs != null && (
            <span style={{ color: "var(--hg-fg-4)", fontSize: 10, marginRight: 4 }}>
              {fetchMs}ms
            </span>
          )}
          <button
            onClick={refreshManual}
            title="Refresh now"
            className="hg-focusable"
            style={{
              background: "transparent", border: "1px solid var(--hg-border-soft)",
              color: "var(--hg-fg-3)", padding: "4px 9px",
              fontFamily: WS_FONT_MONO, fontSize: 10, letterSpacing: "0.12em",
              cursor: "pointer", textTransform: "lowercase",
            }}
          >refresh</button>
          <button
            onClick={onClose}
            aria-label="Close"
            className="hg-focusable"
            style={{
              background: "transparent", border: "1px solid var(--hg-border-soft)",
              color: "var(--hg-fg-2)", padding: "4px 11px",
              fontFamily: WS_FONT_MONO, fontSize: 10.5, letterSpacing: "0.12em",
              cursor: "pointer", textTransform: "lowercase",
            }}
          >close · esc</button>
        </span>
      </div>

      {/* Body — `hg-scroll` class applies the themed thin scrollbar. */}
      <div className="hg-scroll" style={{ flex: 1, overflowY: "auto", overflowX: "hidden" }}>
        {/* Loading */}
        {data === null && (
          <div style={{ padding: "40px 24px", textAlign: "center", color: "var(--hg-fg-3)", fontSize: 11 }}>
            loading world state…
          </div>
        )}

        {/* Reconnecting (retrying through a box-reboot window) */}
        {reconnecting != null && !error && (
          <div style={{ padding: "20px 24px", color: "var(--hg-ice)", fontSize: 11 }}>
            reconnecting to world state… waiting for the network (attempt {reconnecting}).
          </div>
        )}

        {/* Error */}
        {error && data !== null && (
          <div style={{
            padding: "20px 24px", color: "var(--hg-crit)", fontSize: 11,
            display: "flex", alignItems: "center", gap: 12,
          }}>
            <span style={{ flex: 1 }}>{error}</span>
            <button
              onClick={refreshManual}
              className="hg-focusable"
              style={{
                background: "transparent", border: "1px solid var(--hg-crit)",
                color: "var(--hg-crit)", padding: "4px 11px",
                fontFamily: WS_FONT_MONO, fontSize: 10, letterSpacing: "0.12em",
                cursor: "pointer", textTransform: "lowercase", whiteSpace: "nowrap",
              }}
            >retry now</button>
          </div>
        )}

        {/* Disabled */}
        {data && data.enabled === false && (
          <div style={{ padding: "30px 24px", textAlign: "center", color: "var(--hg-warn)", fontSize: 11 }}>
            world state disabled
            {data.reason && (
              <div style={{ color: "var(--hg-fg-4)", marginTop: 6, fontSize: 10 }}>
                reason: {data.reason}
              </div>
            )}
          </div>
        )}

        {/* Content (only when enabled + no error) */}
        {data && enabled && !error && (
          <>
            {/* Top meta */}
            <div style={{ padding: "10px 24px 4px", color: "var(--hg-fg-4)", fontSize: 10.5, fontFamily: WS_FONT_MONO }}>
              {updatedAge != null && (
                <span>updated <span style={{ color: "var(--hg-fg-2)" }}>{_fmtAge(updatedAge)} ago</span></span>
              )}
            </div>

            {/* System */}
            <WsSection title="system">
              <div style={{ fontFamily: WS_FONT_MONO, fontSize: 11.5, color: "var(--hg-fg-2)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "3px 0" }}>
                  <_Dot color={system.frigate_online ? "var(--hg-ice)" : "var(--hg-crit)"} />
                  <span>frigate:</span>
                  <span style={{ color: "var(--hg-fg-0)" }}>
                    {system.frigate_online ? "online" : "offline"}
                  </span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "3px 0" }}>
                  <span style={{ width: 8, height: 8, display: "inline-block" }} />
                  <span>tracked entities:</span>
                  <span style={{ color: "var(--hg-fg-0)" }}>{system.tracked_entity_count ?? "—"}</span>
                </div>
              </div>
            </WsSection>

            {/* Rooms — section title reflects filtered/total when filter active */}
            <WsSection
              title="rooms"
              count={filterApplied ? `${roomCount}/${totalRoomCount}` : roomCount}
            >
              {roomCount === 0 ? (
                <div style={{ color: "var(--hg-fg-4)", fontSize: 11, padding: "4px 0" }}>
                  {filterApplied
                    ? `no room matching "${roomFilter}" — clear filter to see all ${totalRoomCount}`
                    : "no rooms tracked"}
                </div>
              ) : (
                <div>
                  {(window.wsSortRoomKeys ? window.wsSortRoomKeys(rooms) : Object.keys(rooms))
                    .map((name) => (
                      <RoomRow key={name} name={name} room={rooms[name]} now={now} />
                    ))}
                </div>
              )}
            </WsSection>

            {/* People */}
            <WsSection
              title="people"
              count={filterApplied ? `${peopleCount}/${totalPeopleCount}` : peopleCount}
            >
              {peopleCount === 0 ? (
                <div style={{ color: "var(--hg-fg-4)", fontSize: 11, padding: "4px 0" }}>
                  {filterApplied
                    ? `no one last-seen in ${roomFilter}`
                    : "no people in cache yet"}
                </div>
              ) : (
                <div>
                  {(window.wsSortPersonKeys ? window.wsSortPersonKeys(people, now) : Object.keys(people))
                    .map((name) => (
                      <PersonRow key={name} name={name} person={people[name]} now={now} />
                    ))}
                </div>
              )}
            </WsSection>

            {/* Raw JSON */}
            <WsSection title="raw json" defaultOpen={false}>
              <pre style={{
                margin: 0, padding: "8px",
                background: "var(--hg-bg-1)",
                border: "1px solid var(--hg-border-soft)",
                fontFamily: WS_FONT_MONO, fontSize: 10,
                color: "var(--hg-fg-2)", whiteSpace: "pre-wrap",
                wordBreak: "break-word", overflowX: "auto",
                maxHeight: 300, overflowY: "auto",
              }}>{JSON.stringify(data, null, 2)}</pre>
            </WsSection>
          </>
        )}
      </div>
    </div>
  );
}

window.HomeWorldStateDrawer = HomeWorldStateDrawer;

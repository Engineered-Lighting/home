/* ============================================================================
 * home-people.jsx — relationship & identity context UI (Addendum 14)
 *
 * Opens as a full-app overlay from a header button. NOT inside the metrics
 * drawer (per AR-13: metrics drawer is for runtime health, not content).
 * Voice replies continue to flow into the underlying chat feed; this
 * overlay shows an "X new messages" pip near the close button when voice
 * activity occurs while open (per AR2-11).
 *
 * Three view modes, switchable via top tab strip:
 *   - graph: radial relationship visualization (Slice 5)
 *   - list:  flat sortable table view (Slice 5+)
 *   - queue: discovery queue of unenrolled/unnamed faces (Slice 4)
 *
 * Data flow: reads identities from HA REST at /api/extended_openai_conversation/identities
 * (registered by the integration in Slice 2.5). Mutations POST/PATCH/DELETE
 * to the same domain. All requests use the HA bearer token from prefs,
 * same pattern as MetricsStrip's metricsBase calls.
 *
 * Disable knob: if the HA integration reports `enabled: false`, the
 * overlay shows a clear "Identity store is disabled" state so the user
 * knows the env flag is set, rather than silently empty.
 * ============================================================================ */

const { useState, useEffect, useRef, useCallback, useMemo } = React;

// The agent authority speaks predicates; this view speaks the legacy
// relationship vocabulary. Map only what is actually defined -- an unmapped
// predicate keeps its own name rather than being coerced into a nearby one,
// because guessing here would misdescribe someone's relationship.
const AGENT_PREDICATE_TO_REL_TYPE = {
  parent_of: "parent",
  partner_of: "partner",
  sibling_of: "sibling",
  friend_of: "friend",
  roommate_of: "roommate",
  neighbor_of: "neighbor",
  colleague_of: "colleague",
};

function agentPredicateToRelType(predicate) {
  return AGENT_PREDICATE_TO_REL_TYPE[predicate] || String(predicate || "").replace(/_of$/, "");
}

// The agent authority does not carry the legacy per-person relationship_type,
// so it is derived from the edges actually recorded against the account holder.
// Anyone with no recorded edge to them stays "unknown" rather than being
// assigned a category nobody asserted.
function deriveRelationshipType(personId, isSelf, edges) {
  if (isSelf) return "me";
  const selfEdge = edges.find(
    (edge) => edge.from_uuid === personId || edge.to_uuid === personId,
  );
  if (!selfEdge) return "unknown";
  switch (selfEdge.rel_type) {
    case "partner": return "partner";
    case "parent": return "family_immediate";
    case "sibling": return "family_immediate";
    case "friend": return "friend";
    case "roommate": return "roommate";
    case "neighbor": return "neighbor";
    case "colleague": return "friend";
    default: return "unknown";
  }
}

// Same-origin: the web gateway serves this app and proxies /api/agent to the
// BFF, so the browser session cookie applies and no CORS is involved.
async function fetchAgentHousehold(signal) {
  const request = (path) =>
    fetch(path, {
      credentials: "include",
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal,
    });

  const [householdResponse, relationshipsResponse] = await Promise.all([
    request("/api/agent/v1/household"),
    request("/api/agent/v1/relationships"),
  ]);
  if (!householdResponse.ok) {
    const error = new Error(`agent_household_${householdResponse.status}`);
    error.status = householdResponse.status;
    throw error;
  }
  const household = await householdResponse.json();
  // Relationships are perspective-filtered and may legitimately be refused
  // while the roster is readable. An empty edge list is a valid household.
  const relationships = relationshipsResponse.ok
    ? await relationshipsResponse.json()
    : { relationships: [] };

  const edges = (relationships.relationships || []).map((edge) => ({
    id: edge.fact_id,
    from_uuid: edge.subject_person_id,
    to_uuid: edge.object_person_id,
    rel_type: agentPredicateToRelType(edge.predicate),
    status: "active",
  }));

  const identities = (household.people || []).map((person) => ({
    uuid: person.person_id,
    display_name: person.display_name,
    pronouns: person.pronouns || null,
    relationship_type: deriveRelationshipType(
      person.person_id, person.is_self === true, edges,
    ),
    relationship_subrole: null,
    // The agent authority carries no enrolment or face data. Declaring these
    // empty keeps the avatar and queue paths from treating absence as
    // "not loaded yet" and retrying against a store that cannot answer.
    enrollment_count: 0,
    avatar_present: false,
    source: "agent_authority",
  }));

  return { identities, edges };
}



const PEOPLE_FONT_MONO = "'Geist Mono', ui-monospace, monospace";
const PEOPLE_FONT_SANS = "'Geist', system-ui, sans-serif";
const EMPTY_PEOPLE_MAP = Object.freeze({});
const EMPTY_FACES_STATUS = Object.freeze({ state: "idle" });

function peopleCredentialScopeKey(open, endpoint, token) {
  // This value is never logged or persisted. It exists only to synchronously
  // invalidate work started by a prior credential set before React effects run.
  return JSON.stringify([
    !!open,
    String(endpoint || "").replace(/\/+$/, ""),
    String(token || ""),
  ]);
}

function peoplePrincipalScopedValue(
  currentScopeKey,
  valueScopeKey,
  value,
  fallback,
) {
  return currentScopeKey === valueScopeKey ? value : fallback;
}

function abortPeopleOperationState(state) {
  if (!state?.controllers) return;
  for (const controller of state.controllers.values()) {
    try { controller.abort(); } catch {}
  }
  state.controllers.clear();
}

function usePeopleOperationGuard(scopeKey) {
  const stateRef = useRef(null);
  if (!stateRef.current || stateRef.current.scopeKey !== scopeKey) {
    abortPeopleOperationState(stateRef.current);
    stateRef.current = {
      scopeKey,
      controllers: new Map(),
    };
  }

  useEffect(() => () => abortPeopleOperationState(stateRef.current), []);

  return useCallback((channel) => {
    const state = stateRef.current;
    const key = String(channel || "default");
    const previous = state.controllers.get(key);
    if (previous) {
      try { previous.abort(); } catch {}
    }
    const controller = new AbortController();
    state.controllers.set(key, controller);
    let finished = false;
    return {
      signal: controller.signal,
      isCurrent: () => (
        !finished
        && !controller.signal.aborted
        && stateRef.current === state
        && state.scopeKey === scopeKey
      ),
      finish: () => {
        if (finished) return;
        finished = true;
        if (state.controllers.get(key) === controller) {
          state.controllers.delete(key);
        }
      },
      cancel: () => {
        try { controller.abort(); } catch {}
        if (state.controllers.get(key) === controller) {
          state.controllers.delete(key);
        }
        finished = true;
      },
    };
  }, [scopeKey]);
}

function peopleOperationAborted(error, operation) {
  return !operation.isCurrent()
    || error?.name === "AbortError"
    || error?.reason === "abort";
}

function usePeopleViewport() {
  const read = useCallback(() => {
    if (typeof window === "undefined") return { width: 1024, height: 768, mobile: false };
    const vv = window.visualViewport;
    const width = Math.round(vv?.width || window.innerWidth || 1024);
    const height = Math.round(vv?.height || window.innerHeight || 768);
    return { width, height, mobile: width < 700 };
  }, []);
  const [viewport, setViewport] = useState(read);
  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    let raf = null;
    const update = () => {
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => setViewport(read()));
    };
    window.addEventListener("resize", update);
    window.visualViewport?.addEventListener("resize", update);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      window.removeEventListener("resize", update);
      window.visualViewport?.removeEventListener("resize", update);
    };
  }, [read]);
  return viewport;
}

function clampPeopleGraphCamera(camera, viewBox, isMobile) {
  const maxScale = isMobile ? 3 : 2.4;
  const scale = Math.max(1, Math.min(maxScale, Number(camera?.scale) || 1));
  if (!viewBox || !Number.isFinite(viewBox.w) || !Number.isFinite(viewBox.h)) {
    return { scale, x: 0, y: 0 };
  }
  const visibleW = viewBox.w / scale;
  const visibleH = viewBox.h / scale;
  const maxX = Math.max(0, (viewBox.w - visibleW) / 2);
  const maxY = Math.max(0, (viewBox.h - visibleH) / 2);
  return {
    scale,
    x: Math.max(-maxX, Math.min(maxX, Number(camera?.x) || 0)),
    y: Math.max(-maxY, Math.min(maxY, Number(camera?.y) || 0)),
  };
}

function peopleGraphCameraViewBox(viewBox, camera) {
  const scale = Math.max(1, Number(camera?.scale) || 1);
  const visibleW = viewBox.w / scale;
  const visibleH = viewBox.h / scale;
  return {
    x: viewBox.x + (viewBox.w - visibleW) / 2 + (Number(camera?.x) || 0),
    y: viewBox.y + (viewBox.h - visibleH) / 2 + (Number(camera?.y) || 0),
    w: visibleW,
    h: visibleH,
  };
}

/* ─────────────────────────────────────────────────────────────────────
 * Sub-component: HomePeopleOverlay
 *
 * Full-app slide-in overlay. Receives `open` + `onClose` from
 * home-app.jsx + an endpoint/token pair for HA REST calls.
 * ──────────────────────────────────────────────────────────────────── */
function HomePeopleOverlay({ open, onClose, endpoint, token, client = null, connection = null, sim, spatialMode = false }) {
  const credentialScopeKey = peopleCredentialScopeKey(open, endpoint, token);
  const principalDataScopeRef = useRef(null);
  const [view, setView] = useState("graph");   // graph | list | queue
  const [storedIdentities, setIdentities] = useState(null);  // null=loading, []=empty
  const [error, setError] = useState(null);
  // Attempt number while fetchWithRetry is still reconnecting (null = idle).
  // Distinct from `error`, which is the terminal state after retries are spent.
  const [reconnecting, setReconnecting] = useState(null);
  const [loadedAt, setLoadedAt] = useState(null);
  const [storedSelectedUuid, setSelectedUuid] = useState(null);
  // F-4b: backend reports {ready:false, setup_error:"..."} when the
  // SQLite open failed at integration setup. Different from `error`
  // (which is a network-side failure) — `notReady` means HA is
  // reachable but the identity_store didn't initialize.
  const [notReady, setNotReady] = useState(null);
  const [agentEdges, setAgentEdges] = useState([]);
  // Set only from the authenticated HA identity-list response. The legacy
  // People UI becomes read-only only after the explicit E4 SQLite fence is
  // present and verified server-side.
  const [storedLegacyBoundary, setLegacyBoundary] = useState(null);
  // The authenticated, typed Frigate faces operation returns metadata only.
  // Legacy face-crop bytes are intentionally not loaded into the browser.
  const [storedFacesByPerson, setFacesByPerson] = useState(null);
  const [storedFacesStatus, setFacesStatus] = useState(EMPTY_FACES_STATUS);
  const [storedFrigateDiagnostics, setFrigateDiagnostics] = useState(null);
  // Addendum 24 Phase 3: HEAD-pre-check map per identity uuid → boolean.
  // AR24-9: SVG <image onError> can't distinguish 404 from network blip,
  // so we pre-fetch presence once and render <image> only when truthy.
  // Cache-bust string bumps on successful upload (AR24-8) so re-renders
  // pull the new bytes through WebView2's cache.
  const [storedAvatarPresence, setAvatarPresence] = useState(EMPTY_PEOPLE_MAP);
  const [avatarCacheBust, setAvatarCacheBust] = useState(null);
  // Post-Phase-3 fix: SVG <image href="..."> and HTML <img src="...">
  // cannot send `Authorization: Bearer <token>` headers. Our AvatarView
  // requires auth → unauthenticated GETs return 401 → browser shows a
  // broken-image placeholder. Fix: fetch the bytes WITH the auth header
  // via tauriFetch, convert to a blob URL, store per-uuid, render the
  // blob URL as the src/href. Revoke on unmount or when bytes change.
  const [storedAvatarBlobUrls, setAvatarBlobUrls] = useState(EMPTY_PEOPLE_MAP);
  const avatarBlobUrlsRef = useRef(EMPTY_PEOPLE_MAP);
  // Track in-flight fetches so we don't double-fire for the same
  // (uuid, cacheBust) tuple. Keyed by `${uuid}:${cb}`.
  const blobFetchInFlightRef = useRef({});
  const requestGenerationRef = useRef(0);
  const beginPeopleOperation = usePeopleOperationGuard(credentialScopeKey);
  const identities = peoplePrincipalScopedValue(
    credentialScopeKey, principalDataScopeRef.current, storedIdentities, null,
  );
  const selectedUuid = peoplePrincipalScopedValue(
    credentialScopeKey, principalDataScopeRef.current, storedSelectedUuid, null,
  );
  const legacyBoundary = peoplePrincipalScopedValue(
    credentialScopeKey, principalDataScopeRef.current, storedLegacyBoundary, null,
  );
  const facesByPerson = peoplePrincipalScopedValue(
    credentialScopeKey, principalDataScopeRef.current, storedFacesByPerson, null,
  );
  const facesStatus = peoplePrincipalScopedValue(
    credentialScopeKey,
    principalDataScopeRef.current,
    storedFacesStatus,
    EMPTY_FACES_STATUS,
  );
  const frigateDiagnostics = peoplePrincipalScopedValue(
    credentialScopeKey,
    principalDataScopeRef.current,
    storedFrigateDiagnostics,
    null,
  );
  const avatarPresence = peoplePrincipalScopedValue(
    credentialScopeKey,
    principalDataScopeRef.current,
    storedAvatarPresence,
    EMPTY_PEOPLE_MAP,
  );
  const avatarBlobUrls = peoplePrincipalScopedValue(
    credentialScopeKey,
    principalDataScopeRef.current,
    storedAvatarBlobUrls,
    EMPTY_PEOPLE_MAP,
  );
  avatarBlobUrlsRef.current = storedAvatarBlobUrls;
  // Revoke ALL blob URLs when the overlay closes — prevents memory leak
  // for users who open + close repeatedly.
  useEffect(() => {
    return () => {
      Object.values(avatarBlobUrlsRef.current).forEach((u) => {
        if (u) try { URL.revokeObjectURL(u); } catch {}
      });
    };
  }, []);
  // Notifier function the detail panel calls after a successful upload
  // or delete. `present` is the truth state we already know from the
  // POST/DELETE result, so we set avatarPresence directly + bump the
  // cache-buster. No probe needed — the server already told us. Also
  // revoke any existing blob URL for this uuid so the next render
  // triggers a re-fetch.
  const refreshAvatars = useCallback((uuid, present) => {
    setAvatarCacheBust(Date.now());
    if (uuid) {
      setAvatarPresence((prev) => ({ ...prev, [uuid]: !!present }));
      setAvatarBlobUrls((prev) => {
        if (prev[uuid]) {
          try { URL.revokeObjectURL(prev[uuid]); } catch {}
        }
        const { [uuid]: _gone, ...rest } = prev;
        return rest;
      });
    }
  }, []);

  // Overlay layer: topmost-only Escape + Tab trap + focus-in/restore via
  // HomeOverlay (no per-overlay window listener — stacked layers like the
  // detail panel and avatar modal each claim their own Escape press).
  const peopleRootRef = useRef(null);
  window.HomeOverlay.useOverlayLayer({
    key: "people-root",
    active: !!open,
    onEscape: () => onClose(),
    rootRef: peopleRootRef,
    trap: true,
    initialFocus: "root",
  });

  // Initial load + refresh on open. Sim mode short-circuits to fixture data.
  const refresh = useCallback(async () => {
    const operation = beginPeopleOperation("identity-refresh");
    const generation = ++requestGenerationRef.current;
    const isCurrent = () => (
      operation.isCurrent()
      && requestGenerationRef.current === generation
    );
    const publishCurrentScope = () => {
      if (!isCurrent()) return false;
      principalDataScopeRef.current = credentialScopeKey;
      return true;
    };
    principalDataScopeRef.current = null;
    setError(null);
    setNotReady(null);
    setReconnecting(null);
    // Never render a prior principal's identities, faces, or authorization
    // decision while a fresh HA-admin request is pending.
    setIdentities(null);
    setLegacyBoundary(null);
    setFrigateDiagnostics(null);
    setFacesByPerson(null);
    setFacesStatus({ state: "idle" });
    setAvatarPresence({});
    setSelectedUuid(null);
    setAvatarBlobUrls((previous) => {
      Object.values(previous).forEach((url) => {
        if (url) try { URL.revokeObjectURL(url); } catch {}
      });
      return {};
    });
    if (sim?.active) {
      // AR16-3 guard: sim mode never shows the ready:false banner — it
      // would conflict with sim's own fixture data shape.
      const simIdentities = (sim.snapshot?.people?.identities) || [];
      if (!publishCurrentScope()) return;
      setIdentities(simIdentities);
      setLegacyBoundary(null);
      setFacesByPerson(null);
      setFacesStatus({ state: "sim" });
      setFrigateDiagnostics(null);
      setLoadedAt(Date.now());
      operation.finish();
      return;
    }
    if (!endpoint || !token) {
      if (!publishCurrentScope()) return;
      setError("HA endpoint or token not configured");
      setIdentities([]);
      operation.finish();
      return;
    }
    try {
      const url = `${endpoint.replace(/\/+$/, "")}/api/extended_openai_conversation/identities`;
      // fetchWithRetry rides out the AI-box reboot window: while it is still
      // retrying we surface a "Reconnecting…" banner (via onAttempt); it only
      // throws once retries are exhausted or the status is non-retryable.
      const resp = await window.fetchWithRetry({
        url,
        options: {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store",
          signal: operation.signal,
        },
        timeoutMs: 20000,
        maxAttempts: 4,
        onAttempt: ({ attempt, nextDelay }) => {
          if (nextDelay != null && isCurrent()) setReconnecting(attempt);
        },
      });
      if (!isCurrent()) return;
      setReconnecting(null);
      if (!resp.ok) {
        // fetchWithRetry throws rather than resolving non-ok, so this is a
        // defensive fallback only.
        if (!publishCurrentScope()) return;
        setError(`HTTP ${resp.status} ${resp.statusText}`);
        setIdentities([]);
        return;
      }
      const payload = await resp.json();
      if (!isCurrent()) return;
      if (payload.enabled === false) {
        if (!publishCurrentScope()) return;
        setError("Identity store disabled (EXTENDED_OPENAI_IDENTITY_STORE=off)");
        setIdentities([]);
        return;
      }
      // F-4b: backend reports {ready:false, setup_error} when the
      // identity_store SQLite open failed. Surface explicitly so the
      // user knows it's a setup problem, not "no identities yet".
      if (payload.ready === false) {
        // The legacy store is fenced by the E4 cutover, so it can no longer
        // answer. The same household is readable from the agent authority,
        // which is where these records were migrated; fall back to it rather
        // than rendering an empty map.
        try {
          const fromAgent = await fetchAgentHousehold(operation.signal);
          if (!isCurrent()) return;
          if (!publishCurrentScope()) return;
          setNotReady(null);
          setAgentEdges(fromAgent.edges);
          setIdentities(fromAgent.identities);
          setLegacyBoundary(payload.legacy_identity_boundary || null);
          setLoadedAt(Date.now());
          return;
        } catch (agentError) {
          if (peopleOperationAborted(agentError, operation)) return;
          // Report the legacy failure, not the fallback's: the fenced store is
          // the actual condition, and the agent read failing on top of it is a
          // second symptom rather than the cause.
          if (!publishCurrentScope()) return;
          setNotReady({
            error: payload.setup_error || "identity_store initialization failed",
            fallback: agentError.message || String(agentError),
          });
          setAgentEdges([]);
          setIdentities([]);
          return;
        }
      }
      if (!publishCurrentScope()) return;
      setAgentEdges([]);
      setIdentities(payload.identities || []);
      setLegacyBoundary(payload.legacy_identity_boundary || null);
      setFrigateDiagnostics({
        seedReport: payload.frigate_seed_report || null,
        capabilities: payload.frigate_capabilities || null,
      });
      setLoadedAt(Date.now());
      // Fetch only the exact typed metadata operation. The browser never
      // receives a Frigate base URL and never loads crop bytes directly.
      if (payload.frigate_faces_available === true) {
        setFacesStatus({ state: "loading" });
        const proxyUrl = `${endpoint.replace(/\/+$/, "")}/api/extended_openai_conversation/frigate_proxy/faces`;
        try {
          const fresp = await window.tauriFetch(proxyUrl, {
            headers: { Authorization: `Bearer ${token}` },
            cache: "no-store",
            signal: operation.signal,
          });
          if (!isCurrent()) return;
          if (fresp.ok) {
            const fpayload = await fresp.json();
            if (!isCurrent()) return;
            if (fpayload && typeof fpayload === "object") {
              setFacesByPerson(fpayload);
              setFacesStatus({
                state: "loaded",
                bucketCount: Object.keys(fpayload).length,
                loadedAt: Date.now(),
              });
            }
          } else {
            setFacesByPerson(null);
            setFacesStatus({ state: "error", error: `HTTP ${fresp.status}` });
          }
        } catch (e) {
          if (!isCurrent()) return;
          // Proxy unreachable — gallery shows empty state.
          setFacesByPerson(null);
          setFacesStatus({ state: "error", error: e?.message || String(e) });
        }
      } else {
        setFacesByPerson(null);
        setFacesStatus({ state: "missing_url" });
      }
    } catch (e) {
      if (!isCurrent()) return;
      if (!publishCurrentScope()) return;
      setReconnecting(null);
      const status = e && e.lastStatus;
      if (status === 401 || status === 403) {
        setError("Home Assistant administrator access required");
      } else if (e && e.reason === "http" && status) {
        setError(`HTTP ${status}`);
      } else if (e && e.attempts) {
        setError(`Identity store unreachable after ${e.attempts} ${e.attempts === 1 ? "attempt" : "attempts"}.`);
      } else {
        setError(`Network error: ${e.message || e}`);
      }
      setIdentities([]);
    } finally {
      operation.finish();
    }
    // NOTE: `sim?.snapshot` deliberately omitted — it's an object reference
    // recreated on every parent render, so including it would make `refresh`
    // unstable, which would re-trigger the WS subscriber effect below on
    // every render → drawer flickers because the detail-panel useEffect
    // also depends on `refresh`. The sim snapshot is read at call time from
    // the closure, so removing it from deps is safe; we just don't auto-
    // refresh when the snapshot mutates without sim.active toggling.
  }, [
    endpoint,
    token,
    sim?.active,
    beginPeopleOperation,
    credentialScopeKey,
  ]);

  const legacyFrozenPendingCutover =
    legacyBoundary?.semantic_write_fence_installed === true;
  const legacyMutationsReadOnly =
    !sim?.active && !(
      legacyBoundary?.state === "legacy_migration_only"
      && legacyBoundary?.semantic_writes_frozen === false
    );

  useEffect(() => {
    if (legacyMutationsReadOnly && view === "queue") setView("graph");
  }, [legacyMutationsReadOnly, view]);

  // Closing the private surface or changing credentials clears every
  // principal-bearing value. There is intentionally no in-memory/localStorage
  // People cache and no stale-while-revalidate behavior.
  useEffect(() => {
    principalDataScopeRef.current = null;
    requestGenerationRef.current += 1;
    setIdentities(null);
    setLegacyBoundary(null);
    setSelectedUuid(null);
    setFacesByPerson(null);
    setFrigateDiagnostics(null);
    setAvatarPresence({});
    setAvatarBlobUrls((previous) => {
      Object.values(previous).forEach((url) => {
        if (url) try { URL.revokeObjectURL(url); } catch {}
      });
      return {};
    });
  }, [open, endpoint, token]);

  useEffect(() => {
    if (open) refresh();
  }, [open, refresh]);

  // Phase 3 avatar pre-check (AR24-9). One HEAD per identity at load
  // time + after every identity_mutation refresh. Sim mode skips —
  // sim fixtures don't have avatar files on disk.
  useEffect(() => {
    if (!open) return;
    if (sim?.active) return;
    if (!identities || identities.length === 0) return;
    if (!endpoint || !token) return;
    let cancelled = false;
    const generation = requestGenerationRef.current;
    const operation = beginPeopleOperation("avatar-presence");
    (async () => {
      const next = {};
      for (const i of identities) {
        if (cancelled) return;
        try {
          const url = `${endpoint.replace(/\/+$/, "")}/api/extended_openai_conversation/identity/${encodeURIComponent(i.uuid)}/avatar`;
          const resp = await window.tauriFetch(url, {
            method: "HEAD",
            headers: { Authorization: `Bearer ${token}` },
            cache: "no-store",
            signal: operation.signal,
          });
          if (
            cancelled
            || !operation.isCurrent()
            || requestGenerationRef.current !== generation
          ) return;
          next[i.uuid] = resp.ok;
        } catch {
          next[i.uuid] = false;
        }
      }
      if (
        !cancelled
        && operation.isCurrent()
        && requestGenerationRef.current === generation
      ) setAvatarPresence(next);
    })().finally(operation.finish);
    return () => {
      cancelled = true;
      operation.cancel();
    };
  }, [open, sim?.active, identities, endpoint, token, beginPeopleOperation]);

  // Avatar blob fetcher — for every identity where avatarPresence is
  // true AND we don't yet have a blob URL (or cacheBust changed), fetch
  // the bytes with auth + create a blob URL. Stored per-uuid; revoked
  // on cache-bust replacement or overlay unmount.
  useEffect(() => {
    if (!open) return;
    if (sim?.active) return;
    if (!endpoint || !token) return;
    const ids = identities || [];
    let cancelled = false;
    const generation = requestGenerationRef.current;
    const operation = beginPeopleOperation("avatar-blobs");
    // CRITICAL: avatarBlobUrls is INTENTIONALLY NOT in the dep list. If
    // it were, every setAvatarBlobUrls call (one per identity fetched)
    // would re-run this effect, run cleanup → cancelled=true → and the
    // in-flight fetch for the NEXT identity (the one being awaited)
    // would return early without setting its blob URL. The inflight ref
    // would clear in finally, but no further state change would re-trigger
    // the effect, so the last identity in iteration order (Marcelo, the
    // 'me') would never get its blob URL. The closure captures
    // avatarBlobUrls at effect-start; the inner `if (avatarBlobUrls[i.uuid])`
    // check is correct for sequential iteration since we only move
    // forward through identities and never re-visit. We rely on
    // avatarPresence + avatarCacheBust to trigger re-runs (the legitimate
    // signals that more fetching may be needed).
    (async () => {
      for (const i of ids) {
        if (cancelled) return;
        if (!avatarPresence[i.uuid]) continue;
        const inflightKey = `${generation}:${i.uuid}:${avatarCacheBust || "init"}`;
        if (blobFetchInFlightRef.current[inflightKey]) continue;
        if (avatarBlobUrls[i.uuid]) continue;
        blobFetchInFlightRef.current[inflightKey] = true;
        try {
          const url = `${endpoint.replace(/\/+$/, "")}/api/extended_openai_conversation/identity/${encodeURIComponent(i.uuid)}/avatar`;
          const resp = await window.tauriFetch(url, {
            headers: { Authorization: `Bearer ${token}` },
            cache: "no-store",
            signal: operation.signal,
          });
          if (
            cancelled
            || !operation.isCurrent()
            || requestGenerationRef.current !== generation
          ) return;
          if (!resp.ok) continue;
          const blob = await resp.blob();
          if (
            cancelled
            || !operation.isCurrent()
            || requestGenerationRef.current !== generation
          ) return;
          const blobUrl = URL.createObjectURL(blob);
          setAvatarBlobUrls((prev) => {
            if (prev[i.uuid]) {
              try { URL.revokeObjectURL(prev[i.uuid]); } catch {}
            }
            return { ...prev, [i.uuid]: blobUrl };
          });
        } catch {
          // Network failure — leave avatarBlobUrls untouched; next
          // legitimate trigger (presence/cacheBust change) retries.
        } finally {
          delete blobFetchInFlightRef.current[inflightKey];
        }
      }
    })().finally(operation.finish);
    return () => {
      cancelled = true;
      operation.cancel();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, sim?.active, identities, endpoint, token, avatarPresence, avatarCacheBust, beginPeopleOperation]);

  // F-5b: subscribe to the integration's `identity_mutation` HA bus
  // event. Any create/update/delete on the HA side (made via curl, via
  // the LLM, by the auto-seed, OR by another instance of this app)
  // fires the event; we debounce-refresh in ~500 ms so the UI stays
  // synced without thrashing on bulk operations.
  // Pattern mirrors Addendum 1's routing-decision subscriber.
  useEffect(() => {
    if (!open || sim?.active) return undefined;
    if (connection != null && connection !== "online") return undefined;
    const eventClient = client || ((typeof window !== "undefined") && window.__hav_haClient);
    if (!eventClient || typeof eventClient.subscribeEvents !== "function") return undefined;
    let pending = null;
    const scheduleRefresh = () => {
      if (pending) clearTimeout(pending);
      pending = setTimeout(() => { pending = null; refresh(); }, 500);
    };
    let unsub;
    try {
      unsub = eventClient.subscribeEvents(
        "extended_openai_conversation.identity_mutation",
        scheduleRefresh,
      );
    } catch (e) {
      return undefined;
    }
    return () => {
      if (pending) clearTimeout(pending);
      try { unsub && unsub(); } catch {}
    };
  }, [open, sim?.active, connection, client, refresh]);

  if (!open) return null;

  const overlay = (
    <div
      ref={peopleRootRef}
      data-theme={spatialMode ? "dark" : undefined}
      style={{
        position: "fixed",
        ...(spatialMode
          ? {
              left: 82,
              right: "calc(clamp(352px, 19vw, 388px) + 18px)",
              top: 82,
              bottom: 46,
              border: "1px solid rgba(184,216,255,0.075)",
              borderRadius: 7,
              background: "rgba(5,7,11,0.86)",
              backdropFilter: "blur(16px)",
              boxShadow: "0 18px 56px rgba(0,0,0,0.36)",
            }
          : { inset: 0, background: "var(--hg-bg-0)" }),
        zIndex: 1000,
        display: "flex", flexDirection: "column",
        fontFamily: PEOPLE_FONT_MONO,
        animation: "people-fade-in 220ms cubic-bezier(0.16,1,0.3,1)",
        overflow: "hidden",
      }}
      role="dialog"
      aria-modal="true"
      aria-label="People — relationships and identities"
    >
      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", gap: 12,
        padding: spatialMode ? "12px 18px 11px" : "16px 24px 14px",
        borderBottom: "1px solid var(--hg-border-soft)",
      }}>
        <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.05 }}>
          <span style={{
            fontFamily: PEOPLE_FONT_SANS, fontSize: spatialMode ? 15 : 17, fontWeight: 500,
            color: "var(--hg-fg-0)", letterSpacing: "-0.02em",
          }}>people</span>
          <span style={{
            fontSize: 10.5, letterSpacing: "0.24em", fontWeight: 500,
            color: "var(--hg-fg-3)", marginTop: 3,
          }}>identities · relationships · preferences</span>
        </div>
        <span style={{ marginLeft: "auto", display: "inline-flex", gap: 8, alignItems: "center" }}>
          <button
            onClick={refresh}
            title="Refresh"
            className="hg-focusable hg-mobile-touch"
            style={{
              background: "transparent", border: "1px solid var(--hg-border-soft)",
              color: "var(--hg-fg-3)", padding: "4px 9px",
              fontFamily: PEOPLE_FONT_MONO, fontSize: 10, letterSpacing: "0.12em",
              cursor: "pointer", textTransform: "lowercase",
            }}
          >refresh</button>
          <button
            onClick={onClose}
            aria-label="Close"
            className="hg-focusable hg-mobile-touch"
            style={{
              background: "transparent", border: "1px solid var(--hg-border-soft)",
              color: "var(--hg-fg-2)", padding: "4px 11px",
              fontFamily: PEOPLE_FONT_MONO, fontSize: 11, letterSpacing: "0.12em",
              cursor: "pointer", textTransform: "lowercase",
            }}
          >close · esc</button>
        </span>
      </div>

      {/* View tabs */}
      <div style={{
        display: "flex", gap: 0, padding: "0 24px",
        borderBottom: "1px solid var(--hg-border-soft)",
        fontFamily: PEOPLE_FONT_MONO,
      }}>
        {(legacyMutationsReadOnly ? ["graph", "list"] : ["graph", "list", "queue"]).map((v) => (
          <button
            key={v}
            className="hg-mobile-touch"
            onClick={() => setView(v)}
            style={{
              background: "transparent", border: "none",
              padding: "10px 16px",
              fontFamily: PEOPLE_FONT_MONO,
              fontSize: 11,
              letterSpacing: "0.16em",
              textTransform: "lowercase",
              color: view === v ? "var(--hg-fg-0)" : "var(--hg-fg-3)",
              borderBottom: view === v ? "1px solid var(--hg-fg-0)" : "1px solid transparent",
              cursor: "pointer",
              marginBottom: "-1px",
            }}
          >{v}</button>
        ))}
        <span style={{ flex: 1 }} />
        {loadedAt && (
          <span style={{
            display: "inline-flex", alignItems: "center",
            fontSize: 10.5, letterSpacing: "0.18em", textTransform: "uppercase",
            color: "var(--hg-fg-3)",
          }}>
            {identities ? `${identities.length} identities` : "loading"}
          </span>
        )}
      </div>

      {/* Body */}
      <div className="hg-scroll" style={{
        flex: 1, overflow: "auto", padding: "24px 32px",
      }}>
        {reconnecting != null && !error && (
          <div role="status" style={{
            border: "1px solid var(--hg-ice)",
            background: "color-mix(in oklab, var(--hg-ice) 6%, transparent)",
            padding: "10px 14px",
            color: "var(--hg-ice)",
            fontSize: 11, letterSpacing: "0.04em",
            marginBottom: 16,
          }}>
            <strong style={{ marginRight: 8 }}>reconnecting to identity store…</strong>
            waiting for the network (attempt {reconnecting}).
          </div>
        )}
        {error && (
          <div role="alert" style={{
            border: "1px solid var(--hg-warn)",
            background: "color-mix(in oklab, var(--hg-warn) 6%, transparent)",
            padding: "10px 14px",
            color: "var(--hg-warn)",
            fontSize: 11, letterSpacing: "0.04em",
            marginBottom: 16,
            display: "flex", alignItems: "center", gap: 12,
          }}>
            <span style={{ flex: 1 }}>
              <strong style={{ marginRight: 8 }}>error:</strong>{error}
            </span>
            <button
              onClick={() => refresh()}
              className="hg-focusable hg-mobile-touch"
              style={{
                background: "transparent", border: "1px solid var(--hg-warn)",
                color: "var(--hg-warn)", padding: "4px 11px",
                fontFamily: PEOPLE_FONT_MONO, fontSize: 10, letterSpacing: "0.12em",
                cursor: "pointer", textTransform: "lowercase", whiteSpace: "nowrap",
              }}
            >retry now</button>
          </div>
        )}
        {notReady && (
          <div style={{
            border: "1px solid var(--hg-crit)",
            background: "color-mix(in oklab, var(--hg-crit) 6%, transparent)",
            padding: "10px 14px",
            color: "var(--hg-crit)",
            fontSize: 11, letterSpacing: "0.04em",
            marginBottom: 16,
          }}>
            <strong style={{ marginRight: 8 }}>identity store not ready:</strong>
            {notReady.error}
            <div style={{
              marginTop: 6, fontSize: 10, color: "var(--hg-fg-3)",
              letterSpacing: 0,
            }}>
              Check Home Assistant logs: <code>ha core logs | grep identity_store</code>.
              The integration loaded but the SQLite database did not open.
            </div>
          </div>
        )}
        {legacyFrozenPendingCutover && !notReady && (
          <div style={{
            border: "1px solid var(--hg-border-soft)",
            background: "var(--hg-bg-1)",
            padding: "10px 14px",
            color: "var(--hg-fg-2)",
            fontSize: 11, letterSpacing: "0.04em",
            marginBottom: 16,
          }}>
            <strong style={{ marginRight: 8 }}>legacy read-only · cutover pending</strong>
            This verified legacy People view is read-only. The replacement
            authority has not been promoted by this boundary. Semantic identity,
            relationship, preference, privacy, profile, and avatar edits are
            closed here.
          </div>
        )}
        {identities === null && !error && !notReady && reconnecting == null && (
          <div style={{ color: "var(--hg-fg-3)", fontSize: 11 }}>loading identities…</div>
        )}
        {identities !== null && (
          <>
            {view === "graph" && (
              <PeopleGraphView
                identities={identities}
                relationships={agentEdges}
                endpoint={endpoint}
                avatarPresence={avatarPresence}
                avatarBlobUrls={avatarBlobUrls}
                onNodeClick={(id) => id && setSelectedUuid(id.uuid)}
              />
            )}
            {view === "list" && (
              <PeopleListView
                identities={identities}
                endpoint={endpoint}
                avatarPresence={avatarPresence}
                avatarBlobUrls={avatarBlobUrls}
                onRowClick={(id) => setSelectedUuid(id.uuid)}
              />
            )}
            {view === "queue" && !legacyMutationsReadOnly && (
              <PeopleQueueView
                identities={identities}
                facesByPerson={facesByPerson}
                facesStatus={facesStatus}
                frigateDiagnostics={frigateDiagnostics}
                sim={sim}
                endpoint={endpoint}
                token={token}
                operationScopeKey={credentialScopeKey}
                avatarPresence={avatarPresence}
                avatarBlobUrls={avatarBlobUrls}
                onSaved={refresh}
              />
            )}
          </>
        )}
      </div>

      <style>{`
        @keyframes people-fade-in {
          from { opacity: 0; transform: translateY(8px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>

      {/* Detail panel — opens when a node/row is clicked */}
      <PeopleDetailPanel
        identityUuid={selectedUuid}
        endpoint={endpoint}
        token={token}
        operationScopeKey={credentialScopeKey}
        sim={sim}
        avatarPresence={avatarPresence}
        avatarBlobUrls={avatarBlobUrls}
        onAvatarChanged={(uuid, present) => refreshAvatars(uuid, present)}
        onClose={() => setSelectedUuid(null)}
        onChanged={refresh}
        readOnly={legacyMutationsReadOnly}
      />
    </div>
  );
  return typeof document !== "undefined" && document.body
    ? ReactDOM.createPortal(overlay, document.body)
    : overlay;
}

/* ─────────────────────────────────────────────────────────────────────
 * Sub-component: PeopleGraphView — radial relationship visualization
 * ──────────────────────────────────────────────────────────────────── */
function PeopleGraphView({ identities, relationships, endpoint, avatarPresence, avatarBlobUrls, onNodeClick }) {
  const H = (typeof window !== "undefined" && window.HomePeopleHelpers) || null;
  const peopleViewport = usePeopleViewport();
  const isMobile = peopleViewport.mobile;
  const [graphCamera, setGraphCamera] = useState({ scale: 1, x: 0, y: 0 });
  const graphCameraRef = useRef(graphCamera);
  const graphGestureRef = useRef(null);
  const graphSvgRef = useRef(null);
  useEffect(() => {
    const el = graphSvgRef.current;
    if (!el) return undefined;
    const preventPagePinch = (ev) => ev.preventDefault();
    el.addEventListener("gesturestart", preventPagePinch);
    el.addEventListener("gesturechange", preventPagePinch);
    el.addEventListener("gestureend", preventPagePinch);
    return () => {
      el.removeEventListener("gesturestart", preventPagePinch);
      el.removeEventListener("gesturechange", preventPagePinch);
      el.removeEventListener("gestureend", preventPagePinch);
    };
  }, []);

  // Addendum 23: hover state. Tracks the uuid currently being
  // pointed at; null when no hover. Mouse-leave is debounced ~50ms
  // so rapid cross-traversal between adjacent cluster members
  // (parents are only ~58px apart) doesn't flash the state off + on.
  const [hoveredUuid, setHoveredUuid] = useState(null);
  const leaveTimerRef = useRef(null);
  const handleHoverEnter = useCallback((uuid, ev) => {
    // AR23-9: skip hover state on non-mouse pointer types (touch
    // would otherwise flash hover before navigating).
    if (ev && ev.pointerType && ev.pointerType !== "mouse") return;
    if (leaveTimerRef.current) {
      clearTimeout(leaveTimerRef.current);
      leaveTimerRef.current = null;
    }
    setHoveredUuid(uuid);
  }, []);
  const handleHoverLeave = useCallback(() => {
    if (leaveTimerRef.current) clearTimeout(leaveTimerRef.current);
    leaveTimerRef.current = setTimeout(() => {
      setHoveredUuid(null);
      leaveTimerRef.current = null;
    }, 50);
  }, []);
  // Clear pending timer on unmount to avoid setState on unmounted.
  useEffect(() => () => {
    if (leaveTimerRef.current) clearTimeout(leaveTimerRef.current);
  }, []);

  // Empty state — no identities at all OR no in-graph identities
  const graphable = (identities || []).filter((i) => {
    if (!H) return false;
    return H.ringForIdentity(i) !== null;
  });

  if (graphable.length === 0) {
    const hasUnknown = (identities || []).some((i) => i.relationship_type === "unknown");
    return (
      <div style={{
        textAlign: "center", padding: "60px 20px",
        color: "var(--hg-fg-3)",
      }}>
        <div style={{
          fontSize: 32, marginBottom: 12, color: "var(--hg-fg-5)",
        }}>ï¼‹</div>
        <div style={{
          fontFamily: PEOPLE_FONT_MONO, fontSize: 11, letterSpacing: "0.16em",
          textTransform: "uppercase", color: "var(--hg-fg-3)",
          marginBottom: 8,
        }}>
          {hasUnknown ? "tag identities to populate the graph" : "tag your first face"}
        </div>
        <div style={{
          fontSize: 12, color: "var(--hg-fg-4)", lineHeight: 1.6, maxWidth: 420,
          margin: "0 auto",
        }}>
          {hasUnknown
            ? "Open the queue tab and assign a relationship to bring a face into the web."
            : "The system detects faces from your cameras and surfaces them in the queue tab."}
        </div>
      </div>
    );
  }

  if (!H) {
    return (
      <div style={{ color: "var(--hg-fg-3)", fontSize: 11 }}>
        helpers not loaded — check home-people-helpers.js
      </div>
    );
  }

  // Layout. Mobile gets a slightly larger, tighter graph: larger faces
  // make the relationship web legible, while reduced radii keep every
  // node inside the phone viewport.
  const layout = H.buildRadialLayout(graphable, isMobile
    ? {
        radiusScale: 1.2,
        avatarScale: 1.4,
        clusterStepScale: 1.2,
        textScale: 1.28,
        collisionPadding: 64,
        centerCollisionPadding: 88,
        collisionIterations: 120,
        portraitLayout: true,
      }
    : undefined);

  // Edge geometry. In addition to HA relationship rows, overlay a small
  // trusted-family map from the seeded face folders so the graph can show
  // parent/partner branches immediately after identities sync.
  const knownFamilyRelationships = H.buildKnownFamilyRelationships
    ? H.buildKnownFamilyRelationships(graphable)
    : [];
  const relationshipRows = [...(relationships || []), ...knownFamilyRelationships];
  const edges = H.buildEdgeGeometry(layout, relationshipRows);

  // SVG sizing. Desktop keeps the full relationship-ring field; mobile
  // fits to the actual visible nodes so sparse graphs do not render as a
  // tiny cluster in an oversized 920px coordinate space.
  const activeRings = new Set(layout.filter((n) => !n.overflow).map((n) => n.ring));
  const nodeBounds = layout.reduce((bounds, n) => {
    if (!n || n.overflow) return bounds;
    const textScale = n.textScale || 1;
    const name = String(n.identity?.display_name || "?");
    const role = String(n.identity?.relationship_subrole || n.identity?.relationship_type || "");
    const nameFont = Math.max(9, Math.round((n.size || 0) * 0.18 * textScale));
    const roleFont = Math.max(7, Math.round((n.size || 0) * 0.14 * textScale));
    const labelHalf = Math.max(
      (n.size || 0) / 2,
      name.length * nameFont * 0.31,
      role.length * roleFont * 0.34,
    );
    const padX = labelHalf + (isMobile ? 18 : 28);
    const padTop = (n.size || 0) / 2 + (isMobile ? 28 : 46);
    const padBottom = (n.size || 0) / 2 + (isMobile ? 52 * textScale : 64);
    return {
      minX: Math.min(bounds.minX, (n.x || 0) - padX),
      maxX: Math.max(bounds.maxX, (n.x || 0) + padX),
      minY: Math.min(bounds.minY, (n.y || 0) - padTop),
      maxY: Math.max(bounds.maxY, (n.y || 0) + padBottom),
    };
  }, { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity });
  const hasNodeBounds = Number.isFinite(nodeBounds.minX) && Number.isFinite(nodeBounds.maxX);
  const mobileViewBox = hasNodeBounds
    ? {
        x: nodeBounds.minX,
        y: nodeBounds.minY - 28,
        w: Math.max(260, nodeBounds.maxX - nodeBounds.minX),
        h: Math.max(220, nodeBounds.maxY - nodeBounds.minY + 44),
      }
    : { x: -260, y: -220, w: 520, h: 440 };
  const VIEW_HALF = 460;
  const VIEW_SIZE = VIEW_HALF * 2;
  const graphViewBox = isMobile
    ? mobileViewBox
    : { x: -VIEW_HALF, y: -VIEW_HALF, w: VIEW_SIZE, h: VIEW_SIZE };
  useEffect(() => {
    graphCameraRef.current = graphCamera;
  }, [graphCamera]);
  useEffect(() => {
    setGraphCamera((prev) => clampPeopleGraphCamera(prev, graphViewBox, isMobile));
  }, [graphViewBox.x, graphViewBox.y, graphViewBox.w, graphViewBox.h, isMobile]);
  const zoomedGraphViewBox = useMemo(
    () => peopleGraphCameraViewBox(graphViewBox, graphCamera),
    [graphViewBox.x, graphViewBox.y, graphViewBox.w, graphViewBox.h, graphCamera],
  );
  const graphClientDeltaToViewBox = useCallback((dx, dy, camera = graphCameraRef.current) => {
    const rect = graphSvgRef.current?.getBoundingClientRect?.();
    if (!rect || rect.width <= 0 || rect.height <= 0) return { dx: 0, dy: 0 };
    const current = peopleGraphCameraViewBox(graphViewBox, camera);
    return {
      dx: (dx / rect.width) * current.w,
      dy: (dy / rect.height) * current.h,
    };
  }, [graphViewBox.x, graphViewBox.y, graphViewBox.w, graphViewBox.h]);
  const handleGraphWheel = useCallback((ev) => {
    if (!ev.ctrlKey) return;
    ev.preventDefault();
    const current = graphCameraRef.current;
    const factor = Math.exp(-ev.deltaY * 0.002);
    setGraphCamera(clampPeopleGraphCamera({
      ...current,
      scale: current.scale * factor,
    }, graphViewBox, isMobile));
  }, [graphViewBox.x, graphViewBox.y, graphViewBox.w, graphViewBox.h, isMobile]);
  const handleGraphTouchStart = useCallback((ev) => {
    if (ev.touches.length >= 2) {
      ev.preventDefault();
      const [a, b] = ev.touches;
      graphGestureRef.current = {
        type: "pinch",
        distance: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY) || 1,
        scale: graphCameraRef.current.scale,
        x: graphCameraRef.current.x,
        y: graphCameraRef.current.y,
        cx: (a.clientX + b.clientX) / 2,
        cy: (a.clientY + b.clientY) / 2,
      };
    } else if (ev.touches.length === 1 && graphCameraRef.current.scale > 1.01) {
      const t = ev.touches[0];
      graphGestureRef.current = {
        type: "pan",
        x: graphCameraRef.current.x,
        y: graphCameraRef.current.y,
        clientX: t.clientX,
        clientY: t.clientY,
      };
    }
  }, []);
  const handleGraphTouchMove = useCallback((ev) => {
    const gesture = graphGestureRef.current;
    if (!gesture) return;
    ev.preventDefault();
    if (gesture.type === "pinch" && ev.touches.length >= 2) {
      const [a, b] = ev.touches;
      const distance = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY) || gesture.distance;
      const cx = (a.clientX + b.clientX) / 2;
      const cy = (a.clientY + b.clientY) / 2;
      const move = graphClientDeltaToViewBox(gesture.cx - cx, gesture.cy - cy, {
        scale: gesture.scale,
        x: gesture.x,
        y: gesture.y,
      });
      setGraphCamera(clampPeopleGraphCamera({
        scale: gesture.scale * (distance / gesture.distance),
        x: gesture.x + move.dx,
        y: gesture.y + move.dy,
      }, graphViewBox, isMobile));
    } else if (gesture.type === "pan" && ev.touches.length === 1) {
      const t = ev.touches[0];
      const move = graphClientDeltaToViewBox(gesture.clientX - t.clientX, gesture.clientY - t.clientY);
      setGraphCamera(clampPeopleGraphCamera({
        scale: graphCameraRef.current.scale,
        x: gesture.x + move.dx,
        y: gesture.y + move.dy,
      }, graphViewBox, isMobile));
    }
  }, [graphClientDeltaToViewBox, graphViewBox.x, graphViewBox.y, graphViewBox.w, graphViewBox.h, isMobile]);
  const handleGraphTouchEnd = useCallback((ev) => {
    if (ev.touches.length === 0) {
      graphGestureRef.current = null;
    } else if (ev.touches.length === 1 && graphCameraRef.current.scale > 1.01) {
      const t = ev.touches[0];
      graphGestureRef.current = {
        type: "pan",
        x: graphCameraRef.current.x,
        y: graphCameraRef.current.y,
        clientX: t.clientX,
        clientY: t.clientY,
      };
    }
  }, []);
  const branchGroups = (() => {
    const labels = {
      "holly-family": "holly family",
      "felipe-ashley-family": "ashley + felipe",
      friends: "friends",
    };
    const grouped = new Map();
    for (const n of layout) {
      if (!n || n.overflow || !n.familyBranch) continue;
      if (!grouped.has(n.familyBranch)) grouped.set(n.familyBranch, []);
      grouped.get(n.familyBranch).push(n);
    }
    return Array.from(grouped.entries()).flatMap(([branch, nodes]) => {
      if (nodes.length < 2) return [];
      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      for (const n of nodes) {
        const pad = (n.size || 0) / 2 + (isMobile ? 22 : 18);
        minX = Math.min(minX, (n.x || 0) - pad);
        maxX = Math.max(maxX, (n.x || 0) + pad);
        minY = Math.min(minY, (n.y || 0) - pad);
        maxY = Math.max(maxY, (n.y || 0) + pad);
      }
      if (!Number.isFinite(minX) || !Number.isFinite(maxX)) return [];
      const cx = (minX + maxX) / 2;
      const cy = (minY + maxY) / 2;
      return [{
        key: branch,
        label: labels[branch] || branch.replace(/-/g, " "),
        cx,
        cy,
        rx: Math.max(54, (maxX - minX) / 2),
        ry: Math.max(44, (maxY - minY) / 2),
        labelY: minY - (isMobile ? 12 : 10),
      }];
    });
  })();

  // Addendum 23: pre-compute the connected-uuid set + tooltip placement
  // for the currently-hovered node. useMemo prevents recomputation
  // when only the layout/edges shift without a hover change.
  const connectedSet = useMemo(
    () => (H && H.connectedUuidsForHovered)
      ? H.connectedUuidsForHovered(layout, edges, hoveredUuid)
      : new Set(),
    [layout, edges, hoveredUuid, H],
  );
  // AR23-2: hovering the center node means "show me everyone connected
  // to me" — DON'T dim anything; just highlight the center itself.
  const hoveredNode = hoveredUuid
    ? layout.find((n) => n.uuid === hoveredUuid)
    : null;
  const hoveringCenter = !!hoveredNode && hoveredNode.ring === 0;
  const TOOLTIP_BASE_H = 70;
  const tooltipH = TOOLTIP_BASE_H;
  const tooltipPos = useMemo(() => {
    if (!hoveredNode || !H || !H.tooltipPlacement) return null;
    return H.tooltipPlacement(hoveredNode, VIEW_HALF, layout, { tooltipH });
  }, [hoveredNode, layout, VIEW_HALF, H, tooltipH]);

  return (
    <div style={{
      display: "flex", justifyContent: "center", alignItems: "center",
      padding: isMobile ? "0 0 max(22px, env(safe-area-inset-bottom))" : "20px 0",
      minHeight: isMobile ? "calc(100dvh - 118px)" : "auto",
      width: "100%",
    }}>
      <svg
        ref={graphSvgRef}
        width="100%"
        height={isMobile ? "calc(100dvh - 118px)" : "auto"}
        viewBox={`${zoomedGraphViewBox.x} ${zoomedGraphViewBox.y} ${zoomedGraphViewBox.w} ${zoomedGraphViewBox.h}`}
        style={{
          maxHeight: isMobile ? "calc(100dvh - 118px)" : "min(80vh, 800px)",
          maxWidth: isMobile ? "100vw" : "min(80vw, 1000px)",
          minHeight: isMobile ? "min(720px, calc(100dvh - 118px))" : "auto",
          display: "block",
          touchAction: "none",
          overscrollBehavior: "contain",
        }}
        preserveAspectRatio="xMidYMid meet"
        onWheel={handleGraphWheel}
        onTouchStart={handleGraphTouchStart}
        onTouchMove={handleGraphTouchMove}
        onTouchEnd={handleGraphTouchEnd}
        onTouchCancel={handleGraphTouchEnd}
        onDoubleClick={() => setGraphCamera({ scale: 1, x: 0, y: 0 })}
        aria-label="Relationship graph"
      >
        <defs>
          <radialGradient id="people-graph-field" cx="50%" cy="48%" r="66%">
            <stop offset="0%" stopColor="var(--hg-fg-2)" stopOpacity="0.11" />
            <stop offset="52%" stopColor="var(--hg-fg-3)" stopOpacity="0.035" />
            <stop offset="100%" stopColor="var(--hg-bg-0)" stopOpacity="0" />
          </radialGradient>
          <filter id="people-node-soft-glow" x="-35%" y="-35%" width="170%" height="170%">
            <feGaussianBlur stdDeviation="3" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        <rect
          x={graphViewBox.x}
          y={graphViewBox.y}
          width={graphViewBox.w}
          height={graphViewBox.h}
          fill="url(#people-graph-field)"
          opacity={isMobile ? 0.9 : 0.45}
          aria-hidden="true"
        />
        {/* Subtle ring guides — radial vignette per Addendum 14 visual polish */}
        {[1, 2, 3].map((r) => (
          <circle
            key={`ring-${r}`}
            cx={0} cy={0} r={H.radiusForRing(r) * (isMobile ? 1.2 : 1)}
            fill="none"
            stroke="var(--hg-border-soft)"
            strokeWidth={isMobile ? 0.75 : 0.5}
            strokeDasharray={isMobile ? "2,8" : undefined}
            opacity={activeRings.has(r) ? (isMobile ? 0.28 : 0.4) : isMobile ? 0 : 0.18}
          />
        ))}

        {/* Edges first, so nodes render above. Hover state (Addendum 23):
            edges incident on hovered node stay full opacity + bump
            stroke-width; other edges fade to 0.25. When hovering the
            center, no fade (AR23-2). */}
        {edges.map((e, idx) => {
          const incident = hoveredUuid && (e.fromUuid === hoveredUuid || e.toUuid === hoveredUuid);
          const dimmed = hoveredUuid && !incident && !hoveringCenter;
          return (
            <line
              key={`edge-${idx}`}
              x1={e.x1} y1={e.y1}
              x2={e.x2} y2={e.y2}
              stroke={e.style.stroke}
              strokeWidth={incident ? (e.style.width + 0.8) : e.style.width}
              strokeDasharray={e.style.dash || undefined}
              opacity={dimmed ? 0.2 : isMobile ? 0.68 : 0.85}
              strokeLinecap="round"
              style={{ transition: "opacity 180ms ease, stroke-width 180ms ease" }}
              aria-label={e.relType
                ? `${e.relType}${e.status === "ended" ? " (ended)" : ""}`
                : "implicit relationship"}
            />
          );
        })}

        {/* Branch labels use the final node positions, not idealized ring
            math, so they stay attached to the actual family/friend
            groups after mobile collision resolution. */}
        {branchGroups.map((g) => (
          <g key={`branch-${g.key}`} aria-hidden="true">
            <text
              x={g.cx}
              y={g.labelY}
              textAnchor="middle"
              fontFamily={PEOPLE_FONT_MONO}
              fontSize={isMobile ? 9.5 : 8}
              letterSpacing="0.16em"
              fill="var(--hg-fg-3)"
              stroke="var(--hg-bg-0)"
              strokeWidth={3}
              strokeLinejoin="round"
              paintOrder="stroke fill"
              opacity={isMobile ? 0.78 : 0.58}
              style={{ textTransform: "uppercase" }}
            >
              {g.label}
            </text>
          </g>
        ))}

        {/* Nodes */}
        {layout.map((node) => {
          if (node.overflow) {
            return (
              <g key={`overflow-r${node.ring}`}
                 transform={`translate(${node.x}, ${node.y})`}
                 style={{ cursor: "pointer" }}
                 aria-label={`${node.overflowCount} more in ring ${node.ring}`}>
                <circle r={node.size / 2}
                        fill="var(--hg-bg-1)"
                        stroke="var(--hg-border)" strokeWidth={1} strokeDasharray="3,2" />
                <text x={0} y={4} textAnchor="middle"
                      fontFamily={PEOPLE_FONT_MONO}
                      fontSize={Math.max(9, node.size * 0.22 * (node.textScale || 1))}
                      fill="var(--hg-fg-3)">
                  +{node.overflowCount}
                </text>
              </g>
            );
          }
          const isCenter = node.ring === 0;
          const id = node.identity;
          const isSilent = id?.is_silent;
          const isPrivate = id?.is_private;
          const isArchived = id?.is_archived;
          const initials = H.initialsFor(id?.display_name || "?");
          // Addendum 23: hover state per-node.
          const isHovered = hoveredUuid === node.uuid;
          const isConnected = !!hoveredUuid && connectedSet.has(node.uuid);
          const isDimmed = !!hoveredUuid && !isConnected && !hoveringCenter;
          const baseOpacity = isArchived ? 0.45 : 1;
          const groupOpacity = isDimmed ? 0.4 : baseOpacity;
          // Scale up the hovered node by 1.10 (transform-origin = node
          // center which is already (0,0) inside the <g>).
          const scaleTransform = isHovered ? " scale(1.10)" : "";
          // Stroke brightens on hover.
          const strokeColor = isCenter
            ? "var(--hg-ice)"
            : (isHovered ? "var(--hg-fg-1)" : "var(--hg-fg-3)");
          const strokeWidth = isHovered ? (isCenter ? 2.5 : 2) : (isCenter ? 1.5 : 1);
          return (
            <g key={node.uuid}
               transform={`translate(${node.x}, ${node.y})${scaleTransform}`}
               style={{
                 cursor: "pointer",
                 opacity: groupOpacity,
                 transition: "transform 180ms ease, opacity 180ms ease",
               }}
               onClick={() => onNodeClick?.(id)}
               tabIndex={0}
               role="button"
               onKeyDown={(ev) => {
                 if (ev.key === "Enter" || ev.key === " ") {
                   if (ev.key === " ") ev.preventDefault();
                   onNodeClick?.(id);
                 }
               }}
               onPointerEnter={(ev) => handleHoverEnter(node.uuid, ev.nativeEvent)}
               onPointerLeave={handleHoverLeave}
               /* :focus-visible outlines don't apply reliably to SVG <g>,
                  so keyboard focus reuses the hover-highlight state —
                  the brightened stroke + scale acts as the focus ring. */
               onFocus={() => handleHoverEnter(node.uuid)}
               onBlur={handleHoverLeave}
               aria-label={`${id?.display_name || "unknown"}, ${id?.relationship_type || "?"}`}>
              <circle
                r={node.size / 2}
                fill="var(--hg-bg-1)"
                stroke={strokeColor}
                strokeWidth={strokeWidth}
                filter={isCenter && isMobile ? "url(#people-node-soft-glow)" : undefined}
                style={{ transition: "stroke 180ms ease, stroke-width 180ms ease" }}
              />
              {/* Addendum 24 Phase 3: custom avatar wins when the parent
                  has fetched + blob-URL-converted it (auth required to
                  GET, but <image href> can't send headers — see the
                  blob-fetcher useEffect in HomePeopleOverlay). Otherwise
                  initials fallback. */}
              {(() => {
                const blobUrl = avatarBlobUrls && avatarBlobUrls[node.uuid];
                if (!blobUrl) {
                  return (
                    <text
                      x={0} y={Math.round(node.size * 0.13)}
                      textAnchor="middle"
                      fontFamily={PEOPLE_FONT_SANS}
                      fontSize={Math.round(node.size * 0.36 * (node.textScale || 1))}
                      fontWeight={300}
                      fill={isCenter ? "var(--hg-ice)" : "var(--hg-fg-1)"}
                      opacity={isArchived ? 0.6 : 1}
                    >{initials}</text>
                  );
                }
                const clipId = `avatar-clip-${node.uuid}`;
                return (
                  <>
                    <defs>
                      <clipPath id={clipId}>
                        <circle r={(node.size / 2) - 1} />
                      </clipPath>
                    </defs>
                    <image
                      href={blobUrl}
                      x={-node.size / 2} y={-node.size / 2}
                      width={node.size} height={node.size}
                      clipPath={`url(#${clipId})`}
                      preserveAspectRatio="xMidYMid slice"
                      opacity={isArchived ? 0.6 : 1}
                    />
                  </>
                );
              })()}
              {/* Privacy badge bottom-right (compact) */}
              {(isPrivate || isSilent) && (
                <circle
                  cx={node.size * 0.32} cy={node.size * 0.32}
                  r={5}
                  fill="var(--hg-bg-0)"
                  stroke={isPrivate ? "var(--hg-warn)" : "var(--hg-fg-3)"}
                  strokeWidth={1}
                />
              )}
              {/* Name label below the avatar. paint-order: stroke fill
                  + bg-color stroke is the standard SVG trick for text
                  that visually "masks out" edges passing under it —
                  the thick bg-color stroke is rendered BELOW the fill,
                  creating a clean halo that hides the edge stroke
                  beneath the text. No bbox math needed. */}
              <text
                x={0} y={node.size / 2 + 14 * (node.textScale || 1)}
                textAnchor="middle"
                fontFamily={PEOPLE_FONT_MONO}
                fontSize={Math.max(9, Math.round(node.size * 0.18 * (node.textScale || 1)))}
                fill="var(--hg-fg-0)"
                stroke="var(--hg-bg-0)"
                strokeWidth={4}
                strokeLinejoin="round"
                paintOrder="stroke fill"
                letterSpacing="0.04em"
                opacity={isArchived ? 0.6 : 1}
              >{id?.display_name || "?"}</text>
              {/* Relationship label. For cluster members (mother /
                  father / sister / ...), the cluster arc above already
                  shows the GROUP (e.g., "PARENTS"); using the subrole
                  here is both shorter (avoids "family immediate"
                  collision with neighbor labels) AND more specific.
                  Same paint-order halo as the name label so edges
                  don't visibly cut through this text either. */}
              {!isCenter && (() => {
                const cluster = H.subroleCluster ? H.subroleCluster(id) : null;
                const labelText = cluster && id?.relationship_subrole
                  ? id.relationship_subrole.replace(/_/g, " ")
                  : (id?.relationship_type || "").replace(/_/g, " ");
                return (
                  <text
                    x={0} y={node.size / 2 + 28 * (node.textScale || 1)}
                    textAnchor="middle"
                    fontFamily={PEOPLE_FONT_MONO}
                    fontSize={Math.max(7, Math.round(node.size * 0.14 * (node.textScale || 1)))}
                    fill="var(--hg-fg-3)"
                    stroke="var(--hg-bg-0)"
                    strokeWidth={3}
                    strokeLinejoin="round"
                    paintOrder="stroke fill"
                    letterSpacing="0.10em"
                  >{labelText.toUpperCase()}</text>
                );
              })()}
            </g>
          );
        })}

        {/* Addendum 23: hover tooltip — small floating SVG box near
            the hovered node. Position is viewport-aware (auto-flips
            to avoid clipping + neighbor collision per tooltipPlacement).
            pointer-events: none so it can't steal hover from the
            underlying node. */}
        {hoveredNode && tooltipPos && hoveredNode.identity && (() => {
          const id = hoveredNode.identity;
          const tw = 180;
          const th = tooltipH;
          const lines = [];
          const sub = id.relationship_subrole;
          const relLine = sub
            ? `${id.relationship_type || ""} · ${sub}`.replace(/_/g, " ")
            : (id.relationship_type || "").replace(/_/g, " ");
          if (relLine) lines.push(relLine);
          const aliasCount = (id.aliases || []).length;
          if (aliasCount > 0) lines.push(`${aliasCount} alias${aliasCount === 1 ? "" : "es"}`);
          const flags = [];
          if (id.is_private) flags.push("private");
          if (id.is_silent)  flags.push("silent");
          if (id.is_archived) flags.push("archived");
          if (flags.length) lines.push(flags.join(" · "));
          const editHintY = th - 8;
          return (
            <g
              transform={`translate(${tooltipPos.x}, ${tooltipPos.y})`}
              style={{ pointerEvents: "none" }}
              aria-hidden="true"
            >
              <rect
                width={tw} height={th}
                rx={4} ry={4}
                fill="var(--hg-bg-1)"
                stroke="var(--hg-border-soft)"
                strokeWidth={1}
                opacity={0.96}
              />
              <text
                x={10} y={20}
                fontFamily={PEOPLE_FONT_SANS}
                fontSize={13}
                fontWeight={500}
                fill="var(--hg-fg-0)"
              >{id.display_name || "?"}</text>
              {lines.map((line, i) => (
                <text
                  key={`tt-line-${i}`}
                  x={10} y={36 + i * 13}
                  fontFamily={PEOPLE_FONT_MONO}
                  fontSize={9}
                  letterSpacing="0.06em"
                  fill="var(--hg-fg-3)"
                >{line.toUpperCase()}</text>
              ))}
              <text
                x={tw - 10} y={editHintY}
                textAnchor="end"
                fontFamily={PEOPLE_FONT_MONO}
                fontSize={10.5}
                letterSpacing="0.12em"
                fill="var(--hg-fg-3)"
              >click to edit</text>
            </g>
          );
        })()}
      </svg>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────
 * Sub-component: PeopleListView (Slice 3 sketch — sortable table later)
 * ──────────────────────────────────────────────────────────────────── */
function PeopleListView({ identities, endpoint, avatarPresence, avatarBlobUrls, onRowClick }) {
  const H = (typeof window !== "undefined" && window.HomePeopleHelpers) || null;
  if (!identities || identities.length === 0) {
    return (
      <div style={{ color: "var(--hg-fg-3)", fontSize: 11 }}>no identities yet</div>
    );
  }
  return (
    <table style={{
      width: "100%", borderCollapse: "collapse",
      fontFamily: PEOPLE_FONT_MONO, fontSize: 11,
    }}>
      <thead>
        <tr style={{
          textAlign: "left",
          color: "var(--hg-fg-3)",
          fontSize: 9, letterSpacing: "0.18em", textTransform: "uppercase",
          borderBottom: "1px solid var(--hg-border-soft)",
        }}>
          <th style={{ padding: "8px 12px", width: 32 }}></th>
          <th style={{ padding: "8px 12px" }}>name</th>
          <th style={{ padding: "8px 12px" }}>relationship</th>
          <th style={{ padding: "8px 12px" }}>aliases</th>
          <th style={{ padding: "8px 12px" }}>flags</th>
        </tr>
      </thead>
      <tbody>
        {identities.map((i) => (
          <tr key={i.uuid}
              onClick={() => onRowClick?.(i)}
              tabIndex={0}
              role="button"
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  if (e.key === " ") e.preventDefault();
                  onRowClick?.(i);
                }
              }}
              style={{
                borderBottom: "1px solid var(--hg-border-soft)",
                color: i.is_archived ? "var(--hg-fg-4)" : "var(--hg-fg-1)",
                cursor: onRowClick ? "pointer" : "default",
              }}
              onMouseEnter={(e) => {
                if (onRowClick) e.currentTarget.style.background = "var(--hg-bg-2)";
              }}
              onMouseLeave={(e) => {
                if (onRowClick) e.currentTarget.style.background = "transparent";
              }}
              onFocus={(e) => {
                if (onRowClick) e.currentTarget.style.background = "var(--hg-bg-2)";
              }}
              onBlur={(e) => {
                if (onRowClick) e.currentTarget.style.background = "transparent";
              }}>
            <td style={{ padding: "8px 12px" }}>
              <span style={{
                display: "inline-block", width: 6, height: 6, borderRadius: "50%",
                background: i.is_ignored ? "var(--hg-fg-5)"
                          : i.is_private ? "var(--hg-warn)"
                          : "var(--hg-ice)",
                boxShadow: i.is_private || i.is_ignored ? "none" : "0 0 4px var(--hg-ice-glow)",
              }} />
            </td>
            <td style={{ padding: "8px 12px", color: "var(--hg-fg-0)" }}>
              {(() => {
                // Addendum 24 Phase 3: small avatar before name when present;
                // otherwise nothing (just the name). Initial-letter fallback
                // in list view would visually crowd; the dot+name already
                // identify the row. Uses parent-fetched blob URL so the
                // auth header makes it to the GET.
                const aUrl = avatarBlobUrls && avatarBlobUrls[i.uuid];
                return (
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                    {aUrl && (
                      <img src={aUrl} alt=""
                        style={{
                          width: 20, height: 20, borderRadius: "50%",
                          objectFit: "cover",
                          border: "1px solid var(--hg-border-soft)",
                        }} />
                    )}
                    <span>{i.display_name}</span>
                  </span>
                );
              })()}
            </td>
            <td style={{ padding: "8px 12px", color: "var(--hg-fg-3)" }}>
              {i.relationship_type}{i.relationship_subrole ? ` · ${i.relationship_subrole}` : ""}
            </td>
            <td style={{ padding: "8px 12px", color: "var(--hg-fg-3)" }}>
              {(i.aliases || [])
                .filter((a) => a.kind === "nickname" || a.kind === "frigate_name")
                .map((a) => a.alias).join(", ") || "—"}
            </td>
            <td style={{ padding: "8px 12px", color: "var(--hg-fg-3)" }}>
              {[
                i.is_private && "private",
                i.is_silent && "silent",
                i.is_ignored && "ignored",
                i.is_archived && "archived",
                i.do_not_track && "do-not-track",
              ].filter(Boolean).join(" · ") || "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* ─────────────────────────────────────────────────────────────────────
 * Discovery queue — relationship types offered + handful of subroles
 * ──────────────────────────────────────────────────────────────────── */

// Mirror identity_store.RELATIONSHIP_TYPES but with display-friendly labels.
// The system reserves 'me' for exactly one identity; we surface it as the
// first option so first-run tag-yourself is one click away.
const REL_TYPE_OPTIONS = [
  { value: "me",                label: "me (only one)" },
  { value: "partner",           label: "partner" },
  { value: "family_immediate",  label: "family — immediate" },
  { value: "family_extended",   label: "family — extended" },
  { value: "roommate",          label: "roommate / co-resident" },
  { value: "friend",            label: "friend" },
  { value: "neighbor",          label: "neighbor" },
  { value: "service",           label: "service worker" },
  { value: "guest",             label: "guest (transient)" },
  { value: "unknown",           label: "leave as unknown" },
  { value: "do_not_identify",   label: "do not identify" },
];

/* ─────────────────────────────────────────────────────────────────────
 * Sub-component: PeopleQueueCard — single unknown identity card with
 * inline name + relationship editor. PATCHes the HA REST endpoint on
 * save; on success, the parent refreshes the list and this card
 * disappears (because the identity is no longer relationship_type=unknown).
 * ──────────────────────────────────────────────────────────────────── */
function PeopleQueueCard({ identity, endpoint, token, operationScopeKey, sim, avatarBlobUrls, onSaved }) {
  const [name, setName] = useState(identity.display_name);
  const [rel, setRel] = useState(identity.relationship_type);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const beginOperation = usePeopleOperationGuard(
    `${operationScopeKey}:queue-identity:${identity.uuid}`,
  );

  const frigateAlias = (identity.aliases || []).find((a) => a.kind === "frigate_name");
  // Parent-fetched blob URL (handles auth). Null when no avatar set.
  const avatarSrc = avatarBlobUrls && avatarBlobUrls[identity.uuid];

  const dirty = name.trim() !== identity.display_name || rel !== identity.relationship_type;

  async function save() {
    setError(null);
    if (!name.trim()) {
      setError("name required");
      return;
    }
    const operation = beginOperation("save");
    setSaving(true);
    try {
      if (sim?.active) {
        // In sim mode, no PATCH — just fake the save so the UI updates.
        await new Promise((r) => setTimeout(r, 250));
        if (!operation.isCurrent()) return;
        onSaved?.({ uuid: identity.uuid, display_name: name.trim(), relationship_type: rel });
        return;
      }
      const url = `${endpoint.replace(/\/+$/, "")}/api/extended_openai_conversation/identity/${identity.uuid}`;
      const resp = await window.tauriFetch(url, {
        method: "PATCH",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          display_name: name.trim(),
          relationship_type: rel,
          expected_version: identity.version,
        }),
        signal: operation.signal,
      });
      if (!operation.isCurrent()) return;
      if (!resp.ok) {
        const body = await resp.text().catch(() => "");
        if (!operation.isCurrent()) return;
        setError(`HTTP ${resp.status}: ${body.slice(0, 100)}`);
        return;
      }
      const result = await resp.json();
      if (!operation.isCurrent()) return;
      if (result.error) {
        setError(result.error);
        return;
      }
      onSaved?.({ uuid: identity.uuid, display_name: name.trim(), relationship_type: rel });
    } catch (e) {
      if (peopleOperationAborted(e, operation)) return;
      setError(`network error: ${e.message || e}`);
    } finally {
      if (operation.isCurrent()) setSaving(false);
      operation.finish();
    }
  }

  return (
    <div style={{
      border: "1px solid var(--hg-border-soft)",
      padding: 14,
      display: "flex", flexDirection: "column", gap: 10,
      background: "var(--hg-bg-1)",
    }}>
      {/* Detection metadata strip — Frigate enrollment + raw cluster name */}
      <div style={{
        display: "flex", alignItems: "center", gap: 8,
        fontSize: 9, color: "var(--hg-fg-4)",
        letterSpacing: "0.16em", textTransform: "uppercase",
      }}>
        {/* Addendum 24 Phase 3: avatar thumb if user uploaded one */}
        {avatarSrc && (
          <img src={avatarSrc} alt=""
            style={{
              width: 28, height: 28, borderRadius: "50%",
              objectFit: "cover",
              border: "1px solid var(--hg-border-soft)",
            }} />
        )}
        {frigateAlias && (
          <span title={`Frigate cluster: ${frigateAlias.alias}`}>
            frigate · {frigateAlias.alias}
          </span>
        )}
        {!frigateAlias && <span>no frigate enrollment</span>}
        <span style={{ marginLeft: "auto" }}>v{identity.version}</span>
      </div>

      {/* Inline name editor */}
      <input
        type="text"
        value={name}
        disabled={saving}
        onChange={(e) => setName(e.target.value)}
        placeholder="name"
        className="hg-focusable"
        style={{
          background: "var(--hg-input-bg)",
          border: "1px solid var(--hg-border-soft)",
          padding: "7px 10px",
          fontFamily: PEOPLE_FONT_SANS,
          fontSize: 14, color: "var(--hg-fg-0)",
        }}
      />

      {/* Relationship picker */}
      <select
        value={rel}
        disabled={saving}
        onChange={(e) => setRel(e.target.value)}
        className="hg-focusable"
        style={{
          background: "var(--hg-input-bg)",
          border: "1px solid var(--hg-border-soft)",
          padding: "7px 10px",
          fontFamily: PEOPLE_FONT_MONO,
          fontSize: 11, color: "var(--hg-fg-1)",
          letterSpacing: "0.04em",
          cursor: saving ? "default" : "pointer",
        }}
      >
        {REL_TYPE_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>

      {error && (
        <div role="status" style={{
          color: "var(--hg-crit)", fontSize: 10,
          padding: "4px 0",
        }}>{error}</div>
      )}

      {/* Action row */}
      <div style={{ display: "flex", gap: 8, marginTop: 2 }}>
        <button
          onClick={save}
          disabled={saving || !dirty}
          className="hg-focusable hg-mobile-touch"
          style={{
            flex: 1,
            background: dirty ? "var(--hg-ice)" : "transparent",
            border: `1px solid ${dirty ? "var(--hg-ice)" : "var(--hg-border-soft)"}`,
            color: dirty ? "var(--hg-bg-0)" : "var(--hg-fg-4)",
            padding: "6px 10px",
            fontFamily: PEOPLE_FONT_MONO,
            fontSize: 10, letterSpacing: "0.16em",
            textTransform: "lowercase",
            cursor: (saving || !dirty) ? "default" : "pointer",
          }}
        >
          {saving ? "saving…" : "save"}
        </button>
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────
 * Sub-component: PeopleQueueView
 * ──────────────────────────────────────────────────────────────────── */
function identityFrigateNames(identity) {
  const names = new Set();
  if (!identity) return names;
  if (Array.isArray(identity.aliases)) {
    for (const a of identity.aliases) {
      if (a && a.kind === "frigate_name" && a.alias) names.add(String(a.alias).trim().toLowerCase());
    }
  }
  if (identity.display_name) names.add(String(identity.display_name).trim().toLowerCase());
  return names;
}

function unlinkedFaceBuckets(facesByPerson, identities) {
  if (!facesByPerson || typeof facesByPerson !== "object") return [];
  const linked = new Set();
  for (const identity of identities || []) {
    for (const name of identityFrigateNames(identity)) linked.add(name);
  }
  return Object.entries(facesByPerson)
    .filter(([name, captureCount]) => {
      const key = String(name || "").trim().toLowerCase();
      return key
        && !linked.has(key)
        && Number.isInteger(captureCount)
        && captureCount > 0;
    })
    .map(([name, captureCount]) => ({ name, captureCount }))
    .sort((a, b) => (
      b.captureCount - a.captureCount || a.name.localeCompare(b.name)
    ));
}

function peopleQueueDiagnostics({ identities, facesByPerson, facesStatus, frigateDiagnostics }) {
  const allIdentities = Array.isArray(identities) ? identities : [];
  const unknowns = allIdentities.filter((i) => i.relationship_type === "unknown");
  const linkedNames = new Set();
  for (const identity of allIdentities) {
    for (const name of identityFrigateNames(identity)) linkedNames.add(name);
  }
  const buckets = facesByPerson && typeof facesByPerson === "object"
    ? Object.entries(facesByPerson)
      .filter(([, captureCount]) => (
        Number.isInteger(captureCount) && captureCount >= 0
      ))
      .map(([name, captureCount]) => ({
        name,
        key: String(name || "").trim().toLowerCase(),
        count: captureCount,
        linked: linkedNames.has(String(name || "").trim().toLowerCase()),
      }))
    : [];
  const train = buckets.find((b) => b.key === "train");
  const linkedBuckets = buckets.filter((b) => b.linked && b.key !== "train");
  const unlinkedBuckets = buckets.filter((b) => b.key && !b.linked && b.key !== "train" && b.count > 0);
  const knownBuckets = buckets
    .filter((b) => b.key && b.key !== "train" && b.count > 0)
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  const caps = frigateDiagnostics?.capabilities || null;
  const seed = frigateDiagnostics?.seedReport || null;
  return {
    identityCount: allIdentities.length,
    unknownCount: unknowns.length,
    bucketCount: buckets.length,
    linkedBucketCount: linkedBuckets.length,
    unlinkedBucketCount: unlinkedBuckets.length,
    knownBucketSummary: knownBuckets.slice(0, 6).map((b) => `${b.name} ${b.count}`).join(" · "),
    trainCaptureCount: train ? train.count : null,
    facesState: facesStatus?.state || "idle",
    facesError: facesStatus?.error || null,
    faceLibraryReachable: caps ? !!caps.face_library : null,
    frigateReachable: caps ? !!caps.reachable : null,
    lastSeedFound: seed && typeof seed.frigate_faces_found === "number" ? seed.frigate_faces_found : null,
    lastSeedCreated: seed && typeof seed.identities_created === "number" ? seed.identities_created : null,
    lastSeedErrors: seed && Array.isArray(seed.errors) ? seed.errors : [],
  };
}

function QueueStat({ label, value, tone = "normal" }) {
  const color = tone === "warn" ? "var(--hg-warn)" : tone === "crit" ? "var(--hg-crit)" : "var(--hg-fg-0)";
  return (
    <div style={{
      border: "1px solid var(--hg-border-soft)",
      background: "var(--hg-bg-1)",
      padding: "10px 12px",
      minWidth: 0,
    }}>
      <div style={{
        fontFamily: PEOPLE_FONT_MONO,
        fontSize: 9,
        letterSpacing: "0.14em",
        color: "var(--hg-fg-4)",
        textTransform: "uppercase",
        marginBottom: 6,
      }}>{label}</div>
      <div style={{
        fontFamily: PEOPLE_FONT_MONO,
        fontSize: 16,
        color,
      }}>{value}</div>
    </div>
  );
}

function UnlinkedFaceBucketCard({ bucket, endpoint, token, operationScopeKey, sim, onSaved }) {
  const [name, setName] = useState(bucket.name);
  const [rel, setRel] = useState("unknown");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const beginOperation = usePeopleOperationGuard(
    `${operationScopeKey}:unlinked-face:${bucket.name}`,
  );

  async function createLinkedIdentity(mode = "identity") {
    setError(null);
    const displayName = mode === "ignore" ? `ignored ${bucket.name}` : name.trim();
    const relationshipType = mode === "ignore" ? "do_not_identify" : rel;
    if (!displayName) {
      setError("name required");
      return;
    }
    const operation = beginOperation("create");
    setSaving(true);
    try {
      if (sim?.active) {
        await new Promise((r) => setTimeout(r, 250));
        if (!operation.isCurrent()) return;
        onSaved?.({
          display_name: displayName,
          relationship_type: relationshipType,
          aliases: [{ kind: "frigate_name", alias: bucket.name }],
        });
        return;
      }
      const url = `${endpoint.replace(/\/+$/, "")}/api/extended_openai_conversation/identities/create`;
      const resp = await window.tauriFetch(url, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          display_name: displayName,
          relationship_type: relationshipType,
          notes: mode === "ignore"
            ? `ignored from unlinked Frigate face bucket ${bucket.name}`
            : `created from unlinked Frigate face bucket ${bucket.name}`,
          frigate_person_name: bucket.name,
          actor: "people_queue",
        }),
        signal: operation.signal,
      });
      if (!operation.isCurrent()) return;
      if (!resp.ok) {
        const body = await resp.text().catch(() => "");
        if (!operation.isCurrent()) return;
        setError(`HTTP ${resp.status}: ${body.slice(0, 120)}`);
        return;
      }
      const result = await resp.json();
      if (!operation.isCurrent()) return;
      if (result.error) {
        setError(result.error);
        return;
      }
      onSaved?.(result);
    } catch (e) {
      if (peopleOperationAborted(e, operation)) return;
      setError(`network error: ${e.message || e}`);
    } finally {
      if (operation.isCurrent()) setSaving(false);
      operation.finish();
    }
  }

  return (
    <div style={{
      border: "1px solid var(--hg-warn)",
      padding: 14,
      background: "color-mix(in srgb, var(--hg-warn) 8%, var(--hg-bg-1))",
      display: "flex",
      flexDirection: "column",
      gap: 10,
    }}>
      <div style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        fontFamily: PEOPLE_FONT_MONO,
        fontSize: 10,
        letterSpacing: "0.12em",
        color: "var(--hg-warn)",
      }}>
        <span>unlinked face bucket</span>
        <span style={{ marginLeft: "auto", color: "var(--hg-fg-3)" }}>{bucket.captureCount} captures</span>
      </div>
      <div style={{ fontSize: 16, color: "var(--hg-fg-0)" }}>{bucket.name}</div>
      <div style={{ fontSize: 10, color: "var(--hg-fg-4)" }}>
        face thumbnails are disabled in the legacy inspector
      </div>
      <div style={{ fontSize: 11, lineHeight: 1.5, color: "var(--hg-fg-3)" }}>
        Frigate has captures for this bucket, but Home does not have a matching identity alias.
        Name it if this is a real person, or mark it as do not identify if the bucket is junk.
      </div>
      <input
        type="text"
        value={name}
        disabled={saving}
        onChange={(e) => setName(e.target.value)}
        placeholder="person name"
        className="hg-focusable"
        style={{
          background: "var(--hg-input-bg)",
          border: "1px solid var(--hg-border-soft)",
          padding: "8px 10px",
          fontFamily: PEOPLE_FONT_SANS,
          fontSize: 14,
          color: "var(--hg-fg-0)",
        }}
      />
      <select
        value={rel}
        disabled={saving}
        onChange={(e) => setRel(e.target.value)}
        className="hg-focusable"
        style={{
          background: "var(--hg-input-bg)",
          border: "1px solid var(--hg-border-soft)",
          padding: "8px 10px",
          fontFamily: PEOPLE_FONT_MONO,
          fontSize: 11,
          color: "var(--hg-fg-1)",
          letterSpacing: "0.04em",
          cursor: saving ? "default" : "pointer",
        }}
      >
        {REL_TYPE_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
      {error && (
        <div role="status" style={{ color: "var(--hg-crit)", fontSize: 10, lineHeight: 1.4 }}>{error}</div>
      )}
      <div style={{
        display: "grid",
        gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)",
        gap: 8,
      }}>
        <button
          onClick={() => createLinkedIdentity("identity")}
          disabled={saving || !name.trim()}
          className="hg-focusable"
          style={{
            background: "var(--hg-ice)",
            border: "1px solid var(--hg-ice)",
            color: "var(--hg-bg-0)",
            padding: "8px 10px",
            fontFamily: PEOPLE_FONT_MONO,
            fontSize: 10,
            letterSpacing: "0.14em",
            textTransform: "lowercase",
            cursor: saving || !name.trim() ? "default" : "pointer",
          }}
        >
          {saving ? "saving..." : "create identity"}
        </button>
        <button
          onClick={() => createLinkedIdentity("ignore")}
          disabled={saving}
          className="hg-focusable"
          style={{
            background: "transparent",
            border: "1px solid var(--hg-border-soft)",
            color: "var(--hg-fg-2)",
            padding: "8px 10px",
            fontFamily: PEOPLE_FONT_MONO,
            fontSize: 10,
            letterSpacing: "0.14em",
            textTransform: "lowercase",
            cursor: saving ? "default" : "pointer",
          }}
        >
          do not identify
        </button>
      </div>
    </div>
  );
}

function PeopleQueueView({ identities, facesByPerson, facesStatus, frigateDiagnostics, sim, endpoint, token, operationScopeKey, avatarBlobUrls, onSaved }) {
  // The queue is everyone tagged 'unknown' — the auto-seed default for
  // Frigate-discovered faces. Once the user assigns a relationship type,
  // the card drops out (next refresh removes it from this filter).
  const unknowns = (identities || []).filter((i) => i.relationship_type === "unknown");
  const unlinkedBuckets = unlinkedFaceBuckets(facesByPerson, identities);
  const diagnostics = peopleQueueDiagnostics({ identities, facesByPerson, facesStatus, frigateDiagnostics });
  if (unknowns.length === 0 && unlinkedBuckets.length === 0) {
    return (
      <div style={{
        padding: "28px 0 60px",
        color: "var(--hg-fg-3)",
      }}>
        <div style={{
          border: "1px solid var(--hg-border-soft)",
          background: "var(--hg-bg-1)",
          padding: 16,
          maxWidth: 640,
          margin: "0 auto",
        }}>
          <div style={{
            fontFamily: PEOPLE_FONT_MONO, fontSize: 11, letterSpacing: "0.16em",
            textTransform: "uppercase", color: "var(--hg-fg-2)", marginBottom: 8,
          }}>queue is empty</div>
          <div style={{ fontSize: 13, color: "var(--hg-fg-1)", lineHeight: 1.55, marginBottom: 14 }}>
            home does not currently have any unknown identities or unlinked frigate face buckets to review.
          </div>
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
            gap: 8,
            marginBottom: 14,
          }}>
            <QueueStat label="identities" value={diagnostics.identityCount} />
            <QueueStat label="unknown" value={diagnostics.unknownCount} />
            <QueueStat label="face buckets" value={diagnostics.bucketCount || "none"} />
            <QueueStat label="unlinked" value={diagnostics.unlinkedBucketCount} />
            <QueueStat label="train" value={diagnostics.trainCaptureCount == null ? "unknown" : diagnostics.trainCaptureCount} />
          </div>
          <div style={{
            fontFamily: PEOPLE_FONT_MONO,
            fontSize: 10,
            letterSpacing: "0.08em",
            color: diagnostics.facesState === "error" ? "var(--hg-warn)" : "var(--hg-fg-3)",
            lineHeight: 1.6,
            marginBottom: 12,
          }}>
            frigate faces: {diagnostics.facesState}
            {diagnostics.facesError ? ` · ${diagnostics.facesError}` : ""}
            {diagnostics.lastSeedFound != null ? ` · last seed found ${diagnostics.lastSeedFound}` : ""}
            {diagnostics.lastSeedCreated != null ? ` · created ${diagnostics.lastSeedCreated}` : ""}
          </div>
          <div style={{ fontSize: 12, color: "var(--hg-fg-3)", lineHeight: 1.6 }}>
            {diagnostics.facesState === "loaded" && diagnostics.bucketCount > 0 ? (
              <>
                frigate is only reporting buckets already linked to home identities
                {diagnostics.trainCaptureCount === 0 ? ", and the training bucket is empty." : "."}
              </>
            ) : diagnostics.facesState === "error" ? (
              <>the people tab could not read frigate's face library, so the queue cannot see new buckets.</>
            ) : (
              <>the people tab has not loaded frigate's face library yet.</>
            )}
          </div>
          <div style={{ marginTop: 14, fontSize: 12, color: "var(--hg-fg-4)", lineHeight: 1.7 }}>
            <div>likely causes:</div>
            <div>· driveway face recognition is intentionally disabled, so passerby faces do not enter this queue</div>
            <div>· a new indoor face has not met Frigate's threshold/minimum-capture settings yet</div>
            <div>· frigate recognized the person as an existing bucket instead of creating a new one</div>
            <div>· the ha identity reseed loop has not picked up a new frigate bucket yet</div>
          </div>
          {diagnostics.knownBucketSummary && (
            <div style={{
              marginTop: 12,
              padding: 10,
              border: "1px solid var(--hg-border-soft)",
              background: "var(--hg-bg-0)",
              fontFamily: PEOPLE_FONT_MONO,
              fontSize: 10,
              letterSpacing: "0.06em",
              color: "var(--hg-fg-3)",
              lineHeight: 1.5,
            }}>
              current frigate buckets: {diagnostics.knownBucketSummary}
            </div>
          )}
          {diagnostics.lastSeedErrors.length > 0 && (
            <div style={{
              marginTop: 12,
              padding: 10,
              border: "1px solid var(--hg-warn)",
              color: "var(--hg-warn)",
              fontFamily: PEOPLE_FONT_MONO,
              fontSize: 10,
              lineHeight: 1.5,
            }}>
              seed errors: {diagnostics.lastSeedErrors.slice(0, 2).join(" · ")}
            </div>
          )}
          {sim?.active && (
            <span style={{ display: "block", marginTop: 8, color: "var(--hg-fg-4)" }}>
              (sim mode active — no real Frigate detection)
            </span>
          )}
        </div>
      </div>
    );
  }
  return (
    <div>
      <div style={{
        fontSize: 9, letterSpacing: "0.18em", textTransform: "uppercase",
        color: "var(--hg-fg-3)", marginBottom: 16,
      }}>
        {unknowns.length} unidentified identities
        {unlinkedBuckets.length > 0 ? ` + ${unlinkedBuckets.length} unlinked Frigate buckets` : ""}
      </div>
      {unlinkedBuckets.length > 0 && (
        <div style={{
          marginBottom: 18,
          padding: 12,
          border: "1px solid var(--hg-border-soft)",
          background: "var(--hg-bg-1)",
          color: "var(--hg-fg-3)",
          fontSize: 11,
          lineHeight: 1.5,
        }}>
          These buckets exist in Frigate but are not linked to Home identities. They are shown here
          so the queue does not report a false empty state.
        </div>
      )}
      <div style={{
        display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
        gap: 14,
      }}>
        {unlinkedBuckets.map((bucket) => (
          <UnlinkedFaceBucketCard
            key={bucket.name}
            bucket={bucket}
            endpoint={endpoint}
            token={token}
            operationScopeKey={operationScopeKey}
            sim={sim}
            onSaved={onSaved}
          />
        ))}
        {unknowns.map((i) => (
          <PeopleQueueCard
            key={i.uuid}
            identity={i}
            endpoint={endpoint}
            token={token}
            operationScopeKey={operationScopeKey}
            sim={sim}
            avatarBlobUrls={avatarBlobUrls}
            onSaved={onSaved}
          />
        ))}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────
 * Sub-component: PeopleDetailPanel
 *
 * Right-side slide-in panel showing a single identity's full detail +
 * inline editing. Opens when a node is clicked in the graph view OR
 * a row is clicked in the list view. Closes via close button.
 *
 * Loads the full payload (identity + relationships + preferences) from
 * GET /api/extended_openai_conversation/identity/{uuid} so we don't
 * have to compose from the list endpoint's lighter projection.
 * ──────────────────────────────────────────────────────────────────── */
function PeopleDetailPanel({ identityUuid, endpoint, token, operationScopeKey, sim, avatarPresence, avatarBlobUrls, onAvatarChanged, onClose, onChanged, readOnly = false }) {
  const detailScopeKey =
    `${operationScopeKey}:detail:${identityUuid || "closed"}`;
  const payloadScopeRef = useRef(null);
  const [storedPayload, setPayload] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [dirty, setDirty] = useState(null);   // pending patch object, or null
  const [saving, setSaving] = useState(false);
  // Destructive-action guard (Pattern C, mirrors the apartment-edit
  // remove-link two-step): closing the panel with unsaved edits arms an
  // inline confirm strip instead of discarding silently. First close/Escape
  // → strip shows; "discard" closes, "keep editing" dismisses; a second
  // close/Escape while the strip shows confirms the discard.
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  // Addendum 24 Phase 3: avatar crop modal toggle
  const [showAvatarModal, setShowAvatarModal] = useState(false);
  const H = (typeof window !== "undefined" && window.HomePeopleHelpers) || null;
  const beginOperation = usePeopleOperationGuard(detailScopeKey);
  const payload = peoplePrincipalScopedValue(
    detailScopeKey,
    payloadScopeRef.current,
    storedPayload,
    null,
  );

  // Disarm whenever the edits themselves go away (save success, identity
  // switch, discard) so a stale arm can never swallow a later first Escape.
  useEffect(() => {
    if (!dirty) setConfirmDiscard(false);
  }, [dirty]);

  // Guarded close: every panel close path (close button + the people-detail
  // layer's Escape) funnels through here so unsaved edits are never
  // dropped without an explicit second step.
  const discardAndClose = () => {
    setConfirmDiscard(false);
    setDirty(null);
    onClose();
  };
  const requestClose = () => {
    if (dirty) {
      if (!confirmDiscard) { setConfirmDiscard(true); return; }
      discardAndClose();
      return;
    }
    onClose();
  };

  // Overlay layer: stacks above people-root so one Escape press closes only
  // the panel, not the whole overlay. Non-trapping side panel — aria-modal
  // stays "false".
  const detailRootRef = useRef(null);
  window.HomeOverlay.useOverlayLayer({
    key: "people-detail",
    active: !!identityUuid,
    onEscape: () => requestClose(),
    rootRef: detailRootRef,
    trap: false,
    initialFocus: "root",
  });

  // Load detail on mount / when uuid changes
  useEffect(() => {
    // Clear before any await so a prior principal's detail never remains
    // visible during credential/endpoint revalidation.
    payloadScopeRef.current = null;
    setPayload(null);
    if (!identityUuid) {
      return undefined;
    }
    const operation = beginOperation("load");
    setLoading(true);
    setError(null);
    setDirty(null);
    (async () => {
      try {
        if (sim?.active) {
          // Synthesize a minimal payload from the sim snapshot
          const all = sim.snapshot?.people?.identities || [];
          const found = all.find((i) => i.uuid === identityUuid);
          if (operation.isCurrent()) {
            payloadScopeRef.current = detailScopeKey;
            setPayload(found ? {
              identity: found,
              relationships: [],
              preferences: [],
            } : null);
          }
          return;
        }
        const url = `${endpoint.replace(/\/+$/, "")}/api/extended_openai_conversation/identity/${identityUuid}`;
        const resp = await window.tauriFetch(url, {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store",
          signal: operation.signal,
        });
        if (!operation.isCurrent()) return;
        if (!resp.ok) {
          setError(`HTTP ${resp.status}`);
          return;
        }
        const data = await resp.json();
        if (operation.isCurrent()) {
          payloadScopeRef.current = detailScopeKey;
          setPayload(data);
        }
      } catch (e) {
        if (!peopleOperationAborted(e, operation)) {
          setError(`network error: ${e.message || e}`);
        }
      } finally {
        if (operation.isCurrent()) setLoading(false);
        operation.finish();
      }
    })();
    return operation.cancel;
    // NOTE: `sim` deliberately omitted from deps — it's an object reference
    // recreated on every parent render, which would cause this effect to
    // fire on every render (re-fetch + setState storm = visible flicker).
    // We use `sim?.active` instead (stable boolean); sim.snapshot is read
    // at call time from the closure.
  }, [
    identityUuid,
    endpoint,
    token,
    sim?.active,
    beginOperation,
    detailScopeKey,
  ]);

  function patch(field, value) {
    if (readOnly) return;
    setDirty((prev) => ({ ...(prev || {}), [field]: value }));
  }

  async function save() {
    if (readOnly) return;
    if (!dirty || !payload) return;
    const operation = beginOperation("save");
    setSaving(true);
    setError(null);
    try {
      if (sim?.active) {
        await new Promise((r) => setTimeout(r, 200));
        if (!operation.isCurrent()) return;
        onChanged?.();
        setDirty(null);
        return;
      }
      const url = `${endpoint.replace(/\/+$/, "")}/api/extended_openai_conversation/identity/${identityUuid}`;
      const resp = await window.tauriFetch(url, {
        method: "PATCH",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          ...dirty,
          expected_version: payload.identity.version,
        }),
        signal: operation.signal,
      });
      if (!operation.isCurrent()) return;
      if (!resp.ok) {
        const t = await resp.text().catch(() => "");
        if (!operation.isCurrent()) return;
        setError(`HTTP ${resp.status}: ${t.slice(0, 120)}`);
        return;
      }
      const result = await resp.json();
      if (!operation.isCurrent()) return;
      if (result.error) {
        setError(result.error);
        return;
      }
      setDirty(null);
      onChanged?.();
      // Re-fetch
      const r2 = await window.tauriFetch(url, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
        signal: operation.signal,
      });
      if (!operation.isCurrent()) return;
      if (r2.ok) {
        const nextPayload = await r2.json();
        if (operation.isCurrent()) {
          payloadScopeRef.current = detailScopeKey;
          setPayload(nextPayload);
        }
      }
    } catch (e) {
      if (peopleOperationAborted(e, operation)) return;
      setError(`network error: ${e.message || e}`);
    } finally {
      if (operation.isCurrent()) setSaving(false);
      operation.finish();
    }
  }

  /* Pattern B1 two-click arm→confirm (3s expiry) — replaces the native
   * window.confirm, which WebView2 script-dialog suppression can swallow
   * (the delete would then be silently impossible on desktop). */
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const confirmDeleteTimerRef = useRef(null);
  useEffect(() => () => {
    if (confirmDeleteTimerRef.current) clearTimeout(confirmDeleteTimerRef.current);
  }, []);
  function armOrDelete() {
    if (!confirmingDelete) {
      setConfirmingDelete(true);
      confirmDeleteTimerRef.current = setTimeout(() => {
        setConfirmingDelete(false);
        confirmDeleteTimerRef.current = null;
      }, 3000);
      return;
    }
    if (confirmDeleteTimerRef.current) { clearTimeout(confirmDeleteTimerRef.current); confirmDeleteTimerRef.current = null; }
    setConfirmingDelete(false);
    deletePerson();
  }

  async function deletePerson() {
    if (readOnly) return;
    if (!payload) return;
    const operation = beginOperation("delete");
    setSaving(true);
    setError(null);
    try {
      if (sim?.active) {
        await new Promise((r) => setTimeout(r, 200));
        if (!operation.isCurrent()) return;
        onChanged?.();
        onClose?.();
        return;
      }
      const url = `${endpoint.replace(/\/+$/, "")}/api/extended_openai_conversation/identity/${identityUuid}`;
      const resp = await window.tauriFetch(url, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
        signal: operation.signal,
      });
      if (!operation.isCurrent()) return;
      if (!resp.ok) {
        const t = await resp.text().catch(() => "");
        if (!operation.isCurrent()) return;
        setError(`HTTP ${resp.status}: ${t.slice(0, 120)}`);
        return;
      }
      onChanged?.();
      onClose?.();
    } catch (e) {
      if (peopleOperationAborted(e, operation)) return;
      setError(`network error: ${e.message || e}`);
    } finally {
      if (operation.isCurrent()) setSaving(false);
      operation.finish();
    }
  }

  // Closed state
  if (!identityUuid) return null;

  const ident = payload?.identity || null;
  const merged = ident ? { ...ident, ...(dirty || {}) } : null;

  const panel = (
    <div
      ref={detailRootRef}
      className="hg-scroll"
      style={{
        position: "fixed", top: 0, right: 0, bottom: 0,
        width: 360,
        background: "var(--hg-bg-1)",
        borderLeft: "1px solid var(--hg-border)",
        zIndex: 1100,
        display: "flex", flexDirection: "column",
        fontFamily: PEOPLE_FONT_MONO,
        animation: "people-slide-in 220ms cubic-bezier(0.16,1,0.3,1)",
        overflowY: "auto",
        overflowX: "hidden",
      }}
      role="dialog" aria-modal="false"
      aria-label="Identity detail"
    >
      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", gap: 12,
        padding: "14px 18px",
        borderBottom: "1px solid var(--hg-border-soft)",
      }}>
        <button
          onClick={requestClose}
          className="hg-focusable"
          aria-label="Close panel"
          style={{
            background: "transparent", border: "none",
            color: "var(--hg-fg-3)", cursor: "pointer",
            padding: 4, lineHeight: 0,
          }}
        >
          <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
            <path d="M3 3l8 8M11 3l-8 8" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
          </svg>
        </button>
        <span style={{
          fontSize: 9, letterSpacing: "0.18em", textTransform: "uppercase",
          color: "var(--hg-fg-4)",
        }}>identity</span>
        {ident && (
          <span style={{
            marginLeft: "auto",
            fontSize: 9, letterSpacing: "0.12em", color: "var(--hg-fg-4)",
          }}>v{ident.version}</span>
        )}
        {readOnly && (
          <span style={{
            marginLeft: ident ? 8 : "auto",
            fontSize: 8, letterSpacing: "0.12em",
            color: "var(--hg-fg-3)", textTransform: "uppercase",
          }}>legacy read-only · cutover pending</span>
        )}
      </div>

      {/* Pattern C guard strip — a close attempt with unsaved edits lands
          here instead of silently discarding them. */}
      {confirmDiscard && dirty && (
        <div role="alert" style={{
          display: "flex", alignItems: "center", gap: 8,
          margin: "14px 14px 0", padding: "8px 12px",
          border: "1px solid var(--hg-warn)",
          background: "color-mix(in oklab, var(--hg-warn) 6%, transparent)",
          color: "var(--hg-warn)",
          fontSize: 10, letterSpacing: "0.04em",
        }}>
          <span style={{ flex: 1 }}>unsaved changes</span>
          <button
            className="hg-focusable"
            onClick={discardAndClose}
            style={{
              background: "transparent", border: "1px solid var(--hg-warn)",
              color: "var(--hg-warn)", padding: "4px 9px",
              fontFamily: PEOPLE_FONT_MONO, fontSize: 10, letterSpacing: "0.12em",
              cursor: "pointer", textTransform: "lowercase", whiteSpace: "nowrap",
            }}
          >discard</button>
          <button
            className="hg-focusable"
            onClick={() => setConfirmDiscard(false)}
            style={{
              background: "transparent", border: "1px solid var(--hg-border-soft)",
              color: "var(--hg-fg-2)", padding: "4px 9px",
              fontFamily: PEOPLE_FONT_MONO, fontSize: 10, letterSpacing: "0.12em",
              cursor: "pointer", textTransform: "lowercase", whiteSpace: "nowrap",
            }}
          >keep editing</button>
        </div>
      )}

      {loading && (
        <div style={{ padding: 18, color: "var(--hg-fg-3)", fontSize: 11 }}>
          loading…
        </div>
      )}

      {error && (
        <div role="status" style={{
          margin: 14, padding: "8px 12px",
          border: "1px solid var(--hg-crit)",
          color: "var(--hg-crit)", fontSize: 10,
        }}>{error}</div>
      )}

      {!loading && !ident && !error && (
        <div style={{ padding: 18, color: "var(--hg-fg-3)", fontSize: 11 }}>
          identity not found
        </div>
      )}

      {merged && (
        <div style={{ padding: 18, display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Addendum 24 Phase 3: avatar preview + edit button */}
          <div style={{
            display: "flex", alignItems: "center", gap: 14,
          }}>
            {(() => {
              const aUrl = avatarBlobUrls && avatarBlobUrls[merged.uuid];
              const initials = H && H.initialsFor
                ? H.initialsFor(merged.display_name || "?")
                : (merged.display_name || "?").charAt(0).toUpperCase();
              return aUrl ? (
                <img src={aUrl} alt=""
                  style={{
                    width: 56, height: 56, borderRadius: "50%",
                    objectFit: "cover",
                    border: "1px solid var(--hg-border-soft)",
                  }} />
              ) : (
                <div style={{
                  width: 56, height: 56, borderRadius: "50%",
                  background: "var(--hg-bg-2)",
                  border: "1px solid var(--hg-border-soft)",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontFamily: PEOPLE_FONT_SANS,
                  fontSize: 20, color: "var(--hg-fg-1)",
                  fontWeight: 300,
                }}>{initials}</div>
              );
            })()}
            <button
              onClick={() => setShowAvatarModal(true)}
              disabled={sim?.active || readOnly}
              className="hg-focusable"
              title={readOnly ? "legacy identity is read-only" : (sim?.active ? "avatar upload disabled in sim mode" : "edit avatar")}
              style={{
                background: "transparent",
                border: "1px solid var(--hg-border-soft)",
                color: (sim?.active || readOnly) ? "var(--hg-fg-4)" : "var(--hg-fg-2)",
                padding: "6px 10px",
                fontFamily: PEOPLE_FONT_MONO,
                fontSize: 10, letterSpacing: "0.16em",
                textTransform: "lowercase",
                cursor: (sim?.active || readOnly) ? "default" : "pointer",
              }}
            >{(avatarBlobUrls && avatarBlobUrls[merged.uuid]) || (avatarPresence && avatarPresence[merged.uuid]) ? "change avatar" : "set avatar"}</button>
          </div>

          {/* Display name (editable) */}
          <div>
            <label style={{
              display: "block", fontSize: 9, letterSpacing: "0.18em",
              textTransform: "uppercase", color: "var(--hg-fg-3)",
              marginBottom: 4,
            }}>display name</label>
            <input
              type="text"
              value={merged.display_name || ""}
              onChange={(e) => patch("display_name", e.target.value)}
              className="hg-focusable"
              disabled={saving || readOnly}
              style={{
                width: "100%",
                background: "var(--hg-input-bg)",
                border: "1px solid var(--hg-border-soft)",
                padding: "7px 10px",
                fontFamily: PEOPLE_FONT_SANS,
                fontSize: 14, color: "var(--hg-fg-0)",
                    }}
            />
          </div>

          {/* Relationship type */}
          <div>
            <label style={{
              display: "block", fontSize: 9, letterSpacing: "0.18em",
              textTransform: "uppercase", color: "var(--hg-fg-3)",
              marginBottom: 4,
            }}>relationship</label>
            <select
              value={merged.relationship_type || "unknown"}
              onChange={(e) => patch("relationship_type", e.target.value)}
              className="hg-focusable"
              disabled={saving || readOnly}
              style={{
                width: "100%",
                background: "var(--hg-input-bg)",
                border: "1px solid var(--hg-border-soft)",
                padding: "7px 10px",
                fontFamily: PEOPLE_FONT_MONO,
                fontSize: 11, color: "var(--hg-fg-1)",
                letterSpacing: "0.04em",
                      cursor: (saving || readOnly) ? "default" : "pointer",
              }}
            >
              {REL_TYPE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>

          {/* Subrole (free text — e.g. "sibling", "contractor") */}
          <div>
            <label style={{
              display: "block", fontSize: 9, letterSpacing: "0.18em",
              textTransform: "uppercase", color: "var(--hg-fg-3)",
              marginBottom: 4,
            }}>subrole (optional)</label>
            <input
              type="text"
              value={merged.relationship_subrole || ""}
              onChange={(e) => patch("relationship_subrole", e.target.value || null)}
              placeholder="e.g. sibling, parent, contractor"
              className="hg-focusable"
              disabled={saving || readOnly}
              style={{
                width: "100%",
                background: "var(--hg-input-bg)",
                border: "1px solid var(--hg-border-soft)",
                padding: "6px 9px",
                fontFamily: PEOPLE_FONT_MONO,
                fontSize: 11, color: "var(--hg-fg-1)",
                    }}
            />
          </div>

          {/* Pronouns */}
          <div>
            <label style={{
              display: "block", fontSize: 9, letterSpacing: "0.18em",
              textTransform: "uppercase", color: "var(--hg-fg-3)",
              marginBottom: 4,
            }}>pronouns</label>
            <input
              type="text"
              value={merged.pronouns || ""}
              onChange={(e) => patch("pronouns", e.target.value || null)}
              placeholder="they/them"
              className="hg-focusable"
              disabled={saving || readOnly}
              style={{
                width: "100%",
                background: "var(--hg-input-bg)",
                border: "1px solid var(--hg-border-soft)",
                padding: "6px 9px",
                fontFamily: PEOPLE_FONT_MONO,
                fontSize: 11, color: "var(--hg-fg-1)",
                    }}
            />
          </div>

          {/* Aliases (read-only display in Slice 6; add UI defers to polish) */}
          {(ident.aliases || []).length > 0 && (
            <div>
              <label style={{
                display: "block", fontSize: 9, letterSpacing: "0.18em",
                textTransform: "uppercase", color: "var(--hg-fg-3)",
                marginBottom: 4,
              }}>aliases</label>
              <div style={{
                display: "flex", flexWrap: "wrap", gap: 6,
              }}>
                {(ident.aliases || []).map((a, idx) => (
                  <span key={idx} style={{
                    padding: "3px 8px",
                    border: "1px solid var(--hg-border-soft)",
                    fontSize: 10, color: "var(--hg-fg-2)",
                    background: a.kind === "frigate_name" ? "var(--hg-bg-2)" : "transparent",
                  }}>
                    {a.alias}
                    {a.kind === "frigate_name" && (
                      <span style={{
                        marginLeft: 5, fontSize: 8, letterSpacing: "0.12em",
                        color: "var(--hg-fg-4)", textTransform: "uppercase",
                      }}>frigate</span>
                    )}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Notes */}
          <div>
            <label style={{
              display: "block", fontSize: 9, letterSpacing: "0.18em",
              textTransform: "uppercase", color: "var(--hg-fg-3)",
              marginBottom: 4,
            }}>notes (visible to the assistant)</label>
            <textarea
              value={merged.notes || ""}
              onChange={(e) => patch("notes", e.target.value || null)}
              disabled={saving || readOnly}
              rows={3}
              className="hg-focusable"
              style={{
                width: "100%",
                background: "var(--hg-input-bg)",
                border: "1px solid var(--hg-border-soft)",
                padding: "7px 10px",
                fontFamily: PEOPLE_FONT_SANS,
                fontSize: 12, color: "var(--hg-fg-1)",
                resize: "vertical",
              }}
            />
          </div>

          {/* Privacy toggles */}
          <div>
            <label style={{
              display: "block", fontSize: 9, letterSpacing: "0.18em",
              textTransform: "uppercase", color: "var(--hg-fg-3)",
              marginBottom: 8,
            }}>privacy</label>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {[
                { key: "is_private",   label: "private — no proactive announcements" },
                { key: "is_silent",    label: "silent — never spoken or written in replies" },
                { key: "is_ignored",   label: "ignored — drop all detections (US-1.33)" },
                { key: "do_not_track", label: "do-not-track — exclude from aggregation" },
                { key: "is_archived",  label: "archived — soft-delete, restorable" },
              ].map((f) => (
                <label key={f.key} style={{
                  display: "flex", alignItems: "center", gap: 8,
                  fontSize: 11, color: "var(--hg-fg-1)", cursor: "pointer",
                }}>
                  <input
                    type="checkbox"
                    checked={!!merged[f.key]}
                    onChange={(e) => patch(f.key, e.target.checked)}
                    disabled={saving || readOnly}
                  />
                  <span>{f.label}</span>
                </label>
              ))}
            </div>
          </div>

          {/* Relationships (read-only summary in Slice 6) */}
          {payload.relationships && payload.relationships.length > 0 && (
            <div>
              <label style={{
                display: "block", fontSize: 9, letterSpacing: "0.18em",
                textTransform: "uppercase", color: "var(--hg-fg-3)",
                marginBottom: 6,
              }}>relationships ({payload.relationships.length})</label>
              <ul style={{
                margin: 0, padding: 0, listStyle: "none",
                fontSize: 11, color: "var(--hg-fg-2)",
              }}>
                {payload.relationships.map((r) => (
                  <li key={r.id} style={{
                    padding: "4px 0",
                    color: r.status === "ended" ? "var(--hg-fg-4)" : "var(--hg-fg-1)",
                  }}>
                    <span style={{ color: "var(--hg-fg-3)" }}>{r.rel_type}</span>
                    {" → "}
                    {r.to_display_name}
                    {r.status === "ended" && (
                      <span style={{ marginLeft: 6, fontSize: 9, color: "var(--hg-fg-4)" }}>(ended)</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Preferences — read existing + inline-add new */}
          <PreferencesSection
            identityUuid={identityUuid}
            preferences={payload.preferences || []}
            endpoint={endpoint}
            token={token}
            operationScopeKey={operationScopeKey}
            sim={sim}
            onChanged={onChanged}
            readOnly={readOnly}
          />

          <FaceCapturesSection />

          {/* Save + delete row */}
          {!readOnly && <div style={{
            display: "flex", gap: 8, marginTop: 6,
            paddingTop: 14,
            borderTop: "1px solid var(--hg-border-soft)",
          }}>
            <button
              onClick={save}
              disabled={!dirty || saving}
              className="hg-focusable hg-mobile-touch"
              style={{
                flex: 1,
                background: dirty ? "var(--hg-ice)" : "transparent",
                border: `1px solid ${dirty ? "var(--hg-ice)" : "var(--hg-border-soft)"}`,
                color: dirty ? "var(--hg-bg-0)" : "var(--hg-fg-4)",
                padding: "8px 10px",
                fontFamily: PEOPLE_FONT_MONO, fontSize: 10,
                letterSpacing: "0.16em", textTransform: "lowercase",
                cursor: (!dirty || saving) ? "default" : "pointer",
              }}
            >{saving ? "saving…" : "save changes"}</button>
            <button
              onClick={armOrDelete}
              disabled={saving}
              className="hg-focusable hg-mobile-touch"
              title={`Removes ${payload?.identity?.display_name || "this identity"}, all preferences, and queues a delete to Frigate`}
              style={{
                background: "transparent",
                border: "1px solid var(--hg-crit)",
                color: "var(--hg-crit)",
                padding: "8px 12px",
                fontFamily: PEOPLE_FONT_MONO, fontSize: 10,
                letterSpacing: "0.16em", textTransform: "lowercase",
                cursor: saving ? "default" : "pointer",
              }}
            >{confirmingDelete ? "✓ confirm delete" : "delete…"}</button>
          </div>}
        </div>
      )}

      <style>{`
        @keyframes people-slide-in {
          from { transform: translateX(20px); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
      `}</style>

      {/* Addendum 24 Phase 3: avatar crop modal — portal-mounted */}
      {showAvatarModal && ident && !readOnly && (
        <AvatarCropModal
          identity={ident}
          endpoint={endpoint}
          token={token}
          operationScopeKey={operationScopeKey}
          onClose={() => setShowAvatarModal(false)}
          onUploaded={(uuid, present) => onAvatarChanged?.(uuid, present)}
        />
      )}
    </div>
  );
  return typeof document !== "undefined" && document.body
    ? ReactDOM.createPortal(panel, document.body)
    : panel;
}

/* ─────────────────────────────────────────────────────────────────────
 * Sub-component: FaceCapturesSection (Addendum 24 Phase 1b)
 *
 * Face thumbnails are deliberately absent from the legacy inspector.
 * Reintroducing them requires a typed, authenticated byte endpoint with
 * no-store responses and explicit blob URL revocation.
 * ──────────────────────────────────────────────────────────────────── */

/* ─────────────────────────────────────────────────────────────────────
 * Sub-component: AvatarCropModal (Addendum 24 Phase 3)
 *
 * Lightweight canvas-based crop + resize for identity avatars. User
 * picks an image file → modal shows it on a <canvas> with a square
 * crop overlay → zoom slider + drag to reposition → "Save" computes
 * the crop window, draws into a 256×256 canvas, converts to JPEG via
 * toBlob, POSTs the bytes to AvatarView. Client-side resize means the
 * server doesn't need PIL (AR24-5).
 *
 * a11y: role="dialog", aria-labels on slider + buttons, Escape closes,
 * focus moves to "Save" on open. Arrow keys nudge the crop frame.
 *
 * Errors surface inline; modal stays open on failure so the user can
 * retry.
 * ──────────────────────────────────────────────────────────────────── */
const AVATAR_OUTPUT_SIZE = 256;          // px (square output)
const AVATAR_JPEG_QUALITY = 0.85;
const AVATAR_VIEWPORT_PX = 320;          // size of the in-modal canvas
const AVATAR_MAX_INPUT_BYTES = 12 * 1024 * 1024; // pre-resize input cap

function AvatarCropModal({ identity, endpoint, token, operationScopeKey, onClose, onUploaded }) {
  const fileRef = useRef(null);
  const canvasRef = useRef(null);
  const imgRef = useRef(null);
  const saveBtnRef = useRef(null);
  const [imgLoaded, setImgLoaded] = useState(false);
  const [zoom, setZoom] = useState(1.0);    // 0.5 .. 3.0
  const [offset, setOffset] = useState({ x: 0, y: 0 }); // image translation in viewport px
  const [dragging, setDragging] = useState(null);  // {startX, startY, baseX, baseY}
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [hasImage, setHasImage] = useState(false);
  const beginOperation = usePeopleOperationGuard(
    `${operationScopeKey}:avatar:${identity.uuid}`,
  );

  // Overlay layer: Escape/Tab-trap/focus via HomeOverlay — topmost-only, so
  // one press closes the modal, not the panel/overlay beneath it. The modal
  // is mounted only while shown (showAvatarModal && ident), so active: true.
  const modalRootRef = useRef(null);
  window.HomeOverlay.useOverlayLayer({
    key: "people-avatar",
    active: true,
    onEscape: () => onClose(),
    rootRef: modalRootRef,
    trap: true,
    initialFocus: saveBtnRef,
  });

  // Arrow keys nudge the crop (= shift the image opposite direction)
  useEffect(() => {
    function onKey(e) {
      if (!hasImage) return;
      const step = e.shiftKey ? 10 : 2;
      if (e.key === "ArrowUp")    { setOffset((o) => ({ x: o.x, y: o.y + step })); e.preventDefault(); }
      if (e.key === "ArrowDown")  { setOffset((o) => ({ x: o.x, y: o.y - step })); e.preventDefault(); }
      if (e.key === "ArrowLeft")  { setOffset((o) => ({ x: o.x + step, y: o.y })); e.preventDefault(); }
      if (e.key === "ArrowRight") { setOffset((o) => ({ x: o.x - step, y: o.y })); e.preventDefault(); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [hasImage]);

  // Focus management — move focus to Save when image is ready.
  useEffect(() => {
    if (hasImage && saveBtnRef.current) saveBtnRef.current.focus();
  }, [hasImage]);

  // Render the canvas every time zoom or offset changes.
  useEffect(() => {
    if (!hasImage) return;
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img) return;
    const ctx = canvas.getContext("2d");
    canvas.width = AVATAR_VIEWPORT_PX;
    canvas.height = AVATAR_VIEWPORT_PX;
    ctx.fillStyle = "rgba(0,0,0,0.8)";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    // Compute the displayed image size for a given zoom — "fit cover" at
    // zoom=1 (shortest side == viewport), then scale up by zoom.
    const aspect = img.naturalWidth / img.naturalHeight;
    let baseW, baseH;
    if (aspect >= 1) {
      baseH = AVATAR_VIEWPORT_PX;
      baseW = AVATAR_VIEWPORT_PX * aspect;
    } else {
      baseW = AVATAR_VIEWPORT_PX;
      baseH = AVATAR_VIEWPORT_PX / aspect;
    }
    const drawW = baseW * zoom;
    const drawH = baseH * zoom;
    // Center, then apply offset (offset = how much the image has shifted
    // relative to canvas center).
    const dx = (canvas.width - drawW) / 2 + offset.x;
    const dy = (canvas.height - drawH) / 2 + offset.y;
    ctx.drawImage(img, dx, dy, drawW, drawH);
    // Crop frame overlay — center square (the output will be cropped to
    // the full viewport, so the frame == viewport bounds; draw a thin
    // border to show clipping plane).
    ctx.strokeStyle = "rgba(255,255,255,0.85)";
    ctx.lineWidth = 1;
    ctx.strokeRect(0.5, 0.5, canvas.width - 1, canvas.height - 1);
  }, [zoom, offset, hasImage]);

  function pickFile() {
    fileRef.current?.click();
  }

  function onFileChange(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setError(null);
    if (file.size > AVATAR_MAX_INPUT_BYTES) {
      setError(`file too large (${Math.round(file.size / 1024 / 1024)}MB; max 12MB)`);
      return;
    }
    if (!/^image\//.test(file.type)) {
      setError("not an image file");
      return;
    }
    setImgLoaded(false);
    setHasImage(false);
    setZoom(1.0);
    setOffset({ x: 0, y: 0 });
    const operation = beginOperation("file-read");
    const reader = new FileReader();
    reader.onload = (ev) => {
      if (!operation.isCurrent()) return;
      const img = new Image();
      img.onload = () => {
        if (!operation.isCurrent()) return;
        imgRef.current = img;
        setImgLoaded(true);
        setHasImage(true);
        operation.finish();
      };
      img.onerror = () => {
        if (operation.isCurrent()) setError("failed to decode image");
        operation.finish();
      };
      img.src = ev.target.result;
    };
    reader.onerror = () => {
      if (operation.isCurrent()) setError("failed to read file");
      operation.finish();
    };
    reader.readAsDataURL(file);
  }

  function onPointerDown(ev) {
    if (!hasImage) return;
    setDragging({
      startX: ev.clientX, startY: ev.clientY,
      baseX: offset.x, baseY: offset.y,
    });
  }
  function onPointerMove(ev) {
    if (!dragging) return;
    const dx = ev.clientX - dragging.startX;
    const dy = ev.clientY - dragging.startY;
    setOffset({ x: dragging.baseX + dx, y: dragging.baseY + dy });
  }
  function onPointerUp() { setDragging(null); }

  async function save() {
    if (!hasImage) return;
    const operation = beginOperation("save");
    setError(null);
    setBusy(true);
    try {
      // Draw the viewport content into a 256×256 output canvas (the
      // viewport IS the crop frame, so we just rescale).
      const src = canvasRef.current;
      const out = document.createElement("canvas");
      out.width = AVATAR_OUTPUT_SIZE;
      out.height = AVATAR_OUTPUT_SIZE;
      const octx = out.getContext("2d");
      octx.drawImage(src, 0, 0, AVATAR_OUTPUT_SIZE, AVATAR_OUTPUT_SIZE);
      // Convert to JPEG blob (AR24-5: client-side resize)
      const blob = await new Promise((resolve, reject) => {
        out.toBlob(
          (b) => b ? resolve(b) : reject(new Error("toBlob returned null")),
          "image/jpeg",
          AVATAR_JPEG_QUALITY,
        );
      });
      if (!operation.isCurrent()) return;
      // POST as raw body (AR24-6: simpler than multipart; AvatarView
      // accepts both shapes).
      const url = `${endpoint.replace(/\/+$/, "")}/api/extended_openai_conversation/identity/${encodeURIComponent(identity.uuid)}/avatar`;
      const resp = await window.tauriFetch(url, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "image/jpeg",
        },
        body: blob,
        signal: operation.signal,
      });
      if (!operation.isCurrent()) return;
      if (!resp.ok) {
        const body = await resp.text().catch(() => "");
        if (!operation.isCurrent()) return;
        setError(`HTTP ${resp.status}: ${body.slice(0, 120)}`);
        return;
      }
      const result = await resp.json().catch(() => ({}));
      if (!operation.isCurrent()) return;
      if (result.error) { setError(result.error); return; }
      // present=true — POST succeeded, file is on disk now.
      onUploaded?.(identity.uuid, true);
      onClose();
    } catch (e) {
      if (peopleOperationAborted(e, operation)) return;
      setError(`upload failed: ${e.message || e}`);
    } finally {
      if (operation.isCurrent()) setBusy(false);
      operation.finish();
    }
  }

  async function deleteAvatar() {
    const operation = beginOperation("delete");
    setError(null);
    setBusy(true);
    try {
      const url = `${endpoint.replace(/\/+$/, "")}/api/extended_openai_conversation/identity/${encodeURIComponent(identity.uuid)}/avatar`;
      const resp = await window.tauriFetch(url, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
        signal: operation.signal,
      });
      if (!operation.isCurrent()) return;
      if (!resp.ok && resp.status !== 404) {
        setError(`HTTP ${resp.status}`);
        return;
      }
      // present=false — DELETE succeeded (or file was already gone).
      onUploaded?.(identity.uuid, false);
      onClose();
    } catch (e) {
      if (peopleOperationAborted(e, operation)) return;
      setError(`delete failed: ${e.message || e}`);
    } finally {
      if (operation.isCurrent()) setBusy(false);
      operation.finish();
    }
  }

  const modal = (
    <div
      ref={modalRootRef}
      role="dialog"
      aria-modal="true"
      aria-label={`Avatar editor for ${identity.display_name}`}
      style={{
        position: "fixed", inset: 0,
        background: "rgba(0,0,0,0.78)",
        zIndex: 1100,
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 20,
      }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div style={{
        background: "var(--hg-bg-1)",
        border: "1px solid var(--hg-border-soft)",
        padding: 20,
        maxWidth: 420,
        display: "flex", flexDirection: "column", gap: 14,
        fontFamily: PEOPLE_FONT_MONO,
      }}>
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
        }}>
          <span style={{
            fontSize: 10, letterSpacing: "0.18em", textTransform: "uppercase",
            color: "var(--hg-fg-3)",
          }}>edit avatar · {identity.display_name}</span>
          <button
            onClick={onClose}
            aria-label="close avatar editor"
            style={{
              background: "transparent", border: "none",
              color: "var(--hg-fg-3)", cursor: "pointer",
              fontSize: 16, padding: 4,
            }}
          >✕</button>
        </div>

        {!hasImage && (
          <div style={{
            display: "flex", flexDirection: "column", gap: 12, alignItems: "center",
            padding: 24, border: "1px dashed var(--hg-border-soft)",
            color: "var(--hg-fg-3)", fontSize: 11,
          }}>
            <span>pick an image (JPEG / PNG / WebP, &lt; 12 MB)</span>
            <button onClick={pickFile}
              className="hg-focusable"
              style={{
                background: "var(--hg-ice)",
                color: "var(--hg-bg-0)",
                border: "1px solid var(--hg-ice)",
                padding: "8px 16px",
                fontFamily: PEOPLE_FONT_MONO,
                fontSize: 11, letterSpacing: "0.18em",
                textTransform: "lowercase",
                cursor: "pointer",
              }}
            >choose file</button>
            <input ref={fileRef} type="file" accept="image/*"
              onChange={onFileChange}
              style={{ display: "none" }}
            />
          </div>
        )}

        {hasImage && (
          <>
            <div style={{ display: "flex", justifyContent: "center" }}>
              <canvas ref={canvasRef}
                width={AVATAR_VIEWPORT_PX} height={AVATAR_VIEWPORT_PX}
                style={{
                  border: "1px solid var(--hg-border-soft)",
                  cursor: dragging ? "grabbing" : "grab",
                  touchAction: "none",
                }}
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onPointerLeave={onPointerUp}
              />
            </div>

            <div style={{
              display: "flex", alignItems: "center", gap: 10,
              fontSize: 10, color: "var(--hg-fg-3)",
              letterSpacing: "0.08em",
            }}>
              <span>zoom</span>
              <input
                type="range" min={0.5} max={3.0} step={0.05}
                value={zoom}
                onChange={(e) => setZoom(parseFloat(e.target.value))}
                aria-label="zoom"
                style={{ flex: 1 }}
              />
              <span style={{ width: 32, textAlign: "right" }}>
                {zoom.toFixed(2)}×
              </span>
            </div>

            <div style={{
              fontSize: 10.5, color: "var(--hg-fg-3)", textAlign: "center",
              letterSpacing: "0.08em",
            }}>
              drag to reposition · arrow keys to nudge · saves as 256×256 JPEG
            </div>

            <div style={{ display: "flex", gap: 8 }}>
              <button onClick={pickFile} disabled={busy}
                className="hg-focusable"
                style={{
                  flex: 1,
                  background: "transparent",
                  border: "1px solid var(--hg-border-soft)",
                  color: "var(--hg-fg-2)",
                  padding: "8px 12px",
                  fontFamily: PEOPLE_FONT_MONO,
                  fontSize: 10, letterSpacing: "0.16em",
                  textTransform: "lowercase",
                  cursor: busy ? "default" : "pointer",
                }}
              >change image</button>
              <button onClick={deleteAvatar} disabled={busy}
                className="hg-focusable hg-mobile-touch"
                style={{
                  background: "transparent",
                  border: "1px solid var(--hg-crit)",
                  color: "var(--hg-crit)",
                  padding: "8px 12px",
                  fontFamily: PEOPLE_FONT_MONO,
                  fontSize: 10, letterSpacing: "0.16em",
                  textTransform: "lowercase",
                  cursor: busy ? "default" : "pointer",
                }}
              >remove</button>
              <button ref={saveBtnRef} onClick={save} disabled={busy}
                className="hg-focusable hg-mobile-touch"
                style={{
                  flex: 1,
                  background: "var(--hg-ice)",
                  color: "var(--hg-bg-0)",
                  border: "1px solid var(--hg-ice)",
                  padding: "8px 12px",
                  fontFamily: PEOPLE_FONT_MONO,
                  fontSize: 10, letterSpacing: "0.16em",
                  textTransform: "lowercase",
                  cursor: busy ? "default" : "pointer",
                }}
              >{busy ? "saving…" : "save"}</button>
            </div>
          </>
        )}

        {error && (
          <div role="status" style={{
            color: "var(--hg-crit)", fontSize: 10,
            padding: "4px 0",
          }}>{error}</div>
        )}
      </div>
    </div>
  );

  return typeof document !== "undefined" && document.body
    ? ReactDOM.createPortal(modal, document.body)
    : modal;
}

function FaceCapturesSection() {
  return (
    <div style={{ marginTop: 18, paddingTop: 14, borderTop: "1px solid var(--hg-border-soft)" }}>
      <label style={{
        fontFamily: PEOPLE_FONT_MONO, fontSize: 10,
        letterSpacing: "0.16em", textTransform: "lowercase",
        color: "var(--hg-fg-3)",
        display: "block", marginBottom: 8,
      }}>face captures</label>
      <div style={{ fontSize: 11, color: "var(--hg-fg-4)", fontStyle: "italic" }}>
        Face thumbnails are disabled in the legacy inspector during cutover.
      </div>
    </div>
  );
}


/* ─────────────────────────────────────────────────────────────────────
 * Sub-component: PreferencesSection
 *
 * Reads existing prefs from the parent payload; inline "+ add"
 * affordance POSTs a new preference. Common keys are offered as
 * shortcuts (light_temp_pref, music_genre, etc.) but the user can
 * type any key freeform.
 *
 * Value parser: try JSON first (so numbers + objects work cleanly),
 * fall back to raw string. Scope is optional JSON; empty = unscoped.
 * Source defaults to 'manual' (per US-4.01-4.06).
 * ──────────────────────────────────────────────────────────────────── */
const COMMON_PREF_KEYS = [
  "light_temp_pref",      // Kelvin number
  "light_brightness_pref",// 0-100 number
  "music_genre",          // string
  "music_volume",         // 0-100 number
  "tts_voice",            // string
  "scene_default",        // string
  "notes",                // string (freeform)
];

function PreferencesSection({ identityUuid, preferences, endpoint, token, operationScopeKey, sim, onChanged, readOnly = false }) {
  const [adding, setAdding] = useState(false);
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [newScope, setNewScope] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const beginOperation = usePeopleOperationGuard(
    `${operationScopeKey}:preferences:${identityUuid}`,
  );

  function reset() {
    setNewKey(""); setNewValue(""); setNewScope("");
    setError(null); setAdding(false);
  }

  async function save() {
    if (readOnly) return;
    setError(null);
    if (!newKey.trim()) { setError("key required"); return; }
    if (!newValue) { setError("value required"); return; }
    // Parse value as JSON first; fall back to raw string
    let parsedValue;
    try { parsedValue = JSON.parse(newValue); }
    catch { parsedValue = newValue; }
    let parsedScope = {};
    if (newScope.trim()) {
      try {
        parsedScope = JSON.parse(newScope);
        if (typeof parsedScope !== "object" || Array.isArray(parsedScope)) {
          setError("scope must be a JSON object");
          return;
        }
      } catch {
        setError("scope must be valid JSON");
        return;
      }
    }
    const operation = beginOperation("save");
    setSaving(true);
    try {
      if (sim?.active) {
        await new Promise((r) => setTimeout(r, 200));
        if (!operation.isCurrent()) return;
        onChanged?.(); reset();
        return;
      }
      const url = `${endpoint.replace(/\/+$/, "")}/api/extended_openai_conversation/identity/${identityUuid}/preferences`;
      const resp = await window.tauriFetch(url, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          key: newKey.trim(),
          value: parsedValue,
          scope: parsedScope,
          source: "manual",
        }),
        signal: operation.signal,
      });
      if (!operation.isCurrent()) return;
      if (!resp.ok) {
        const t = await resp.text().catch(() => "");
        if (!operation.isCurrent()) return;
        setError(`HTTP ${resp.status}: ${t.slice(0, 100)}`);
        return;
      }
      const result = await resp.json();
      if (!operation.isCurrent()) return;
      if (result.error) { setError(result.error); return; }
      onChanged?.(); reset();
    } catch (e) {
      if (peopleOperationAborted(e, operation)) return;
      setError(`network error: ${e.message || e}`);
    } finally {
      if (operation.isCurrent()) setSaving(false);
      operation.finish();
    }
  }

  return (
    <div>
      <div style={{
        display: "flex", alignItems: "baseline", marginBottom: 6,
      }}>
        <label style={{
          fontSize: 9, letterSpacing: "0.18em",
          textTransform: "uppercase", color: "var(--hg-fg-3)",
        }}>preferences ({preferences.length})</label>
        <span style={{ marginLeft: "auto" }}>
          {!adding && !readOnly && (
            <button
              onClick={() => setAdding(true)}
              className="hg-focusable hg-mobile-touch"
              style={{
                background: "transparent", border: "1px solid var(--hg-border-soft)",
                color: "var(--hg-fg-2)",
                padding: "3px 8px",
                fontFamily: PEOPLE_FONT_MONO, fontSize: 9,
                letterSpacing: "0.16em", textTransform: "lowercase",
                cursor: "pointer",
              }}
            >+ add</button>
          )}
        </span>
      </div>

      {preferences.length === 0 && !adding && (
        <div style={{ color: "var(--hg-fg-4)", fontSize: 11 }}>none set</div>
      )}

      {preferences.length > 0 && (
        <ul style={{ margin: 0, padding: 0, listStyle: "none",
                     fontSize: 11, color: "var(--hg-fg-2)" }}>
          {preferences.map((p) => (
            <li key={p.id} style={{
              padding: "5px 0",
              borderBottom: "1px solid var(--hg-border-soft)",
              display: "flex", alignItems: "baseline", gap: 8,
            }}>
              <span style={{ color: "var(--hg-fg-3)", flexShrink: 0 }}>{p.key}</span>
              <span style={{ color: "var(--hg-fg-1)", flex: 1, wordBreak: "break-word" }}>
                {typeof p.value === "object" ? JSON.stringify(p.value) : String(p.value)}
              </span>
              {p.source === "inferred" && (
                <span style={{
                  fontSize: 10.5, letterSpacing: "0.16em", color: "var(--hg-fg-3)",
                  textTransform: "uppercase",
                  title: p.confidence ? `confidence ${(p.confidence * 100).toFixed(0)}%` : null,
                }}>inferred{p.confidence ? ` ${(p.confidence * 100).toFixed(0)}%` : ""}</span>
              )}
              {Object.keys(p.scope || {}).length > 0 && (
                <span style={{
                  fontSize: 10.5, color: "var(--hg-fg-3)",
                  title: JSON.stringify(p.scope),
                }}>scoped</span>
              )}
            </li>
          ))}
        </ul>
      )}

      {adding && !readOnly && (
        <div style={{
          marginTop: 10, padding: 12,
          background: "var(--hg-bg-2)",
          border: "1px solid var(--hg-border-soft)",
          display: "flex", flexDirection: "column", gap: 8,
        }}>
          {/* Key — with common-suggestions datalist for convenience */}
          <div>
            <label style={{
              display: "block", fontSize: 9, letterSpacing: "0.16em",
              textTransform: "uppercase", color: "var(--hg-fg-4)", marginBottom: 3,
            }}>key</label>
            <input
              list="pref-key-suggestions"
              type="text"
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              disabled={saving}
              placeholder="e.g. light_temp_pref"
              className="hg-focusable"
              style={{
                width: "100%",
                background: "var(--hg-input-bg)",
                border: "1px solid var(--hg-border-soft)",
                padding: "5px 8px",
                fontFamily: PEOPLE_FONT_MONO, fontSize: 11,
                color: "var(--hg-fg-1)",
              }}
            />
            <datalist id="pref-key-suggestions">
              {COMMON_PREF_KEYS.map((k) => <option key={k} value={k} />)}
            </datalist>
          </div>
          {/* Value */}
          <div>
            <label style={{
              display: "block", fontSize: 9, letterSpacing: "0.16em",
              textTransform: "uppercase", color: "var(--hg-fg-4)", marginBottom: 3,
            }}>value <span style={{ textTransform: "none", letterSpacing: 0, fontSize: 10.5, color: "var(--hg-fg-3)" }}>(JSON or plain string)</span></label>
            <input
              type="text"
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              disabled={saving}
              placeholder='e.g. 2700 or "lofi"'
              className="hg-focusable"
              style={{
                width: "100%",
                background: "var(--hg-input-bg)",
                border: "1px solid var(--hg-border-soft)",
                padding: "5px 8px",
                fontFamily: PEOPLE_FONT_MONO, fontSize: 11,
                color: "var(--hg-fg-1)",
              }}
            />
          </div>
          {/* Scope (optional JSON) */}
          <div>
            <label style={{
              display: "block", fontSize: 9, letterSpacing: "0.16em",
              textTransform: "uppercase", color: "var(--hg-fg-4)", marginBottom: 3,
            }}>scope <span style={{ textTransform: "none", letterSpacing: 0, fontSize: 10.5, color: "var(--hg-fg-3)" }}>(optional JSON object)</span></label>
            <input
              type="text"
              value={newScope}
              onChange={(e) => setNewScope(e.target.value)}
              disabled={saving}
              placeholder='e.g. {"time_of_day":"evening"}'
              className="hg-focusable"
              style={{
                width: "100%",
                background: "var(--hg-input-bg)",
                border: "1px solid var(--hg-border-soft)",
                padding: "5px 8px",
                fontFamily: PEOPLE_FONT_MONO, fontSize: 11,
                color: "var(--hg-fg-1)",
              }}
            />
          </div>
          {error && (
            <div role="status" style={{ color: "var(--hg-crit)", fontSize: 10 }}>{error}</div>
          )}
          <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
            <button onClick={save} disabled={saving} className="hg-focusable hg-mobile-touch" style={{
              flex: 1, background: "var(--hg-ice)",
              border: "1px solid var(--hg-ice)",
              color: "var(--hg-bg-0)",
              padding: "6px 10px",
              fontFamily: PEOPLE_FONT_MONO, fontSize: 10,
              letterSpacing: "0.16em", textTransform: "lowercase",
              cursor: saving ? "default" : "pointer",
            }}>{saving ? "saving…" : "add"}</button>
            <button onClick={reset} disabled={saving} className="hg-focusable" style={{
              background: "transparent",
              border: "1px solid var(--hg-border-soft)",
              color: "var(--hg-fg-2)",
              padding: "6px 10px",
              fontFamily: PEOPLE_FONT_MONO, fontSize: 10,
              letterSpacing: "0.16em", textTransform: "lowercase",
              cursor: "pointer",
            }}>cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}

async function prewarmPeopleData() {
  // Sensitive identity, face, and avatar content is fetched only while the
  // HA-admin People surface is open. Prewarming would recreate a cross-session
  // cache before current authorization is known.
  try { delete window.__HOME_PEOPLE_DATA_CACHE; } catch {}
  return {
    ok: false,
    skipped: true,
    reason: "sensitive_people_cache_disabled",
  };
}

// Expose to window for home-app.jsx consumption
window.HomePeoplePrewarm = {
  ...(window.HomePeoplePrewarm || {}),
  start: prewarmPeopleData,
  readCache: () => null,
  writeCache: () => null,
  status: () => ({
    state: "disabled",
    reason: "sensitive_people_cache_disabled",
  }),
};
try { delete window.__HOME_PEOPLE_DATA_CACHE; } catch {}
window.HomePeopleOverlay = HomePeopleOverlay;

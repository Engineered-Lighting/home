const { useEffect, useMemo, useRef, useState } = React;

const DEFAULT_DESCRIPTOR_TEXT = "This is my parents’ mountain house.";

function capturePrincipalOperation(subject, generation) {
  return Object.freeze({ subject: subject || null, generation });
}

function principalOperationIsCurrent(ticket, subject, generation) {
  return Boolean(ticket?.subject)
    && ticket.subject === subject
    && ticket.generation === generation;
}

function HomeAgentPanel() {
  const api = useMemo(() => new window.HomeAgentApi(""), []);
  const activeSubject = useRef(null);
  const authorityGeneration = useRef(0);
  const refreshGeneration = useRef(0);
  const [phase, setPhase] = useState("loading");
  const [session, setSession] = useState(null);
  const [onboarding, setOnboarding] = useState(null);
  const [snapshot, setSnapshot] = useState(null);
  const [initiatives, setInitiatives] = useState([]);
  const [claimedInitiative, setClaimedInitiative] = useState(null);
  const [relationship, setRelationship] = useState(null);
  const [presence, setPresence] = useState(null);
  const [error, setError] = useState("");
  const [teaching, setTeaching] = useState(DEFAULT_DESCRIPTOR_TEXT);
  const [transaction, setTransaction] = useState(null);
  const [correctionText, setCorrectionText] = useState(DEFAULT_DESCRIPTOR_TEXT);
  const [lifecycle, setLifecycle] = useState(null);

  const clearPrincipalState = () => {
    authorityGeneration.current += 1;
    setOnboarding(null);
    setSnapshot(null);
    setInitiatives([]);
    setClaimedInitiative(null);
    setRelationship(null);
    setPresence(null);
    setTeaching(DEFAULT_DESCRIPTOR_TEXT);
    setTransaction(null);
    setCorrectionText(DEFAULT_DESCRIPTOR_TEXT);
    setLifecycle(null);
  };

  const beginPrincipalOperation = () => capturePrincipalOperation(
    activeSubject.current,
    authorityGeneration.current,
  );
  const principalOperationCurrent = (ticket) => principalOperationIsCurrent(
    ticket,
    activeSubject.current,
    authorityGeneration.current,
  );

  const refresh = async () => {
    const generation = ++refreshGeneration.current;
    const isCurrent = () => generation === refreshGeneration.current;
    setError("");
    try {
      const currentSession = await api.session();
      if (!isCurrent()) return;
      const subject = currentSession?.authenticated === true
        ? (api.invoke ? "native-credential" : currentSession?.user_id)
        : null;
      if (currentSession?.authenticated === true && !subject) {
        throw new Error("authenticated_session_missing_subject");
      }
      if (activeSubject.current !== subject) clearPrincipalState();
      activeSubject.current = subject;
      setSession(currentSession);
      if (currentSession?.authenticated !== true) {
        clearPrincipalState();
        setPhase("signed_out");
        return;
      }
      if (!api.invoke) {
        const nextOnboarding = await api.onboardingStatus();
        if (!isCurrent()) return;
        if (nextOnboarding?.state !== "bound" || nextOnboarding?.rollout_mode !== "canary") {
          clearPrincipalState();
          setOnboarding(nextOnboarding);
          setPhase("onboarding");
          return;
        }
        setOnboarding(nextOnboarding);
      } else {
        setOnboarding(null);
      }
      const nextSnapshot = await api.snapshot();
      if (!isCurrent()) return;
      if (api.invoke && nextSnapshot?.capabilities?.persistent_memory !== "enabled") {
        clearPrincipalState();
        setPhase("native_contained");
        return;
      }
      setSnapshot(nextSnapshot);
      const nextInitiatives = api.invoke ? await api.initiatives() : [];
      if (!isCurrent()) return;
      setInitiatives(nextInitiatives);
      setPhase("ready");
    } catch (cause) {
      if (!isCurrent()) return;
      clearPrincipalState();
      activeSubject.current = null;
      if (cause.status === 401) {
        setSession(null);
        setPhase("signed_out");
        setError("");
        return;
      }
      setPhase("contained");
      setError(cause.message || "agent_unavailable");
    }
  };

  useEffect(() => {
    refresh();
    let disposed = false;
    let unlisten = null;
    const listen = window.__TAURI__?.event?.listen;
    if (listen) {
      Promise.resolve(listen("native-auth-changed", () => { if (!disposed) refresh(); }))
        .then((stop) => { if (disposed) stop?.(); else unlisten = stop; })
        .catch(() => {});
    }
    return () => { disposed = true; unlisten?.(); };
  }, []);

  const propose = async () => {
    const ticket = beginPrincipalOperation();
    setError("");
    try {
      const visitId = snapshot?.latest_visit?.visit_id;
      if (!visitId) throw new Error("location_unresolved");
      const value = await api.proposeMemory(visitId, teaching.trim());
      if (!principalOperationCurrent(ticket)) return;
      setTransaction(value);
    } catch (cause) {
      if (!principalOperationCurrent(ticket)) return;
      setError(cause.message || "proposal_failed");
    }
  };

  const confirm = async () => {
    const ticket = beginPrincipalOperation();
    setError("");
    try {
      const value = await api.confirmMemory(transaction.transaction_id, transaction.preview_digest);
      if (!principalOperationCurrent(ticket)) return;
      setTransaction((current) => ({ ...current, ...value }));
    } catch (cause) {
      if (!principalOperationCurrent(ticket)) return;
      setError(cause.message || "confirmation_failed");
    }
  };

  const setPreference = async (key, enabled) => {
    const ticket = beginPrincipalOperation();
    setError("");
    try {
      await api.setPreference(key, enabled);
      if (!principalOperationCurrent(ticket)) return;
      await refresh();
    } catch (cause) {
      if (!principalOperationCurrent(ticket)) return;
      setError(cause.message || "preference_update_failed");
    }
  };

  const previewLifecycle = async (operation) => {
    const ticket = beginPrincipalOperation();
    setError("");
    setLifecycle(null);
    const factId = transaction?.fact_id;
    if (!factId) return setError("descriptor_fact_unavailable");
    try {
      const value = operation === "correction"
        ? await api.previewCorrection(factId, correctionText.trim())
        : operation === "retraction"
          ? await api.previewRetraction(factId)
          : await api.previewForget(factId);
      if (!principalOperationCurrent(ticket)) return;
      setLifecycle({ ...value, operation });
    } catch (cause) {
      if (!principalOperationCurrent(ticket)) return;
      setError(cause.message || `${operation}_preview_failed`);
    }
  };

  const confirmLifecycle = async () => {
    const ticket = beginPrincipalOperation();
    setError("");
    try {
      let value;
      if (lifecycle.operation === "correction") {
        value = await api.confirmCorrection(lifecycle.transaction_id, lifecycle.preview_digest);
        if (!principalOperationCurrent(ticket)) return;
        setTransaction(value);
      } else if (lifecycle.operation === "retraction") {
        value = await api.confirmRetraction(lifecycle.transaction_id, lifecycle.preview_digest);
        if (!principalOperationCurrent(ticket)) return;
        setTransaction(value);
      } else {
        value = await api.confirmForget(lifecycle.erasure_request_id, lifecycle.preview_digest);
        if (!principalOperationCurrent(ticket)) return;
        setTransaction((current) => current ? { ...current, state: "erased" } : current);
      }
      setLifecycle({ ...value, operation: lifecycle.operation, confirmed: true });
    } catch (cause) {
      if (!principalOperationCurrent(ticket)) return;
      setError(cause.message || `${lifecycle?.operation || "lifecycle"}_confirmation_failed`);
    }
  };

  const claimInitiative = async (initiativeId) => {
    const ticket = beginPrincipalOperation();
    setError("");
    try {
      const value = await api.claimInitiative(initiativeId);
      if (!principalOperationCurrent(ticket)) return;
      setClaimedInitiative(value);
      setInitiatives((current) => current.filter((item) => item.initiative_id !== initiativeId));
    } catch (cause) {
      if (!principalOperationCurrent(ticket)) return;
      setError(cause.message || "initiative_claim_failed");
    }
  };

  const queryPlace = async (kind) => {
    const ticket = beginPrincipalOperation();
    setError("");
    const placeId = transaction?.place_id || snapshot?.latest_visit?.place_id;
    if (!placeId) return setError("specific_place_unavailable");
    try {
      const value = kind === "relationship"
        ? await api.explainDescriptor(placeId)
        : await api.queryParentPresence(placeId);
      if (!principalOperationCurrent(ticket)) return;
      if (kind === "relationship") setRelationship(value);
      else setPresence(value);
    } catch (cause) {
      if (!principalOperationCurrent(ticket)) return;
      setError(cause.message || `${kind}_query_failed`);
    }
  };

  const signOut = async () => {
    // Invalidate every in-flight private result before contacting either
    // logout backend. A pending revocation must never leave private UI live.
    refreshGeneration.current += 1;
    clearPrincipalState();
    activeSubject.current = null;
    setSession(null);
    setPhase("signed_out");
    setError("");
    try {
      await api.logout();
      await refresh();
    } catch (cause) {
      const reason = cause.code || cause.message || "logout_revocation_pending";
      setSession({
        authenticated: false,
        login_enabled: false,
        reason,
      });
      setError(reason);
    }
  };

  return (
    <main className="agent-shell">
      <header className="agent-header">
        <div>
          <div className="agent-kicker">HOME AGENT</div>
          <h1 className="agent-title">Governed intelligence</h1>
        </div>
        <div className="agent-actions">
          <button className="agent-home" onClick={async () => {
            try { await api.returnHome(); }
            catch (cause) { setError(cause.message || String(cause)); }
          }}>Home</button>
          <button onClick={refresh}>Refresh</button>
          {session?.authenticated && <button onClick={signOut}>Sign out</button>}
        </div>
      </header>

      {phase === "signed_out" && (
        <section className="agent-card">
          <h2>Authentication required</h2>
          <p>The Agent surface uses Home Assistant OAuth. No long-lived token is stored in this page.</p>
          {session?.reason === "native_logout_revocation_pending" ? (
            <button onClick={async () => {
              try { await api.logout(); await refresh(); }
              catch (cause) { setError(cause.message || String(cause)); }
            }}>Retry secure sign-out</button>
          ) : <button disabled={session?.login_enabled === false} onClick={async () => {
            try {
              await api.login();
              setPhase("authenticating");
            } catch (cause) { setError(cause.message || String(cause)); }
          }}>Sign in with Home Assistant</button>}
          {session?.reason && <code>{session.reason}</code>}
          {error && error !== session?.reason && <code>{error}</code>}
        </section>
      )}

      {phase === "authenticating" && (
        <section className="agent-card">
          <h2>Complete sign-in in your browser</h2>
          <p>The desktop app is waiting on its loopback OAuth callback. No token is returned to this page.</p>
        </section>
      )}

      {phase === "contained" && (
        <section className="agent-card agent-warning" role="alert">
          <h2>Contained / unavailable</h2>
          <p>Private Agent routes are fail-closed until the BFF, Home Assistant identity, and core are configured.</p>
          {error && <code>{error}</code>}
        </section>
      )}

      {phase === "native_contained" && (
        <section className="agent-card agent-warning" role="status" aria-live="polite">
          <h2>Identity confirmed / rollout contained</h2>
          <p>The native Agent is authenticated, but Core has not enabled canary persistent-memory capability.</p>
          <p>Preferences, teaching, private queries, and initiatives stay unavailable. Location memory and travel greetings remain default-off.</p>
        </section>
      )}

      {phase === "onboarding" && (
        <section className="agent-card agent-warning" role="status" aria-live="polite">
          <h2>{onboarding?.state === "bound"
            ? "Identity confirmed / rollout contained"
            : onboarding?.state === "identity_confirmation_required"
            ? "Identity confirmation required"
            : onboarding?.state === "contained"
              ? "Identity is contained"
              : "Secure setup is still observing"}</h2>
          <p>{onboarding?.state === "bound"
            ? "Home Assistant sign-in and the semantic identity binding are confirmed, but this rollout mode does not authorize the full private Agent surface."
            : "Home Assistant sign-in succeeded. Core has not inferred a semantic identity or enabled either private location choice."}</p>
          <dl className="agent-grid">
            <dt>Rollout mode</dt><dd>{onboarding?.rollout_mode || "unknown"}</dd>
            <dt>Minimum observation window</dt><dd>{onboarding
              ? `${onboarding.phase2_observation_days_required} days`
              : "unknown"}</dd>
            <dt>Qualifying redacted-event threshold</dt><dd>{onboarding
              ? onboarding.qualifying_redacted_envelopes_required
              : "unknown"}</dd>
            <dt>Gate ready</dt><dd>{onboarding?.phase2_ready ? "yes" : "no"}</dd>
          </dl>
          {onboarding?.state === "collecting_evidence" && (
            <p>The seven-day, 500-event record-only gate cannot be skipped. Identity confirmation stays disabled until its reviewed evidence receipt is eligible.</p>
          )}
          {onboarding?.state === "identity_confirmation_required" && (
            <p>Reviewed People import and a private, explicit account-to-person confirmation are next. No mapping will be inferred from a name, device, or this session.</p>
          )}
          {onboarding?.state === "contained" && (
            <p>An existing binding is unavailable under governance or privacy policy. Core will not create a replacement automatically.</p>
          )}
          {onboarding?.state === "bound" && (
            <p>The identity binding remains usable for reviewed rollout work; preferences, teaching, and initiatives stay unavailable here until canary authorization.</p>
          )}
          <p>Location memory default: off. Travel greetings default: off.</p>
          <p>Exact activity counts, timestamps, and evidence content are intentionally omitted from this user-facing status.</p>
          <code>{onboarding?.phase2_blockers?.join(", ") || "no gate blockers"}</code>
        </section>
      )}

      {phase === "ready" && (
        <>
          <section className="agent-card">
            <h2>Current snapshot</h2>
            <dl className="agent-grid">
              <dt>HA user</dt><dd>{session?.user_id || "unknown"}</dd>
              <dt>As of</dt><dd>{snapshot?.as_of || "unknown"}</dd>
              <dt>Visit</dt><dd>{snapshot?.latest_visit?.visit_id || "unknown"}</dd>
              <dt>Coverage</dt><dd>{snapshot?.latest_visit?.coverage || "unknown"}</dd>
            </dl>
          </section>

          <section className="agent-card">
            <h2>Private location consent</h2>
            <p>Location memory and travel greetings are independent, default-off choices.</p>
            <button onClick={() => setPreference("location_memory", !snapshot?.preferences?.location_memory)}>
              Location memory: {snapshot?.preferences?.location_memory ? "on — disable" : "off — enable"}
            </button>{" "}
            <button
              disabled={!snapshot?.preferences?.location_memory && !snapshot?.preferences?.travel_greetings}
              onClick={() => setPreference("travel_greetings", !snapshot?.preferences?.travel_greetings)}
            >
              Travel greetings: {snapshot?.preferences?.travel_greetings ? "on — disable" : "off — enable"}
            </button>
          </section>

          <section className="agent-card">
            <h2>Propose place memory</h2>
            <p>The model may propose a parse, but only the deterministic verifier and your confirmation can commit it.</p>
            <textarea className="agent-textarea" value={teaching} onChange={(event) => setTeaching(event.target.value)} rows={3} />
            <button disabled={!teaching.trim() || !snapshot?.latest_visit?.visit_id} onClick={propose}>Create review transaction</button>
            {transaction && <>
              <dl className="agent-grid">
                <dt>Transaction state</dt><dd>{transaction.state}</dd>
                <dt>Locator resolution</dt><dd>{transaction.locator?.resolution || "unavailable"}</dd>
                <dt>Retained locator</dt><dd>{transaction.locator?.specific_locator_retention || "unavailable"}</dd>
                <dt>Locator radius</dt><dd>{transaction.locator?.radius_m ? `${transaction.locator.radius_m} m` : "unavailable"}</dd>
                <dt>Resolved parents</dt><dd>{transaction.resolved_parents?.map((person) => person.display_name).join(", ") || "unresolved"}</dd>
              </dl>
              <pre>{JSON.stringify(transaction, null, 2)}</pre>
            </>}
            {transaction?.state === "needs_confirmation" && transaction?.locator?.resolution !== "specific" && (
              <p className="agent-warning">Location unresolved. Keep the transaction for review, wait for at least two accurate fixes, then create a new preview. Core will not guess or create a property from this preview.</p>
            )}
            {transaction?.state === "needs_confirmation" &&
              transaction?.preview_digest &&
              transaction?.locator?.resolution === "specific" &&
              transaction?.locator?.specific_locator_retention === "will_retain_on_confirmation" && (
              <button onClick={confirm}>Confirm exact preview</button>
            )}
          </section>

          {api.invoke && (
            <section className="agent-card">
              <h2>Private travel greeting</h2>
              <p>Only a fresh, conflict-free, specifically matched visit can be claimed. The wording is rendered from the confirmed descriptor without a model.</p>
              {initiatives.length === 0 && !claimedInitiative && <p>No eligible greeting.</p>}
              {initiatives.map((initiative) => (
                <div key={initiative.initiative_id}>
                  <p>An eligible private greeting is ready. Its location wording is released only by the atomic one-time claim.</p>
                  <button onClick={() => claimInitiative(initiative.initiative_id)}>Present once</button>
                </div>
              ))}
              {claimedInitiative && <p>{claimedInitiative.message}</p>}
            </section>
          )}

          {api.invoke && (transaction?.place_id || snapshot?.latest_visit?.place_id) && (
            <section className="agent-card">
              <h2>Typed place queries</h2>
              <button onClick={() => queryPlace("relationship")}>Explain this place relationship</button>{" "}
              <button onClick={() => queryPlace("presence")}>Are my parents here?</button>
              {relationship && <pre>{JSON.stringify(relationship, null, 2)}</pre>}
              {presence && <pre>{JSON.stringify(presence, null, 2)}</pre>}
            </section>
          )}

          {transaction?.state === "committed" && transaction?.fact_id && (
            <section className="agent-card">
              <h2>Correct, retract, or forget this descriptor</h2>
              <p>Every operation first shows Core’s exact invalidation and preservation scope. Parent facts, visits, the place, and its locator are not generic edit targets.</p>
              <textarea className="agent-textarea" value={correctionText} onChange={(event) => setCorrectionText(event.target.value)} rows={2} />
              <button disabled={!correctionText.trim()} onClick={() => previewLifecycle("correction")}>Preview correction</button>{" "}
              <button onClick={() => previewLifecycle("retraction")}>Preview retraction</button>{" "}
              <button onClick={() => previewLifecycle("forget")}>Preview descriptor-only forgetting</button>
              {lifecycle && <pre>{JSON.stringify(lifecycle, null, 2)}</pre>}
              {lifecycle?.preview_digest && !lifecycle?.confirmed && (
                <button onClick={confirmLifecycle}>Confirm {lifecycle.operation}</button>
              )}
            </section>
          )}
        </>
      )}

      {error && phase === "ready" && <p className="agent-error">{error}</p>}
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<HomeAgentPanel />);

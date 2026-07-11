const { useEffect, useMemo, useState } = React;

function HomeAgentPanel() {
  const api = useMemo(() => new window.HomeAgentApi(""), []);
  const [phase, setPhase] = useState("loading");
  const [session, setSession] = useState(null);
  const [snapshot, setSnapshot] = useState(null);
  const [initiatives, setInitiatives] = useState([]);
  const [claimedInitiative, setClaimedInitiative] = useState(null);
  const [relationship, setRelationship] = useState(null);
  const [presence, setPresence] = useState(null);
  const [error, setError] = useState("");
  const [teaching, setTeaching] = useState("This is my parents’ mountain house.");
  const [transaction, setTransaction] = useState(null);
  const [correctionText, setCorrectionText] = useState("This is my parents’ mountain house.");
  const [lifecycle, setLifecycle] = useState(null);

  const refresh = async () => {
    setError("");
    try {
      const currentSession = await api.session();
      setSession(currentSession);
      if (currentSession?.authenticated !== true) {
        setSnapshot(null);
        setPhase("signed_out");
        return;
      }
      const nextSnapshot = await api.snapshot();
      setSnapshot(nextSnapshot);
      setInitiatives(api.invoke ? await api.initiatives() : []);
      setPhase("ready");
    } catch (cause) {
      setPhase(cause.status === 401 ? "signed_out" : "contained");
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
    setError("");
    try {
      const visitId = snapshot?.latest_visit?.visit_id;
      if (!visitId) throw new Error("location_unresolved");
      const value = await api.proposeMemory(visitId, teaching.trim());
      setTransaction(value);
    } catch (cause) {
      setError(cause.message || "proposal_failed");
    }
  };

  const confirm = async () => {
    setError("");
    try {
      const value = await api.confirmMemory(transaction.transaction_id, transaction.preview_digest);
      setTransaction((current) => ({ ...current, ...value }));
    } catch (cause) {
      setError(cause.message || "confirmation_failed");
    }
  };

  const setPreference = async (key, enabled) => {
    setError("");
    try {
      await api.setPreference(key, enabled);
      await refresh();
    } catch (cause) {
      setError(cause.message || "preference_update_failed");
    }
  };

  const previewLifecycle = async (operation) => {
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
      setLifecycle({ ...value, operation });
    } catch (cause) {
      setError(cause.message || `${operation}_preview_failed`);
    }
  };

  const confirmLifecycle = async () => {
    setError("");
    try {
      let value;
      if (lifecycle.operation === "correction") {
        value = await api.confirmCorrection(lifecycle.transaction_id, lifecycle.preview_digest);
        setTransaction(value);
      } else if (lifecycle.operation === "retraction") {
        value = await api.confirmRetraction(lifecycle.transaction_id, lifecycle.preview_digest);
        setTransaction(value);
      } else {
        value = await api.confirmForget(lifecycle.erasure_request_id, lifecycle.preview_digest);
        setTransaction((current) => current ? { ...current, state: "erased" } : current);
      }
      setLifecycle({ ...value, operation: lifecycle.operation, confirmed: true });
    } catch (cause) {
      setError(cause.message || `${lifecycle?.operation || "lifecycle"}_confirmation_failed`);
    }
  };

  const claimInitiative = async (initiativeId) => {
    setError("");
    try {
      const value = await api.claimInitiative(initiativeId);
      setClaimedInitiative(value);
      setInitiatives((current) => current.filter((item) => item.initiative_id !== initiativeId));
    } catch (cause) {
      setError(cause.message || "initiative_claim_failed");
    }
  };

  const queryPlace = async (kind) => {
    setError("");
    const placeId = transaction?.place_id || snapshot?.latest_visit?.place_id;
    if (!placeId) return setError("specific_place_unavailable");
    try {
      if (kind === "relationship") setRelationship(await api.explainDescriptor(placeId));
      else setPresence(await api.queryParentPresence(placeId));
    } catch (cause) {
      setError(cause.message || `${kind}_query_failed`);
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
          {session?.authenticated && <button onClick={async () => {
            try { await api.logout(); await refresh(); }
            catch (cause) { setError(cause.message || String(cause)); }
          }}>Sign out</button>}
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
        </section>
      )}

      {phase === "authenticating" && (
        <section className="agent-card">
          <h2>Complete sign-in in your browser</h2>
          <p>The desktop app is waiting on its loopback OAuth callback. No token is returned to this page.</p>
        </section>
      )}

      {phase === "contained" && (
        <section className="agent-card agent-warning">
          <h2>Contained / unavailable</h2>
          <p>Private Agent routes are fail-closed until the BFF, Home Assistant identity, and core are configured.</p>
          {error && <code>{error}</code>}
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

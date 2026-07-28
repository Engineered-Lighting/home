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

function publicNativeInstallationMaterial(session, isNative) {
  const jwk = session?.public_jwk;
  if (!isNative || !session?.installation_id || !jwk) return null;
  const values = [
    session.installation_id,
    jwk.kty,
    jwk.crv,
    jwk.x,
    jwk.y,
    jwk.kid,
  ];
  if (!values.every((value) => typeof value === "string" && value.length > 0)) return null;
  return Object.freeze({
    installation_id: session.installation_id,
    public_jwk: Object.freeze({
      kty: jwk.kty,
      crv: jwk.crv,
      x: jwk.x,
      y: jwk.y,
      kid: jwk.kid,
    }),
  });
}

function fullAgentCapabilityEnabled(snapshot) {
  return snapshot?.rollout_mode === "canary"
    && snapshot?.capabilities?.persistent_memory === "enabled";
}

function containedPreferenceState(snapshot) {
  // A contained UI intentionally discards principal/person/visit identifiers
  // and every other snapshot field after extracting the two revocable choices.
  const rolloutMode = ["record_only", "shadow", "canary"].includes(snapshot?.rollout_mode)
    ? snapshot.rollout_mode
    : "unknown";
  return Object.freeze({
    rollout_mode: rolloutMode,
    location_memory: snapshot?.preferences?.location_memory === true,
    travel_greetings: snapshot?.preferences?.travel_greetings === true,
    opt_out_enabled: snapshot?.capabilities?.preference_opt_out === "enabled",
  });
}

function ParentRelationshipCard({ status, busy, onStage, onConfirm }) {
  const recognized = new Set([
    "not_started",
    "ready_for_confirmation",
    "confirmed",
  ]).has(status?.state);
  return (
    <section className="agent-card" aria-live="polite" aria-busy={busy}>
      <h2>Private relationship review</h2>
      {!recognized && <>
        <h3>Relationship status unavailable</h3>
        <p>Core did not return a recognized, recoverable state. No relationship can be inferred or confirmed.</p>
      </>}
      {status?.state === "not_started" && <>
        <p>Review the two People records previously classified as Marceloâ€™s parents. Staging creates only a private 15-minute preview.</p>
        <button disabled={busy} onClick={onStage}>
          {busy ? "Preparing reviewâ€¦" : "Review parent relationships"}
        </button>
      </>}
      {status?.state === "ready_for_confirmation" && <>
        <h3>Two reviewed relationships are ready</h3>
        <p>{status.confirmation_statement}</p>
        <dl className="agent-grid">
          {status.candidates?.map((candidate) => (
            <React.Fragment key={candidate.ordinal}>
              <dt>Candidate {candidate.ordinal + 1}</dt>
              <dd>{candidate.reviewed_display_label} <code>{candidate.review_code}</code></dd>
            </React.Fragment>
          ))}
          <dt>Preview expires</dt><dd>{status.expires_at || "unavailable"}</dd>
        </dl>
        <p>This creates exactly two private <code>parent_of</code> facts. It does not assert ownership, residence, current presence, or permission to act.</p>
        <button disabled={busy} onClick={onConfirm}>
          {busy ? "Confirming bothâ€¦" : "Confirm both parent relationships"}
        </button>
      </>}
      {status?.state === "confirmed" && <>
        <h3>Parent relationships confirmed</h3>
        <p>Core atomically committed exactly {status.fact_count} private relationship facts.</p>
        <dl className="agent-grid">
          <dt>Confirmed</dt><dd>{status.confirmed_at || "unavailable"}</dd>
          <dt>Location memory</dt><dd>off</dd>
          <dt>Travel greetings</dt><dd>off</dd>
        </dl>
      </>}
    </section>
  );
}

function HomeAgentPanel() {
  const api = useMemo(() => new window.HomeAgentApi(""), []);
  const activeSubject = useRef(null);
  const authorityGeneration = useRef(0);
  const refreshGeneration = useRef(0);
  const onboardingStatusRef = useRef(null);
  const bindingStatusRef = useRef(null);
  const bindingFocusPending = useRef(false);
  const [phase, setPhase] = useState("loading");
  const [session, setSession] = useState(null);
  const [onboarding, setOnboarding] = useState(null);
  const [bindingProposal, setBindingProposal] = useState(null);
  const [bindingBusy, setBindingBusy] = useState(false);
  const [parentRelationship, setParentRelationship] = useState(null);
  const [parentRelationshipBusy, setParentRelationshipBusy] = useState(false);
  const [snapshot, setSnapshot] = useState(null);
  const [containedPreferences, setContainedPreferences] = useState(null);
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
    setBindingProposal(null);
    setBindingBusy(false);
    setParentRelationship(null);
    setParentRelationshipBusy(false);
    setSnapshot(null);
    setContainedPreferences(null);
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
      if (activeSubject.current !== subject) {
        bindingFocusPending.current = false;
        clearPrincipalState();
      }
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
        if (nextOnboarding?.state !== "bound") {
          clearPrincipalState();
          setOnboarding(nextOnboarding);
          if (nextOnboarding?.state === "identity_confirmation_required") {
            const ticket = beginPrincipalOperation();
            const nextBindingProposal = await api.principalBindingProposal();
            if (!isCurrent() || !principalOperationCurrent(ticket)) return;
            setBindingProposal(nextBindingProposal);
          }
          setPhase("onboarding");
          return;
        }
        setOnboarding(nextOnboarding);
        if (nextOnboarding?.parent_relationship_confirmation === "enabled") {
          const ticket = beginPrincipalOperation();
          const nextParentRelationship = await api.parentRelationshipStatus();
          if (!isCurrent() || !principalOperationCurrent(ticket)) return;
          setParentRelationship(nextParentRelationship);
        } else {
          setParentRelationship(null);
        }
      } else {
        setOnboarding(null);
        setParentRelationship(null);
      }
      const nextSnapshot = await api.snapshot();
      if (!isCurrent()) return;
      if (!fullAgentCapabilityEnabled(nextSnapshot)) {
        setSnapshot(null);
        setContainedPreferences(containedPreferenceState(nextSnapshot));
        setPhase("rollout_contained");
        return;
      }
      setContainedPreferences(null);
      setSnapshot(nextSnapshot);
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
      bindingFocusPending.current = false;
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

  useEffect(() => {
    if (!bindingFocusPending.current || phase !== "onboarding") return;
    if (onboarding?.state === "identity_confirmation_required" && !bindingProposal?.state) return;
    const target = bindingStatusRef.current || onboardingStatusRef.current;
    if (!target) return;
    bindingFocusPending.current = false;
    target.focus();
  }, [phase, onboarding?.state, bindingProposal?.state]);

  const requestPrincipalBinding = async () => {
    const ticket = beginPrincipalOperation();
    setBindingBusy(true);
    setError("");
    try {
      await api.requestPrincipalBinding();
      if (!principalOperationCurrent(ticket)) return;
      bindingFocusPending.current = true;
      await refresh();
    } catch (cause) {
      if (!principalOperationCurrent(ticket)) return;
      setBindingBusy(false);
      setError(cause.message || "principal_binding_request_failed");
    }
  };

  const cancelPrincipalBindingRequest = async () => {
    const ticket = beginPrincipalOperation();
    setBindingBusy(true);
    setError("");
    try {
      await api.cancelPrincipalBindingRequest();
      if (!principalOperationCurrent(ticket)) return;
      bindingFocusPending.current = true;
      await refresh();
    } catch (cause) {
      if (!principalOperationCurrent(ticket)) return;
      setBindingBusy(false);
      setError(cause.message || "principal_binding_cancel_failed");
    }
  };

  const stageParentRelationship = async () => {
    const ticket = beginPrincipalOperation();
    setParentRelationshipBusy(true);
    setError("");
    try {
      const value = await api.stageParentRelationship();
      if (!principalOperationCurrent(ticket)) return;
      setParentRelationship(value);
      setParentRelationshipBusy(false);
    } catch (cause) {
      if (!principalOperationCurrent(ticket)) return;
      setParentRelationshipBusy(false);
      setError(cause.message || "parent_relationship_stage_failed");
    }
  };

  const confirmParentRelationship = async () => {
    const ticket = beginPrincipalOperation();
    setParentRelationshipBusy(true);
    setError("");
    try {
      await api.confirmParentRelationship(
        parentRelationship?.proposal_id,
        parentRelationship?.proposal_digest,
      );
      if (!principalOperationCurrent(ticket)) return;
      await refresh();
    } catch (cause) {
      if (!principalOperationCurrent(ticket)) return;
      setParentRelationshipBusy(false);
      setError(cause.message || "parent_relationship_confirmation_failed");
    }
  };

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

  const enablePreference = async (key) => {
    const ticket = beginPrincipalOperation();
    setError("");
    try {
      await api.setPreference(key, true);
      if (!principalOperationCurrent(ticket)) return;
      await refresh();
    } catch (cause) {
      if (!principalOperationCurrent(ticket)) return;
      setError(cause.message || "preference_update_failed");
    }
  };

  const disablePreference = async (key) => {
    const ticket = beginPrincipalOperation();
    setError("");
    try {
      await api.disablePreference(key);
      if (!principalOperationCurrent(ticket)) return;
      await refresh();
    } catch (cause) {
      if (!principalOperationCurrent(ticket)) return;
      setError(cause.message || "preference_opt_out_failed");
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
    bindingFocusPending.current = false;
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

  const nativeInstallationMaterial = publicNativeInstallationMaterial(
    session,
    Boolean(api.invoke),
  );
  const preferenceOptInEnabled = snapshot?.capabilities?.preference_opt_in === "enabled";
  const preferenceOptOutEnabled = snapshot?.capabilities?.preference_opt_out === "enabled";

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

      {phase === "rollout_contained" && (
        <section className="agent-card agent-warning" role="status" aria-live="polite">
          <h2>Identity confirmed / rollout contained</h2>
          <p>Core rollout mode <code>{containedPreferences?.rollout_mode || "unknown"}</code> disables precise-location retention, visit projection, teaching, private queries, and initiatives.</p>
          <p>Stored preference values remain visible only so you can revoke a choice left enabled before containment. They are not effective location authority.</p>
          <dl className="agent-grid">
            <dt>Stored location memory choice</dt>
            <dd>{containedPreferences?.location_memory ? "on — ineffective" : "off"}</dd>
            <dt>Stored travel greeting choice</dt>
            <dd>{containedPreferences?.travel_greetings ? "on — ineffective" : "off"}</dd>
            <dt>Effective location retention</dt><dd>disabled</dd>
            <dt>Effective visit projection</dt><dd>disabled</dd>
          </dl>
          {containedPreferences?.location_memory && (
            <button
              disabled={!containedPreferences.opt_out_enabled}
              onClick={() => disablePreference("location_memory")}
            >Disable stored location memory choice</button>
          )}{" "}
          {containedPreferences?.travel_greetings && (
            <button
              disabled={!containedPreferences.opt_out_enabled}
              onClick={() => disablePreference("travel_greetings")}
            >Disable stored travel greeting choice</button>
          )}
          {!containedPreferences?.location_memory &&
            !containedPreferences?.travel_greetings && (
              <p>No private location opt-in is stored.</p>
            )}
          <p>Enabling either choice remains unavailable in this rollout mode.</p>
          {error && <p className="agent-error" role="alert">{error}</p>}
        </section>
      )}

      {!api.invoke
        && onboarding?.parent_relationship_confirmation === "enabled"
        && new Set(["rollout_contained", "ready"]).has(phase) && (
        <ParentRelationshipCard
          status={parentRelationship}
          busy={parentRelationshipBusy}
          onStage={stageParentRelationship}
          onConfirm={confirmParentRelationship}
        />
      )}

      {nativeInstallationMaterial && (
        <section className="agent-card">
          <h2>Public installation enrollment material</h2>
          <p>This public key material is not proof that enrollment is complete. A private operator must bind it offline to your exact Home Assistant user UUID.</p>
          <pre>{JSON.stringify(nativeInstallationMaterial, null, 2)}</pre>
        </section>
      )}

      {phase === "onboarding" && (
        <section
          className="agent-card agent-warning"
          ref={onboardingStatusRef}
          tabIndex="-1"
          role="status"
          aria-live="polite"
        >
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
            <>
              <p>Reviewed People import and a private, explicit account-to-person confirmation are next. No mapping will be inferred from a name, device, or this session.</p>
              <div
                className="agent-binding-status"
                ref={bindingStatusRef}
                tabIndex="-1"
                aria-live="polite"
                aria-atomic="true"
                aria-busy={bindingBusy}
              >
                {bindingProposal?.state === "not_requested" && <>
                  <h3>Identity review has not been requested</h3>
                  <p>Request a private operator review. This sends no person choice from the browser and creates no binding.</p>
                  <button disabled={bindingBusy} onClick={requestPrincipalBinding}>
                    {bindingBusy ? "Requesting review…" : "Request identity review"}
                  </button>
                </>}
                {bindingProposal?.state === "awaiting_operator_review" && <>
                  <h3>Awaiting private operator review</h3>
                  <p>No identity is selected or bound yet. Return here after the reviewed candidate is staged.</p>
                  <p>Review code <code>{bindingProposal.review_code}</code></p>
                  <button disabled={bindingBusy} onClick={cancelPrincipalBindingRequest}>
                    {bindingBusy ? "Cancelling request…" : "Cancel identity review request"}
                  </button>
                </>}
                {bindingProposal?.state === "ready_for_confirmation" && <>
                  <h3>Reviewed identity staged / confirmation disabled</h3>
                  <p id="principal-binding-preview">{bindingProposal.confirmation_statement}</p>
                  <dl className="agent-grid">
                    <dt>Confirmation expires</dt><dd>{bindingProposal.expires_at || "unavailable"}</dd>
                  </dl>
                  <p><code>capability_disabled</code>: the candidate cannot create a principal, confirmation artifact, or binding until the atomic database confirmation kernel is deployed.</p>
                  <button disabled={bindingBusy} onClick={cancelPrincipalBindingRequest}>
                    Cancel identity review request
                  </button>
                </>}
                {bindingProposal?.state === "unavailable" && <>
                  <h3>Identity review unavailable</h3>
                  <p>Core cannot safely offer or confirm a reviewed identity for this account. No replacement mapping will be inferred.</p>
                </>}
                {!new Set([
                  "not_requested",
                  "awaiting_operator_review",
                  "ready_for_confirmation",
                  "unavailable",
                ]).has(bindingProposal?.state) && <>
                  <h3>Identity review unavailable</h3>
                  <p>The binding workflow failed closed because its status was not recognized.</p>
                </>}
              </div>
            </>
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
          {error && <p className="agent-error" role="alert">{error}</p>}
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
            {snapshot?.preferences?.location_memory ? (
              <button
                disabled={!preferenceOptOutEnabled}
                onClick={() => disablePreference("location_memory")}
              >Location memory: on — disable</button>
            ) : preferenceOptInEnabled ? (
              <button onClick={() => enablePreference("location_memory")}>
                Location memory: off — enable
              </button>
            ) : <span>Location memory: off</span>}{" "}
            {snapshot?.preferences?.travel_greetings ? (
              <button
                disabled={!preferenceOptOutEnabled}
                onClick={() => disablePreference("travel_greetings")}
              >Travel greetings: on — disable</button>
            ) : preferenceOptInEnabled && snapshot?.preferences?.location_memory ? (
              <button onClick={() => enablePreference("travel_greetings")}>
                Travel greetings: off — enable
              </button>
            ) : <span>Travel greetings: off</span>}
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

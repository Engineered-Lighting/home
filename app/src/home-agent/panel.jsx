const { useEffect, useMemo, useRef, useState } = React;

const DEFAULT_DESCRIPTOR_TEXT = "This is my parents’ mountain house.";

function sharedLinkCeremonyFromHash(hash) {
  return /^#shared-link\/([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$/.exec(hash || "")?.[1] || null;
}

function sharedLinkReviewValid(value, ceremonyId, now) {
  const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
  return value?.version === 1 && value.ceremony_id === ceremonyId &&
    typeof value.gesture_id === "string" && uuid.test(value.gesture_id) &&
    typeof value.reviewed_digest === "string" && /^[a-f0-9]{64}$/.test(value.reviewed_digest) &&
    typeof value.expires_at === "string" && Number.isFinite(Date.parse(value.expires_at)) &&
    Date.parse(value.expires_at) > now && Date.parse(value.expires_at) <= now + 300_000 &&
    Array.isArray(value.accounts) && value.accounts.length === 2 &&
    value.accounts.every((account, index) => account.site_id === (index ? "victoria" : "echo") &&
      account.issuer_id === `home-assistant:${account.site_id}` &&
      typeof account.subject === "string" && account.subject.length > 0 &&
      [...account.subject].length <= 64 && account.subject.trim() === account.subject &&
      !/[\x00-\x1f\x7f]/.test(account.subject));
}

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
    private_locality_approval: snapshot?.capabilities?.private_locality_approval,
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
        <p>Review the two People records previously classified as Marcelo’s parents. Staging creates only a private 15-minute preview.</p>
        <button disabled={busy} onClick={onStage}>
          {busy ? "Preparing review…" : "Review parent relationships"}
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
          {busy ? "Confirming both…" : "Confirm both parent relationships"}
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

function HouseholdCard({
  people, relationships, error,
  busy, partnerChoice, onPartnerChoice, onAttestPartner,
  personName, onPersonName, onAddPerson,
  edgeDraft, onEdgeDraft, onAttestEdge,
}) {
  // Read-only. Core applies the visibility rule -- privacy directives, edge
  // blocks and erasure -- so anything absent here is absent deliberately and
  // must never be reconstructed client-side from another response.
  const edgesFor = (personId) =>
    (relationships || []).filter(
      (edge) => edge.subject_person_id === personId || edge.object_person_id === personId,
    );
  return (
    <section className="agent-card" aria-live="polite">
      <h2>Household</h2>
      {error && <p>The household could not be loaded. Nothing is inferred from a failed read.</p>}
      {!error && (people || []).length === 0 && <p>No people are visible to you.</p>}
      {!error && (people || []).length > 0 && (
        <ul className="agent-people">
          {people.map((person) => {
            const edges = edgesFor(person.person_id);
            return (
              <li key={person.person_id}>
                <strong>{person.display_name}</strong>
                {person.is_self && <span className="agent-people-self"> — you</span>}
                {person.pronouns && <span className="agent-people-pronouns"> ({person.pronouns})</span>}
                {edges.length > 0 && (
                  <ul>
                    {edges.map((edge) => (
                      <li key={edge.fact_id}>
                        {edge.subject_person_id === person.person_id
                          ? `parent of ${edge.object_display_name}`
                          : `child of ${edge.subject_display_name}`}
                        {edge.authority === "authorized_administrator" && " (recorded by you)"}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {!error && (relationships || []).length === 0 && (people || []).length > 0 && (
        <p>No confirmed relationships yet.</p>
      )}
      {!error && onAddPerson && (
        <>
          <h3>Add someone</h3>
          <p>
            Adding someone records that your household knows them. It does not
            give them an account or any authority here.
          </p>
          <label htmlFor="agent-person-name">Name</label>
          <input
            id="agent-person-name"
            type="text"
            maxLength={255}
            value={personName}
            disabled={busy}
            onChange={(event) => onPersonName(event.target.value)}
          />
          <button
            disabled={busy || !personName.trim()}
            onClick={() => onAddPerson(personName)}
          >
            {busy ? "Adding…" : "Add to household"}
          </button>
        </>
      )}
      {!error && onAttestPartner && (people || []).length > 0 && (
        <>
          <h3>Record a partner</h3>
          <p>
            You are recording this yourself. It is stored as your account&rsquo;s
            statement about your household, not as something the other person
            confirmed.
          </p>
          <label htmlFor="agent-partner-select">Partner</label>
          <select
            id="agent-partner-select"
            value={partnerChoice || ""}
            disabled={busy}
            onChange={(event) => onPartnerChoice(event.target.value)}
          >
            <option value="">Choose someone…</option>
            {people
              .filter((person) => !person.is_self)
              .map((person) => (
                <option key={person.person_id} value={person.person_id}>
                  {person.display_name}
                </option>
              ))}
          </select>
          <button
            disabled={busy || !partnerChoice}
            onClick={() => onAttestPartner(partnerChoice)}
          >
            {busy ? "Recording…" : "Record partner"}
          </button>
        </>
      )}
      {!error && onAttestEdge && (people || []).length > 1 && (
        <>
          <h3>Record a relationship between two people</h3>
          <p>
            For people other than yourself &mdash; who is whose parent, who is
            partnered with whom. Recorded as your account&rsquo;s statement, the
            same as above, and never as something either person confirmed.
          </p>
          <label htmlFor="agent-edge-subject">Person</label>
          <select
            id="agent-edge-subject"
            value={edgeDraft.subject || ""}
            disabled={busy}
            onChange={(event) => onEdgeDraft({ subject: event.target.value })}
          >
            <option value="">Choose someone…</option>
            {people.map((person) => (
              <option key={person.person_id} value={person.person_id}>
                {person.display_name}
              </option>
            ))}
          </select>
          <label htmlFor="agent-edge-predicate">Relationship</label>
          <select
            id="agent-edge-predicate"
            value={edgeDraft.predicate}
            disabled={busy}
            onChange={(event) => onEdgeDraft({ predicate: event.target.value })}
          >
            {/* The kernel accepts these two and refuses anything else, so the
                list is closed here rather than free text. */}
            <option value="parent_of">is a parent of</option>
            <option value="partner_of">is partnered with</option>
          </select>
          <label htmlFor="agent-edge-object">Person</label>
          <select
            id="agent-edge-object"
            value={edgeDraft.object || ""}
            disabled={busy}
            onChange={(event) => onEdgeDraft({ object: event.target.value })}
          >
            <option value="">Choose someone…</option>
            {people
              .filter((person) => person.person_id !== edgeDraft.subject)
              .map((person) => (
                <option key={person.person_id} value={person.person_id}>
                  {person.display_name}
                </option>
              ))}
          </select>
          <button
            disabled={
              busy
              || !edgeDraft.subject
              || !edgeDraft.object
              || edgeDraft.subject === edgeDraft.object
            }
            onClick={() => onAttestEdge(edgeDraft)}
          >
            {busy ? "Recording…" : "Record relationship"}
          </button>
        </>
      )}
    </section>
  );
}

function SharedPreferenceConsent({ api }) {
  const [status, setStatus] = useState("idle");
  const [review, setReview] = useState(null);
  const [checked, setChecked] = useState(false);
  const [now, setNow] = useState(Date.now());
  const running = useRef(null);
  const alive = useRef(true);
  const blocked = useRef(false);
  const dispatched = useRef(false);
  const retained = useRef(null);
  useEffect(() => {
    const unsubscribe = api.subscribeAuthority(() => {
      blocked.current = true; running.current?.abort(); retained.current = null;
      setReview(null); setChecked(false); setStatus("unavailable");
    });
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => { alive.current = false; running.current?.abort(); unsubscribe(); window.clearInterval(timer); };
  }, [api]);
  const busy = ["loading", "confirming", "checking"].includes(status);
  const expired = review && now >= Date.parse(review.expires_at);
  async function perform(kind) {
    if (running.current || !alive.current || blocked.current) return;
    if (kind === "confirm" && (!checked || !review || dispatched.current || Date.now() >= Date.parse(review.expires_at))) return;
    if (kind === "propose" && dispatched.current || kind === "outcome" && !retained.current) return;
    const controller = new AbortController(), generation = api.authorityGeneration;
    running.current = controller;
    const current = () => alive.current && !blocked.current && !controller.signal.aborted && generation === api.authorityGeneration;
    setStatus(kind === "propose" ? "loading" : kind === "confirm" ? "confirming" : "checking");
    try {
      if (kind === "propose") {
        const operation_id = window.crypto.randomUUID();
        const result = (await api.personalMemory("sharing-propose", {version:1, operation_id}, {signal:controller.signal})).result;
        if (!current()) return;
        if (result?.version !== 1 || result.operation_id !== operation_id || result.source !== "core.personal-preferences.v1" ||
            result.applies_to !== "both_homes" || result.effect !== "read_and_manage_confirmed_preferences" ||
            !/^[a-f0-9]{64}$/.test(result.reviewed_digest) || !Number.isFinite(Date.parse(result.grants_expire_at)) ||
            !(Date.parse(result.expires_at) > Date.now() && Date.parse(result.expires_at) <= Date.now()+61000)) throw new Error("invalid_review");
        retained.current = result; setReview(result); setChecked(false); setNow(Date.now()); setStatus("review");
      } else {
        if (kind === "confirm") dispatched.current = true;
        const body = {version:1, operation_id:retained.current.operation_id};
        if (kind === "confirm") body.reviewed_digest = retained.current.reviewed_digest;
        const result = (await api.personalMemory("sharing-"+kind, body, {signal:controller.signal})).result;
        if (!current()) return;
        if (result?.version !== 1 || result.operation_id !== retained.current.operation_id || result.status !== "committed") throw new Error("outcome_unknown");
        setReview(null); setChecked(false); setStatus("committed");
      }
    } catch {
      if (current()) { setChecked(false); setStatus(kind === "propose" ? "unavailable" : "unknown"); }
    } finally { if (running.current === controller) running.current = null; }
  }
  return <section className="agent-card agent-preference-sharing" aria-busy={busy}>
    <h2>Share preferences between homes</h2>
    <p>Link your Los Angeles and Victoria accounts first. Then choose whether Home can read and manage your confirmed evening lighting preference across both homes.</p>
    <div role="status" aria-live="polite">
      {busy && <p>{status === "loading" ? "Preparing your sharing review..." : status === "confirming" ? "Confirming sharing..." : "Checking the original confirmation..."}</p>}
      {status === "unavailable" && <p>Sharing setup is unavailable. Check that both accounts are linked and you are signed in.</p>}
      {status === "unknown" && <p>The outcome is not confirmed. Check its status instead of submitting again.</p>}
      {status === "committed" && <p>Preference sharing was confirmed. You can now return to Home and ask it to remember your evening lighting preference.</p>}
      {expired && status === "review" && <p>This review expired. Request a new review to continue.</p>}
    </div>
    {["idle", "unavailable"].includes(status) && !blocked.current && !dispatched.current &&
      <button disabled={busy} onClick={() => perform("propose")}>Review preference sharing</button>}
    {status === "review" && review && <>
      <p>Applies to Los Angeles and Victoria until {new Date(review.grants_expire_at).toLocaleString()}.</p>
      <p>This saves and retrieves preferences. It does not control lights or share camera history.</p>
      <label><input type="checkbox" checked={checked} disabled={expired || busy} onChange={event => setChecked(event.target.checked)} /> Allow Home to read and manage this shared preference.</label>
      <p><button disabled={!checked || expired || busy} onClick={event => { if (event.nativeEvent.isTrusted) perform("confirm"); }}>Confirm preference sharing</button></p>
      {expired && <button onClick={() => perform("propose")}>Get a new review</button>}
    </>}
    {status === "unknown" && <button disabled={busy} onClick={() => perform("outcome")}>Check sharing status</button>}
  </section>;
}

function SharedLinkReviewCard({ api, ceremonyId }) {
  const [status, setStatus] = useState("idle");
  const [review, setReview] = useState(null);
  const [checked, setChecked] = useState(false);
  const [now, setNow] = useState(Date.now());
  const operation = useRef(null);
  const alive = useRef(true);
  const blocked = useRef(false);
  const approved = useRef(false);
  useEffect(() => {
    const unsubscribe = api.subscribeAuthority(() => {
      blocked.current = true;
      operation.current?.abort();
      setReview(null); setChecked(false); setStatus("unavailable");
    });
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => {
      alive.current = false;
      operation.current?.abort();
      unsubscribe(); window.clearInterval(timer);
    };
  }, [api]);
  const expired = review && now >= Date.parse(review.expires_at);
  const busy = ["loading", "confirming", "checking"].includes(status);
  const perform = async (kind) => {
    if (operation.current || blocked.current || !alive.current) return;
    if (kind === "confirm" && (!checked || approved.current ||
        !sharedLinkReviewValid(review, ceremonyId, Date.now()))) return;
    if (kind === "review" && approved.current) return;
    const controller = new AbortController();
    operation.current = controller;
    const generation = api.authorityGeneration;
    const current = () => alive.current && !blocked.current && !controller.signal.aborted &&
      generation === api.authorityGeneration;
    setStatus(kind === "review" ? "loading" : kind === "confirm" ? "confirming" : "checking");
    try {
      if (kind === "review") {
        const value = await api.sharedLinkReview(ceremonyId, { signal: controller.signal });
        if (!current()) return;
        if (!sharedLinkReviewValid(value, ceremonyId, Date.now())) throw new Error("review_unavailable");
        setReview(value); setChecked(false); setNow(Date.now()); setStatus("review");
      } else {
        if (kind === "confirm") approved.current = true;
        const value = kind === "confirm" ?
          await api.confirmSharedLink(ceremonyId, review.gesture_id, review.reviewed_digest, { signal: controller.signal }) :
          await api.sharedLinkOutcome(ceremonyId, { signal: controller.signal });
        if (!current()) return;
        if (value?.version !== 1 || value.status !== "confirmed" || value.ceremony_id !== ceremonyId) {
          throw new Error("outcome_unavailable");
        }
        approved.current = true;
        setReview(null); setChecked(false); setStatus("confirmed");
      }
    } catch {
      if (current()) { setReview(null); setChecked(false); setStatus(kind === "review" ? "unavailable" : "unknown"); }
    } finally { if (operation.current === controller) operation.current = null; }
  };
  return <section className="agent-card agent-shared-link" aria-labelledby="shared-link-title" aria-busy={busy}>
    <h2 id="shared-link-title">Connect your two homes</h2>
    <p>Link your Los Angeles and Victoria accounts for your personal assistant. Memory access and home controls require separate permissions.</p>
    <div role="status" aria-live="polite">
      {status === "idle" && <p>Review the accounts you authenticated before linking them.</p>}
      {status === "loading" && <p>Loading your account review…</p>}
      {status === "confirming" && <p>Confirming your accounts…</p>}
      {status === "checking" && <p>Checking the original confirmation…</p>}
      {status === "confirmed" && <p>Your two accounts are linked.</p>}
      {status === "unavailable" && <p>This account review is unavailable. You can check an earlier confirmation below.</p>}
      {status === "unknown" && <p>The confirmation outcome is unknown. Check its status before starting another link.</p>}
      {status === "review" && expired && <p>This review expired. Start a new account-linking interaction to confirm.</p>}
    </div>
    {status === "idle" && <button onClick={() => perform("review")}>Review both accounts</button>}
    {status === "review" && review && <>
      <dl className="agent-grid">
        {review.accounts.map((account) => <React.Fragment key={account.site_id}>
          <dt>{account.site_id === "echo" ? "Los Angeles" : "Victoria"}</dt>
          <dd>Home Assistant account <code>{account.subject}</code></dd>
        </React.Fragment>)}
      </dl>
      <label><input type="checkbox" checked={checked} disabled={expired}
        onChange={(event) => setChecked(event.target.checked)} /> These are both my accounts.</label>
      <button disabled={!checked || expired || busy} onClick={() => perform("confirm")}>Link these two accounts</button>
    </>}
    {["idle", "unavailable", "unknown"].includes(status) && !blocked.current &&
      <button onClick={() => perform("outcome")}>Check confirmation status</button>}
  </section>;
}

function SharedLinkLoginForm({ form, busy, onSubmit }) {
  const valid = form?.status === "form" && /^[a-f0-9]{64}$/.test(form.handle || "") &&
    Array.isArray(form.fields) && form.fields.length > 0 && form.fields.length <= 8 &&
    new Set(form.fields).size === form.fields.length && form.fields.every(name => ["username", "password", "code", "multi_factor_auth_module"].includes(name));
  if (!valid) return <p>The authentication form is unavailable. Check the original authentication below.</p>;
  return <form onSubmit={event => {
    event.preventDefault();
    const element = event.currentTarget, data = new FormData(element), input = {};
    for (const field of form.fields) input[field] = String(data.get(field) || "");
    element.reset();
    onSubmit(form.handle, input);
  }}>
    {form.invalid && <p role="alert">Authentication was not accepted. Check your details.</p>}
    {form.fields.map(field => <label key={field}>{({username:"Username",password:"Password",code:"Authentication code",multi_factor_auth_module:"Verification method"})[field]}
      {field === "multi_factor_auth_module" ? <select name={field} required disabled={busy}>
        {(form.choices || []).map(([value,label]) => <option key={value} value={value}>{label}</option>)}
      </select> : <input name={field} type={field === "password" ? "password" : "text"}
        autoComplete={field === "password" ? "current-password" : field === "username" ? "username" : "one-time-code"}
        maxLength={256} required disabled={busy} />}
    </label>)}
    <button disabled={busy}>Authenticate Los Angeles account</button>
  </form>;
}

function SharedLinkSetupCard({ api, setup }) {
  const [pair, setPair] = useState(null), [stage,setStage] = useState("start"), [busy,setBusy] = useState(false);
  const [message,setMessage] = useState(""), [form,setForm] = useState(null), [token,setToken] = useState("");
  const operation = useRef(null), blocked = useRef(false), alive = useRef(true);
  const [now,setNow] = useState(Date.now());
  let origin;
  try {
    const url=new URL(setup?.victoria_origin);
    if(url.protocol==="https:" && url.origin===setup.victoria_origin && url.hostname!==window.location.hostname) origin=url.origin;
  } catch (_) {}
  useEffect(()=>{
    const unsubscribe=api.subscribeAuthority(()=>{
      blocked.current=true;operation.current?.abort();setPair(null);setForm(null);setToken("");setStage("unavailable");
    });
    const timer=window.setInterval(()=>setNow(Date.now()),1000);
    return ()=>{alive.current=false;operation.current?.abort();unsubscribe();window.clearInterval(timer);};
  },[api]);
  const run=async(operationName,extra={})=>{
    if(!origin || blocked.current || operation.current || !alive.current) return;
    const controller=new AbortController(),generation=api.authorityGeneration;
    operation.current=controller;setBusy(true);setMessage("");
    const current=()=>alive.current && !blocked.current && !controller.signal.aborted && generation===api.authorityGeneration;
    // Claim UI steps before sending. Unknown results expose lookup controls,
    // never an automatic second write or replacement pairing.
    if(operationName==="start")setStage("starting");
    if(operationName==="handoff") {setStage("issuance-unknown");setToken("");}
    if(operationName==="auth-begin" || operationName==="auth-submit") {setStage("echo-unknown");setForm(null);}
    try {
      let result=await api.sharedLinkSetup(operationName,operationName==="start"?{}:{pairing_id:pair.pairing_id,...extra},{signal:controller.signal});
      if(!current())return;
      if(operationName==="start") {
        if(!/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(result?.pairing_id || "") ||
          !Number.isSafeInteger(result.expires_at) || result.expires_at<=Date.now() || result.expires_at>Date.now()+60000)throw new Error();
        setPair(result);setStage("handoff");
      } else if(operationName==="handoff" || operationName==="issuance-outcome") {
        if(result?.ceremony_id!==pair.pairing_id || result.status!=="authentication_required")throw new Error();
        setStage("echo-ready");
      } else if(operationName.startsWith("auth-")) {
        if(result?.status==="form") {setForm(result);setStage("echo-form");}
        else if(result?.status==="authenticated" && result.site_id==="echo" && result.ceremony_id===pair.pairing_id)setStage("victoria-ready");
        else throw new Error();
      } else if(operationName==="victoria-auth-admit") {
        if(result?.status!=="authentication_required" || result.ceremony_id!==pair.pairing_id)throw new Error();
        setStage("victoria-waiting");
      } else if(operationName==="victoria-auth-outcome") {
        if(result?.status!=="authenticated" || result.site_id!=="victoria" || result.ceremony_id!==pair.pairing_id)throw new Error();
        setStage("prepare");
      } else if(operationName==="prepare-review") {
        if(!sharedLinkReviewValid(result,pair.pairing_id,Date.now()))throw new Error();
        setStage("review");
      }
    } catch (_) {if(current())setMessage("This step did not return a verified result. Use its status check; no action was retried.");}
    finally {if(operation.current===controller)operation.current=null;if(current())setBusy(false);}
  };
  if(api.invoke || !origin)return null;
  if(stage==="review")return <SharedLinkReviewCard api={api} ceremonyId={pair.pairing_id}/>;
  return <section className="agent-card agent-link-setup" aria-busy={busy}>
    <h2>Connect your two homes</h2>
    <p>Sign in to Victoria first, then link the two accounts. You will review both accounts before confirming.</p>
    <a href={origin+"/"} target="_blank" rel="noopener noreferrer">Open Victoria sign-in</a>
    {message && <p role="status">{message}</p>}
    {stage==="start" && <p><button disabled={busy} onClick={()=>run("start")}>Start account linking</button></p>}
    {stage==="handoff" && <>
      <p><a href={origin+"/#shared-link/"+pair.pairing_id} target="_blank" rel="noopener noreferrer">Create a Victoria connection code</a></p>
      <p>{now>=pair.expires_at?"This pairing expired. Reload to start again.":"Copy the connection code from Victoria and paste it here within one minute."}</p>
      <label>Victoria connection code<input autoComplete="off" value={token} maxLength={64} onChange={e=>setToken(e.target.value.trim())}/></label>
      <button disabled={busy || now>=pair.expires_at || !/^[a-f0-9]{64}$/.test(token)} onClick={()=>run("handoff",{token})}>Connect this Victoria session</button>
    </>}
    {stage==="issuance-unknown" && <button disabled={busy} onClick={()=>run("issuance-outcome")}>Check pairing status</button>}
    {stage==="echo-ready" && <button disabled={busy} onClick={()=>run("auth-begin")}>Verify Los Angeles account</button>}
    {stage==="echo-form" && <SharedLinkLoginForm form={form} busy={busy} onSubmit={(handle,input)=>run("auth-submit",{handle,input})}/>}
    {stage==="echo-unknown" && <button disabled={busy} onClick={()=>run("auth-outcome")}>Check Los Angeles authentication</button>}
    {stage==="victoria-ready" && <button disabled={busy} onClick={()=>run("victoria-auth-admit")}>Prepare Victoria verification</button>}
    {stage==="victoria-waiting" && <>
      <p><a href={origin+"/#shared-link/"+pair.pairing_id} target="_blank" rel="noopener noreferrer">Verify your account in Victoria</a></p>
      <button disabled={busy} onClick={()=>run("victoria-auth-outcome")}>Check Victoria authentication</button>
    </>}
    {stage==="prepare" && <button disabled={busy} onClick={()=>run("prepare-review")}>Prepare account review</button>}
    {stage==="starting" && !busy && <p>The pairing result is unavailable. No accounts were linked.</p>}
    {stage==="unavailable" && <p>Your session changed. Sign in again before linking.</p>}
  </section>;
}

function SharedLinkEntry({ api, setup }) {
  const [ceremonyId, setCeremonyId] = useState(sharedLinkCeremonyFromHash(window.location.hash));
  useEffect(() => {
    const changed = () => setCeremonyId(sharedLinkCeremonyFromHash(window.location.hash));
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  return ceremonyId ? <SharedLinkReviewCard key={ceremonyId} api={api} ceremonyId={ceremonyId} /> : setup ? <SharedLinkSetupCard api={api} setup={setup}/> : null;
}

function HomeAgentPanel() {
  const api = useMemo(() => new window.HomeAgentApi(""), []);
  const activeSubject = useRef(null);
  const authorityGeneration = useRef(0);
  const refreshGeneration = useRef(0);
  const onboardingStatusRef = useRef(null);
  const bindingStatusRef = useRef(null);
  const bindingFocusPending = useRef(false);
  const initiativePresentationInFlight = useRef(false);
  const initiativePresentationRetry = useRef(false);
  const [phase, setPhase] = useState("loading");
  const [session, setSession] = useState(null);
  const [onboarding, setOnboarding] = useState(null);
  const [bindingProposal, setBindingProposal] = useState(null);
  const [bindingBusy, setBindingBusy] = useState(false);
  const [parentRelationship, setParentRelationship] = useState(null);
  const [household, setHousehold] = useState(null);
  const [householdError, setHouseholdError] = useState(false);
  const [partnerChoice, setPartnerChoice] = useState("");
  const [edgeDraft, setEdgeDraft] = useState({ subject: "", predicate: "parent_of", object: "" });
  const [partnerBusy, setPartnerBusy] = useState(false);
  const [personName, setPersonName] = useState("");
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
  const [arrivalGreeting, setArrivalGreeting] = useState(null);
  const [arrivalGreetingDismissed, setArrivalGreetingDismissed] = useState(false);
  const [arrivalGreetingCheck, setArrivalGreetingCheck] = useState(0);
  const [localityStatus, setLocalityStatus] = useState(null);
  const [localityDraft, setLocalityDraft] = useState({
    canonicalName: "Itaipava",
    latitude: "",
    longitude: "",
    radiusM: "",
    travelGreetingEligible: false,
  });
  const [localityPreview, setLocalityPreview] = useState(null);
  const [localityBusy, setLocalityBusy] = useState(false);

  // What the server told us about this principal. Safe to drop whenever a
  // refresh fails: the next successful refresh puts it back.
  const clearPrincipalData = () => {
    authorityGeneration.current += 1;
    setOnboarding(null);
    setBindingProposal(null);
    setBindingBusy(false);
    setParentRelationship(null);
    setHousehold(null);
    setHouseholdError(false);
    setPartnerBusy(false);
    setParentRelationshipBusy(false);
    setSnapshot(null);
    setContainedPreferences(null);
    setRelationship(null);
    setPresence(null);
    setTransaction(null);
    setLifecycle(null);
    setArrivalGreeting(null);
    setArrivalGreetingDismissed(false);
    initiativePresentationRetry.current = false;
    setLocalityStatus(null);
    setLocalityPreview(null);
    setLocalityBusy(false);
  };

  // What the person typed or chose. Only cleared when the principal actually
  // changes -- a transient refresh failure used to wipe it, which silently
  // reset the partner dropdown mid-interaction and left the button disabled
  // with no visible reason.
  const clearDraftInput = () => {
    setPartnerChoice("");
    setEdgeDraft({ subject: "", predicate: "parent_of", object: "" });
    setPersonName("");
    setTeaching(DEFAULT_DESCRIPTOR_TEXT);
    setCorrectionText(DEFAULT_DESCRIPTOR_TEXT);
    setLocalityDraft({
      canonicalName: "Itaipava",
      latitude: "",
      longitude: "",
      radiusM: "",
      travelGreetingEligible: false,
    });
  };

  const clearPrincipalState = () => {
    clearPrincipalData();
    clearDraftInput();
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
    let acceptedAuthorityGeneration = null;
    const isCurrent = () => generation === refreshGeneration.current &&
      (api.invoke || acceptedAuthorityGeneration === null || acceptedAuthorityGeneration === api.authorityGeneration);
    setError("");
    try {
      const currentSession = await api.session();
      if (!isCurrent()) return;
      if (!api.invoke) {
        if (!api.authority) return;
        // Capture after session() installs a newly verified subject/CSRF. Any
        // later denial must invalidate values already held by this refresh too.
        acceptedAuthorityGeneration = api.authorityGeneration;
      }
      const subject = currentSession?.authenticated === true
        ? (api.invoke ? "native-credential" : JSON.stringify([
          currentSession?.authority?.ha_issuer_id, currentSession?.authority?.site_id, currentSession?.user_id,
        ]))
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
      if (!api.invoke) {
        try {
          const [directory, edges] = await Promise.all([
            api.household(),
            api.relationships(),
          ]);
          if (!isCurrent()) return;
          setHousehold({ people: directory.people, relationships: edges.relationships });
          setHouseholdError(false);
        } catch (loadError) {
          if (!isCurrent()) return;
          // A failed read shows nothing rather than something stale or partial:
          // absence here is a privacy decision, not a rendering fallback.
          setHousehold(null);
          setHouseholdError(true);
        }
      } else {
        setHousehold(null);
        setHouseholdError(false);
      }
      if (
        api.invoke
        && nextSnapshot?.capabilities?.private_locality_approval
          === "attested_native_confirmation_gated"
      ) {
        const nextLocalityStatus = await api.privateLocalities();
        if (!isCurrent()) return;
        setLocalityStatus(nextLocalityStatus);
      } else {
        setLocalityStatus(null);
      }
      if (!fullAgentCapabilityEnabled(nextSnapshot)) {
        setSnapshot(null);
        setContainedPreferences(containedPreferenceState(nextSnapshot));
        setPhase("rollout_contained");
        return;
      }
      setContainedPreferences(null);
      setSnapshot(nextSnapshot);
      setArrivalGreetingCheck((current) => current + 1);
      setPhase("ready");
    } catch (cause) {
      if (!isCurrent()) return;
      // Data only. A refresh that failed says nothing about what the person
      // was in the middle of choosing, and clearing it here reset the partner
      // dropdown mid-interaction and disabled the button with nothing on
      // screen to explain why. Sign-out and an actual change of principal
      // still clear the draft, just below and above.
      clearPrincipalData();
      activeSubject.current = null;
      if (cause.status === 401 || (!api.invoke && api.logoutPending)) {
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
    const unsubscribe = api.subscribeAuthority(() => {
      clearPrincipalState();
      activeSubject.current = null;
      setSession(null);
      setPhase("signed_out");
    });
    refresh();
    let disposed = false;
    let unlisten = null;
    const listen = window.__TAURI__?.event?.listen;
    if (listen) {
      Promise.resolve(listen("native-auth-changed", () => { if (!disposed) refresh(); }))
        .then((stop) => { if (disposed) stop?.(); else unlisten = stop; })
        .catch(() => {});
    }
    return () => { disposed = true; unsubscribe(); unlisten?.(); api.invalidateAuthority(); };
  }, []);

  useEffect(() => {
    if (!bindingFocusPending.current || phase !== "onboarding") return;
    if (onboarding?.state === "identity_confirmation_required" && !bindingProposal?.state) return;
    const target = bindingStatusRef.current || onboardingStatusRef.current;
    if (!target) return;
    bindingFocusPending.current = false;
    target.focus();
  }, [phase, onboarding?.state, bindingProposal?.state]);

  useEffect(() => {
    const authorized = phase === "ready"
      && snapshot?.capabilities?.private_initiatives === "attested_native_consent_gated"
      && snapshot?.preferences?.location_memory === true
      && snapshot?.preferences?.travel_greetings === true;
    const sameVisit = !arrivalGreeting
      || snapshot?.latest_visit?.visit_id === arrivalGreeting.visit_id;
    if (!authorized || !sameVisit) {
      setArrivalGreeting(null);
      setArrivalGreetingDismissed(false);
    }
  }, [
    phase,
    snapshot?.capabilities?.private_initiatives,
    snapshot?.preferences?.location_memory,
    snapshot?.preferences?.travel_greetings,
    snapshot?.latest_visit?.visit_id,
    arrivalGreeting?.visit_id,
  ]);

  useEffect(() => {
    if (
      !api.invoke
      || phase !== "ready"
      || arrivalGreeting
      || arrivalGreetingDismissed
      || snapshot?.capabilities?.private_initiatives !== "attested_native_consent_gated"
      || snapshot?.preferences?.location_memory !== true
      || snapshot?.preferences?.travel_greetings !== true
    ) return;
    if (initiativePresentationInFlight.current) {
      initiativePresentationRetry.current = true;
      return;
    }
    const ticket = beginPrincipalOperation();
    initiativePresentationInFlight.current = true;
    (async () => {
      try {
        const pending = await api.initiatives();
        const initiativeId = Array.isArray(pending) ? pending[0]?.initiative_id : null;
        if (!initiativeId || !principalOperationCurrent(ticket)) return;
        const claimed = await api.claimInitiative(initiativeId);
        if (principalOperationCurrent(ticket)) setArrivalGreeting(claimed);
      } catch (cause) {
        // A 409 means another private native session won the one-per-visit
        // claim. It is an expected privacy/dedupe outcome, not a user error.
        if (principalOperationCurrent(ticket) && cause?.status !== 409) {
          setError(cause?.message || "private_greeting_unavailable");
        }
      } finally {
        initiativePresentationInFlight.current = false;
        if (initiativePresentationRetry.current) {
          initiativePresentationRetry.current = false;
          setArrivalGreetingCheck((current) => current + 1);
        }
      }
    })();
  }, [
    api,
    phase,
    snapshot?.capabilities?.private_initiatives,
    snapshot?.preferences?.location_memory,
    snapshot?.preferences?.travel_greetings,
    arrivalGreeting,
    arrivalGreetingDismissed,
    arrivalGreetingCheck,
  ]);

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

  const updateLocalityDraft = (field, value) => {
    setLocalityDraft((current) => ({ ...current, [field]: value }));
    setLocalityPreview(null);
  };

  const previewLocality = async () => {
    const ticket = beginPrincipalOperation();
    const latitude = Number(localityDraft.latitude);
    const longitude = Number(localityDraft.longitude);
    const radiusM = Number(localityDraft.radiusM);
    if (
      !localityDraft.canonicalName.trim()
      || !Number.isFinite(latitude)
      || !Number.isFinite(longitude)
      || !Number.isInteger(radiusM)
      || latitude < -90
      || latitude > 90
      || longitude < -180
      || longitude > 180
      || radiusM < 1
      || radiusM > 50000
    ) {
      setError("private_locality_geometry_invalid");
      return;
    }
    setLocalityBusy(true);
    setError("");
    try {
      const value = await api.previewPrivateLocality({
        canonicalName: localityDraft.canonicalName.trim(),
        latitude,
        longitude,
        radiusM,
        travelGreetingEligible: localityDraft.travelGreetingEligible,
      });
      if (!principalOperationCurrent(ticket)) return;
      setLocalityPreview(value);
    } catch (cause) {
      if (principalOperationCurrent(ticket)) {
        setError(cause.message || "private_locality_preview_failed");
      }
    } finally {
      if (principalOperationCurrent(ticket)) setLocalityBusy(false);
    }
  };

  const confirmLocality = async () => {
    const ticket = beginPrincipalOperation();
    if (
      localityPreview?.state !== "needs_confirmation"
      || !localityPreview?.preview_digest
      || !localityPreview?.confirmation_nonce
    ) return setError("private_locality_preview_invalid");
    setLocalityBusy(true);
    setError("");
    try {
      await api.confirmPrivateLocality({
        canonicalName: localityPreview.canonical_name,
        latitude: localityPreview.locator.latitude,
        longitude: localityPreview.locator.longitude,
        radiusM: localityPreview.locator.radius_m,
        travelGreetingEligible: localityPreview.travel_greeting_eligible,
        confirmationNonce: localityPreview.confirmation_nonce,
        previewDigest: localityPreview.preview_digest,
      });
      if (!principalOperationCurrent(ticket)) return;
      setLocalityPreview(null);
      setLocalityBusy(false);
      await refresh();
    } catch (cause) {
      if (principalOperationCurrent(ticket)) {
        setError(cause.message || "private_locality_confirmation_failed");
        setLocalityBusy(false);
      }
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

  const attestEdge = async (draft) => {
    if (!draft?.subject || !draft?.object) return;
    if (draft.subject === draft.object) return;
    const ticket = beginPrincipalOperation();
    setPartnerBusy(true);
    setError("");
    try {
      // Same shape as attesting your own partner; the subject makes it a
      // third-party assertion, and Core derives assertion_scope itself rather
      // than trusting anything sent from here.
      await api.attestPartner({
        ceremony_id: randomUuid7(),
        partner_person_id: draft.object,
        attestation_nonce: crypto.randomUUID(),
        subject_person_id: draft.subject,
        predicate: draft.predicate,
      });
      if (!principalOperationCurrent(ticket)) return;
      setEdgeDraft({ subject: "", predicate: "parent_of", object: "" });
      setPartnerBusy(false);
      // Re-read rather than patch: the card must never show a relationship
      // Core did not record.
      const edges = await api.relationships();
      if (!principalOperationCurrent(ticket)) return;
      setHousehold((current) =>
        current ? { ...current, relationships: edges.relationships } : current,
      );
    } catch (cause) {
      if (!principalOperationCurrent(ticket)) return;
      setPartnerBusy(false);
      setError(cause.message || "relationship_attestation_failed");
    }
  };

  const attestPartner = async (partnerPersonId) => {
    if (!partnerPersonId) return;
    const ticket = beginPrincipalOperation();
    setPartnerBusy(true);
    setError("");
    try {
      // v7 seed so derived identifiers sort with the ceremony that produced
      // them; v4 nonce because a v7 would leak the wall-clock time of an
      // otherwise unlinkable value. Core re-checks both.
      await api.attestPartner({
        ceremony_id: randomUuid7(),
        partner_person_id: partnerPersonId,
        attestation_nonce: crypto.randomUUID(),
      });
      if (!principalOperationCurrent(ticket)) return;
      setPartnerChoice("");
      setPartnerBusy(false);
      // Re-read rather than patch: the card must never show a relationship
      // Core did not record.
      const edges = await api.relationships();
      if (!principalOperationCurrent(ticket)) return;
      setHousehold((current) =>
        current ? { ...current, relationships: edges.relationships } : current,
      );
    } catch (cause) {
      if (!principalOperationCurrent(ticket)) return;
      setPartnerBusy(false);
      setError(cause.message || "partner_attestation_failed");
    }
  };

  const addHouseholdPerson = async (displayName) => {
    if (!displayName.trim()) return;
    const ticket = beginPrincipalOperation();
    setPartnerBusy(true);
    setError("");
    try {
      await api.createHouseholdPerson({
        ceremony_id: randomUuid7(),
        display_name: displayName.trim(),
      });
      if (!principalOperationCurrent(ticket)) return;
      setPersonName("");
      setPartnerBusy(false);
      // Someone added under a privacy directive may correctly not appear;
      // patching locally would show a person the household may not see.
      const directory = await api.household();
      if (!principalOperationCurrent(ticket)) return;
      setHousehold((current) =>
        current ? { ...current, people: directory.people } : current,
      );
    } catch (cause) {
      if (!principalOperationCurrent(ticket)) return;
      setPartnerBusy(false);
      setError(cause.message || "household_person_failed");
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

      {!api.invoke && session?.authenticated && api.authority && session.shared_link_review_enabled === true &&
        <SharedLinkEntry key={`${api.authority}:${api.authorityGeneration}`} api={api} setup={session.shared_link_setup} />}

      {!api.invoke && session?.authenticated && api.authority && session.personal_memory_enabled === true &&
        <SharedPreferenceConsent key={`sharing:${api.authority}:${api.authorityGeneration}`} api={api} />}

      {phase === "signed_out" && (
        <section className="agent-card">
          <h2>Authentication required</h2>
          <p>The Agent surface uses Home Assistant OAuth. No long-lived token is stored in this page.</p>
          {(session?.reason === "native_logout_revocation_pending" || (!api.invoke && api.logoutPending)) ? (
            <>
            <button onClick={async () => {
              try { await api.logout(); await refresh(); }
              catch (cause) { setError(cause.message || String(cause)); }
            }}>Retry secure sign-out</button>
            {!api.invoke && <button onClick={async () => {
              try { await api.login(); setPhase("authenticating"); }
              catch (cause) { setError(cause.message || String(cause)); }
            }}>Start a new sign-in</button>}
            </>
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

      {!api.invoke && (household || householdError) && (
        <HouseholdCard
          people={household?.people}
          relationships={household?.relationships}
          error={householdError}
          busy={partnerBusy}
          partnerChoice={partnerChoice}
          onPartnerChoice={setPartnerChoice}
          onAttestPartner={attestPartner}
          personName={personName}
          onPersonName={setPersonName}
          onAddPerson={addHouseholdPerson}
          edgeDraft={edgeDraft}
          onEdgeDraft={(patch) =>
            setEdgeDraft((current) => ({ ...current, ...patch }))}
          onAttestEdge={attestEdge}
        />
      )}

      {nativeInstallationMaterial && (
        <section className="agent-card">
          <h2>Public installation enrollment material</h2>
          <p>This public key material is not proof that enrollment is complete. A private operator must bind it offline to your exact Home Assistant user UUID.</p>
          <pre>{JSON.stringify(nativeInstallationMaterial, null, 2)}</pre>
        </section>
      )}

      {api.invoke
        && new Set(["rollout_contained", "ready"]).has(phase)
        && (snapshot?.capabilities?.private_locality_approval
          || containedPreferences?.private_locality_approval)
          === "attested_native_confirmation_gated" && (
        <section className="agent-card" aria-live="polite" aria-busy={localityBusy}>
          <h2>Private locality approval</h2>
          {localityStatus?.state === "configured" ? <>
            <p>The following reviewed private locality is configured:</p>
            <ul>
              {localityStatus.localities.map((locality) => (
                <li key={locality.place_id}>
                  {locality.canonical_name} — travel greeting eligibility {locality.travel_greeting_eligible ? "allowed" : "off"}
                </li>
              ))}
            </ul>
            <p>Eligibility is not consent. Location memory and travel greetings remain separate, default-off choices.</p>
          </> : <>
            <p>Approve one circular locality only in this private native window. Core encrypts the exact center and retains no ownership, residence, presence, or action claim.</p>
            <label>Locality name
              <input value={localityDraft.canonicalName} onChange={(event) => updateLocalityDraft("canonicalName", event.target.value)} />
            </label>
            <label>Latitude
              <input type="number" min="-90" max="90" step="any" value={localityDraft.latitude} onChange={(event) => updateLocalityDraft("latitude", event.target.value)} />
            </label>
            <label>Longitude
              <input type="number" min="-180" max="180" step="any" value={localityDraft.longitude} onChange={(event) => updateLocalityDraft("longitude", event.target.value)} />
            </label>
            <label>Radius in meters
              <input type="number" min="1" max="50000" step="1" value={localityDraft.radiusM} onChange={(event) => updateLocalityDraft("radiusM", event.target.value)} />
            </label>
            <label>
              <input type="checkbox" checked={localityDraft.travelGreetingEligible} onChange={(event) => updateLocalityDraft("travelGreetingEligible", event.target.checked)} />
              Permit this locality to become greeting-eligible after the separate opt-ins
            </label>
            <button disabled={localityBusy} onClick={previewLocality}>Review exact private geometry</button>
            {localityPreview && <>
              <dl className="agent-grid">
                <dt>Name</dt><dd>{localityPreview.canonical_name}</dd>
                <dt>Exact center</dt><dd>{localityPreview.locator.latitude}, {localityPreview.locator.longitude}</dd>
                <dt>Radius</dt><dd>{localityPreview.locator.radius_m} m</dd>
                <dt>Retention</dt><dd>{localityPreview.locator.retention}</dd>
                <dt>Greeting eligibility</dt><dd>{localityPreview.travel_greeting_eligible ? "allowed after opt-in" : "off"}</dd>
              </dl>
              <p>Does not create: {localityPreview.does_not_create.join(", ")}.</p>
              <button disabled={localityBusy} onClick={confirmLocality}>Confirm this exact private locality</button>
            </>}
          </>}
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
          {api.invoke && arrivalGreeting && !arrivalGreetingDismissed && (
            <section className="agent-card" role="status" aria-live="polite">
              <h2>Private arrival greeting</h2>
              <p>{arrivalGreeting.message}</p>
              <p>This was shown once for this visit after fresh location and consent were rechecked.</p>
              <button onClick={() => setArrivalGreetingDismissed(true)}>Dismiss</button>
            </section>
          )}
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

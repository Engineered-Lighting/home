/* Generated local-only preference review. */
/* Provisioned connection contracts, independent of the visual site registry.
 * This factory grants no authority. BFF/Core validate every session and grant.
 * No default Victoria endpoint or new native IPC is introduced here.
 */
(function () {
  "use strict";
  var SITES = ["echo", "victoria"];
  var OPERATIONS = ["session", "observations", "memory", "proposeLighting", "confirmLighting"];

  function fail(message) { throw new Error(message); }
  function fixedHttps(value) {
    var url;
    try { url = new URL(value); } catch (_) { fail("Invalid provisioned endpoint"); }
    if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash) {
      fail("Provisioned endpoints require HTTPS without embedded credentials");
    }
    return url;
  }
  function freezeRecord(input) {
    if (!input || SITES.indexOf(input.siteId) < 0 || !input.revision || !input.trustRevision) {
      fail("Site and trust revisions are required");
    }
    var issuer = input.issuer;
    if (issuer !== "home-assistant:" + input.siteId) fail("Unregistered stable issuer identity");
    if (!input.transports || !Object.keys(input.transports).length) fail("No provisioned transport");
    var transports = {};
    Object.keys(input.transports).forEach(function (name) {
      if (["lan", "private"].indexOf(name) < 0) fail("Unregistered transport");
      transports[name] = fixedHttps(input.transports[name]).href;
    });
    if (!Array.isArray(input.capabilities) || input.capabilities.some(function (x) {
      return OPERATIONS.indexOf(x) < 0;
    })) fail("Unknown connection capability");
    return Object.freeze({ siteId: input.siteId, revision: input.revision,
      issuer: issuer, trustRevision: input.trustRevision,
      transports: Object.freeze(transports), capabilities: Object.freeze(input.capabilities.slice()) });
  }

  function create(provisionedRecords, typedClient, monotonicClock) {
    var mono = monotonicClock || function () { return performance.now() / 1000; };
    if (!Array.isArray(provisionedRecords) || !typedClient || typeof typedClient.call !== "function") {
      fail("Provisioned records and a typed client are required");
    }
    var records = new Map();
    var hosts = new Map();
    var issuers = new Set();
    provisionedRecords.forEach(function (input) {
      var record = freezeRecord(input);
      if (records.has(record.siteId) || issuers.has(record.issuer)) fail("Duplicate site or issuer");
      issuers.add(record.issuer);
      Object.keys(record.transports).forEach(function (name) {
        var host = new URL(record.transports[name]).hostname.toLowerCase();
        if (hosts.has(host) && hosts.get(host) !== record.siteId) fail("BFF homes require distinct hostnames");
        hosts.set(host, record.siteId);
      });
      records.set(record.siteId, record);
    });
    var current = null;
    var generation = 0;
    var controllers = new Set();
    var cleanups = new Set();
    var volatile = new Map();
    var contextHistory = new Map();
    var listeners = new Set();

    function invalidate() {
      generation += 1;
      controllers.forEach(function (controller) { controller.abort(); });
      controllers.clear();
      cleanups.forEach(function (cleanup) { try { cleanup(); } catch (_) {} });
      cleanups.clear();
      volatile.clear();
      contextHistory.clear();
      listeners.forEach(function (listener) { try { listener(snapshot()); } catch (_) {} });
    }
    function snapshot() {
      return Object.freeze({ siteId: current && current.siteId,
        transport: current && current.transport, generation: generation });
    }
    function select(siteId, transport) {
      var record = records.get(siteId);
      if (!record || !Object.prototype.hasOwnProperty.call(record.transports, transport)) {
        fail("Home connection is not provisioned");
      }
      if (current && current.siteId === siteId && current.transport === transport) return snapshot();
      current = { siteId: siteId, transport: transport };
      invalidate();
      return snapshot();
    }
    async function request(operation, payload) {
      if (!current) fail("Select a provisioned home connection");
      var record = records.get(current.siteId);
      if (OPERATIONS.indexOf(operation) < 0 || record.capabilities.indexOf(operation) < 0) {
        fail("Operation is not provisioned");
      }
      var selected = snapshot();
      var controller = new AbortController();
      controllers.add(controller);
      try {
        var result = await typedClient.call(Object.freeze({
          siteId: selected.siteId, issuer: record.issuer, endpoint: record.transports[selected.transport],
          revision: record.revision, trustRevision: record.trustRevision,
          operation: operation, payload: payload, signal: controller.signal,
        }));
        if (selected.generation !== generation || controller.signal.aborted) fail("Stale home response");
        // The transport must return a server-authenticated site envelope, never a client header echo.
        if (!result || result.siteId !== selected.siteId) fail("Response home mismatch");
        return result;
      } finally { controllers.delete(controller); }
    }
    return Object.freeze({
      select: select, snapshot: snapshot, request: request,
      clear: function () { current = null; invalidate(); },
      revoke: function (siteId) { if (current && current.siteId === siteId) invalidate(); },
      getProvisioned: function (siteId) { return records.get(siteId) || null; },
      subscribe: function (listener) { listeners.add(listener); return function () { listeners.delete(listener); }; },
      trackSubscription: function (generationAtStart, cleanup) {
        if (typeof cleanup !== "function") fail("Subscription disposer required");
        if (generationAtStart !== generation) { cleanup(); return false; }
        cleanups.add(cleanup); return true;
      },
      putContext: function (generationAtStart, key, value, nowSeconds) {
        if (generationAtStart !== generation || !current || !value || value.site_id !== current.siteId
            || typeof key !== "string" || !key || key.length > 128
            || key !== value.camera_id || !value.adapter_session || !Number.isInteger(value.sequence)
            || value.sequence < 0 || !Number.isInteger(value.occupancy_generation) || value.occupancy_generation < 0
            || !Number.isFinite(nowSeconds) || !Number.isFinite(value.observed_at)
            || value.observed_at > nowSeconds || value.valid_until !== value.observed_at + 180
            || value.valid_until <= nowSeconds || (!contextHistory.has(key) && contextHistory.size >= 64)) return false;
        var previous = contextHistory.get(key);
        if (previous && (previous.retired.has(value.adapter_session)
            || (value.adapter_session === previous.session && (
              value.occupancy_generation < previous.occupancyGeneration
              || value.sequence <= previous.sequence || value.observed_at < previous.observedAt)))) return false;
        var retired = previous ? previous.retired : new Set();
        if (previous && previous.session !== value.adapter_session) retired.add(previous.session);
        contextHistory.set(key, { retired: retired, session: value.adapter_session,
          occupancyGeneration: value.occupancy_generation, sequence: value.sequence, observedAt: value.observed_at });
        volatile.set(key, { value: Object.freeze(Object.assign({}, value)),
          expiresMono: mono() + Math.min(180, value.valid_until - nowSeconds) }); return true;
      },
      currentContext: function (key, nowSeconds, availability) {
        var entry = volatile.get(key);
        var value = entry && entry.value;
        if (value && availability && availability.site_id === value.site_id
            && availability.adapter_session === value.adapter_session && availability.online === false) {
          volatile.delete(key); return null;
        }
        if (entry && (mono() >= entry.expiresMono || nowSeconds >= value.valid_until || nowSeconds < value.observed_at)) {
          volatile.delete(key); return null;
        }
        if (!value || !current || !Number.isFinite(nowSeconds) || !availability || availability.online !== true
            || availability.site_id !== current.siteId || availability.retained === true
            || !Number.isFinite(availability.checked_at) || nowSeconds < availability.checked_at
            || nowSeconds - availability.checked_at > 10
            || availability.adapter_session !== value.adapter_session
            || availability.occupancy_generation !== value.occupancy_generation
            || !Number.isFinite(value.observed_at) || !Number.isFinite(value.valid_until)
            || value.observed_at > nowSeconds || value.valid_until !== value.observed_at + 180
            || nowSeconds >= value.valid_until) return null;
        return value;
      },
    });
  }
  window.HomeConnectionRegistry = Object.freeze({ create: create });
}());
;
(function(root) {
  "use strict";
  // Inline shared-preference review. Home frames this Agent-origin page inside
  // the chat card; only the provisioned Home origins may frame it (Origin CSP
  // frame-ancestors). The Agent session cookie, CSRF token and API calls stay
  // on this origin: the Home page sees only the nonce-bound result messages.
  const document=root.document, api=new root.HomeAgentApi();
  const elements=Object.fromEntries(["status","detail","confirm","check","cancel","sign-in","retry-session"].map(id=>[id,document.getElementById(id)]));
  const nonce=root.location.hash.slice(1);
  const parent=root.parent;
  const ARM_DELAY_MS=500;
  let origins=[], bound=null, review=null, confirmation=null, busy=false, finished=false;
  let expiryTimer, armTimer, armed=false, lastHeight=0;
  const exact=(v,keys)=>v && typeof v==="object" && !Array.isArray(v) && Object.keys(v).sort().join() === [...keys].sort().join();
  const tone=v=>["warm","neutral","cool"].includes(v);
  const setStatus=text=>{elements.status.textContent=text;resize();};
  const post=value=>{if(bound) parent.postMessage({version:1,nonce,...value},bound.origin);};
  const send=value=>post({type:"home.preference.result",...value});
  function resize() {
    const height=Math.ceil(document.documentElement.getBoundingClientRect().height);
    if(!bound || height===lastHeight) return;
    lastHeight=height;post({type:"home.preference.size",height});
  }
  function end(status,text) {
    finished=true;clearTimeout(expiryTimer);disarm();
    elements.confirm.hidden=true;elements.check.hidden=true;elements.cancel.hidden=true;
    setStatus(text);send({status});
  }
  // Confirm accepts only a trusted click after the button has been fully on
  // screen for a moment. This blocks instant or off-screen click placement; it
  // cannot stop a script already running in Home from styling the frame.
  function disarm() {armed=false;clearTimeout(armTimer);elements.confirm.disabled=true;}
  const visibility=new root.IntersectionObserver(entries=>{
    const entry=entries.at(-1);
    disarm();
    if(entry.isIntersecting && entry.intersectionRatio>=0.99 && document.visibilityState==="visible" && !elements.confirm.hidden) {
      armTimer=setTimeout(()=>{if(!elements.confirm.hidden && !finished) {armed=true;elements.confirm.disabled=false;}},ARM_DELAY_MS);
    }
  },{threshold:[0,0.99,1]});
  function showConfirm(label) {
    elements.confirm.textContent=label;elements.confirm.hidden=false;elements.cancel.hidden=false;disarm();
    visibility.unobserve(elements.confirm);visibility.observe(elements.confirm);resize();
  }
  document.addEventListener("visibilitychange",()=>{
    if(document.visibilityState!=="visible") disarm();
    else if(!elements.confirm.hidden) {visibility.unobserve(elements.confirm);visibility.observe(elements.confirm);}
  });
  async function session() {
    const value=await api.session();
    if(!value.authenticated || value.personal_memory_enabled!==true ||
        !Array.isArray(value.personal_memory_home_origins) || !value.personal_memory_home_origins.length) throw new Error("unavailable");
    origins=value.personal_memory_home_origins.filter(origin=>{
      try {const url=new URL(origin);return url.protocol==="https:" && url.origin===origin && url.origin!==root.location.origin;}
      catch {return false;}
    });
    if(bound && !origins.includes(bound.origin)) throw new Error("authority changed");
    return value;
  }
  async function authenticate() {
    try {
      await session();
      elements["sign-in"].hidden=true;elements["retry-session"].hidden=true;
      setStatus("Connecting to your Home conversation…");
    } catch {
      setStatus("Sign in to Home Agent and enable shared preferences, then check sign-in.");
      elements["sign-in"].hidden=false;elements["retry-session"].hidden=false;
    }
  }
  async function receive(event) {
    const value=event.data;
    if(finished || bound || event.source!==parent || !origins.includes(event.origin) ||
        !exact(value,["version","type","nonce","intent"]) || value.version!==1 ||
        value.type!=="home.preference.request" || value.nonce!==nonce ||
        !exact(value.intent,["operation","tone"]) || !["read","remember","correct","forget"].includes(value.intent.operation) ||
        (["remember","correct"].includes(value.intent.operation) ? !tone(value.intent.tone) : value.intent.tone!==null)) return;
    bound={origin:event.origin,intent:{...value.intent}};
    busy=true;setStatus("Checking your shared preference…");
    try {
      await session();
      const snapshot=(await api.personalMemory("read")).result;
      if(finished) return;
      // Reads need no review: the frame answers the Home origin that framed it.
      if(bound.intent.operation==="read" || bound.intent.operation==="forget" && snapshot.preference===null) {
        const current=snapshot.preference?.value ?? null;
        finished=true;elements.cancel.hidden=true;
        setStatus(current ? `You prefer ${current} lighting in the evening.` : "No evening lighting preference is saved.");
        send({status:"read",tone:current});
        return;
      }
      if(bound.intent.operation==="correct" && snapshot.preference===null) {
        end("absent","There is no saved preference to change.");return;
      }
      const operation=bound.intent.operation==="forget" ? "forget" : snapshot.preference ? "correct" : "remember";
      const proposed=operation==="forget" ? null : {version:1,kind:"personal_preference",key:"lighting.evening.tone",scope:"owner",value:bound.intent.tone};
      review=(await api.personalMemory("propose",{version:1,operation_id:root.crypto.randomUUID(),operation,
        expected_revision:snapshot.revision,expected_fact_id:snapshot.fact_id,preference:proposed})).result;
      if(finished) return;
      elements.detail.textContent=review.current ? `Currently saved: ${review.current.value}.` : "No preference is currently saved.";
      setStatus(operation==="forget" ? "Forget your evening lighting preference in both homes?" :
        `Save ${review.proposed.value} evening lighting as your preference in both homes?`);
      showConfirm(operation==="forget" ? "Forget" : "Confirm");
      send({status:"review_pending"});
      expiryTimer=setTimeout(()=>{
        if(!confirmation && !finished) end("expired","This review expired. No change was made.");
      },Math.max(0,Date.parse(review.expires_at)-Date.now()));
    } catch { if(!finished) end("unavailable","Shared preferences are unavailable. No confirmation was sent."); }
    finally {busy=false;}
  }
  function outcome(result) {
    if(finished) return;
    if(result?.status==="committed") {
      finished=true;elements.check.hidden=true;setStatus("Saved for both homes.");send({status:"saved",tone:review.proposed.value});
    } else if(result?.status==="forgotten") end("forgotten","Forgotten in both homes.");
    else {
      // An unknown outcome offers lookup only: never a second confirm or a cancel.
      elements.cancel.hidden=true;elements.check.hidden=false;
      setStatus(result?.status==="ledger_pending" ? "The preference is hidden. Durable forgetting is still pending." :
        "The outcome is not confirmed yet. Check its status; do not submit again.");
      send({status:"pending"});
    }
  }
  elements.confirm.addEventListener("click",async event=>{
    if(!event.isTrusted || !armed || busy || finished || confirmation || !review || Date.now()>=Date.parse(review.expires_at)) return;
    busy=true;elements.confirm.hidden=true;elements.cancel.hidden=true;disarm();clearTimeout(expiryTimer);
    confirmation={version:1,operation_id:review.operation_id,reviewed_digest:review.reviewed_digest};
    setStatus("Confirming…");send({status:"confirming"});
    try {
      await session();
      outcome((await api.personalMemory("confirm",{...confirmation,gesture_id:root.crypto.randomUUID()})).result);
    } catch { outcome(null); }
    finally {busy=false;}
  });
  elements.check.addEventListener("click",async event=>{
    if(!event.isTrusted || busy || finished || !confirmation) return;
    busy=true;elements.check.disabled=true;
    try {await session();outcome((await api.personalMemory("outcome",confirmation)).result);}
    catch {outcome(null);}
    finally {busy=false;elements.check.disabled=false;}
  });
  elements.cancel.addEventListener("click",event=>{
    if(!event.isTrusted || busy || finished || confirmation || !bound) return;
    end("cancelled","Cancelled. No confirmation was sent.");
  });
  elements["retry-session"].addEventListener("click",authenticate);
  root.addEventListener("message",receive);
  root.addEventListener("resize",resize);
  root.addEventListener("pagehide",()=>{finished=true;clearTimeout(expiryTimer);disarm();api.invalidateAuthority?.();});
  if(root.top===root || root.top!==parent || !/^[a-f0-9]{32}$/.test(nonce)) end("unavailable","Open this review from a preference request in the Home chat.");
  else {root.history.replaceState(null,"",root.location.pathname);authenticate();}
})(window);

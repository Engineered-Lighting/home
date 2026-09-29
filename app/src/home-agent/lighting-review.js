/* Generated local-only lighting review. */
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
  // Inline cross-home lighting review. Home frames this Agent-origin page inside
  // the chat card; only the provisioned Home origins may frame it (Origin CSP
  // frame-ancestors). The Agent session cookie, CSRF token and API calls stay
  // on this origin: Home sends only a typed request and receives only
  // nonce-bound result messages. Nothing is sent to a home before an armed,
  // trusted click on Confirm.
  const document=root.document, api=new root.HomeAgentApi();
  const elements=Object.fromEntries(["status","changes","detail","confirm","check","cancel","sign-in","retry-session"]
    .map(id=>[id,document.getElementById(id)]));
  const nonce=root.location.hash.slice(1);
  const parent=root.parent;
  const ARM_DELAY_MS=500;
  const SITES=["echo","victoria"], OPERATIONS=["on","off","brightness"];
  const HOME={echo:"Los Angeles",victoria:"Victoria"};
  const RESULTS=["succeeded","failed","not_sent","unknown","pending"];
  const CLARIFY=["unknown_light","ambiguous_light","no_eligible_lights","not_dimmable","light_unavailable","too_many_lights"];
  let origins=[], bound=null, review=null, confirmation=null, busy=false, finished=false;
  let expiryTimer, armTimer, armed=false, lastHeight=0;
  const exact=(v,keys)=>v && typeof v==="object" && !Array.isArray(v) && Object.keys(v).sort().join()===[...keys].sort().join();
  const name=v=>typeof v==="string" && v.length>=1 && v.length<=120 && !/[\u0000-\u001f\u007f]/.test(v);
  const change=op=>op.operation==="brightness" ? `set to ${op.brightness}%` : `turn ${op.operation}`;
  const setStatus=text=>{elements.status.textContent=text;resize();};
  const post=value=>{if(bound) parent.postMessage({version:1,nonce,...value},bound.origin);};
  const send=value=>post({type:"home.lighting.result",...value});
  function resize() {
    const height=Math.ceil(document.documentElement.getBoundingClientRect().height);
    if(!bound || height===lastHeight) return;
    lastHeight=height;post({type:"home.lighting.size",height});
  }
  function end(status,text,extra={}) {
    finished=true;clearTimeout(expiryTimer);disarm();
    elements.confirm.hidden=true;elements.check.hidden=true;elements.cancel.hidden=true;
    setStatus(text);send({status,...extra});
  }
  function validRequest(v) {
    return exact(v,["sites","targets","operation","brightness"]) && Array.isArray(v.sites) && v.sites.length>=1 &&
      v.sites.length<=2 && new Set(v.sites).size===v.sites.length && v.sites.every(s=>SITES.includes(s)) &&
      (v.targets==="all" || Array.isArray(v.targets) && v.targets.length>=1 && v.targets.length<=8 &&
        new Set(v.targets).size===v.targets.length && v.targets.every(t=>typeof t==="string" && /^[a-z0-9][a-z0-9 '\-]{0,39}$/.test(t))) &&
      OPERATIONS.includes(v.operation) &&
      (v.operation==="brightness" ? Number.isInteger(v.brightness) && v.brightness>=1 && v.brightness<=100 : v.brightness===null);
  }
  function results(summary) {
    return summary.results.map(r=>({site_id:r.site_id,name:r.name,operation:r.operation,brightness:r.brightness,status:r.status}));
  }
  function validSummary(value) {
    return exact(value,["version","operation_id","status","results"]) && value.version===1 &&
      ["done","partial","failed","unknown"].includes(value.status) && Array.isArray(value.results) &&
      value.results.length>=1 && value.results.length<=16 && value.results.every(r=>
        exact(r,["site_id","name","operation","brightness","status"]) && SITES.includes(r.site_id) && name(r.name) &&
        OPERATIONS.includes(r.operation) && RESULTS.includes(r.status));
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
  function showConfirm() {
    elements.confirm.textContent="Confirm";elements.confirm.hidden=false;elements.cancel.hidden=false;disarm();
    visibility.unobserve(elements.confirm);visibility.observe(elements.confirm);resize();
  }
  document.addEventListener("visibilitychange",()=>{
    if(document.visibilityState!=="visible") disarm();
    else if(!elements.confirm.hidden) {visibility.unobserve(elements.confirm);visibility.observe(elements.confirm);}
  });
  async function session() {
    const value=await api.session();
    if(!value.authenticated || value.lighting_enabled!==true ||
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
      setStatus("Sign in to Home Agent and allow lighting control, then check sign-in.");
      elements["sign-in"].hidden=false;elements["retry-session"].hidden=false;
    }
  }
  function showChanges(operations) {
    elements.changes.replaceChildren(...operations.map(op=>{
      const item=document.createElement("li");
      item.textContent=`${HOME[op.site_id]} · ${op.name}: ${change(op)}`;
      return item;
    }));
    elements.changes.hidden=false;
  }
  async function receive(event) {
    const value=event.data;
    if(finished || bound || event.source!==parent || !origins.includes(event.origin) ||
        !exact(value,["version","type","nonce","request"]) || value.version!==1 ||
        value.type!=="home.lighting.request" || value.nonce!==nonce || !validRequest(value.request)) return;
    bound={origin:event.origin,request:structuredClone(value.request)};
    busy=true;setStatus("Checking the lights…");
    try {
      await session();
      const result=(await api.lighting("propose",{version:1,operation_id:root.crypto.randomUUID(),...bound.request})).result;
      if(finished) return;
      if(result?.status==="clarify") {
        const c=result.clarification;
        if(!exact(c,["version","reason","site_id","target","candidates"]) || !CLARIFY.includes(c.reason) ||
            !SITES.includes(c.site_id) || !Array.isArray(c.candidates) || !c.candidates.every(name)) throw new Error("invalid");
        end("clarify","Nothing was changed.",{clarification:{reason:c.reason,site_id:c.site_id,target:c.target,
          candidates:c.candidates.slice(0,8)}});
        return;
      }
      if(result?.status!=="review" || !exact(result.review,["version","operation_id","expires_at","reviewed_digest","operations"]) ||
          !Array.isArray(result.review.operations) || !result.review.operations.length) throw new Error("invalid");
      review=result.review;
      showChanges(review.operations);
      setStatus(review.operations.length===1 ? "Change this light?" : `Change these ${review.operations.length} lights?`);
      showConfirm();
      send({status:"review_pending"});
      expiryTimer=setTimeout(()=>{
        if(!confirmation && !finished) end("expired","This review expired. Nothing was changed.");
      },Math.max(0,Date.parse(review.expires_at)-Date.now()));
    } catch (error) {
      if(finished) return;
      if(error?.status===403 && error.message==="lighting_not_permitted") {
        end("not_permitted","Lighting control is not allowed yet. Nothing was changed.");
      } else end("unavailable","Lighting is unavailable. Nothing was changed.");
    } finally {busy=false;}
  }
  function outcome(summary) {
    if(finished) return;
    if(summary && validSummary(summary) && summary.status!=="unknown") {
      finished=true;elements.check.hidden=true;
      showChanges(summary.results);
      setStatus(summary.status==="done" ? "Done." : summary.status==="failed" ? "No light was changed." : "Some lights were not changed.");
      send({status:summary.status,results:results(summary)});
      return;
    }
    // An unknown outcome offers lookup only: never a second confirm or a cancel.
    elements.cancel.hidden=true;elements.check.hidden=false;
    setStatus("The result is not confirmed yet. Check its status; do not ask again.");
    send({status:"pending"});
  }
  elements.confirm.addEventListener("click",async event=>{
    if(!event.isTrusted || !armed || busy || finished || confirmation || !review || Date.now()>=Date.parse(review.expires_at)) return;
    busy=true;elements.confirm.hidden=true;elements.cancel.hidden=true;disarm();clearTimeout(expiryTimer);
    confirmation={version:1,operation_id:review.operation_id,reviewed_digest:review.reviewed_digest};
    setStatus("Switching…");send({status:"confirming"});
    try {await session();outcome((await api.lighting("confirm",confirmation)).result);}
    catch {outcome(null);}
    finally {busy=false;}
  });
  elements.check.addEventListener("click",async event=>{
    if(!event.isTrusted || busy || finished || !confirmation) return;
    busy=true;elements.check.disabled=true;
    try {await session();outcome((await api.lighting("outcome",{version:1,operation_id:confirmation.operation_id})).result);}
    catch {outcome(null);}
    finally {busy=false;elements.check.disabled=false;}
  });
  elements.cancel.addEventListener("click",event=>{
    if(!event.isTrusted || busy || finished || confirmation || !bound) return;
    end("cancelled","Cancelled. Nothing was changed.");
  });
  elements["retry-session"].addEventListener("click",authenticate);
  root.addEventListener("message",receive);
  root.addEventListener("resize",resize);
  root.addEventListener("pagehide",()=>{finished=true;clearTimeout(expiryTimer);disarm();api.invalidateAuthority?.();});
  if(root.top===root || root.top!==parent || !/^[a-f0-9]{32}$/.test(nonce)) end("unavailable","Open this review from a lighting request in the Home chat.");
  else {root.history.replaceState(null,"",root.location.pathname);authenticate();}
})(window);

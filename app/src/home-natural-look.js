/* Natural-language visual routing for the Home app.
 *
 * This file is intentionally plain browser JavaScript so the routing contract
 * can be tested without mounting React or needing Home Assistant credentials.
 */
(function () {
  const DEEP_LOOK_CAMERA_META = [
    { id: "living_room", name: "living room", indoor: true, priority: 10, aliases: ["living", "couch"] },
    { id: "kitchen", name: "kitchen", indoor: true, priority: 20, aliases: ["counter", "stove"] },
    { id: "dining_room", name: "dining room", indoor: true, priority: 30, aliases: ["dining"] },
    { id: "workshop", name: "workshop", indoor: true, priority: 40, aliases: ["office", "desk"] },
    { id: "driveway", name: "driveway", indoor: false, priority: 90, aliases: ["outside", "front", "car", "street"] },
  ];

  function deepLookNormalize(text) {
    return String(text || "").toLowerCase().replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  }

  function escapeRegExp(text) {
    return String(text || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function deepLookCameraMeta(id) {
    const key = String(id || "").replace(/\s+/g, "_");
    return DEEP_LOOK_CAMERA_META.find((c) => c.id === key) || {
      id: key,
      name: String(id || key).replace(/_/g, " "),
      indoor: true,
      priority: 70,
      aliases: [],
    };
  }

  function detectDeepLookIntent(text) {
    const raw = String(text || "").trim();
    const s = deepLookNormalize(raw);
    if (!s) return null;

    const actionish = /\b(turn|switch|set|dim|brighten|open|close|lock|unlock|start|stop|restart|enable|disable|toggle|run|play|pause)\b/.test(s);
    const nonVisualDomain = /\b(lights?|lamps?|travel mode|temperature|thermostat|token|stack|models?|vllm|password|github|deploy|release|app|build|repo|branch|pr|pull request|installer)\b/.test(s);
    if (actionish || nonVisualDomain) return null;

    const explicitCamera = DEEP_LOOK_CAMERA_META.find((cam) => {
      const names = [cam.id.replace(/_/g, " "), cam.name].concat(cam.aliases || []);
      return names.some((name) => name && new RegExp("\\b" + escapeRegExp(name) + "\\b").test(s));
    });
    const broadVisual =
      /\b(does anything look weird|anything look weird|visual status|look around|take a look|what can you see)\b/.test(s) ||
      /\b(is anything happening|anything happening|what(?:'s| is) happening|what(?:'s| is) going on)\b/.test(s);
    const directVisual =
      /\b(what do you see|look at|describe)\b/.test(s) ||
      /\b(what(?:'s| is) (?:in|inside|visible))\b/.test(s) ||
      /\b(camera|cameras|visually|see in|seeing in)\b/.test(s);
    const place =
      /\b(apartment|home|house|room|camera|kitchen|living room|dining room|workshop|office|driveway|outside|inside)\b/.test(s) ||
      !!explicitCamera;
    if (!(directVisual && place) && !broadVisual) return null;
    const scope = /\b(home|house|outside|driveway)\b/.test(s) && !/\b(apartment|inside)\b/.test(s)
      ? "home"
      : "apartment";
    return {
      text: raw,
      scope,
      explicitCamera: explicitCamera ? explicitCamera.id : null,
    };
  }

  function selectDeepLookCameras(intent, roomContext, limit) {
    const max = Number.isFinite(Number(limit)) ? Math.max(1, Number(limit)) : 3;
    if (!intent) return [];
    if (intent.explicitCamera) return [deepLookCameraMeta(intent.explicitCamera)];
    const includeOutdoor = intent.scope === "home";
    const metas = DEEP_LOOK_CAMERA_META
      .filter((cam) => includeOutdoor || cam.indoor)
      .map((cam) => ({ ...cam, score: cam.priority }));
    const rooms = roomContext && typeof roomContext.rooms === "object" && roomContext.rooms
      ? roomContext.rooms
      : {};
    for (const cam of metas) {
      const d = rooms[cam.id] && typeof rooms[cam.id] === "object" ? rooms[cam.id] : {};
      const occupied = d.occupied === true || !!d.occupant || !!d.identity || !!d.person ||
        (Array.isArray(d.persons) && d.persons.length > 0);
      const age = Number(d.age_s ?? d.age_seconds ?? d.perception_age_seconds);
      if (occupied) cam.score -= 1000;
      if (Number.isFinite(age)) cam.score += Math.min(age, 600) / 10;
    }
    return metas.sort((a, b) => a.score - b.score || a.priority - b.priority).slice(0, max);
  }

  function isBroadDeepLookQuestion(question) {
    const s = deepLookNormalize(question);
    return /\b(what do you see|what can you see|look around|take a look|visual status|anything happening|what(?:'s| is) happening|what(?:'s| is) going on|anything look weird)\b/.test(s);
  }

  function buildFocusedLookQuestion(question, intent, camera) {
    const original = String(question || "").trim();
    if (!isBroadDeepLookQuestion(original)) {
      return `${original}\nAnswer in one short sentence. Focus on what directly answers the question.`;
    }
    const place = camera && camera.name ? camera.name : "this camera";
    return [
      `Look at the ${place} camera for this user question: "${original}"`,
      "Answer in one short, plain sentence for a home-monitoring dashboard.",
      "Focus only on people, activity, packages, pets, vehicles, open doors/windows, hazards, or unusual changes.",
      "If nothing important is happening, say exactly: No obvious activity.",
      "Do not list ordinary furniture or room contents unless they are the important finding.",
    ].join(" ");
  }

  function summarizeDeepLookResults(question, results, failures) {
    const ok = (results || []).filter((r) => r && r.ok);
    const bad = failures || [];
    if (!ok.length) return "";
    const cleanAnswer = (answer) => String(answer || "")
      .replace(/\s+/g, " ")
      .replace(/\s+([.,!?;:])/g, "$1")
      .trim();
    const sentence = (text) => {
      const t = cleanAnswer(text);
      if (!t) return "I got a fresh grounded frame, but no text answer came back.";
      return /[.!?]$/.test(t) ? t : `${t}.`;
    };
    const failed = bad.length
      ? ` I could not inspect ${bad.map((f) => f.name).join(", ")}.`
      : "";
    const isBroad = isBroadDeepLookQuestion(question);
    if (ok.length === 1) return sentence(ok[0].answer) + failed;
    const readableList = (items) => {
      if (items.length <= 1) return items[0] || "";
      if (items.length === 2) return `${items[0]} and ${items[1]}`;
      return `${items.slice(0, -1).join(", ")}, and ${items[items.length - 1]}`;
    };
    const isLowSignal = (answer) => {
      const s = cleanAnswer(answer).toLowerCase();
      if (!s) return true;
      if (/^no obvious activity\.?$/.test(s)) return true;
      if (/\b(looks|seems|appears)\s+(normal|quiet|empty|clear)\b/.test(s)) return true;
      const notable = /\b(person|people|someone|motion|moving|walk|standing|sitting|dog|cat|pet|package|vehicle|car|truck|door\s+open|open\s+door|window\s+open|hazard|smoke|water|leak|fallen|unusual|weird)\b/.test(s);
      const inventory = (s.match(/,/g) || []).length >= 3 || /\b(couch|coffee table|table|chair|chairs|plant|bicycle|painting|cabinet|sink|stove|island|window|speaker|surfboard|floor)\b/.test(s);
      return !notable && inventory;
    };
    const observation = (r) => {
      const roomPattern = new RegExp("^(?:a|an|the)?\\s*" + escapeRegExp(r.name).replace(/\\s+/g, "\\s+") + "\\s+(?:with|shows?|contains?|has)\\s+", "i");
      let text = sentence(r.answer).replace(roomPattern, "");
      if (/^(a|an|the)\s+/i.test(text)) text = text.replace(/^a\s+/i, "I see a ").replace(/^an\s+/i, "I see an ").replace(/^the\s+/i, "I see the ");
      else if (!/^(i\s+see|there\s+is|there\s+are|it\s+looks|nothing|no\s+)/i.test(text)) text = `I see ${text.charAt(0).toLowerCase()}${text.slice(1)}`;
      return `In the ${r.name}, ${text}`;
    };
    const rooms = readableList(ok.map((r) => r.name));
    if (isBroad) {
      const notable = ok.filter((r) => !isLowSignal(r.answer));
      if (!notable.length) return `I checked ${rooms}. No people, movement, or obvious issues stand out.${failed}`;
      const focused = notable.map(observation).join(" ");
      const normal = ok.filter((r) => isLowSignal(r.answer)).map((r) => r.name);
      const normalText = normal.length ? ` The other checked areas look quiet: ${readableList(normal)}.` : "";
      return `Quick scan: ${focused}${normalText}${failed}`;
    }
    const observations = ok.map(observation);
    return `I checked ${rooms}. ${observations.join(" ")}${failed}`;
  }

  async function runNaturalDeepLook(text, options) {
    const opts = options || {};
    const addEvent = typeof opts.addEvent === "function" ? opts.addEvent : function () {};
    const intent = detectDeepLookIntent(text);
    if (!intent || opts.simActive) return { handled: false, reason: !intent ? "no-intent" : "simulation" };
    const cameras = selectDeepLookCameras(intent, opts.roomContext, opts.limit || 3);
    if (!cameras.length) return { handled: false, reason: "no-cameras" };

    addEvent({ kind: "user", text });
    addEvent({
      kind: "system",
      tone: "info",
      text: `looking deeply · ${cameras.map((c) => c.name).join(", ")}`,
    });

    const ensureFeature = opts.ensureFeature || (async () => true);
    const loaded = await ensureFeature("look", "look", "natural-language");
    const runner = opts.lookRunner || (typeof window !== "undefined" && window.HomeLookReasonZoomRequest);
    if (!loaded || typeof runner !== "function") {
      addEvent({
        kind: "system",
        tone: "warn",
        text: !loaded
          ? "grounded look unavailable - look feature did not load; using quick caption fallback"
          : "grounded look unavailable - look runner missing; using quick caption fallback",
      });
      if (typeof opts.sendToHA === "function") await opts.sendToHA(text, { echoUser: false });
      return { handled: true, reason: !loaded ? "feature-unavailable" : "runner-missing", fallback: true };
    }

    const metricsBase = opts.metricsBase || (typeof opts.metricsBaseFromEndpoint === "function"
      ? opts.metricsBaseFromEndpoint(opts.endpoint)
      : "");
    const normalize = typeof opts.normalizeAnswer === "function" ? opts.normalizeAnswer : (v) => String(v || "").trim();
    const results = [];
    const failures = [];
    for (let i = 0; i < cameras.length; i++) {
      const cam = cameras[i];
      const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
      const timeoutMs = Number.isFinite(Number(opts.timeoutMs)) ? Number(opts.timeoutMs) : 12000;
      const timeout = controller && timeoutMs > 0 ? setTimeout(() => {
        try { controller.abort(); } catch (_) {}
      }, timeoutMs) : null;
      try {
        addEvent({
          kind: "system",
          tone: "info",
          text: `grounded look ${i + 1}/${cameras.length} · ${cam.name}`,
        });
        const data = await runner({
          metricsBase,
          camera: cam.id,
          question: buildFocusedLookQuestion(text, intent, cam),
          signal: controller ? controller.signal : undefined,
        });
        if (!data || typeof data !== "object") throw new Error("malformed look response");
        const answer = normalize(data.answer || "");
        results.push({ ok: true, id: cam.id, name: cam.name, answer, data });
        addEvent({
          kind: "perception",
          text: `${cam.id}: ${answer || "(grounded look)"}`,
          snapshotUrl: data.detailUrl || data.overviewUrl || null,
          imageMode: "annotated",
        });
      } catch (e) {
        const aborted = e && e.name === "AbortError";
        failures.push({
          id: cam.id,
          name: cam.name,
          error: aborted ? "timeout" : (e && e.message ? e.message : String(e)),
        });
        addEvent({
          kind: "system",
          tone: "warn",
          text: `grounded look failed · ${cam.name} · ${aborted ? "timeout" : (e && e.message ? e.message : "error")}`,
        });
      } finally {
        if (timeout) clearTimeout(timeout);
      }
    }

    const summary = summarizeDeepLookResults(text, results, failures);
    if (summary) {
      addEvent({ kind: "home", text: summary });
      return { handled: true, reason: "look-success", results, failures, fallback: false };
    }

    addEvent({
      kind: "system",
      tone: "warn",
      text: "grounded look failed for every selected camera - using quick caption fallback",
    });
    if (typeof opts.sendToHA === "function") await opts.sendToHA(text, { echoUser: false });
    return { handled: true, reason: "all-look-failed", results, failures, fallback: true };
  }

  const api = {
    DEEP_LOOK_CAMERA_META,
    deepLookNormalize,
    deepLookCameraMeta,
    detectDeepLookIntent,
    selectDeepLookCameras,
    summarizeDeepLookResults,
    buildFocusedLookQuestion,
    runNaturalDeepLook,
  };
  if (typeof window !== "undefined") {
    window.HomeNaturalLook = api;
    Object.assign(window, api);
  }
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})();

/* Natural-language visual routing for the Home app.
 *
 * This file is intentionally plain browser JavaScript so the routing contract
 * can be tested without mounting React or needing Home Assistant credentials.
 */
(function () {
  const DEEP_LOOK_CAMERA_META = [
    { id: "living_room", name: "living room", indoor: true, priority: 10, aliases: ["living", "couch", "coffee table"] },
    { id: "kitchen", name: "kitchen", indoor: true, priority: 20, aliases: ["counter", "stove"] },
    { id: "dining_room", name: "dining room", indoor: true, priority: 30, aliases: ["dining"] },
    { id: "workshop", name: "workshop", indoor: true, priority: 40, aliases: ["office", "desk"] },
    { id: "driveway", name: "driveway", indoor: false, priority: 90, aliases: ["outside", "front", "car", "street"] },
  ];

  function deepLookNormalize(text) {
    return String(text || "")
      .toLowerCase()
      .replace(/[\u2018\u2019\u02bc\uff07`]/g, "'")
      .replace(/[_-]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
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
    const nonVisualDomain = /\b(lights?|lamps?|travel mode|temperature|thermostat|token|stack|models?|vllm|password|github|deploy|release|app|build|repo|branch|pr|pull request|installer|bug|error|ui|desktop|mobile|website|code|test|tests?)\b/.test(s);
    const historicalOrRemembered =
      /\b(yesterday|last night|earlier|previously|history|historical|archive|archived|timeline|memory|remember|remembered|recall|recalled|past)\b/.test(s) ||
      /\b(recorded|recording|footage|clip)\b/.test(s);
    const suppliedVisual =
      /\b(?:this|that|the|attached|uploaded|sent)\s+(?:photo|image|picture|screenshot|attachment)\b/.test(s) ||
      /\b(?:photo|image|picture|screenshot|attachment)\s+(?:i|we)\s+(?:sent|uploaded|attached)\b/.test(s);
    if (actionish || nonVisualDomain || historicalOrRemembered || suppliedVisual) return null;

    const explicitCamera = DEEP_LOOK_CAMERA_META.find((cam) => {
      const names = [cam.id.replace(/_/g, " "), cam.name].concat(cam.aliases || []);
      return names.some((name) => name && new RegExp("\\b" + escapeRegExp(name) + "\\b").test(s));
    });
    const anomalyVisual =
      /\b(does anything look weird|anything look weird)\b/.test(s) ||
      /\b(anything (?:wrong|off|strange|unusual|different|changed|out of place)|does anything look (?:wrong|off|strange|unusual|different)|what changed)\b/.test(s);
    const broadVisual =
      anomalyVisual ||
      /\b(visual status|look around|take a look|what can you see)\b/.test(s) ||
      /\b(is anything happening|anything happening|what(?:'s| is) happening|what(?:'s| is) going on)\b/.test(s);
    const targetedSurfaceQuestion =
      !!explicitCamera && /\bwhat(?:'s| is)\s+(?:on|at)\b/.test(s);
    const directVisual =
      /\b(what do you see|look at|describe)\b/.test(s) ||
      /\b(what(?:'s| is) (?:in|inside|visible))\b/.test(s) ||
      /\b(camera|cameras|visually|see in|seeing in)\b/.test(s) ||
      targetedSurfaceQuestion;
    const place =
      /\b(apartment|home|house|room|camera|kitchen|living room|dining room|workshop|office|driveway|outside|inside|coffee table)\b/.test(s) ||
      !!explicitCamera;
    if (!(directVisual && place) && !broadVisual) return null;
    const scope = /\b(home|house|outside|driveway)\b/.test(s) && !/\b(apartment|inside)\b/.test(s)
      ? "home"
      : "apartment";
    return {
      text: raw,
      scope,
      explicitCamera: explicitCamera ? explicitCamera.id : null,
      mode: detectDeepLookMode(s, !!explicitCamera),
    };
  }

  function detectDeepLookMode(normalizedText, hasExplicitCamera) {
    const s = deepLookNormalize(normalizedText);
    if (/\b(describe everything|everything you see|in detail|detailed|full visual|inventory|list everything|all objects|object list)\b/.test(s)) {
      return "detailed_scan";
    }
    if (/\b(anything (?:wrong|off|weird|strange|unusual|different|changed|out of place)|does anything look (?:wrong|off|weird|strange|unusual|different)|what changed)\b/.test(s)) {
      return "anomaly_scan";
    }
    if (hasExplicitCamera || /\b(look at|what(?:'s| is) in|what(?:'s| is) visible in|show me|check the)\b/.test(s)) {
      return "targeted_look";
    }
    return "quick_scan";
  }

  function normalizePerceptionHints(perceptionHints, nowMs) {
    if (!Array.isArray(perceptionHints)) return [];
    const now = Number.isFinite(nowMs) ? nowMs : Date.now();
    return perceptionHints
      .map((hint) => hint && (hint.perception || hint.homePerception || hint))
      .filter((hint) => {
        if (!hint || typeof hint !== "object") return false;
        const camera = String(hint.camera || "").replace(/\s+/g, "_");
        if (!camera) return false;
        const freshUntil = Date.parse(hint.fresh_until || hint.freshUntil || "");
        return Number.isFinite(freshUntil) && freshUntil >= now;
      });
  }

  function perceptionHintScore(hint, intent) {
    const text = deepLookNormalize([
      hint.summary,
      hint.semantic_hint,
      hint.sub_label,
      hint.label,
      Array.isArray(hint.zones) ? hint.zones.join(" ") : "",
    ].join(" "));
    let score = -350;
    if (intent.mode === "anomaly_scan" && /\b(unusual|unknown|linger|package|delivery|person|motion|activity|vehicle|car)\b/.test(text)) score -= 250;
    if (/\b(person|people|occupied|someone|unknown)\b/.test(text)) score -= 200;
    if (/\b(no obvious activity|all clear|quiet)\b/.test(text)) score += 80;
    return score;
  }

  function selectDeepLookCameras(intent, roomContext, limit, perceptionHints) {
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
      if (intent.mode === "anomaly_scan" && Number.isFinite(age)) cam.score += Math.min(age, 900) / 20;
    }
    const hints = normalizePerceptionHints(perceptionHints, Date.now());
    for (const hint of hints) {
      const cameraId = String(hint.camera || "").replace(/\s+/g, "_");
      const cam = metas.find((m) => m.id === cameraId);
      if (!cam) continue;
      cam.score += perceptionHintScore(hint, intent);
      cam.perceptionHint = hint.summary || hint.semantic_hint || hint.label || "";
    }
    return metas.sort((a, b) => a.score - b.score || a.priority - b.priority).slice(0, max);
  }

  function isBroadDeepLookQuestion(question) {
    const s = deepLookNormalize(question);
    return /\b(what do you see|what can you see|look around|take a look|visual status|anything happening|what(?:'s| is) happening|what(?:'s| is) going on|anything look weird)\b/.test(s);
  }

  function buildFocusedLookQuestion(question, intent, camera) {
    const original = String(question || "").trim();
    const place = camera && camera.name ? camera.name : "this camera";
    const mode = intent && intent.mode ? intent.mode : (isBroadDeepLookQuestion(original) ? "quick_scan" : "targeted_look");
    if (mode === "detailed_scan") {
      return [
        `Look carefully at the ${place} camera for this user question: "${original}"`,
        "Answer naturally in one or two short sentences.",
        "A detailed inventory is allowed because the user asked for detail.",
        "Use zoom/crop plus segmentation or bounding boxes when useful.",
        "Mention uncertainty if the frame is unclear.",
      ].join(" ");
    }
    if (mode === "targeted_look") {
      return [
        `${original}`,
        `Use only the ${place} camera.`,
        "Answer in one short sentence.",
        "Use zoom/crop plus segmentation or bounding boxes when useful.",
        "Focus on what directly answers the question and mention uncertainty if the frame is unclear.",
      ].join(" ");
    }
    const anomalyLine = mode === "anomaly_scan"
      ? "Focus first on anything unsafe, unusual, changed, out of place, or worth checking."
      : "Focus first on anything the user needs to care about.";
    return [
      `Look at the ${place} camera for this user question: "${original}"`,
      "Answer in one short, plain sentence for a home-monitoring dashboard.",
      anomalyLine,
      "Use zoom/crop plus segmentation or bounding boxes when useful.",
      "Only call out people, activity, packages, pets, vehicles, open doors/windows, hazards, lights, leaks, smoke, or unusual changes.",
      "If nothing important is happening, say exactly: No obvious activity.",
      "Do not list ordinary furniture or room contents unless they are the important finding.",
    ].join(" ");
  }

  function dedupeDeepLookText(answer) {
    const text = String(answer || "")
      .replace(/\s+/g, " ")
      .replace(/\s+([.,!?;:])/g, "$1")
      .trim();
    if (!text) return "";
    const sentences = text
      .split(/(?<=[.!?])\s+/)
      .map((s) => s.trim())
      .filter(Boolean);
    const out = [];
    const seen = new Set();
    for (const sentence of sentences.length ? sentences : [text]) {
      const key = deepLookNormalize(sentence).replace(/[^\w\s]/g, "");
      if (!key || seen.has(key)) continue;
      const duplicate = out.some((existing) => {
        const a = deepLookNormalize(existing).replace(/[^\w\s]/g, "");
        return a && (a.includes(key) || key.includes(a));
      });
      if (!duplicate) {
        seen.add(key);
        out.push(sentence);
      }
    }
    return out.join(" ").trim();
  }

  function deepLookSentence(text) {
    const t = dedupeDeepLookText(text);
    if (!t) return "";
    return /[.!?]$/.test(t) ? t : `${t}.`;
  }

  function classifyLookFinding(answer) {
    const s = deepLookNormalize(answer);
    // Unanchored on purpose: the prompt asks for the exact sentence, but a
    // model that pads it ("No obvious activity right now.") must still land
    // in "quiet" — an anchored miss reclassifies every idle camera as a
    // notable "activity" finding (importance 0 -> 55).
    if (!s || /\bno obvious activity\b/.test(s)) return "quiet";
    if (/\b(unclear|not sure|hard to tell|cannot tell|can't tell|blurry|blocked|obscured|dark|low light)\b/.test(s)) return "uncertain";
    if (/\b(smoke|fire|water|leak|flood|fallen|fall|broken|hazard|spill|sparks?|alarm)\b/.test(s)) return "hazard";
    if (/\b(package|parcel|delivery|box)\b/.test(s)) return "package";
    // Keyed on human NOUNS, not posture gerunds. The original matched
    // "standing" and "sitting" — which furniture does too — while missing
    // "man", "woman", "stands" and "walks" entirely. Measured on 50 real
    // daylight frames that cost 6 missed people (three filed at importance
    // 10, i.e. "nothing to report", including "a man in a white t-shirt
    // walks through a dining room") and 2 phantom alerts at importance 90
    // from "a pink bicycle standing upright". Nouns fix both directions:
    // the gerunds were redundant whenever a human was named, and harmful
    // whenever one was not. "walking" survives — objects do not walk.
    if (/\b(person|people|someone|somebody|human|man|men|woman|women|child|children|kid|kids|guy|girl|boy|toddler|baby|walking|walks|motion|moving)\b/.test(s)) return "person";
    if (/\b(dog|cat|pet)\b/.test(s)) return "pet";
    if (/\b(vehicle|car|truck|van|bus|driveway|parked)\b/.test(s)) return "vehicle";
    if (/\b(door\s+open|open\s+door|window\s+open|open\s+window|garage\s+open)\b/.test(s)) return "door_window";
    if (/\b(light(?:s)?\s+(?:on|off)|lamp(?:s)?\s+(?:on|off)|lit|dark)\b/.test(s)) return "light";
    if (/\b(looks|seems|appears)\s+(normal|quiet|empty|clear)\b/.test(s)) return "quiet";
    const hasManyObjects = (s.match(/,/g) || []).length >= 2 ||
      /\b(couch|coffee table|table|chair|chairs|plant|bicycle|painting|cabinet|sink|stove|island|window|speaker|surfboard|floor)\b/.test(s);
    return hasManyObjects ? "inventory" : "activity";
  }

  function lookFindingImportance(category) {
    const weights = {
      hazard: 100,
      person: 90,
      package: 80,
      door_window: 76,
      pet: 72,
      vehicle: 68,
      light: 62,
      activity: 55,
      uncertain: 30,
      inventory: 10,
      quiet: 0,
    };
    return Object.prototype.hasOwnProperty.call(weights, category) ? weights[category] : 20;
  }

  function normalizeLookFinding(result) {
    const rawText = String(result && result.answer ? result.answer : "");
    const summary = deepLookSentence(rawText);
    const category = classifyLookFinding(summary);
    return {
      camera: result && result.id ? result.id : "",
      name: result && result.name ? result.name : "",
      summary: summary || "I got a fresh grounded frame, but no text answer came back.",
      category,
      importance: lookFindingImportance(category),
      confidence: category === "uncertain" ? "low" : "normal",
      rawText,
      imageUrl: result && result.data ? (result.data.annotatedUrl || result.data.detailUrl || result.data.overviewUrl || null) : null,
      data: result && result.data ? result.data : null,
    };
  }

  function isLowSignalFinding(finding, mode) {
    if (!finding) return true;
    if (mode === "detailed_scan") return false;
    return finding.category === "quiet" || finding.category === "inventory";
  }

  function readableDeepLookList(items) {
    const list = (items || []).filter(Boolean);
    if (list.length <= 1) return list[0] || "";
    if (list.length === 2) return `${list[0]} and ${list[1]}`;
    return `${list.slice(0, -1).join(", ")}, and ${list[list.length - 1]}`;
  }

  function formatLookObservation(finding) {
    const roomPattern = new RegExp("^(?:a|an|the)?\\s*" + escapeRegExp(finding.name).replace(/\\s+/g, "\\s+") + "\\s+(?:with|shows?|contains?|has|appears?|looks?)\\s+", "i");
    let text = deepLookSentence(finding.summary).replace(roomPattern, "");
    const lowerFirst = (value) => value ? value.charAt(0).toLowerCase() + value.slice(1) : value;
    if (/^(i\s+see)\b/i.test(text)) {
      // Already a natural first-person observation.
    } else if (/^(there\s+is|there\s+are|it\s+looks|nothing|no\s+|someone|people|a\s+person|a\s+package|a\s+vehicle|a\s+dog|a\s+cat|a\s+car|the\s+)/i.test(text)) {
      text = lowerFirst(text);
    } else if (/^(a|an)\s+/i.test(text) && /\b(is|are|walks?|stands?|sits?|lies?|appears?|looks?|moves?|parks?|holds?|carries?)\b/i.test(text)) {
      text = lowerFirst(text);
    } else if (/^(a|an|the)\s+/i.test(text)) {
      text = text.replace(/^a\s+/i, "I see a ").replace(/^an\s+/i, "I see an ").replace(/^the\s+/i, "I see the ");
    } else if (!/^(nothing|no\s+)/i.test(text)) {
      text = `I see ${lowerFirst(text)}`;
    }
    const prefix = finding.confidence === "low" ? "I'm not fully sure, but " : "";
    return `In the ${finding.name}, ${prefix}${text}`;
  }

  function summarizeDeepLookResults(question, results, failures) {
    const ok = (results || []).filter((r) => r && r.ok);
    const bad = failures || [];
    if (!ok.length) return "";
    const failed = bad.length
      ? ` I could not inspect ${readableDeepLookList(bad.map((f) => f.name))}.`
      : "";
    const intent = detectDeepLookIntent(question);
    const mode = intent && intent.mode ? intent.mode : (isBroadDeepLookQuestion(question) ? "quick_scan" : "targeted_look");
    const findings = ok.map(normalizeLookFinding);
    if (ok.length === 1 && mode !== "quick_scan" && mode !== "anomaly_scan") {
      return formatLookObservation(findings[0]) + failed;
    }
    const rooms = readableDeepLookList(findings.map((r) => r.name));
    const notable = findings
      .filter((finding) => !isLowSignalFinding(finding, mode))
      .sort((a, b) => b.importance - a.importance);
    if (mode === "quick_scan" || mode === "anomaly_scan") {
      if (!notable.length) return `I checked ${rooms}. Nothing important stands out.${failed}`;
      const focused = notable.map(formatLookObservation).join(" ");
      const quiet = findings.filter((finding) => isLowSignalFinding(finding, mode)).map((finding) => finding.name);
      const quietText = quiet.length ? ` The other checked areas look quiet: ${readableDeepLookList(quiet)}.` : "";
      return `Quick scan: ${focused}${quietText}${failed}`;
    }
    const observations = findings.map(formatLookObservation);
    return `I checked ${rooms}. ${observations.join(" ")}${failed}`;
  }

  async function runNaturalDeepLook(text, options) {
    const opts = options || {};
    const addEvent = typeof opts.addEvent === "function" ? opts.addEvent : function () {};
    const intent = detectDeepLookIntent(text);
    if (!intent || opts.simActive) return { handled: false, reason: !intent ? "no-intent" : "simulation" };
    const cameras = selectDeepLookCameras(intent, opts.roomContext, opts.limit || 3, opts.perceptionHints);
    if (!cameras.length) return { handled: false, reason: "no-cameras" };

    addEvent({ kind: "user", text });
    addEvent({
      kind: "system",
      tone: "info",
      text: `looking deeply · ${cameras.map((c) => c.name).join(", ")}`,
    });

    const ensureFeature = opts.ensureFeature || (async () => true);
    const loaded = await ensureFeature("look", "look", "natural-language");
    const runner = opts.lookRunner
      || opts.lookFullFrameRunner
      || (typeof window !== "undefined" && (window.HomeLookReasonZoomRequest || window.HomeLookReasonRequest));
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
      const timeoutMs = Number.isFinite(Number(opts.timeoutMs)) ? Number(opts.timeoutMs) : 90000;
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
        const answer = dedupeDeepLookText(normalize(data.answer || ""));
        results.push({ ok: true, id: cam.id, name: cam.name, answer, data });
        addEvent({
          kind: "perception",
          text: `${cam.id}: ${answer || "(grounded look)"}`,
          snapshotUrl: data.annotatedUrl || data.detailUrl || data.overviewUrl || null,
          imageMode: "annotated",
          rawText: data.answer || "",
        });
      } catch (e) {
        const aborted = e && e.name === "AbortError";
        failures.push({
          id: cam.id,
          name: cam.name,
          error: aborted ? "timeout" : (e && e.message ? e.message : String(e)),
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
      text: "deep visual look timed out - using quick caption fallback",
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
    normalizePerceptionHints,
    summarizeDeepLookResults,
    buildFocusedLookQuestion,
    classifyLookFinding,
    dedupeDeepLookText,
    normalizeLookFinding,
    runNaturalDeepLook,
  };
  if (typeof window !== "undefined") {
    window.HomeNaturalLook = api;
    Object.assign(window, api);
  }
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})();

/* Explicit cross-home lighting: turn the owner's words into a typed request.
 *
 * Pure and deterministic; no model output chooses a home, a light or a change.
 * parse(text, defaultHome) returns:
 *   null                       not a cross-home lighting command (existing routing continues)
 *   {request[, inferred]}      { sites, targets, operation, brightness } for Core to resolve
 *   {clarify, message}         a question for the owner instead of a guess
 *   {needsHome: true}          a lighting command naming no home, when no defaultHome is given
 * A named home ("in Victoria", "in LA", "in both homes") always wins. With no
 * home named, defaultHome decides (HomeLightingControl works it out from where
 * the owner is): "victoria" targets Victoria (inferred: true); "echo" leaves the
 * command to the existing Los Angeles path. Only on, off and brightness 1-100%
 * are representable: no scenes, colors, scripts or devices.
 */
(function (root) {
  "use strict";
  const HOME = /\s*\b(?:in|at)\s+(?:the\s+|my\s+)?(victoria|los angeles|la|echo|both homes|both houses|both)\s*$/;
  const PREFIX_HOME = /^(?:my\s+)?(victoria|los angeles|la)(?:'s)?\s+/;
  const ALL = /^(?:all(?: of)?(?: the| my)? lights|the lights|lights|my lights|every light|everything)$/;
  const PRONOUN = /^(?:it|them|those|these|that|this|that one|those ones)$/;
  const TARGET = /^[a-z0-9][a-z0-9 '\-]{0,39}$/;
  const SITES = {"victoria": ["victoria"], "la": ["echo"], "los angeles": ["echo"], "echo": ["echo"],
    "both": ["echo", "victoria"], "both homes": ["echo", "victoria"], "both houses": ["echo", "victoria"]};
  const WHICH_HOME = "Which home do you mean: Victoria, Los Angeles, or both homes?";
  const WHICH_LIGHTS = "Which lights do you mean? Name them, for example \"the kitchen light in Victoria\".";

  const normalize = text => String(text || "").normalize("NFKC").toLowerCase()
    .replace(/[‘’]/g, "'").replace(/\s+/g, " ").trim()
    .replace(/[?.!]+$/, "").replace(/^please\s+/, "").replace(/\s+please$/, "").trim();

  function command(text) {
    let match = /^(?:turn|switch) (on|off) (.+)$/.exec(text);
    if (match) return {operation: match[1], object: match[2], brightness: null};
    match = /^(?:turn|switch) (.+?) (on|off)((?:\s+(?:in|at)\s+.+)?)$/.exec(text);
    if (match) return {operation: match[2], object: match[1] + match[3], brightness: null};
    match = /^(?:set|dim|brighten) (.+?) to (\d{1,3})\s?(?:%|percent)((?:\s+(?:in|at)\s+.+)?)$/.exec(text);
    if (match) return {operation: "brightness", object: match[1] + match[3], brightness: Number(match[2])};
    return null;
  }

  function parse(text, defaultHome) {
    const value = normalize(text);
    if (!value || value.length > 180) return null;
    const found = command(value);
    if (!found) return null;
    let object = found.object.trim(), sites = null, inferred = false;
    const suffix = HOME.exec(object);
    if (suffix) {
      sites = SITES[suffix[1]];
      object = object.slice(0, suffix.index).trim();
    }
    const prefix = PREFIX_HOME.exec(object.replace(/^the\s+/, ""));
    if (prefix) {
      if (sites && sites.join() !== SITES[prefix[1]].join()) return {clarify: "which_home", message: WHICH_HOME};
      sites = SITES[prefix[1]];
      object = object.replace(/^the\s+/, "").slice(prefix[0].length).trim();
    }
    if (!sites) {
      if (defaultHome == null) return {needsHome: true};
      // Unnamed commands for Los Angeles stay on the existing path.
      if (defaultHome !== "victoria") return null;
      sites = ["victoria"];
      inferred = true;
    }
    const result = request => inferred ? {request, inferred} : {request};
    if (found.operation === "brightness" && !(found.brightness >= 1 && found.brightness <= 100)) {
      return {clarify: "brightness_range", message: "Brightness can be set from 1 to 100 percent."};
    }
    object = object.replace(/^the\s+/, "").trim();
    if (!object) return {clarify: "which_lights", message: WHICH_LIGHTS};
    if (ALL.test(object) || ALL.test("the " + object)) {
      return result({sites, targets: "all", operation: found.operation, brightness: found.brightness});
    }
    const names = object.split(/\s*(?:,|\band\b|&)\s*/).map(name => name.replace(/^(?:the|my)\s+/, "").trim())
      .filter(Boolean);
    if (!names.length || names.some(name => PRONOUN.test(name))) return {clarify: "which_lights", message: WHICH_LIGHTS};
    if (names.length > 8 || new Set(names).size !== names.length || !names.every(name => TARGET.test(name))) {
      return {clarify: "which_lights", message: WHICH_LIGHTS};
    }
    return result({sites, targets: names, operation: found.operation, brightness: found.brightness});
  }

  root.HomeLightingIntent = Object.freeze({parse});
})(typeof window !== "undefined" ? window : globalThis);

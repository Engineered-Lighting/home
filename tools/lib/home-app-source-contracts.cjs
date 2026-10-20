"use strict";

// Parse the actual controller through JSX/wrapper changes, then execute only
// bounded expressions/callbacks. Never mount Home or contact household services.
const path = require("node:path");
const vm = require("node:vm");
const babel = require(path.resolve(__dirname, "../../app/src/vendor/babel-7.29.0/babel.min.js"));

function walk(node, visit, ancestors = []) {
  if (!node || typeof node !== "object") return;
  if (node.type) visit(node, ancestors);
  const parents = node.type ? [...ancestors, node] : ancestors;
  for (const [key, value] of Object.entries(node)) {
    if (["loc", "extra", "leadingComments", "trailingComments", "innerComments"].includes(key)) continue;
    if (Array.isArray(value)) value.forEach(child => walk(child, visit, parents));
    else if (value && typeof value === "object") walk(value, visit, parents);
  }
}

function containsIdentifier(node, name) {
  let found = false;
  walk(node, child => { if (child.type === "Identifier" && child.name === name) found = true; });
  return found;
}

function inspectHomeApp(source) {
  const parsed = babel.transform(source, { parserOpts: { plugins: ["jsx"] }, ast: true, code: false }).ast.program;
  const text = node => source.slice(node.start, node.end);
  const functions = parsed.body.filter(node => node.type === "FunctionDeclaration");
  const controllers = functions.filter(node => node.body.body.some(statement => statement.type === "VariableDeclaration"
    && statement.declarations.some(item => item.id.type === "Identifier" && item.id.name === "sim"
      && item.init?.type === "CallExpression" && item.init.callee?.type === "MemberExpression"
      && item.init.callee.object?.name === "window" && item.init.callee.property?.name === "useSimulation")));
  if (controllers.length !== 1) throw new Error("Expected one live Home controller owning window.useSimulation");
  const controller = controllers[0];
  function optionalDeclaration(name, body = controller.body) {
    for (const statement of body.body) {
      if (statement.type !== "VariableDeclaration") continue;
      const found = statement.declarations.find(item => item.id.type === "Identifier" ? item.id.name === name
        : item.id.type === "ArrayPattern" && item.id.elements.some(element => element?.type === "Identifier" && element.name === name));
      if (found) return found;
    }
    return null;
  }
  function declaration(name) {
    const found = optionalDeclaration(name);
    if (!found) throw new Error("Home controller declaration missing: " + name);
    return found;
  }
  // Rehearsal is a real optional controller feature, not a requirement invented
  // by these tests. If declared, every relevant boundary must still honor it.
  const rehearsalIsolation = !!optionalDeclaration("mapRehearsalOffline");
  function evaluate(node, bindings) {
    return vm.runInNewContext("(" + text(node) + ")", bindings, { filename: "home-app.extracted-contract.js", timeout: 1000 });
  }
  function priorityCases() {
    const expression = declaration("apartmentNetworkPriority").init, cases = [];
    for (const mapRehearsalOffline of [false, true]) for (const web of [false, true])
      for (const apartmentOpen of [false, true]) for (const spatialLayout of [false, true]) for (const shellMetricsOpen of [false, true]) {
        const expected = rehearsalIsolation && mapRehearsalOffline || web && (apartmentOpen || spatialLayout) && !shellMetricsOpen;
        const input = { mapRehearsalOffline, web, apartmentOpen, spatialLayout, shellMetricsOpen };
        const actual = evaluate(expression, { ...input, window: { HG_WEB_MODE: web } });
        cases.push({ input, expected, actual, ok: actual === expected });
      }
    return cases;
  }
  function initializationCases() {
    const declarations = ["hasStoredHaCredentials", "connection", "endpoint", "token"].map(declaration);
    const code = declarations.map(node => "const " + text(node) + ";").join("\n") + "\n({ connection, endpoint, token })";
    const cases = [];
    for (const mapRehearsalOffline of [false, true]) for (const endpoint of ["", "/authored-ha"])
      for (const token of ["", "authored-test-token"]) for (const initialEvents of [null, [{ kind: "home", text: "Stored history" }]]) {
        const initialPrefs = { endpoint, token };
        const actual = vm.runInNewContext(code, { mapRehearsalOffline, initialPrefs, initialEvents,
          useState: value => [typeof value === "function" ? value() : value, () => {}] },
        { filename: "home-app.extracted-initialization.js", timeout: 1000 });
        const isolated = rehearsalIsolation && mapRehearsalOffline;
        const expected = { connection: !isolated && endpoint && token ? "reconnecting" : "disconnected",
          endpoint: isolated ? "" : endpoint, token: isolated ? "" : token };
        cases.push({ input: { mapRehearsalOffline, endpoint: !!endpoint, token: !!token, storedEvents: !!initialEvents },
          actual, expected, ok: Object.keys(expected).every(key => actual[key] === expected[key]) });
      }
    return cases;
  }
  function firstRunContract() {
    const mounts = [];
    walk(controller.body, (node, ancestors) => {
      if (node.type !== "JSXOpeningElement" || node.name?.type !== "JSXIdentifier" || node.name.name !== "FirstRun") return;
      const conditional = ancestors.slice().reverse().find(parent => parent.type === "ConditionalExpression"
        && parent.consequent.start <= node.start && parent.consequent.end >= node.end);
      mounts.push({ node, conditional });
    });
    if (mounts.length !== 1 || !mounts[0].conditional) throw new Error("Expected one gated FirstRun mount in the live controller");
    const { node, conditional } = mounts[0], gate = functions.find(item => item.id.name === "isFirstRunVisible");
    if (!gate) throw new Error("FirstRun visibility function missing");
    const visible = evaluate(gate, {}), gateCases = [];
    for (const mapRehearsalOffline of [false, true]) for (const connection of ["disconnected", "reconnecting", "online", "auth_invalid", "picking-model"]) {
      const actual = evaluate(conditional.test, { mapRehearsalOffline, connection, events: [{ kind: "home", text: "Stored history" }], isFirstRunVisible: visible });
      const expected = !(rehearsalIsolation && mapRehearsalOffline) && !["reconnecting", "online"].includes(connection);
      gateCases.push({ mapRehearsalOffline, connection, actual, expected, ok: actual === expected });
    }
    const connectCalls = [], simulationCalls = [], events = [];
    const bindings = { connection: "authored-connection", endpoint: "/authored-ha", token: "authored-token",
      availableModels: [{ name: "authored-model" }], confirmModel: () => {},
      connectTo: (...args) => connectCalls.push(args), sim: { activate: (...args) => simulationCalls.push(args) },
      addEvent: event => events.push(event) };
    const props = {};
    for (const attribute of node.attributes) {
      if (attribute.type === "JSXAttribute" && attribute.value?.type === "JSXExpressionContainer"
          && ["connection", "endpoint", "token", "availableModels", "onPickModel", "onConnect", "onSimulation"].includes(attribute.name.name)) {
        props[attribute.name.name] = evaluate(attribute.value.expression, bindings);
      }
    }
    const forwarded = ["connection", "endpoint", "token", "availableModels"].every(name => props[name] === bindings[name]);
    const picker = props.onPickModel === bindings.confirmModel;
    if (typeof props.onConnect !== "function" || typeof props.onSimulation !== "function") throw new Error("FirstRun connect/simulation callbacks missing");
    const firstRun = functions.find(item => item.id.name === "FirstRun");
    const remembersLogin = !!firstRun && !!optionalDeclaration("rememberHa", firstRun.body);
    props.onConnect("/typed-authored-ha", "typed-authored-token", { rememberHa: false });
    props.onSimulation();
    return { gateCases, forwarded, picker, connectCalls, simulationCalls, events,
      connectManually: connectCalls.length === 1 && connectCalls[0][0] === "/typed-authored-ha"
        && connectCalls[0][1] === "typed-authored-token" && connectCalls[0][2]?.manual === true,
      connectAuthorized: connectCalls.length === 1 && connectCalls[0][0] === "/typed-authored-ha"
        && connectCalls[0][1] === "typed-authored-token" && (!rehearsalIsolation || connectCalls[0][2]?.manual === true)
        && (!remembersLogin || connectCalls[0][2]?.rememberHa === false),
      simulationActivated: simulationCalls.length === 1 && simulationCalls[0][0] === "healthy" && events.length > 0 };
  }
  function firstRunSubmissionCases() {
    const firstRun = functions.find(item => item.id.name === "FirstRun");
    if (!firstRun) throw new Error("FirstRun component missing");
    const callbacks = [];
    walk(firstRun.body, node => {
      if (node.type === "JSXOpeningElement" && node.name?.name === "form") {
        const attribute = node.attributes.find(item => item.name?.name === "onSubmit");
        if (attribute?.value?.expression) callbacks.push(attribute.value.expression);
      }
    });
    if (callbacks.length !== 1) throw new Error("Expected one actual FirstRun form submit callback");
    const remembersLogin = !!optionalDeclaration("rememberHa", firstRun.body);
    return [false, true].map(rememberHa => {
      let prevented = 0; const calls = [];
      const submit = evaluate(callbacks[0], { url: "/typed-authored-ha", tok: "typed-authored-token", rememberHa,
        onConnect: (...args) => calls.push(args) });
      submit({ preventDefault: () => { prevented++; } });
      return { rememberHa, remembersLogin, prevented, calls, ok: prevented === 1 && calls.length === 1
        && calls[0][0] === "/typed-authored-ha" && calls[0][1] === "typed-authored-token"
        && (!remembersLogin || calls[0][2]?.rememberHa === rememberHa) };
    });
  }
  async function connectPreparationCases() {
    const callback = declaration("connectTo").init.arguments?.[0];
    if (!callback) throw new Error("Actual connectTo callback missing");
    const rememberSupported = containsIdentifier(callback, "rememberHa");
    const results = [];
    for (const native of [false, true]) for (const rememberHa of [false, true]) {
      const operations = [], genericPrefs = [], saves = [], events = [], afterHa = new Error("authored_stop_after_ha");
      const client = { url: "", token: "", disconnect: () => operations.push(["disconnect"]),
        async connect(url, token) { operations.push(["connect", url, token]); this.url = url; this.token = token; } };
      const bindings = { window: { IS_TAURI: native, HG_WEB_MODE: true, localStorage: {}, HomeSecurity: {
        sanitizeServiceUrl: value => value, remembersHaLogin: () => true,
        saveHaLogin: (_storage, url, token, remember) => { saves.push({ url, token, remember }); operations.push(["saveHaLogin", remember]); return true; },
      } }, haClientRef: { current: client },
      setEndpoint: value => operations.push(["setEndpoint", value]), setToken: value => operations.push(["setToken", value]),
      setAvailableModels: value => operations.push(["setAvailableModels", value]), setConnection: value => operations.push(["setConnection", value]),
      addEvent: value => events.push(value), savePrefs: value => genericPrefs.push(value),
      probeMetricsBase: async () => { throw afterHa; } };
      try { await evaluate(callback, bindings)("/typed-authored-ha", "typed-authored-token", { manual: true, rememberHa }); }
      catch (error) { if (error !== afterHa) throw error; }
      const connectIndex = operations.findIndex(row => row[0] === "connect");
      const ordered = [["setEndpoint", "/typed-authored-ha"], ["setToken", "typed-authored-token"], ["setAvailableModels", null], ["setConnection", "connecting"]]
        .every(([name, value]) => { const index = operations.findIndex(row => row[0] === name && row[1] === value); return index >= 0 && index < connectIndex; });
      const tokenCleared = operations.some(row => row[0] === "setToken" && row[1] === "");
      const rememberPreserved = rememberHa ? saves.some(row => row.url === "/typed-authored-ha" && row.token === "typed-authored-token" && row.remember === true)
        : saves.length > 0 && saves.every(row => row.remember === false);
      results.push({ native, rememberHa, operations, saves, events, genericPrefs,
        ok: genericPrefs.length === 0 && (native ? tokenCleared && connectIndex < 0 && saves.length === 0
          : ordered && (!rememberSupported || rememberPreserved)) });
    }
    return results;
  }
  function gatewayTokenCases() {
    const helper = functions.find(item => item.id.name === "withGatewayHaToken");
    if (!helper) return [];
    const cases = [];
    for (const proxy of [false, true]) for (const ownEndpoint of [false, true]) for (const existingToken of ["", "existing-authored-token"]) {
      const prefs = { endpoint: ownEndpoint ? "/proxy/ha" : "https://other.invalid", token: existingToken };
      const actual = evaluate(helper, { window: { __HOME_WEB_HA_TOKEN_PROXY: proxy, __HOME_WEB_HA_TOKEN: "authored-gateway-marker", HG_DEFAULT_HA_BASE: "/proxy/ha" } })(prefs);
      const expected = proxy && ownEndpoint && !existingToken ? "authored-gateway-marker" : existingToken;
      cases.push({ proxy, ownEndpoint, existingToken: !!existingToken, ok: actual.token === expected && actual.endpoint === prefs.endpoint && prefs.token === existingToken });
    }
    return cases;
  }
  function simulationHookOrder() {
    const sim = declaration("sim"), dependencies = [];
    walk(controller.body, node => {
      if (node.type === "CallExpression" && (node.callee?.name === "useEffect"
          || node.callee?.type === "MemberExpression" && node.callee.property?.name === "useEffect")
          && node.arguments[1] && containsIdentifier(node.arguments[1], "sim")) {
        dependencies.push({ start: node.arguments[1].start, source: text(node.arguments[1]) });
      }
    });
    return { controller: controller.id.name, dependencyCount: dependencies.length,
      prematureDependencies: dependencies.filter(item => item.start < sim.end).map(item => item.source),
      ok: dependencies.length > 0 && dependencies.every(item => item.start > sim.end) };
  }
  return { controllerName: controller.id.name, rehearsalIsolation, priorityCases, initializationCases, firstRunContract,
    firstRunSubmissionCases, connectPreparationCases, gatewayTokenCases, simulationHookOrder };
}

module.exports = { inspectHomeApp };

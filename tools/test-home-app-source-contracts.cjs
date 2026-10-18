"use strict";
// Negative fixtures prove the replacement source contracts still reject unsafe
// behavior; they do not execute app hooks, sockets or household service calls.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { inspectHomeApp } = require("./lib/home-app-source-contracts.cjs");

function fixture(name = "HouseholdController") {
  return `
function isFirstRunVisible(connection, events) { return connection !== "online" && connection !== "reconnecting"; }
function FirstRun() {
  const [rememberHa, setRememberHa] = useState(true);
  return <form onSubmit={event => { event.preventDefault(); onConnect(url, tok, { rememberHa }); }} />;
}
function ${name}() {
  const [mapRehearsalOffline, setMapRehearsalOffline] = useState(false);
  const hasStoredHaCredentials = !mapRehearsalOffline && !!(initialPrefs.endpoint && initialPrefs.token);
  const [connection, setConnection] = useState(hasStoredHaCredentials ? "reconnecting" : "disconnected");
  const [endpoint, setEndpoint] = useState(mapRehearsalOffline ? "" : initialPrefs.endpoint);
  const [token, setToken] = useState(mapRehearsalOffline ? "" : initialPrefs.token);
  const apartmentNetworkPriority = mapRehearsalOffline || !!window.HG_WEB_MODE && (apartmentOpen || spatialLayout);
  const deferred = () => sim.active;
  const sim = window.useSimulation({});
  useEffect(() => { if (sim.active) return; }, [mapRehearsalOffline, connection, sim.active]);
  const connectTo = useCallback(async (haUrl, accessToken, options = {}) => {
    if (!haUrl || !accessToken) return;
    if (window.IS_TAURI) { setToken(""); addEvent({ kind: "system", text: "native bearer login refused" }); return; }
    haUrl = window.HomeSecurity.sanitizeServiceUrl(haUrl);
    setEndpoint(haUrl); setToken(accessToken); setAvailableModels(null); setConnection("connecting");
    const client = haClientRef.current;
    if (options.rememberHa === false) window.HomeSecurity.saveHaLogin(window.localStorage, "", "", false);
    await client.connect(haUrl, accessToken);
    window.HomeSecurity.saveHaLogin(window.localStorage, haUrl, accessToken, options.rememberHa ?? true);
    await probeMetricsBase(haUrl);
  }, []);
  return !mapRehearsalOffline && isFirstRunVisible(connection, events) ? <FirstRun
    connection={connection} endpoint={endpoint} token={token} availableModels={availableModels}
    onConnect={(url, accessToken, options = {}) => connectTo(url, accessToken, { ...options, manual: true })}
    onPickModel={confirmModel} onSimulation={() => { sim.activate("healthy"); addEvent({kind:"system",text:"[sim]"}); }} /> : null;
}
${name === "HomeApp" ? "" : `function HomeApp(props) { return <${name} {...props} />; }`}
`;
}
const allPass = rows => rows.every(row => row.ok);

test("actual Home controller passes behavior and authentication contracts through its wrapper", async () => {
  const source = fs.readFileSync(path.resolve(__dirname, "../app/src/home-app.jsx"), "utf8");
  const actual = inspectHomeApp(source), firstRun = actual.firstRunContract();
  assert.ok(allPass(actual.priorityCases()));
  assert.ok(allPass(actual.initializationCases()));
  assert.ok(allPass(firstRun.gateCases));
  assert.equal(firstRun.forwarded && firstRun.picker && firstRun.connectAuthorized && firstRun.simulationActivated, true);
  assert.ok(allPass(actual.firstRunSubmissionCases()));
  assert.ok(allPass(await actual.connectPreparationCases()));
  assert.ok(allPass(actual.gatewayTokenCases()));
  assert.equal(actual.simulationHookOrder().ok, true);
});

test("accepted direct handlers without rehearsal preserve browser remember intent", async () => {
  const source = fixture("HomeApp")
    .replace("  const [mapRehearsalOffline, setMapRehearsalOffline] = useState(false);\n", "")
    .replace("!mapRehearsalOffline && !!(initialPrefs.endpoint && initialPrefs.token)", "!!(initialPrefs.endpoint && initialPrefs.token)")
    .replace('mapRehearsalOffline ? "" : initialPrefs.endpoint', "initialPrefs.endpoint")
    .replace('mapRehearsalOffline ? "" : initialPrefs.token', "initialPrefs.token")
    .replace("mapRehearsalOffline || !!window.HG_WEB_MODE", "!!window.HG_WEB_MODE")
    .replace("return !mapRehearsalOffline && isFirstRunVisible", "return isFirstRunVisible")
    .replace("onConnect={(url, accessToken, options = {}) => connectTo(url, accessToken, { ...options, manual: true })}", "onConnect={connectTo}");
  const actual = inspectHomeApp(source);
  assert.equal(actual.rehearsalIsolation, false);
  assert.ok(allPass(actual.priorityCases())); assert.ok(allPass(actual.initializationCases()));
  assert.equal(actual.firstRunContract().connectAuthorized, true);
  assert.ok(allPass(actual.firstRunSubmissionCases())); assert.ok(allPass(await actual.connectPreparationCases()));
});

test("controller discovery accepts direct, renamed and wrapped live controllers", () => {
  for (const name of ["HomeApp", "HomeNormalApp", "RenamedController"]) {
    const actual = inspectHomeApp(fixture(name));
    assert.equal(actual.controllerName, name);
    assert.ok(allPass(actual.priorityCases()));
    assert.ok(allPass(actual.initializationCases()));
    assert.equal(actual.simulationHookOrder().ok, true);
  }
});

test("network priority contracts reject an absent gate and missing rehearsal/web boundaries", () => {
  const source = fixture();
  assert.throws(() => inspectHomeApp(source.replace(/  const apartmentNetworkPriority =[^;]+;\n/, "")).priorityCases(), /apartmentNetworkPriority/);
  for (const unsafe of ["false", "!!window.HG_WEB_MODE && (apartmentOpen || spatialLayout)", "mapRehearsalOffline || apartmentOpen || spatialLayout"]) {
    const changed = source.replace(/const apartmentNetworkPriority =[^;]+;/, "const apartmentNetworkPriority = " + unsafe + ";");
    assert.equal(allPass(inspectHomeApp(changed).priorityCases()), false, unsafe);
  }
});

test("initialization contracts reject stored-history online claims and live credentials during rehearsal", () => {
  const source = fixture();
  for (const changed of [
    source.replace('useState(hasStoredHaCredentials ? "reconnecting" : "disconnected")', 'useState(initialEvents ? "online" : "disconnected")'),
    source.replace("!mapRehearsalOffline && !!(initialPrefs.endpoint && initialPrefs.token)", "!!(initialPrefs.endpoint && initialPrefs.token)"),
    source.replace('useState(mapRehearsalOffline ? "" : initialPrefs.endpoint)', "useState(initialPrefs.endpoint)"),
    source.replace('useState(mapRehearsalOffline ? "" : initialPrefs.token)', "useState(initialPrefs.token)"),
  ]) assert.equal(allPass(inspectHomeApp(changed).initializationCases()), false);
});

test("FirstRun contracts reject losing rehearsal isolation or explicit manual connection intent", () => {
  const source = fixture();
  const unguarded = inspectHomeApp(source.replace("return !mapRehearsalOffline && isFirstRunVisible", "return isFirstRunVisible")).firstRunContract();
  assert.equal(allPass(unguarded.gateCases), false);
  const implicit = inspectHomeApp(source.replace("onConnect={(url, accessToken, options = {}) => connectTo(url, accessToken, { ...options, manual: true })}", "onConnect={connectTo}")).firstRunContract();
  assert.equal(implicit.connectManually, false);
  assert.equal(implicit.connectAuthorized, false);
});

test("authentication contracts reject native bearer bypass, lost remember intent and generic token persistence", async () => {
  for (const changed of [
    fixture().replace("if (window.IS_TAURI)", "if (false)"),
    fixture().replace("options.rememberHa ?? true", "true"),
    fixture().replace("await client.connect(haUrl, accessToken);", "savePrefs({ token: accessToken }); await client.connect(haUrl, accessToken);"),
  ]) assert.equal(allPass(await inspectHomeApp(changed).connectPreparationCases()), false);
  const lostRemember = inspectHomeApp(fixture().replace("onConnect(url, tok, { rememberHa })", "onConnect(url, tok)")).firstRunSubmissionCases();
  assert.equal(allPass(lostRemember), false);
});

test("hook-order contracts reject dependencies before Simulation initialization and a missing hook", () => {
  const source = fixture(), initialization = "  const sim = window.useSimulation({});\n";
  const moved = source.replace(initialization, "").replace("  return !mapRehearsalOffline", initialization + "  return !mapRehearsalOffline");
  const broken = inspectHomeApp(moved).simulationHookOrder();
  assert.equal(broken.ok, false); assert.equal(broken.prematureDependencies.length, 1);
  assert.throws(() => inspectHomeApp(source.replace(initialization, "")), /one live Home controller/);
  assert.equal(inspectHomeApp(source).simulationHookOrder().ok, true, "deferred callbacks before declaration do not create a temporal dead zone");
});

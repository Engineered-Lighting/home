import { ENVIRONMENT_PRESETS, SYNTHETIC_SITES } from "./fixtures.js";
import {
  FRAME_TO_HOST,
  HOST_TO_FRAME,
  RENDERER_ADAPTER_IDS,
  createConnectionEnvelope,
  createEnvelope,
  parseEnvelope,
  summarizeEnvelope,
} from "./protocol.js";

const frame = document.getElementById("spatial-frame");
const protocolStatus = document.getElementById("protocol-status");
const protocolState = protocolStatus.closest(".protocol-state");
const ledger = document.getElementById("protocol-ledger");
const siteOptions = document.getElementById("host-site-options");
const environmentSelect = document.getElementById("environment-select");
const rendererSelect = document.getElementById("renderer-select");
const reducedMotion = document.getElementById("reduced-motion");
const controls = [
  document.getElementById("host-enter"),
  document.getElementById("host-planet"),
  document.getElementById("host-step"),
  rendererSelect,
  environmentSelect,
  reducedMotion,
  document.getElementById("request-snapshot"),
];

let port = null;
let ready = false;
let requestSequence = 0;
let activeIntentId = null;
let selectedSiteId = SYNTHETIC_SITES[0].id;
let activeAdapterId = rendererSelect.value;
let initSent = false;

const adapterLabel = (adapterId) => rendererSelect.querySelector(`option[value="${CSS.escape(adapterId)}"]`)?.textContent || adapterId;

const nextId = (prefix) => `${prefix}-${String(++requestSequence).padStart(4, "0")}`;

function setReady(nextReady, message) {
  ready = nextReady;
  protocolState.classList.toggle("is-ready", nextReady);
  protocolState.classList.toggle("is-error", !nextReady && message.startsWith("Protocol error"));
  protocolStatus.textContent = message;
  controls.forEach((control) => { control.disabled = !nextReady; });
  siteOptions.querySelectorAll("input").forEach((input) => { input.disabled = !nextReady; });
}

function appendLedger(direction, envelope) {
  const item = document.createElement("li");
  item.className = "ledger-entry";
  const directionLabel = document.createElement("span");
  directionLabel.className = "ledger-direction";
  directionLabel.textContent = direction;
  const content = document.createElement("span");
  const summary = summarizeEnvelope(envelope);
  content.textContent = [
    summary.type.replace("home.spatial-spike/", ""),
    summary.siteId,
    summary.intentId,
    summary.kind,
    summary.scale,
  ].filter(Boolean).join(" · ");
  item.append(directionLabel, content);
  ledger.prepend(item);
  while (ledger.children.length > 24) ledger.lastElementChild.remove();
}

function send(type, payload = {}, requestId = nextId("host")) {
  if (!port) return null;
  const envelope = createEnvelope(type, requestId, payload);
  port.postMessage(envelope);
  appendLedger("→", envelope);
  return requestId;
}

function renderSiteOptions() {
  siteOptions.replaceChildren();
  SYNTHETIC_SITES.forEach((site, index) => {
    const wrapper = document.createElement("div");
    wrapper.className = "host-site-option";
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "host-site";
    input.id = `host-site-${index}`;
    input.value = site.id;
    input.checked = site.id === selectedSiteId;
    input.disabled = !ready;
    const label = document.createElement("label");
    label.htmlFor = input.id;
    const name = document.createElement("span");
    name.textContent = site.label;
    const country = document.createElement("span");
    country.className = "country-code";
    country.textContent = site.countryCode;
    label.append(name, country);
    input.addEventListener("change", () => {
      if (input.checked) selectedSiteId = site.id;
    });
    wrapper.append(input, label);
    siteOptions.append(wrapper);
  });
}

function selectHostSite(siteId) {
  if (!SYNTHETIC_SITES.some((site) => site.id === siteId)) return;
  selectedSiteId = siteId;
  const option = siteOptions.querySelector(`input[value="${CSS.escape(siteId)}"]`);
  if (option) option.checked = true;
}

function beginJourney(destination) {
  activeIntentId = nextId("intent");
  send(HOST_TO_FRAME.NAVIGATE, {
    intentId: activeIntentId,
    siteId: selectedSiteId,
    destination,
    playback: "auto",
  });
}

function handleFrameMessage(event) {
  const parsed = parseEnvelope(event.data, "frame-to-host");
  if (!parsed.ok) {
    setReady(false, `Protocol error: ${parsed.code}`);
    return;
  }
  const envelope = parsed.value;
  appendLedger("←", envelope);

  if (envelope.type === FRAME_TO_HOST.READY) {
    if (!initSent) {
      initSent = true;
      setReady(false, `Loading ${adapterLabel(activeAdapterId)}`);
      const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      reducedMotion.checked = prefersReducedMotion;
      send(HOST_TO_FRAME.INIT, {
        sites: SYNTHETIC_SITES,
        environment: ENVIRONMENT_PRESETS.nominal,
        reducedMotion: prefersReducedMotion,
        adapterId: activeAdapterId,
      });
    } else if (envelope.payload.adapterId === activeAdapterId) {
      setReady(true, `${adapterLabel(activeAdapterId)} ready`);
    } else {
      setReady(false, "Protocol error: renderer identity mismatch");
      rendererSelect.disabled = false;
    }
    return;
  }

  if (envelope.type === FRAME_TO_HOST.EVENT && envelope.payload.kind === "site-selected") {
    selectHostSite(envelope.payload.siteId);
  }

  if (envelope.type === FRAME_TO_HOST.ERROR) {
    setReady(false, `Frame error: ${envelope.payload.code}`);
    protocolState.classList.add("is-error");
    rendererSelect.disabled = false;
  }
}

function connectFrame() {
  if (!frame.contentWindow) return;
  if (port) port.close();
  initSent = false;
  setReady(false, "Opening sandbox channel");
  const channel = new MessageChannel();
  port = channel.port1;
  port.onmessage = handleFrameMessage;
  port.onmessageerror = () => setReady(false, "Protocol error: unreadable message");
  port.start();
  const connectEnvelope = createConnectionEnvelope(nextId("connect"));
  frame.contentWindow.postMessage(connectEnvelope, "*", [channel.port2]);
  appendLedger("→", connectEnvelope);
}

document.getElementById("host-enter").addEventListener("click", () => beginJourney("interior"));
document.getElementById("host-planet").addEventListener("click", () => beginJourney("planet"));
document.getElementById("host-step").addEventListener("click", () => {
  send(HOST_TO_FRAME.ADVANCE_JOURNEY, activeIntentId ? { intentId: activeIntentId } : {});
});
document.getElementById("request-snapshot").addEventListener("click", () => {
  send(HOST_TO_FRAME.REQUEST_SNAPSHOT, {});
});

environmentSelect.addEventListener("change", () => {
  const environment = ENVIRONMENT_PRESETS[environmentSelect.value];
  if (environment) send(HOST_TO_FRAME.SET_ENVIRONMENT, { environment });
});

reducedMotion.addEventListener("change", () => {
  send(HOST_TO_FRAME.SET_REDUCED_MOTION, { reducedMotion: reducedMotion.checked });
});

rendererSelect.addEventListener("change", () => {
  if (!RENDERER_ADAPTER_IDS.includes(rendererSelect.value)) return;
  activeAdapterId = rendererSelect.value;
  activeIntentId = null;
  if (port) port.close();
  port = null;
  setReady(false, `Recreating sandbox for ${adapterLabel(activeAdapterId)}`);
  frame.src = frame.getAttribute("src");
});

frame.addEventListener("load", connectFrame);
renderSiteOptions();
setReady(false, "Waiting for sandbox");

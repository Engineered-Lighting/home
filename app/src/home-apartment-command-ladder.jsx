/* Apartment command ladder (APT-A06) and the Echo Manual state (APT-A09).
 *
 * Presentation only. Source owners pass the existing solve, the canonical
 * Product Aim builder's result and Home Assistant state; this module builds no
 * device payload, sends no request and writes nothing to Home Assistant.
 * Accepted execution and reported state stay "not available" until a receipt
 * contract exists. Victoria has no canonical command builder and no
 * commissioned travel limits, so it never claims either. Copy request stays
 * disabled while execution is blocked (SPEC › Command ladder). */
(function () {
  "use strict";
  const { useState } = React;

  const STATUS = Object.freeze({
    modeled: "Modeled · nothing sent",
    simulated: "Simulated · execution blocked",
    preview: "Preview only · execution blocked",
  });
  const NO_CANONICAL_COMMAND = "// No canonical command exists for an uncommissioned socket.\n// Nothing here is a device packet.";
  const COPY_REQUEST_BLOCKED = "Copy request is blocked · no executable request exists.";
  const DOCUMENTED_PRODUCT_AIM = Object.freeze({ target_x_m: null, target_y_m: null, post_dwell_ms: null });
  const DEFAULT_PRODUCT_AIM_PATH = "/api/group/aim";

  const num = value => typeof value === "number" && Number.isFinite(value) ? value : null;
  const tenth = value => num(value) == null ? null : Math.round(value * 10) / 10;
  const point = value => Array.isArray(value) && value.length === 3 && value.every(item => num(item) != null)
    ? value.map(item => Math.round(item * 1000) / 1000) : null;
  const short = value => typeof value === "string" && value.length > 12 ? value.slice(0, 8) : value || "—";
  const degrees = value => `${Math.round(value)}°`;
  const humanize = value => String(value || "").replace(/_/g, " ");
  // Indented JSON with short number arrays kept on one line; still valid JSON.
  const pretty = value => JSON.stringify(value, null, 2).replace(/\[\s+([-\d.,\s]*?)\s+\]/g,
    (_, items) => `[${items.split(/,\s*/).join(", ")}]`);

  function lightPhrase(name, on, where, pct, kelvin) {
    if (!on) return `Keep ${name}'s spotlight off, aimed ${where}.`;
    const level = [pct != null ? `${pct}%` : null, kelvin != null ? `${kelvin} K` : null].filter(Boolean).join(" and ");
    return `Point ${name}'s spotlight ${where}${level ? `, ${level}` : ""}.`;
  }

  /* Den sockets (Victoria): modeled only. Nothing is commissioned, so there is
   * no canonical command, no accepted execution and no reported state. */
  function victoriaLadder({ fixture, binding = null, draft = null } = {}) {
    const state = fixture?.state || {}, spot = state.spotlight || {};
    const name = fixture?.label || fixture?.fixture_id || "This socket";
    const pan = num(state.pan_deg), tilt = num(state.tilt_deg);
    const aimed = pan != null && tilt != null;
    const pct = num(spot.brightness) == null ? null : Math.round(spot.brightness * 100);
    const kelvin = num(spot.color_temp_kelvin);
    const intent = lightPhrase(name, !!spot.on, aimed ? `at pan ${degrees(pan)} and tilt ${degrees(tilt)}` : "where it points now", pct, kelvin);
    const steps = [
      draft ? { key: "draft", mark: "proposed", title: "Draft target", detail: "Proposed in this view. Nothing has changed yet." }
        : { key: "draft", mark: "current", title: "Current aim", detail: "Modeled aim in this view." },
      { key: "modeled", mark: aimed ? "modeled" : "none", title: "Modeled command",
        detail: aimed ? `Modeled in the Apartment frame · pan ${degrees(pan)}, tilt ${degrees(tilt)} · travel limits not commissioned`
          : "No modeled aim yet." },
      { key: "accepted", mark: "none", title: "Accepted execution", detail: "not available · not commissioned" },
      { key: "reported", mark: "none", title: "Reported state", detail: "none · nothing physical reports back for this socket" },
    ];
    const modeled = {
      frame: "apartment", frame_digest: binding?.frame_digest ?? null, fixture_id: fixture?.fixture_id ?? null,
      pan_deg: tenth(pan), tilt_deg: tenth(tilt),
      spotlight: { on: !!spot.on, brightness: num(spot.brightness), color_temp_kelvin: kelvin },
      limit_role: fixture?.limits?.limit_role ?? null, physical_travel: fixture?.limits?.physical_travel ?? "unknown",
      optical_origin_m: null, physical_commands: false,
    };
    return {
      variant: "modeled", status: STATUS.modeled, intent, steps,
      exact: { modeledLabel: "Modeled solve · visualization only", modeledJson: pretty(modeled),
        requestLabel: "Command", request: NO_CANONICAL_COMMAND, blockers: [], simulationBody: null,
        copyRequest: { disabled: true, reason: COPY_REQUEST_BLOCKED } },
      provenance: [
        ["Model", binding ? `Den registration · revision ${binding.content_revision ?? "—"}` : "Den registration · not bound"],
        ["Registration", binding?.registration_version != null ? `version ${binding.registration_version}` : "—"],
        ["Frame", `Apartment frame · ${short(binding?.frame_digest)}`],
        ["Scene", `manifest · ${short(binding?.manifest_digest)}`],
        ["Collision", `canonical collision · ${short(binding?.canonical_collision_digest)}`],
        ["Optic", "Display model · profile values are assumed defaults, not measured"],
        ["Origin", "Socket position · optical origin not commissioned"],
        ["Data", "Authored equipment registration"],
      ],
    };
  }

  /* Echo engineered profile: simulated (or preview-only live). Uses the solve
   * and the Product Aim builder result its owner already computed. */
  function echoLadder({ fixture, model, destination, isCurrent = false, solve, product, sim = false, spotlight,
    telemetry, blockers = [], endpoint = DEFAULT_PRODUCT_AIM_PATH, collisionDigest = null, modelLabel = null } = {}) {
    const name = fixture?.name || fixture?.id || "This light";
    const mirror = destination?.kind === "mirror";
    const surface = !destination ? null : destination.kind === "mesh" ? "a point on the room mesh"
      : destination.name || destination.id || "the chosen point";
    const on = spotlight?.state === "on";
    const brightness = num(+spotlight?.attributes?.brightness);
    const pct = on && brightness != null ? Math.round(brightness / 2.55) : null;
    const kelvin = on ? num(+spotlight?.attributes?.color_temp_kelvin) : null;
    const intent = destination ? lightPhrase(name, on, `at ${surface}`, pct, kelvin)
      : `Choose where ${name}'s spotlight should point.`;
    const solved = !mirror && solve?.ok;
    const fault = solved && !solve.executable
      ? solve.ambiguity || solve.violations?.join(", ") || solve.raw_conversion_error || "not executable" : null;
    const steps = [
      !destination ? { key: "draft", mark: "none", title: "Current aim", detail: "No destination chosen yet." }
        : isCurrent ? { key: "draft", mark: "current", title: "Current aim", detail: `${sim ? "Simulated" : "Previewed"} aim in this view.` }
          : { key: "draft", mark: "proposed", title: "Draft target", detail: "Proposed in this view. Nothing has changed yet." },
      mirror ? { key: "modeled", mark: "modeled", title: "Modeled command", detail: "Mirror wash · approximate soft reflection, no pan/tilt solve" }
        : solved ? { key: "modeled", mark: fault ? "blocked" : "modeled", title: "Modeled command",
          detail: `Solved in the Apartment frame · pan ${degrees(solve.pan_deg)}, tilt ${degrees(solve.tilt_deg)} · `
            + (fault ? `not executable: ${humanize(fault)}` : "travel limits not commissioned") }
          : { key: "modeled", mark: "none", title: "Modeled command", detail: destination ? "No solve for this destination." : "No destination yet." },
      { key: "accepted", mark: "blocked", title: "Accepted execution", detail: "blocked · no movement authority and no target-plane binding" },
      { key: "reported", mark: "none", title: "Reported state",
        detail: sim ? "none · simulated pose only, never live"
          : telemetry?.valid ? "none · no command was sent; read-only telemetry is in Details"
            : "no telemetry · the bench is loopback-only and not open here" },
    ];
    const modeled = {
      frame: "apartment", fixture_id: fixture?.id ?? null, origin_m: point(fixture?.pos),
      destination_m: point(destination?.pos), surface,
      pan_deg: solved ? tenth(solve.pan_deg) : null, tilt_deg: solved ? tenth(solve.tilt_deg) : null,
      executable: solved ? !!solve.executable : false, violations: solved ? [...(solve.violations || [])] : [],
      full_fwhm_deg: num(fixture?.spotlight?.optic_profile?.configured_fwhm_deg),
    };
    const productBlockers = [...blockers];
    if (product && !product.qualified && product.reason && product.reason !== "no_authoritative_target_plane_binding") {
      productBlockers.push(humanize(product.reason));
    }
    const optic = fixture?.spotlight?.optic_profile || {};
    return {
      variant: sim ? "simulated" : "preview", status: sim ? STATUS.simulated : STATUS.preview, intent, steps,
      exact: { modeledLabel: "Modeled solve · visualization only", modeledJson: pretty(modeled),
        requestLabel: "Product Aim request · docs/APARTMENT-LIGHT-AIMING.md",
        request: `POST ${endpoint}\n${JSON.stringify(DOCUMENTED_PRODUCT_AIM, null, 2)}`, blockers: productBlockers,
        // The builder's own output, shown only in simulation and never copyable.
        simulationBody: sim && product?.qualified ? `SIMULATION ONLY\nPOST ${product.endpoint}\n${JSON.stringify(product.body, null, 2)}` : null,
        copyRequest: { disabled: true, reason: COPY_REQUEST_BLOCKED } },
      provenance: [
        ["Model", `Apartment model · revision ${model?.revision ?? "—"}`],
        ["Frame", "Apartment frame · fixture-bottom origin"],
        ["Scene", modelLabel || (sim ? "Simulation" : "Saved Apartment model")],
        ["Collision", collisionDigest ? `collision.glb · ${short(collisionDigest)}` : "—"],
        ["Optic", `${[optic.manufacturer, optic.part].filter(Boolean).join(" ") || "unknown"} · ${num(optic.configured_fwhm_deg) ?? "—"}° FWHM`
          + (optic.confidence === "nominal" ? " · nominal, not measured" : "")],
        ["Origin", `Fixture bottom · tape ${fixture?.fixture_calibration?.status || "proposed"}`],
        ["Data", sim ? "Simulation · authored states" : "Saved Apartment model · Home Assistant state"],
      ],
    };
  }

  /* Echo light → Living Lights zones whose last_manual_at a hand change stamps.
   * Mirrors the generated ha-config/packages/living_lights_manual_detection.yaml
   * (fast manual hold arm); a test fails if the two drift apart. */
  const LIVING_LIGHTS_ZONES_BY_LIGHT = Object.freeze({
    "light.kitchen": ["sink", "island_left", "island_right"],
    "light.kitchen_lights": ["sink", "island_left", "island_right"],
    "light.dining_room": ["dining_left", "dining_right"],
    "light.dining_room_lights": ["dining_left", "dining_right"],
    "light.living_room_lights": ["sofa", "office"],
    "light.dining_table_left": ["dining_left"],
    "light.dining_table_right": ["dining_right"],
    "light.front_right": ["front_door", "weights", "sofa"],
    "light.sink": ["sink"],
    "light.island_left": ["island_left"],
    "light.island_right": ["island_right"],
    "light.office": ["office"],
    "light.rear_right": ["weights", "sofa"],
    "light.front_left": ["front_left", "sofa"],
    "light.rear_left": ["sofa"],
  });
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  /* Read-only. A hand change stamps last_manual_at; the 1970 sentinel means no
   * hold. Returns the latest stamp across the light's zones, or null. */
  function manualHold(entityId, states, { now = Date.now() } = {}) {
    const zones = LIVING_LIGHTS_ZONES_BY_LIGHT[entityId];
    if (!zones) return null;
    let latest = null;
    const held = [];
    for (const zone of zones) {
      const value = states?.[`input_datetime.living_lights_zone_${zone}_last_manual_at`]?.state;
      const match = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(typeof value === "string" ? value : "");
      if (!match || +match[1] < 2000) continue;
      held.push(zone);
      const key = match.slice(1, 6).join("");
      if (!latest || key > latest.key) latest = { key, match };
    }
    if (!latest) return null;
    const [, year, month, day, hour, minute] = latest.match;
    const today = new Date(now);
    const sameDay = today.getFullYear() === +year && today.getMonth() + 1 === +month && today.getDate() === +day;
    const at = sameDay ? `${hour}:${minute}` : `${MONTHS[+month - 1]} ${+day}, ${hour}:${minute}`;
    return { zones: held, at };
  }

  const Badge = props => window.HomeApartmentUI?.Badge ? <window.HomeApartmentUI.Badge {...props} />
    : <span className="apt-ui-badge" data-state={props.state}>{props.children}</span>;

  function AptManualState({ entityId, states, offline = false, now }) {
    // Offline shows the last reported state elsewhere; a hold can't be confirmed then.
    if (offline || !entityId) return null;
    const hold = manualHold(entityId, states, now == null ? undefined : { now });
    if (!hold) return null;
    return <div className="apt-ui-callout" data-apt-manual-state="1" role="status">
      <div className="apt-ui-row"><Badge state="held">Manual</Badge>
        <span className="apt-ui-copy grow">Changed by hand {hold.at}. Adaptive lighting is paused for this light.</span></div>
      <p className="apt-ui-meta">Read-only · Living Lights holds it until the room has been empty for 15 minutes.</p>
    </div>;
  }

  const MARK_COLOR = { current: "var(--apt-ok)", modeled: "var(--apt-muted, var(--hg-fg-3))", proposed: "var(--apt-proposed)",
    blocked: "var(--apt-blocked)", none: "var(--hg-border-strong, var(--hg-fg-3))" };
  const CODE_STYLE = { margin: 0, padding: 8, borderRadius: 8, background: "var(--apt-fill, var(--hg-bg-2))", whiteSpace: "pre-wrap",
    overflowWrap: "anywhere", fontFamily: "'Geist Mono', ui-monospace, monospace", fontSize: 11, lineHeight: 1.45, color: "var(--hg-fg-1)" };
  // Explicit token colors so the legacy (non-shell) Aim panel never falls back to browser button colors.
  const buttonStyle = disabled => ({ padding: "5px 10px", border: "1px solid var(--apt-border, var(--hg-border-soft))", borderRadius: 7,
    background: "var(--apt-fill, transparent)", color: "var(--hg-fg-1)", font: "inherit", fontSize: 12,
    opacity: disabled ? .5 : 1, cursor: disabled ? "default" : "pointer" });

  function AptCommandLadder(props) {
    const [copied, setCopied] = useState("");
    const A = window.HomeApartmentAiming;
    const ladder = props.site === "victoria" ? victoriaLadder(props) : echoLadder({ ...props,
      endpoint: A?.PRODUCT_AIM_PATH || DEFAULT_PRODUCT_AIM_PATH, collisionDigest: A?.COLLISION_GEOMETRY_SHA256 || null,
      modelLabel: window.HomeApartmentData?.modelSourceMeta?.(props.model)?.label || null });
    const copy = (label, text) => {
      try {
        const pending = navigator.clipboard?.writeText?.(text);
        if (!pending) { setCopied("Copy is unavailable here · select the text instead"); return; }
        pending.then(() => setCopied(`${label} copied`), () => setCopied("Copy failed · select the text instead"));
      } catch (_) { setCopied("Copy failed · select the text instead"); }
    };
    const { exact } = ladder;
    return <div data-apt-command-ladder={ladder.variant} style={{ display: "grid", gap: 8, minWidth: 0,
      borderTop: "1px solid var(--apt-border-soft, var(--hg-border-soft))", paddingTop: 10 }}>
      <details className="apt-ui-details">
        <summary aria-label={`Command · ${ladder.status}`}><span style={{ color: "var(--hg-fg-0)", fontWeight: 560 }}>Command</span>
          {" "}<span className="apt-ui-meta" data-apt-command-status="1">{ladder.status}</span></summary>
        <div style={{ display: "grid", gap: 10, minWidth: 0 }}>
          <p className="apt-ui-copy" data-apt-command-intent="1" style={{ color: "var(--hg-fg-0)" }}>{ladder.intent}</p>
          <ol aria-label="Command steps" style={{ display: "grid", gap: 8, margin: 0, padding: 0, listStyle: "none" }}>
            {ladder.steps.map(step => <li key={step.key} data-apt-command-step={step.key} data-mark={step.mark}
              style={{ display: "grid", gridTemplateColumns: "12px minmax(0,1fr)", gap: 8, alignItems: "start" }}>
              <span aria-hidden="true" style={{ width: 8, height: 8, marginTop: 5, borderRadius: "50%",
                border: `1.5px solid ${MARK_COLOR[step.mark] || MARK_COLOR.none}`,
                background: step.mark === "current" ? MARK_COLOR.current : "transparent" }} />
              <div style={{ minWidth: 0 }}><div style={{ color: "var(--hg-fg-1)", fontSize: 12.5 }}>{step.title}</div>
                <div className="apt-ui-meta">{step.detail}</div></div>
            </li>)}
          </ol>
          <details className="apt-ui-details"><summary>Exact output</summary>
            <div style={{ display: "grid", gap: 8, minWidth: 0 }}>
              <div className="apt-ui-meta">{exact.modeledLabel}</div>
              <pre data-apt-command-modeled="1" style={CODE_STYLE}>{exact.modeledJson}</pre>
              <div className="apt-ui-meta">{exact.requestLabel}</div>
              <pre data-apt-command-request="1" style={CODE_STYLE}>{exact.request}</pre>
              {exact.blockers.map(blocker => <div key={blocker} className="apt-ui-meta" data-apt-command-blocker="1"
                style={{ color: "var(--apt-attn)" }}>blocked · {blocker}</div>)}
              {exact.simulationBody && <pre data-apt-command-simulation="1" style={CODE_STYLE}>{exact.simulationBody}</pre>}
              <div className="apt-ui-actions">
                <button type="button" className="apt-ui-button is-small" style={buttonStyle(false)} onClick={() => copy("Intent", ladder.intent)}>Copy intent</button>
                <button type="button" className="apt-ui-button is-small" style={buttonStyle(false)} onClick={() => copy("Modeled JSON", exact.modeledJson)}>Copy modeled JSON</button>
                <button type="button" className="apt-ui-button is-small" style={buttonStyle(exact.copyRequest.disabled)} disabled={exact.copyRequest.disabled}
                  title={exact.copyRequest.reason}>Copy request</button>
              </div>
              <p className="apt-ui-meta" data-apt-command-copy-blocked="1">{exact.copyRequest.reason}</p>
            </div>
          </details>
          <details className="apt-ui-details"><summary>Provenance</summary>
            <dl className="apt-ui-key-values">{ladder.provenance.map(([key, value]) => [
              <dt key={`${key}-k`}>{key}</dt>, <dd key={`${key}-v`}>{value}</dd>])}</dl>
          </details>
          {copied && <p role="status" className="apt-ui-meta">{copied}</p>}
        </div>
      </details>
    </div>;
  }

  window.HomeApartmentCommandLadder = Object.freeze({ STATUS, NO_CANONICAL_COMMAND, COPY_REQUEST_BLOCKED,
    LIVING_LIGHTS_ZONES_BY_LIGHT, victoriaLadder, echoLadder, manualHold });
  window.AptCommandLadder = AptCommandLadder;
  window.AptManualState = AptManualState;
})();

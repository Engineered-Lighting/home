/* Home Intelligence tab.
 *
 * Read-only review surface for the Home Intelligence Core sidecar. This
 * intentionally starts small: health, preference candidates, blockers, and
 * suppression. No HA writes, no automation edits, no cloud calls.
 */

(function () {
  const { useCallback, useEffect, useMemo, useState } = React;
  const DEFAULT_INTELLIGENCE_PORT = "8095";
  const DEFAULT_INTELLIGENCE_BASE = "http://192.168.0.100:8095";

  function intelligenceBaseFrom(metricsBase, endpoint) {
    try {
      const resolved = window.HomeServices?.get?.("intelligence");
      if (resolved) return String(resolved).replace(/\/+$/, "");
    } catch (e) { /* */ }
    if (typeof window !== "undefined" && window.HG_DEFAULT_INTELLIGENCE_BASE) {
      return window.HG_DEFAULT_INTELLIGENCE_BASE.replace(/\/+$/, "");
    }
    try {
      if (!window.HomeServices) {
        const saved = localStorage.getItem("hg-intelligence-base");
        if (saved) return saved.replace(/\/+$/, "");
      }
    } catch {}
    const source = metricsBase;
    if (!source) return DEFAULT_INTELLIGENCE_BASE;
    try {
      const u = new URL(source);
      return `${u.protocol}//${u.hostname}:${DEFAULT_INTELLIGENCE_PORT}`;
    } catch {
      return DEFAULT_INTELLIGENCE_BASE;
    }
  }

  async function fetchJson(url, init) {
    const f = window.tauriFetch || fetch;
    const r = await f(url, { cache: "no-store", ...(init || {}) });
    if (!r.ok) throw new Error(`HTTP ${r.status || "?"}`);
    return r.json();
  }

  function fmtPct(v) {
    if (v == null || !isFinite(v)) return "-";
    return `${Math.round(v)}%`;
  }

  function fmtKelvin(v) {
    if (v == null || !isFinite(v)) return null;
    return `${Math.round(v)}K`;
  }

  function friendlyEntityName(entity, fallback = "light") {
    if (!entity) return fallback;
    const parts = String(entity).split(".");
    const raw = parts.length > 1 ? parts.slice(1).join(".") : String(entity);
    const name = raw.replace(/_/g, " ").trim();
    if (!name) return fallback;
    if (parts[0] === "light" && !/\blight\b/i.test(name)) return `${name} light`;
    return name;
  }

  function lightValueText({ brightnessPct, colorTempKelvin, lightState, empty = "unknown" }) {
    if (lightState === "off") return "off";
    const pieces = [];
    const kelvin = fmtKelvin(colorTempKelvin);
    if (kelvin) pieces.push(kelvin);
    if (brightnessPct != null && isFinite(brightnessPct)) pieces.push(fmtPct(brightnessPct));
    if (pieces.length === 0) return empty;
    return pieces.join(" ");
  }

  function transitionBrightnessPct(state = {}) {
    if (state.brightness_pct != null && isFinite(Number(state.brightness_pct))) {
      return Number(state.brightness_pct);
    }
    if (state.state === "off") return 0;
    if (state.brightness != null && isFinite(Number(state.brightness))) {
      return Number(state.brightness) / 2.55;
    }
    return null;
  }

  function transitionKelvin(state = {}) {
    if (state.color_temp_kelvin != null && isFinite(Number(state.color_temp_kelvin))) {
      return Number(state.color_temp_kelvin);
    }
    if (state.color_temp != null && isFinite(Number(state.color_temp)) && Number(state.color_temp) > 0) {
      return 1000000 / Number(state.color_temp);
    }
    return null;
  }

  function transitionValueText(state = {}, empty = "unknown") {
    return lightValueText({
      brightnessPct: transitionBrightnessPct(state),
      colorTempKelvin: transitionKelvin(state),
      lightState: state.state,
      empty,
    });
  }

  function captureChangeSummary(packet, metadata = {}) {
    const kind = packet?.event_type || metadata.kind || "observation_packet";
    const zone = (packet?.zone || metadata.zone || "this zone").replace(/_/g, " ");
    const lightName = friendlyEntityName(metadata.light_entity, `${zone} light`);
    const actual = lightValueText({
      brightnessPct: metadata.actual_brightness_pct,
      colorTempKelvin: metadata.actual_color_temp_kelvin || metadata.captured_color_temp_kelvin,
      lightState: metadata.light_state,
      empty: "observed",
    });
    const predicted = lightValueText({
      brightnessPct: metadata.predicted_brightness_pct,
      colorTempKelvin: metadata.predicted_color_temp_kelvin || metadata.would_have_done_color_temp_kelvin,
      lightState: metadata.predicted_light_state,
      empty: "target unknown",
    });
    const captureTrigger = metadata.capture_provenance?.capture_trigger;
    const contextBits = [
      metadata.profile,
      metadata.state,
      metadata.context?.tv_playing || metadata.tv_playing ? "TV on" : null,
      metadata.context?.gaming_active || metadata.gaming_active ? "gaming" : null,
      metadata.context?.asleep || metadata.asleep ? "asleep" : null,
      metadata.shadow_mode ? "shadow" : null,
    ].filter(Boolean);

    if (kind === "pending_preference") {
      const note = captureTrigger === "vacancy_settle"
        ? "Captured after occupancy settled vacant. This is the durable preference evidence."
        : "Captured as pending preference evidence. This is weighted above raw override events.";
      return {
        eyebrow: "capture trigger",
        title: `${lightName} preference captured`,
        beforeLabel: "automation would have done",
        before: predicted,
        afterLabel: "captured state",
        after: actual,
        note,
        context: contextBits.join(" / "),
      };
    }

    if (kind === "override_event") {
      const transition = metadata.light_transition || metadata.lightTransition;
      if (transition?.from || transition?.to) {
        return {
          eyebrow: "actual transition",
          title: `${lightName} manual override detected`,
          beforeLabel: "before",
          before: transitionValueText(transition.from, "unknown"),
          afterLabel: "after",
          after: transitionValueText(transition.to, actual),
          note: `System target was ${predicted}. This transition is the Home Assistant state change that triggered capture.`,
          context: contextBits.join(" / "),
        };
      }
      return {
        eyebrow: "capture trigger",
        title: `${lightName} manual override detected`,
        beforeLabel: "system target",
        before: predicted,
        afterLabel: "observed light",
        after: actual,
        note: "Previous bulb state is not in this packet yet, so this compares the automation target with the state that triggered capture.",
        context: contextBits.join(" / "),
      };
    }

    return {
      eyebrow: "capture trigger",
      title: `${zone} observation captured`,
      beforeLabel: "source",
      before: String(kind).replace(/_/g, " "),
      afterLabel: "observed",
      after: actual,
      note: "This packet was created from stored home evidence for review and visual labeling.",
      context: contextBits.join(" / "),
    };
  }

  function frameEmptyState(packet, captureTiming = {}) {
    const status = String(packet?.status || "");
    const trigger = String(packet?.trigger || "");
    const error = packet?.error;
    if (/deleted/i.test(status)) {
      return {
        title: "Frames deleted",
        detail: "The observation remains for audit, but its image frames were removed.",
      };
    }
    if (error || status === "frames_missing") {
      return {
        title: "Frames unavailable",
        detail: error || "The capture job ran, but no usable camera frames were available.",
      };
    }
    if (captureTiming?.source === "none" || Number(packet?.frame_count || 0) === 0) {
      const backfilled = /batch|backfill|api/i.test(trigger);
      return {
        title: "No frames captured",
        detail: backfilled
          ? "This packet was created from existing HA evidence; no visual frame packet was attached."
          : "This observation has metadata, but no camera frames were attached.",
      };
    }
    return {
      title: "Frames unavailable",
      detail: "No frame records are attached to this observation.",
    };
  }

  function fmtTime(ts) {
    if (!ts) return "-";
    const d = new Date(ts);
    if (!isFinite(d.getTime())) return String(ts).slice(0, 16);
    return d.toLocaleString([], {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  }

  function fmtRatio(v) {
    if (v == null || !isFinite(v)) return "-";
    return `${Math.round(v * 100)}%`;
  }

  function fmtBytes(v) {
    const n = Number(v);
    if (!isFinite(n)) return "-";
    if (Math.abs(n) >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
    if (Math.abs(n) >= 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${Math.round(n)} B`;
  }

  function Chip({ children, tone = "default", title, ariaLabel, role, className = "", onClick }) {
    const color = tone === "warn" ? "var(--hg-warn)"
      : tone === "ok" ? "var(--hg-ice-bright)"
      : tone === "crit" ? "var(--hg-crit)"
      : "var(--hg-fg-4)";
    const interactive = Boolean(onClick);
    const Tag = interactive ? "button" : "span";
    return (
      <Tag
        type={interactive ? "button" : undefined}
        role={role}
        onClick={onClick}
        className={className}
        title={title}
        data-tooltip={title || undefined}
        aria-label={ariaLabel || title}
        tabIndex={title && !interactive ? 0 : undefined}
        style={{
        display: "inline-flex",
        alignItems: "center",
        minHeight: 18,
        padding: "1px 6px",
        border: `1px solid ${tone === "default" ? "var(--hg-border-soft)" : color}`,
        borderRadius: 3,
        color,
        fontFamily: "'Geist Mono', ui-monospace, monospace",
        fontSize: 10,
        letterSpacing: "0.04em",
        whiteSpace: "nowrap",
        background: "transparent",
        appearance: "none",
        cursor: interactive ? "pointer" : undefined,
      }}>{children}</Tag>
    );
  }

  function evidenceStatusLabel(mode) {
    const value = String(mode || "unknown");
    if (value === "fresh_ingested") return "just synced";
    if (value === "fresh_idle") return "synced";
    if (value === "behind") return "sync behind";
    if (value === "unreachable") return "HA unreachable";
    if (value === "auth_failed") return "HA auth failed";
    if (value === "fallback_snapshot") return "snapshot mode";
    return value.replace(/_/g, " ");
  }

  function evidenceStatusHelp(mode) {
    const value = String(mode || "unknown");
    if (value === "fresh_ingested") return "New Home Assistant evidence was pulled and stored by Intelligence Core recently.";
    if (value === "fresh_idle") return "Home Assistant is reachable, and Intelligence Core has caught up with the latest evidence.";
    if (value === "behind") return "Home Assistant has new evidence that Intelligence Core has not pulled yet.";
    if (value === "unreachable") return "Intelligence Core cannot reach the Home Assistant evidence API right now.";
    if (value === "auth_failed") return "Home Assistant rejected the token used by Intelligence Core.";
    if (value === "fallback_snapshot") return "Intelligence Core is using a local snapshot instead of live Home Assistant evidence.";
    return "Current live-evidence sync health.";
  }

  function promptGateLabel(gate = {}, evalStatus = {}) {
    if (gate.ready) return "prompt test ready";
    const reviewed = evalStatus?.audited_items ?? gate?.audited_items ?? 0;
    const target = evalStatus?.minimum_audited_items ?? gate?.minimum_audited_items ?? 50;
    return `prompt locked ${reviewed}/${target}`;
  }

  function promptGateHelp(gate = {}, evalStatus = {}) {
    if (gate.ready) {
      return "Enough human-reviewed packets exist to test Qwen prompt changes against a held-out set.";
    }
    const reviewed = evalStatus?.audited_items ?? gate?.audited_items ?? 0;
    const target = evalStatus?.minimum_audited_items ?? gate?.minimum_audited_items ?? 50;
    return `Qwen labeling is running. Prompt changes are locked until ${target} human-reviewed packets exist; currently ${reviewed}/${target}. Manual reviews build that test set.`;
  }

  function ContextChips({ context }) {
    const entries = Object.entries(context || {})
      .filter(([, v]) => v !== null && v !== undefined && v !== "");
    return (
      <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
        {entries.map(([k, v]) => (
          <Chip key={k}>{k.replace(/_/g, " ")}={String(v)}</Chip>
        ))}
      </div>
    );
  }

  function warningTone(w) {
    return w?.severity === "warn" ? "warn"
      : w?.severity === "crit" ? "crit"
      : "default";
  }

  function WarningList({ warnings = [], limit = 4 }) {
    const shown = warnings.slice(0, limit);
    if (shown.length === 0) {
      return <span style={{ color: "var(--hg-fg-5)", fontSize: 11 }}>no quality warnings</span>;
    }
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {shown.map((w, i) => (
          <div key={`${w.code || "warning"}-${i}`} style={{
            display: "grid",
            gridTemplateColumns: "auto 1fr",
            gap: 6,
            alignItems: "baseline",
            fontSize: 11,
            lineHeight: 1.35,
          }}>
            <Chip tone={warningTone(w)}>{w.code || "warning"}</Chip>
            <span style={{ color: "var(--hg-fg-3)" }}>
              {w.message}
              {w.details?.count > 1 ? ` (${w.details.count}x)` : ""}
            </span>
          </div>
        ))}
        {warnings.length > shown.length && (
          <span style={{ color: "var(--hg-fg-5)", fontSize: 10.5 }}>
            +{warnings.length - shown.length} more
          </span>
        )}
      </div>
    );
  }

  function CandidateRow({ candidate, onSuppress, onSelect, selected }) {
    const ready = candidate.status === "candidate";
    const target = candidate.suggested_target || {};
    const blockers = candidate.blockers || [];
    const warnings = candidate.quality_warnings || [];
    return (
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
        gap: 10,
        alignItems: "start",
        padding: "10px 0",
        borderBottom: "1px solid var(--hg-border-soft)",
        background: selected ? "var(--hg-bg-2)" : "transparent",
      }}>
        <div style={{ minWidth: 0 }}>
          <div style={{
            fontFamily: "'Geist Mono', ui-monospace, monospace",
            fontSize: 11,
            color: "var(--hg-fg-1)",
            fontWeight: 600,
          }}>{(candidate.zone || "unknown").replace(/_/g, " ")}</div>
          <div style={{ marginTop: 5 }}>
            <Chip tone={ready ? "ok" : "warn"}>{candidate.status}</Chip>
          </div>
        </div>

        <div style={{ minWidth: 0 }}>
          <ContextChips context={candidate.context} />
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(58px, 1fr))",
            gap: 8,
            marginTop: 9,
            fontFamily: "'Geist Mono', ui-monospace, monospace",
            fontSize: 10.5,
          }}>
            <Metric label="actual" value={fmtPct(candidate.median_actual_brightness_pct)} />
            <Metric label="pred" value={fmtPct(candidate.median_predicted_brightness_pct)} />
            <Metric label="delta" value={fmtPct(candidate.median_delta_pct)} />
            <Metric label="seen" value={`${candidate.non_shadow_count || 0}/${candidate.evidence_count || 0}`} />
            <Metric label="support" value={candidate.supporting_evidence_count || 0} />
            {(candidate.raw_evidence_count || 0) > (candidate.evidence_count || 0) && (
              <Metric label="raw" value={`${candidate.raw_evidence_count}→${candidate.evidence_count}`} />
            )}
          </div>
        </div>

        <div style={{ minWidth: 0 }}>
          <div style={{
            color: "var(--hg-fg-2)",
            fontFamily: "'Geist Mono', ui-monospace, monospace",
            fontSize: 10.5,
            marginBottom: 6,
          }}>
            target {target.light_state === "off" ? "off" : fmtPct(target.brightness_pct)}
          </div>
          {blockers.length > 0 ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
              {blockers.slice(0, 3).map((b) => (
                <span key={b} style={{
                  color: "var(--hg-fg-4)",
                  fontSize: 11,
                  lineHeight: 1.35,
                }}>{b}</span>
              ))}
            </div>
          ) : (
            <span style={{ color: "var(--hg-ice-bright)", fontSize: 11 }}>
              ready for proposal review
            </span>
          )}
          {warnings.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <Chip tone={warnings.some((w) => w.severity === "warn") ? "warn" : "default"}>
                {warnings.length} quality
              </Chip>
            </div>
          )}
        </div>

        <div style={{ display: "flex", gap: 6, justifyContent: "flex-end", flexWrap: "wrap" }}>
          <button
            className="hg-focusable"
            onClick={() => onSelect(candidate)}
            title="Inspect evidence"
            style={buttonStyle}
          >details</button>
          <button
            className="hg-focusable"
            onClick={() => onSuppress(candidate)}
            title="Do not learn this context"
            style={buttonStyle}
          >suppress</button>
        </div>
      </div>
    );
  }

  function Metric({ label, value }) {
    return (
      <div style={{ minWidth: 0 }}>
        <div style={{ color: "var(--hg-fg-3)", fontSize: 10 }}>{label}</div>
        <div style={{ color: "var(--hg-fg-1)", fontWeight: 600 }}>{value}</div>
      </div>
    );
  }

  function SummaryCard({ title, value, meta, tone }) {
    const Card = window.HmCard || ((props) => <div>{props.children}</div>);
    return (
      <Card title={title} badge={tone ? { text: tone, tone } : null}>
        <div style={{
          fontFamily: "'Geist Mono', ui-monospace, monospace",
          fontSize: 24,
          lineHeight: 1,
          fontWeight: 600,
          color: "var(--hg-fg-0)",
        }}>{value}</div>
        {meta && (
          <div style={{
            marginTop: 8,
            color: "var(--hg-fg-4)",
            fontSize: 11,
            lineHeight: 1.35,
          }}>{meta}</div>
        )}
      </Card>
    );
  }

  function EvidenceRow({ evidence }) {
    const payload = evidence.raw_event?.redacted_payload || {};
    const context = evidence.context || {};
    const predictions = evidence.predictions_by_zone || {};
    return (
      <div style={{
        borderTop: "1px solid var(--hg-border-soft)",
        padding: "10px 0",
        display: "grid",
        gridTemplateColumns: "minmax(120px, 170px) 1fr",
        gap: 12,
      }}>
        <div style={{ minWidth: 0 }}>
          <div style={{
            fontFamily: "'Geist Mono', ui-monospace, monospace",
            fontSize: 11,
            color: "var(--hg-fg-1)",
            fontWeight: 600,
          }}>{evidence.kind}</div>
          <div style={{ marginTop: 5 }}>
            <Chip tone={evidence.evidence_role === "supporting" ? "default" : "ok"}>
              {evidence.evidence_role || "primary"}
            </Chip>
          </div>
          <div style={{ marginTop: 5, color: "var(--hg-fg-5)", fontSize: 10.5 }}>
            {fmtTime(evidence.ts)}
          </div>
          <div style={{ marginTop: 7, display: "flex", flexWrap: "wrap", gap: 5 }}>
            <Chip>{fmtPct(evidence.actual_brightness_pct)} actual</Chip>
            <Chip>{fmtPct(evidence.predicted_brightness_pct)} predicted</Chip>
            <Chip>{fmtPct(evidence.delta_pct)} delta</Chip>
          </div>
        </div>
        <div style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 8 }}>
          <ContextChips context={context} />
          <pre style={preStyle}>{JSON.stringify({
            m4: {
              context_id: evidence.context_id,
              evidence_id: evidence.evidence_id,
              dedupe_key: evidence.dedupe_key,
              dedupe_window_s: evidence.dedupe_window_s,
              capture_suppressed_count: evidence.capture_suppressed_count,
              related_override_context_id: evidence.related_override_context_id,
              occupancy_flicker_count_5min: evidence.occupancy_flicker_count_5min,
            },
            predictions_by_zone: predictions,
            capture_provenance: evidence.capture_provenance || {},
            prediction_consistency: evidence.prediction_consistency || {},
            occupancy: evidence.occupancy || {},
            raw_event: payload,
          }, null, 2)}</pre>
        </div>
      </div>
    );
  }

  function EpisodeRow({ episode }) {
    const summary = episode.summary || {};
    const pending = summary.pending || {};
    const overrides = summary.overrides || [];
    const trigger = pending.capture_provenance?.capture_trigger || "-";
    const mismatch = pending.prediction_consistency?.prediction_mismatch_pct;
    const linkMethod = summary.link_method || "-";
    return (
      <div style={{
        borderTop: "1px solid var(--hg-border-soft)",
        padding: "8px 0",
        display: "grid",
        gridTemplateColumns: "minmax(120px, 170px) 1fr",
        gap: 12,
      }}>
        <div style={{
          fontFamily: "'Geist Mono', ui-monospace, monospace",
          fontSize: 10.5,
          color: "var(--hg-fg-4)",
        }}>
          {fmtTime(episode.end_ts)}
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          <Chip>{fmtPct(pending.actual_brightness_pct)} captured</Chip>
          <Chip>{fmtPct(pending.predicted_brightness_pct)} predicted</Chip>
          <Chip>{trigger}</Chip>
          <Chip>{linkMethod}</Chip>
          {pending.capture_suppressed_count > 0 && <Chip tone="warn">saved {pending.capture_suppressed_count}</Chip>}
          {mismatch != null && <Chip tone={Math.abs(mismatch) > 10 ? "warn" : "default"}>mismatch {mismatch}%</Chip>}
          <Chip>{overrides.length} linked touch{overrides.length === 1 ? "" : "es"}</Chip>
        </div>
      </div>
    );
  }

  function OverrideSessionRow({ session }) {
    const events = session.events || [];
    return (
      <div style={{
        borderTop: "1px solid var(--hg-border-soft)",
        padding: "8px 0",
        display: "grid",
        gridTemplateColumns: "minmax(120px, 170px) 1fr",
        gap: 12,
      }}>
        <div style={{
          fontFamily: "'Geist Mono', ui-monospace, monospace",
          fontSize: 10.5,
          color: "var(--hg-fg-4)",
        }}>
          {fmtTime(session.last_ts)}
          <div style={{ marginTop: 4, color: "var(--hg-fg-5)" }}>
            {session.duration_s != null ? `${Math.round(session.duration_s)}s` : "-"}
          </div>
        </div>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            <Chip tone={(session.size || 0) > 1 ? "warn" : "default"}>
              {session.size || 0} raw touch{session.size === 1 ? "" : "es"}
            </Chip>
            <Chip>{fmtPct(session.final_actual_brightness_pct)} final</Chip>
            {session.learning_value_pct !== session.final_actual_brightness_pct && (
              <Chip tone="ok">{fmtPct(session.learning_value_pct)} learn</Chip>
            )}
            <Chip>{fmtPct(session.final_predicted_brightness_pct)} predicted</Chip>
            <Chip>{fmtPct(session.final_delta_pct)} delta</Chip>
            {session.automation_tail_detected && <Chip tone="warn">automation tail</Chip>}
            {(session.burst_event_count || 0) > 0 && (
              <Chip tone="warn">{session.burst_event_count} collapsed</Chip>
            )}
          </div>
          {events.length > 0 && (
            <div style={{
              marginTop: 7,
              display: "flex",
              flexWrap: "wrap",
              gap: 4,
            }}>
              {events.slice(0, 10).map((event) => (
                <Chip key={event.id}>{fmtPct(event.actual_brightness_pct)}</Chip>
              ))}
              {events.length > 10 && <Chip>+{events.length - 10}</Chip>}
            </div>
          )}
        </div>
      </div>
    );
  }

  function ReadinessPanel({ readiness }) {
    if (!readiness) return null;
    const failed = (readiness.gates || []).filter((gate) => !gate.passed);
    const passed = (readiness.gates || []).filter((gate) => gate.passed);
    return (
      <div style={{
        borderTop: "1px solid var(--hg-border-soft)",
        padding: "10px 0",
        display: "grid",
        gridTemplateColumns: "minmax(160px, 220px) 1fr",
        gap: 12,
      }}>
        <div>
          <div style={{
            fontFamily: "'Geist Mono', ui-monospace, monospace",
            fontSize: 9.5,
            letterSpacing: "0.16em",
            textTransform: "uppercase",
            color: "var(--hg-fg-4)",
            marginBottom: 7,
          }}>readiness</div>
          <Chip tone={readiness.ready ? "ok" : "warn"}>{readiness.status}</Chip>
          <div style={{ marginTop: 7, color: "var(--hg-fg-5)", fontSize: 10.5 }}>
            {passed.length}/{(readiness.gates || []).length} gates passed
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          {failed.length === 0 ? (
            <span style={{ color: "var(--hg-ice-bright)", fontSize: 11 }}>
              ready for human proposal review
            </span>
          ) : failed.slice(0, 6).map((gate) => (
            <div key={gate.code} style={{
              display: "grid",
              gridTemplateColumns: "auto 1fr",
              gap: 6,
              alignItems: "baseline",
              fontSize: 11,
              lineHeight: 1.35,
            }}>
              <Chip tone="warn">{gate.code}</Chip>
              <span style={{ color: "var(--hg-fg-3)" }}>{gate.message}</span>
            </div>
          ))}
          {failed.length > 6 && (
            <span style={{ color: "var(--hg-fg-5)", fontSize: 10.5 }}>
              +{failed.length - 6} more failed gates
            </span>
          )}
        </div>
      </div>
    );
  }

  function ProposalPreview({ preview }) {
    if (!preview) return null;
    const rule = preview.proposed_rule || {};
    const target = rule.target || {};
    return (
      <div style={{
        borderTop: "1px solid var(--hg-border-soft)",
        padding: "10px 0",
        display: "grid",
        gridTemplateColumns: "minmax(160px, 220px) 1fr",
        gap: 12,
      }}>
        <div>
          <div style={{
            fontFamily: "'Geist Mono', ui-monospace, monospace",
            fontSize: 9.5,
            letterSpacing: "0.16em",
            textTransform: "uppercase",
            color: "var(--hg-fg-4)",
            marginBottom: 7,
          }}>experiment preview</div>
          <Chip tone={preview.ready ? "ok" : "warn"}>{preview.status}</Chip>
          <div style={{ marginTop: 7, color: "var(--hg-fg-5)", fontSize: 10.5 }}>
            no write path
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ color: "var(--hg-fg-2)", fontSize: 12, lineHeight: 1.35 }}>
            {preview.title}
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            <Chip>{preview.affected_zone}</Chip>
            <Chip>target {target.light_state === "off" ? "off" : fmtPct(target.brightness_pct)}</Chip>
            <Chip>current {fmtPct(rule.current_median_predicted_brightness_pct)}</Chip>
            <Chip>observed {fmtPct(rule.observed_median_actual_brightness_pct)}</Chip>
            <Chip>saved {preview.why_now?.capture_suppressed_total || 0}</Chip>
          </div>
          <div style={{ color: "var(--hg-fg-4)", fontSize: 11, lineHeight: 1.4 }}>
            metric: {preview.success_metric}
          </div>
          <div style={{ color: "var(--hg-fg-4)", fontSize: 11, lineHeight: 1.4 }}>
            rollback: {preview.rollback?.condition}
          </div>
        </div>
      </div>
    );
  }

  function healthTone(status) {
    return status === "needs_attention" ? "crit"
      : status === "watch" ? "warn"
      : "ok";
  }

  function CompactEvidenceLine({ item }) {
    if (!item) return <span style={{ color: "var(--hg-fg-5)", fontSize: 10.5 }}>none</span>;
    return (
      <div style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        flexWrap: "wrap",
        minWidth: 0,
      }}>
        <span style={{
          color: "var(--hg-fg-3)",
          fontFamily: "'Geist Mono', ui-monospace, monospace",
          fontSize: 10.5,
        }}>{fmtTime(item.ts)}</span>
        <Chip>{(item.zone || "").replace(/_/g, " ")}</Chip>
        <Chip>{fmtPct(item.actual_brightness_pct)} actual</Chip>
        <Chip>{fmtPct(item.predicted_brightness_pct)} pred</Chip>
        {item.capture_trigger && <Chip>{item.capture_trigger}</Chip>}
        {item.capture_suppressed_count > 0 && <Chip tone="warn">saved {item.capture_suppressed_count}</Chip>}
        {item.occupancy_flicker_count_5min >= 3 && <Chip tone="warn">flicker {item.occupancy_flicker_count_5min}</Chip>}
      </div>
    );
  }

  const activityColors = {
    desk_work: "#8fd7ff",
    reading: "#b7e07a",
    watching_tv: "#a78bfa",
    cooking: "#ffbf69",
    eating: "#f6d365",
    exercise: "#ff7a90",
    cleaning: "#7dd3fc",
    relaxing: "#8ee8c8",
    passing_through: "#cbd5e1",
    arriving_or_leaving: "#f59e0b",
    sleeping_or_napping: "#93a4ff",
    standing_idle: "#d8b4fe",
    unknown: "var(--hg-fg-5)",
  };

  const PRIMARY_ACTIVITY_OPTIONS = [
    ["desk_work", "desk work"],
    ["reading", "reading"],
    ["watching_tv", "watching tv"],
    ["cooking", "cooking"],
    ["eating", "eating"],
    ["exercise", "exercise"],
    ["cleaning", "cleaning"],
    ["relaxing", "relaxing"],
    ["passing_through", "passing through"],
    ["arriving_or_leaving", "arriving/leaving"],
    ["sleeping_or_napping", "sleeping"],
    ["standing_idle", "standing"],
  ];

  const NOT_LABELABLE_REASONS = [
    ["empty_room", "empty room"],
    ["too_dark", "too dark"],
    ["occluded", "occluded"],
    ["no_person_visible", "no person visible"],
    ["ambiguous", "ambiguous"],
  ];

  function labelAxis(labels, axis) {
    const value = labels?.[axis];
    if (!value?.label) return { label: "unknown", confidence: 0 };
    return value;
  }

  function compactLabelText(value) {
    const label = String(value?.label || "unknown").replace(/_/g, " ");
    const confidence = Number(value?.confidence || 0);
    return confidence > 0 ? `${label} ${Math.round(confidence * 100)}%` : label;
  }

  function packetDisplayLabels(packet) {
    return packet?.effective_labels || packet?.latest_labels || {};
  }

  function packetNeedsReview(packet) {
    const server = packet?.needs_review;
    if (server && Array.isArray(server.reasons)) return server;
    const labels = packetDisplayLabels(packet);
    const qwenLabels = packet?.latest_labels || {};
    const activity = labelAxis(qwenLabels, "primary_activity");
    const presence = labelAxis(qwenLabels, "presence");
    const quality = labelAxis(qwenLabels, "image_quality");
    const privacy = labelAxis(qwenLabels, "privacy_sensitivity");
    const consistency = labelAxis(qwenLabels, "metadata_consistency");
    const confidentEmpty = presence.label === "empty" && (presence.confidence || 0) >= 0.75;
    const reasons = [];
    if (packet?.qwen_status === "invalid") reasons.push("invalid qwen label");
    if (!packet?.latest_labels) reasons.push("no qwen label");
    if ((activity.label === "unknown" || !activity.label) && !confidentEmpty) reasons.push("unknown activity");
    else if (activity.label !== "unknown" && (activity.confidence || 0) < 0.55) reasons.push("low confidence");
    if (["blurry", "too_dark", "overexposed", "occluded", "camera_blocked", "uncertain"].includes(quality.label)) reasons.push(`image quality: ${quality.label}`);
    if (["uncertain", "private_activity_possible", "face_closeup", "screen_content_visible"].includes(privacy.label)) reasons.push(`privacy: ${privacy.label}`);
    if (["metadata_contradiction", "insufficient_visual_evidence", "uncertain"].includes(consistency.label)) reasons.push(`metadata: ${consistency.label}`);
    if ((packet?.human_label_overrides || {}).primary_activity) return { primary_activity: false, reasons: [], reviewed: true };
    return { primary_activity: reasons.length > 0, reasons: Array.from(new Set(reasons)), reviewed: false };
  }

  function ObservationExplorer({
    base,
    status,
    packets = [],
    map,
    detail,
    evalStatus,
    evalQueue,
    promptGate,
    manifests,
    onSelect,
    onRunPilot,
    onLabelNext,
    onSeedEval,
    onAcceptEval,
    onCreateManifest,
    onAudit,
    onDeleteFrames,
  }) {
    const points = map?.points || [];
    const selectedId = detail?.packet?.id;
    const selectedLabels = detail?.latest_label?.labels || detail?.packet?.latest_labels || {};
    const selectedActivity = labelAxis(selectedLabels, "primary_activity");
    const selectedQuality = labelAxis(selectedLabels, "image_quality");
    const selectedPrivacy = labelAxis(selectedLabels, "privacy_sensitivity");
    const captureTiming = detail?.capture_timing || {};
    const labelUsability = detail?.label_usability || detail?.packet?.label_usability || {};
    const packetCounts = status?.counts || {};
    const ring = status?.ring_buffer || {};
    const queueItems = evalQueue?.items || [];
    const manifestRows = manifests?.manifests || [];
    const promptGateBody = promptGate?.gate || evalStatus?.prompt_gate || {};
    return (
      <div style={{
        background: "var(--hg-bg-1)",
        border: "1px solid var(--hg-border-soft)",
        borderRadius: 6,
        padding: "12px 14px",
      }}>
        <div style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 10,
          marginBottom: 10,
        }}>
          <span style={{
            fontFamily: "'Geist Mono', ui-monospace, monospace",
            fontSize: 9.5,
            letterSpacing: "0.18em",
            textTransform: "uppercase",
            color: "var(--hg-fg-4)",
          }}>observation explorer</span>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, justifyContent: "flex-end" }}>
            <Chip
              tone={status?.config?.enabled ? "ok" : "warn"}
              title="Multimodal capture and labeling run locally; no cloud upload is enabled."
            >{status?.config?.enabled ? "local" : "off"}</Chip>
            <Chip
              tone={ring.running ? "ok" : "default"}
              title="The short camera frame ring buffer is used to recover pre-event frames."
            >ring {ring.running ? "on" : "off"}</Chip>
            <Chip title="Observation packets are event-linked records that may include frames, HA metadata, and labels.">
              {packetCounts.observation_packets || 0} packets
            </Chip>
            <Chip title="Qwen visual labeling results stored for observation packets.">{packetCounts.qwen_label_results || 0} labels</Chip>
            <Chip
              tone={evalStatus?.ready_for_prompt_gate ? "ok" : "default"}
              title="Audited observations available for validating future prompt changes."
            >
              eval {evalStatus?.audited_items || 0}/{evalStatus?.minimum_audited_items || 50}
            </Chip>
            <Chip tone={promptGateBody.ready ? "ok" : "warn"} title={promptGateHelp(promptGateBody)}>
              {promptGateLabel(promptGateBody)}
            </Chip>
            <Chip title="Training clip manifests are future V-JEPA curation references, not active training jobs.">
              {manifestRows.length || packetCounts.training_clip_manifests || 0} manifests
            </Chip>
            <button className="hg-focusable" onClick={onRunPilot} style={buttonStyle}>run pilot</button>
            <button className="hg-focusable" onClick={onLabelNext} style={buttonStyle}>label next</button>
            <button className="hg-focusable" onClick={onSeedEval} style={buttonStyle}>seed eval</button>
          </div>
        </div>

        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
          gap: 12,
          alignItems: "stretch",
        }}>
          <div style={{ minWidth: 0 }}>
            <div style={{
              color: "var(--hg-fg-5)",
              fontFamily: "'Geist Mono', ui-monospace, monospace",
              fontSize: 9.5,
              letterSpacing: "0.12em",
              textTransform: "uppercase",
              marginBottom: 6,
            }}>timeline</div>
            <div style={{
              display: "flex",
              flexDirection: "column",
              gap: 6,
              maxHeight: 310,
              overflow: "auto",
              paddingRight: 2,
            }}>
              {packets.length === 0 ? (
                <span style={{ color: "var(--hg-fg-5)", fontSize: 11 }}>no visual packets yet</span>
              ) : packets.slice(0, 12).map((packet) => {
                const labels = packet.latest_labels || {};
                const activity = labelAxis(labels, "primary_activity");
                const quality = labelAxis(labels, "image_quality");
                const active = selectedId === packet.id;
                return (
                  <button
                    key={packet.id}
                    className="hg-focusable"
                    onClick={() => onSelect(packet)}
                    style={{
                      ...buttonStyle,
                      width: "100%",
                      textAlign: "left",
                      borderColor: active ? "var(--hg-ice-bright)" : "var(--hg-border-soft)",
                      background: active ? "var(--hg-bg-2)" : "transparent",
                      padding: "8px 9px",
                    }}
                  >
                    <div style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                      minWidth: 0,
                      marginBottom: 5,
                    }}>
                      <span style={{
                        width: 7,
                        height: 7,
                        borderRadius: 999,
                        background: activityColors[activity.label] || activityColors.unknown,
                        flex: "0 0 auto",
                      }} />
                      <span style={{
                        color: "var(--hg-fg-2)",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}>{(packet.zone || "unknown").replace(/_/g, " ")}</span>
                      <span style={{ color: "var(--hg-fg-5)", marginLeft: "auto" }}>{fmtTime(packet.event_ts)}</span>
                    </div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                      <Chip>{activity.label}</Chip>
                      <Chip>{packet.qwen_status}</Chip>
                      <Chip>{packet.frame_count || 0} frames</Chip>
                      {quality.label !== "unknown" && <Chip>{quality.label}</Chip>}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          <div style={{ minWidth: 0 }}>
            <div style={{
              color: "var(--hg-fg-5)",
              fontFamily: "'Geist Mono', ui-monospace, monospace",
              fontSize: 9.5,
              letterSpacing: "0.12em",
              textTransform: "uppercase",
              marginBottom: 6,
            }}>atlas</div>
            <div style={{
              position: "relative",
              height: 318,
              overflow: "hidden",
              border: "1px solid var(--hg-border-soft)",
              borderRadius: 5,
              background: "var(--hg-bg-0)",
            }}>
              {Object.entries(activityColors).slice(0, 12).map(([label, color], i) => (
                <span key={label} style={{
                  position: "absolute",
                  left: `${6 + (i % 4) * 23}%`,
                  top: `${7 + Math.floor(i / 4) * 11}%`,
                  color,
                  opacity: 0.42,
                  fontFamily: "'Geist Mono', ui-monospace, monospace",
                  fontSize: 9,
                  pointerEvents: "none",
                }}>{label.replace(/_/g, " ")}</span>
              ))}
              {points.map((point) => {
                const color = activityColors[point.activity] || activityColors.unknown;
                const active = selectedId === point.id;
                return (
                  <button
                    key={point.id}
                    className="hg-focusable"
                    title={`${point.activity} · ${point.zone || "unknown"} · ${fmtTime(point.event_ts)}`}
                    onClick={() => onSelect(point)}
                    style={{
                      position: "absolute",
                      left: `${point.x}%`,
                      top: `${point.y}%`,
                      width: active ? 16 : Math.max(8, Math.min(14, 8 + (point.frame_count || 0))),
                      height: active ? 16 : Math.max(8, Math.min(14, 8 + (point.frame_count || 0))),
                      transform: "translate(-50%, -50%)",
                      borderRadius: 999,
                      border: point.privacy_sensitive ? `1px dashed ${color}` : `1px solid ${active ? "var(--hg-fg-0)" : color}`,
                      background: point.linked ? color : "transparent",
                      opacity: Math.max(0.25, Math.min(1, point.confidence || 0.35)),
                      boxShadow: active ? `0 0 0 4px rgba(143,215,255,0.14)` : "none",
                      cursor: "pointer",
                      padding: 0,
                    }}
                  />
                );
              })}
              {points.length === 0 && (
                <div style={{
                  position: "absolute",
                  inset: 0,
                  display: "grid",
                  placeItems: "center",
                  color: "var(--hg-fg-5)",
                  fontSize: 11,
                }}>no atlas points</div>
              )}
            </div>
          </div>

          <div style={{ minWidth: 0 }}>
            <div style={{
              color: "var(--hg-fg-5)",
              fontFamily: "'Geist Mono', ui-monospace, monospace",
              fontSize: 9.5,
              letterSpacing: "0.12em",
              textTransform: "uppercase",
              marginBottom: 6,
            }}>evidence</div>
            {!detail?.packet ? (
              <div style={{ color: "var(--hg-fg-5)", fontSize: 11, padding: "10px 0" }}>
                select a packet
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  <Chip>{selectedActivity.label} {Math.round((selectedActivity.confidence || 0) * 100)}%</Chip>
                  <Chip>{selectedQuality.label}</Chip>
                  <Chip tone={selectedPrivacy.label === "normal" ? "default" : "warn"}>{selectedPrivacy.label}</Chip>
                  <Chip tone={captureTiming.event_aligned ? "ok" : captureTiming.frame_count ? "warn" : "default"}>
                    timing {captureTiming.quality || "unknown"}
                  </Chip>
                  <Chip tone={labelUsability.usable_for_clustering ? "ok" : "warn"}>
                    cluster {labelUsability.usable_for_clustering ? "yes" : "no"}
                  </Chip>
                  <Chip>{detail.packet.prompt_version || detail.packet.model_versions?.prompt_version || "prompt v1"}</Chip>
                </div>
                <div style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(84px, 1fr))",
                  gap: 6,
                }}>
                  {(detail.frames || []).slice(0, 4).map((frame) => (
                    <div key={frame.id} style={{
                      border: "1px solid var(--hg-border-soft)",
                      borderRadius: 4,
                      overflow: "hidden",
                      background: "var(--hg-bg-0)",
                      minHeight: 74,
                    }}>
                      {!frame.deleted_at ? (
                        <img
                          src={`${base}${frame.image_url}`}
                          alt={frame.frame_id}
                          style={{
                            display: "block",
                            width: "100%",
                            aspectRatio: "4 / 3",
                            objectFit: "cover",
                          }}
                        />
                      ) : (
                        <div style={{ height: 74, display: "grid", placeItems: "center", color: "var(--hg-fg-5)", fontSize: 10 }}>
                          deleted
                        </div>
                      )}
                      <div style={{
                        padding: "4px 5px",
                        color: "var(--hg-fg-5)",
                        fontFamily: "'Geist Mono', ui-monospace, monospace",
                        fontSize: 9,
                      }}>{frame.frame_role}</div>
                    </div>
                  ))}
                  {(detail.frames || []).length === 0 && (
                    <span className="intel-atlas-empty-frames-inline">
                      <b>{frameEmptyState(detail.packet, captureTiming).title}</b>
                      <span>{frameEmptyState(detail.packet, captureTiming).detail}</span>
                    </span>
                  )}
                </div>
                {detail.latest_label?.labels?.short_visible_summary && (
                  <div style={{ color: "var(--hg-fg-3)", fontSize: 12, lineHeight: 1.4 }}>
                    {detail.latest_label.labels.short_visible_summary}
                  </div>
                )}
                <ContextChips context={{
                  zone: detail.packet.zone,
                  event: detail.packet.event_type,
                  qwen: detail.packet.qwen_status,
                  frames: detail.packet.frame_count,
                  timing: captureTiming.quality,
                  clustering: labelUsability.usable_for_clustering ? "usable" : "blocked",
                }} />
                {(labelUsability.blockers || []).length > 0 && (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                    {labelUsability.blockers.slice(0, 4).map((blocker) => (
                      <Chip key={blocker} tone="warn">{blocker}</Chip>
                    ))}
                  </div>
                )}
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  <button className="hg-focusable" onClick={() => onAudit(detail.packet, "label_looks_right")} style={buttonStyle}>right</button>
                  <button className="hg-focusable" onClick={() => onAudit(detail.packet, "label_looks_wrong")} style={buttonStyle}>wrong</button>
                  <button className="hg-focusable" onClick={() => onAudit(detail.packet, "ignore_observation")} style={buttonStyle}>ignore</button>
                  <button className="hg-focusable" onClick={() => onAudit(detail.packet, "do_not_learn_context")} style={buttonStyle}>do not learn</button>
                  <button className="hg-focusable" onClick={() => onCreateManifest(detail.packet)} style={buttonStyle}>clip manifest</button>
                  <button className="hg-focusable" onClick={() => onDeleteFrames(detail.packet)} style={buttonStyle}>delete frames</button>
                </div>
                <div style={{
                  borderTop: "1px solid var(--hg-border-soft)",
                  paddingTop: 8,
                  display: "flex",
                  flexDirection: "column",
                  gap: 6,
                }}>
                  <div style={{
                    color: "var(--hg-fg-5)",
                    fontFamily: "'Geist Mono', ui-monospace, monospace",
                    fontSize: 9.5,
                    letterSpacing: "0.12em",
                    textTransform: "uppercase",
                  }}>eval queue</div>
                  {queueItems.length === 0 ? (
                    <span style={{ color: "var(--hg-fg-5)", fontSize: 11 }}>no unaudited eval items</span>
                  ) : queueItems.slice(0, 3).map((item) => {
                    const labels = item.qwen_labels || {};
                    const activity = labelAxis(labels, "primary_activity");
                    return (
                      <div key={item.id} style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                        flexWrap: "wrap",
                        border: "1px solid var(--hg-border-soft)",
                        borderRadius: 4,
                        padding: "6px 7px",
                      }}>
                        <Chip>{(item.packet_summary?.zone || "unknown").replace(/_/g, " ")}</Chip>
                        <Chip>{activity.label} {Math.round((activity.confidence || 0) * 100)}%</Chip>
                        <Chip>{item.packet_summary?.frame_count || 0} frames</Chip>
                        <button className="hg-focusable" onClick={() => onAcceptEval(item)} style={{ ...buttonStyle, marginLeft: "auto" }}>accept qwen</button>
                      </div>
                    );
                  })}
                </div>
                <div style={{
                  borderTop: "1px solid var(--hg-border-soft)",
                  paddingTop: 8,
                  display: "flex",
                  flexDirection: "column",
                  gap: 6,
                }}>
                  <div style={{
                    color: "var(--hg-fg-5)",
                    fontFamily: "'Geist Mono', ui-monospace, monospace",
                    fontSize: 9.5,
                    letterSpacing: "0.12em",
                    textTransform: "uppercase",
                  }}>training manifests</div>
                  {manifestRows.length === 0 ? (
                    <span style={{ color: "var(--hg-fg-5)", fontSize: 11 }}>no clip manifests yet</span>
                  ) : manifestRows.slice(0, 3).map((manifest) => (
                    <div key={manifest.id} style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                      flexWrap: "wrap",
                      border: "1px solid var(--hg-border-soft)",
                      borderRadius: 4,
                      padding: "6px 7px",
                    }}>
                      <Chip>{(manifest.zone || "unknown").replace(/_/g, " ")}</Chip>
                      <Chip tone={manifest.status === "draft_eligible" ? "ok" : "warn"}>{manifest.status}</Chip>
                      <Chip>{manifest.frame_count_tier}</Chip>
                      {(manifest.privacy_flags || []).slice(0, 2).map((flag) => (
                        <Chip key={flag} tone="warn">{flag}</Chip>
                      ))}
                    </div>
                  ))}
                </div>
                <pre style={{ ...preStyle, maxHeight: 170 }}>{JSON.stringify({
                  packet: detail.packet,
                  latest_label: detail.latest_label,
                  audits: detail.audits,
                }, null, 2)}</pre>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  function SoakReview({ soak }) {
    if (!soak?.ok) return null;
    const summary = soak.summary || {};
    const change = soak.change || {};
    const zones = soak.zones || [];
    const latestPending = soak.latest_pending_preferences || [];
    const candidateChanges = soak.candidate_changes || [];
    return (
      <div style={{
        background: "var(--hg-bg-1)",
        border: "1px solid var(--hg-border-soft)",
        borderRadius: 6,
        padding: "12px 14px",
      }}>
        <div style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 10,
          marginBottom: 10,
        }}>
          <span style={{
            fontFamily: "'Geist Mono', ui-monospace, monospace",
            fontSize: 9.5,
            letterSpacing: "0.18em",
            textTransform: "uppercase",
            color: "var(--hg-fg-4)",
          }}>soak review</span>
          <span style={{ color: "var(--hg-fg-5)", fontSize: 10.5 }}>
            {fmtTime(soak.window?.start)} to {fmtTime(soak.window?.end)}
          </span>
        </div>

        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(88px, 1fr))",
          gap: 10,
          marginBottom: 12,
        }}>
          <Metric label="observed" value={summary.observation_count || 0} />
          <Metric label="pending" value={summary.pending_count || 0} />
          <Metric label="overrides" value={summary.override_count || 0} />
          <Metric label="m4" value={summary.m4_record_count || 0} />
          <Metric label="saved" value={summary.capture_suppressed_total || 0} />
          <Metric label="bursts" value={summary.burst_event_count || 0} />
          <Metric label="direct" value={summary.direct_link_episode_count || 0} />
          <Metric label="ready" value={`${summary.ready_candidate_count || 0}/${summary.candidate_count || 0}`} />
          <Metric label="delta" value={`${change.pending_delta || 0} pref`} />
        </div>

        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
          gap: 14,
          alignItems: "start",
        }}>
          <div style={{ minWidth: 0 }}>
            <div style={{
              color: "var(--hg-fg-5)",
              fontFamily: "'Geist Mono', ui-monospace, monospace",
              fontSize: 9.5,
              letterSpacing: "0.12em",
              textTransform: "uppercase",
              marginBottom: 4,
            }}>zone health</div>
            {zones.length === 0 ? (
              <div style={{ color: "var(--hg-fg-4)", fontSize: 12, padding: "10px 0" }}>
                no soak evidence
              </div>
            ) : zones.slice(0, 8).map((zone) => (
              <div key={zone.zone} style={{
                display: "grid",
                gridTemplateColumns: "minmax(76px, 0.9fr) minmax(160px, 1.6fr)",
                gap: 10,
                alignItems: "start",
                padding: "8px 0",
                borderTop: "1px solid var(--hg-border-soft)",
              }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{
                    color: "var(--hg-fg-1)",
                    fontFamily: "'Geist Mono', ui-monospace, monospace",
                    fontSize: 11,
                    fontWeight: 600,
                    marginBottom: 5,
                  }}>{(zone.zone || "").replace(/_/g, " ")}</div>
                  <Chip tone={healthTone(zone.health)}>{zone.health}</Chip>
                </div>
                <div style={{ minWidth: 0 }}>
                  <div style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(48px, 1fr))",
                    gap: 8,
                    marginBottom: 7,
                    fontFamily: "'Geist Mono', ui-monospace, monospace",
                    fontSize: 10.5,
                  }}>
                    <Metric label="pref" value={zone.pending_preferences || 0} />
                    <Metric label="touch" value={zone.overrides || 0} />
                    <Metric label="burst" value={zone.burst_event_count || 0} />
                    <Metric label="link" value={`${zone.direct_link_episode_count || 0}/${zone.linked_episode_count || 0}`} />
                    <Metric label="saved" value={zone.capture_suppressed_total || 0} />
                    <Metric label="dup" value={fmtRatio(zone.max_duplicate_pending_ratio || 0)} />
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                    {(zone.top_blockers || []).slice(0, 2).map((b) => (
                      <span key={b.blocker} style={{
                        color: "var(--hg-warn)",
                        fontSize: 10.5,
                        lineHeight: 1.3,
                      }}>{b.blocker} ({b.count})</span>
                    ))}
                    {zone.ready_candidates > 0 && <Chip tone="ok">{zone.ready_candidates} ready</Chip>}
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 12 }}>
            <div>
              <div style={{
                color: "var(--hg-fg-5)",
                fontFamily: "'Geist Mono', ui-monospace, monospace",
                fontSize: 9.5,
                letterSpacing: "0.12em",
                textTransform: "uppercase",
                marginBottom: 5,
              }}>latest captures</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {latestPending.length === 0 ? (
                  <CompactEvidenceLine item={null} />
                ) : latestPending.slice(0, 4).map((item) => (
                  <CompactEvidenceLine key={item.id} item={item} />
                ))}
              </div>
            </div>

            <div>
              <div style={{
                color: "var(--hg-fg-5)",
                fontFamily: "'Geist Mono', ui-monospace, monospace",
                fontSize: 9.5,
                letterSpacing: "0.12em",
                textTransform: "uppercase",
                marginBottom: 5,
              }}>candidate movement</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {candidateChanges.length === 0 ? (
                  <span style={{ color: "var(--hg-fg-5)", fontSize: 10.5 }}>no revision movement</span>
                ) : candidateChanges.slice(0, 4).map((item) => (
                  <div key={`${item.candidate_id}-${item.created_at}`} style={{
                    display: "flex",
                    flexWrap: "wrap",
                    alignItems: "center",
                    gap: 6,
                  }}>
                    <Chip tone={item.status === "candidate" ? "ok" : "warn"}>{item.change_kind}</Chip>
                    <span style={{
                      color: "var(--hg-fg-3)",
                      fontFamily: "'Geist Mono', ui-monospace, monospace",
                      fontSize: 10.5,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}>{item.candidate_id}</span>
                    {item.evidence_count_delta != null && <Chip>evidence {item.evidence_count_delta >= 0 ? "+" : ""}{item.evidence_count_delta}</Chip>}
                    {(item.blockers_cleared || []).length > 0 && <Chip tone="ok">{item.blockers_cleared.length} cleared</Chip>}
                    {(item.blockers_added || []).length > 0 && <Chip tone="warn">{item.blockers_added.length} added</Chip>}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  function JobReview({ scheduler, jobs = [] }) {
    if (!scheduler) return null;
    const latest = jobs[0] || null;
    return (
      <div style={{
        background: "var(--hg-bg-1)",
        border: "1px solid var(--hg-border-soft)",
        borderRadius: 6,
        padding: "12px 14px",
      }}>
        <div style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 10,
          marginBottom: 10,
        }}>
          <span style={{
            fontFamily: "'Geist Mono', ui-monospace, monospace",
            fontSize: 9.5,
            letterSpacing: "0.18em",
            textTransform: "uppercase",
            color: "var(--hg-fg-4)",
          }}>scheduled jobs</span>
          <span style={{ color: "var(--hg-fg-5)", fontSize: 10.5 }}>
            {scheduler.enabled ? "enabled" : "disabled"}
          </span>
        </div>
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(96px, 1fr))",
          gap: 10,
          marginBottom: 10,
        }}>
          <Metric label="running" value={scheduler.running ? "yes" : "no"} />
          <Metric label="in flight" value={scheduler.in_flight || "-"} />
          <Metric label="evidence next" value={fmtTime(scheduler.next_evidence_pull_at)} />
          <Metric label="vision next" value={fmtTime(scheduler.next_multimodal_capture_at)} />
          <Metric label="ingest next" value={fmtTime(scheduler.next_ingest_at)} />
          <Metric label="memory next" value={fmtTime(scheduler.next_memory_at)} />
          <Metric label="last tick" value={fmtTime(scheduler.last_tick_at)} />
        </div>
        {scheduler.last_error && <Chip role="status" tone="crit">{scheduler.last_error}</Chip>}
        <div style={{ display: "flex", flexDirection: "column", gap: 5, marginTop: scheduler.last_error ? 8 : 0 }}>
          {jobs.slice(0, 5).map((job) => (
            <div key={job.id} style={{
              display: "grid",
              gridTemplateColumns: "minmax(120px, 1fr) auto auto",
              gap: 8,
              alignItems: "center",
              borderTop: "1px solid var(--hg-border-soft)",
              paddingTop: 7,
              color: "var(--hg-fg-3)",
              fontFamily: "'Geist Mono', ui-monospace, monospace",
              fontSize: 10.5,
            }}>
              <span>{job.name}</span>
              <Chip tone={job.status === "ok" ? "ok" : job.status === "error" ? "crit" : "warn"}>
                {job.status}
              </Chip>
              <span style={{ color: "var(--hg-fg-5)" }}>{fmtTime(job.finished_at || job.started_at)}</span>
            </div>
          ))}
          {!latest && <span style={{ color: "var(--hg-fg-5)", fontSize: 10.5 }}>no jobs yet</span>}
        </div>
      </div>
    );
  }

  function EvidenceSourceReview({ evidenceSources }) {
    const body = evidenceSources?.evidence_sources || evidenceSources;
    if (!body) return null;
    const sources = body.sources || [];
    const statusTone = body.overall_status === "fresh_ingested" || body.overall_status === "fresh_idle"
      ? "ok"
      : body.overall_status === "behind" || body.overall_status === "fallback_snapshot"
        ? "warn"
        : "crit";
    return (
      <div style={{
        background: "var(--hg-bg-1)",
        border: "1px solid var(--hg-border-soft)",
        borderRadius: 6,
        padding: "12px 14px",
      }}>
        <div style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 10,
          marginBottom: 10,
        }}>
          <span style={{
            fontFamily: "'Geist Mono', ui-monospace, monospace",
            fontSize: 9.5,
            letterSpacing: "0.18em",
            textTransform: "uppercase",
            color: "var(--hg-fg-4)",
          }}>evidence sources</span>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
            <Chip tone={statusTone}>{body.overall_status || "unknown"}</Chip>
            <Chip>{body.mode || body.configured_transport || "source"}</Chip>
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {sources.map((source) => {
            const tone = source.status === "fresh_ingested" || source.status === "fresh_idle"
              ? "ok"
              : source.status === "behind" || source.status === "fallback_snapshot"
                ? "warn"
                : "crit";
            return (
              <div key={source.source} style={{
                display: "grid",
                gridTemplateColumns: "minmax(90px, 130px) minmax(90px, 130px) repeat(3, minmax(90px, 1fr))",
                gap: 8,
                alignItems: "baseline",
                borderTop: "1px solid var(--hg-border-soft)",
                paddingTop: 7,
              }}>
                <Chip tone={tone}>{source.source}</Chip>
                <Chip>{source.transport}</Chip>
                <span style={{ color: "var(--hg-fg-4)", fontSize: 11 }}>
                  lag {fmtBytes(source.lag_bytes)}
                </span>
                <span style={{ color: "var(--hg-fg-4)", fontSize: 11 }}>
                  source {fmtTime(source.latest_source_ts || source.source_mtime)}
                </span>
                <span style={{ color: "var(--hg-fg-4)", fontSize: 11 }}>
                  ingested {fmtTime(source.latest_ingested_ts || source.last_success_at)}
                </span>
                {source.last_error && (
                  <span role="status" style={{ color: "var(--hg-warn)", fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {source.last_error}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  function EvidenceLinkAudit({ audit }) {
    if (!audit?.ok) return null;
    const summary = audit.summary || {};
    const items = audit.items || [];
    const risky = items.filter((item) => item.status === "reconciled" || (item.flags || []).length > 0);
    return (
      <div style={{
        background: "var(--hg-bg-1)",
        border: "1px solid var(--hg-border-soft)",
        borderRadius: 6,
        padding: "12px 14px",
      }}>
        <div style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 10,
          marginBottom: 10,
        }}>
          <span style={{
            fontFamily: "'Geist Mono', ui-monospace, monospace",
            fontSize: 9.5,
            letterSpacing: "0.18em",
            textTransform: "uppercase",
            color: "var(--hg-fg-4)",
          }}>evidence link audit</span>
          <span style={{ color: "var(--hg-fg-5)", fontSize: 10.5 }}>
            {fmtTime(audit.window?.start)} to {fmtTime(audit.window?.end)}
          </span>
        </div>
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(96px, 1fr))",
          gap: 10,
          marginBottom: 10,
        }}>
          <Metric label="pending" value={summary.pending_count || 0} />
          <Metric label="linked" value={summary.linked_count || 0} />
          <Metric label="unlinked" value={summary.unlinked_count || 0} />
          <Metric label="tail links" value={summary.automation_tail_linked_count || 0} />
          <Metric label="reconciled" value={summary.reconciled_count || 0} />
          <Metric label="gaps" value={summary.capture_gap_count || 0} />
        </div>
        {risky.length === 0 ? (
          <div style={{ color: "var(--hg-fg-4)", fontSize: 12, lineHeight: 1.4 }}>
            no linked pending captures need intent reconciliation in this window
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {risky.slice(0, 5).map((item) => {
              const session = item.matched_session || {};
              return (
                <div key={item.pending_observation_id} style={{
                  display: "grid",
                  gridTemplateColumns: "minmax(90px, 130px) 1fr",
                  gap: 8,
                  alignItems: "start",
                  borderTop: "1px solid var(--hg-border-soft)",
                  paddingTop: 7,
                }}>
                  <div style={{
                    color: "var(--hg-fg-3)",
                    fontFamily: "'Geist Mono', ui-monospace, monospace",
                    fontSize: 10.5,
                  }}>
                    {fmtTime(item.ts)}
                    <div style={{ marginTop: 4 }}>
                      <Chip tone={item.status === "reconciled" ? "ok" : "warn"}>{item.status}</Chip>
                    </div>
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    <Chip>{(item.zone || "").replace(/_/g, " ")}</Chip>
                    <Chip>captured {fmtPct(item.captured_actual_brightness_pct)}</Chip>
                    <Chip tone={item.status === "reconciled" ? "ok" : "default"}>
                      learns {fmtPct(item.aggregation_value_pct)}
                    </Chip>
                    <Chip>{item.aggregation_value_source}</Chip>
                    {session.automation_tail_detected && <Chip tone="warn">automation tail</Chip>}
                    {session.learning_context_id && <Chip>{session.match_role || "match"} link</Chip>}
                    {(item.flags || []).map((flag) => (
                      <Chip key={flag} tone="warn">{flag.replace(/_/g, " ")}</Chip>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  }

  function DecisionHistory({ decisions = [] }) {
    if (!decisions.length) {
      return <span style={{ color: "var(--hg-fg-5)", fontSize: 10.5 }}>no review decisions</span>;
    }
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
        {decisions.slice(0, 6).map((decision) => (
          <div key={decision.id} style={{
            display: "grid",
            gridTemplateColumns: "auto minmax(80px, 1fr) auto",
            gap: 8,
            alignItems: "baseline",
            borderTop: "1px solid var(--hg-border-soft)",
            paddingTop: 7,
          }}>
            <Chip tone={decision.new_state === "rejected" || decision.new_state === "suppressed" ? "warn" : "default"}>
              {decision.decision}
            </Chip>
            <span style={{ color: "var(--hg-fg-3)", fontSize: 11, lineHeight: 1.35 }}>
              {decision.reason || decision.note || `${decision.prior_state || "-"} -> ${decision.new_state || "-"}`}
            </span>
            <span style={{
              color: "var(--hg-fg-5)",
              fontFamily: "'Geist Mono', ui-monospace, monospace",
              fontSize: 9.5,
            }}>{fmtTime(decision.created_at)}</span>
          </div>
        ))}
      </div>
    );
  }

  function ProposalReview({
    proposals = [],
    blockedCandidates = [],
    proposalDetail,
    onRun,
    onSelectProposal,
    onProposalDecision,
    onSuppressCandidate,
  }) {
    const active = proposals.filter((p) => p.status === "draft");
    const shownProposals = proposals.slice(0, 6);
    return (
      <div style={{
        background: "var(--hg-bg-1)",
        border: "1px solid var(--hg-border-soft)",
        borderRadius: 6,
        padding: "12px 14px",
      }}>
        <div style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 10,
          marginBottom: 10,
        }}>
          <span style={{
            fontFamily: "'Geist Mono', ui-monospace, monospace",
            fontSize: 9.5,
            letterSpacing: "0.18em",
            textTransform: "uppercase",
            color: "var(--hg-fg-4)",
          }}>proposal engine</span>
          <button className="hg-focusable" onClick={onRun} style={buttonStyle}>run proposals</button>
        </div>

        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(96px, 1fr))",
          gap: 10,
          marginBottom: 10,
        }}>
          <Metric label="drafts" value={active.length} />
          <Metric label="stored" value={proposals.length} />
          <Metric label="blocked" value={blockedCandidates.length} />
          <Metric label="write path" value="none" />
        </div>

        {shownProposals.length === 0 ? (
          <div style={{ color: "var(--hg-fg-4)", fontSize: 12, lineHeight: 1.4, marginBottom: 10 }}>
            no candidates currently pass the strict proposal gate
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 10 }}>
            {shownProposals.map((proposal) => {
              const body = proposal.body || {};
              const rule = body.proposed_rule || {};
              const target = rule.target || {};
              return (
                <button key={proposal.id} className="hg-focusable" onClick={() => onSelectProposal(proposal)} style={{
                  textAlign: "left",
                  background: proposalDetail?.proposal?.id === proposal.id ? "var(--hg-bg-2)" : "transparent",
                  border: 0,
                  borderTop: "1px solid var(--hg-border-soft)",
                  padding: "9px 0 0",
                  cursor: "pointer",
                  display: "flex",
                  flexDirection: "column",
                  gap: 6,
                }}>
                  <div style={{ color: "var(--hg-fg-2)", fontSize: 12, lineHeight: 1.35 }}>
                    {proposal.title}
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    <Chip tone={proposal.status === "draft" || proposal.status === "approved_for_experiment" ? "ok" : "warn"}>
                      {proposal.status}
                    </Chip>
                    <Chip>{body.affected?.zone || proposal.body?.affected_zone || "zone"}</Chip>
                    <Chip>target {target.light_state === "off" ? "off" : fmtPct(target.brightness_pct)}</Chip>
                    <Chip>{(proposal.evidence_ids || []).length} evidence</Chip>
                    <Chip>{fmtTime(proposal.updated_at || proposal.created_at)}</Chip>
                  </div>
                </button>
              );
            })}
          </div>
        )}

        {proposalDetail?.proposal && (
          <div style={{
            borderTop: "1px solid var(--hg-border-soft)",
            paddingTop: 10,
            marginBottom: 10,
          }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 8 }}>
              <Chip tone="ok">{proposalDetail.proposal.status}</Chip>
              <span style={{ color: "var(--hg-fg-2)", fontSize: 12, lineHeight: 1.35 }}>
                {proposalDetail.proposal.title}
              </span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "minmax(180px, 1fr) minmax(220px, 1.2fr)", gap: 12 }}>
              <div style={{ minWidth: 0 }}>
                <ContextChips context={proposalDetail.proposal.body?.context || {}} />
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
                  <Chip>{(proposalDetail.evidence || []).length} evidence</Chip>
                  <Chip>{(proposalDetail.proposal.readiness?.gates || []).filter((g) => g.passed).length} gates passed</Chip>
                  <Chip>{proposalDetail.proposal.body?.safety?.activation_requires_human ? "human review" : "unsafe"}</Chip>
                </div>
              </div>
              <div style={{ color: "var(--hg-fg-4)", fontSize: 11, lineHeight: 1.4 }}>
                <div>benefit: {proposalDetail.proposal.body?.expected_benefit}</div>
                <div style={{ marginTop: 5 }}>metric: {proposalDetail.proposal.body?.success_metric}</div>
                <div style={{ marginTop: 5 }}>rollback: {proposalDetail.proposal.body?.rollback?.condition}</div>
              </div>
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 10 }}>
              {[
                ["approve", "approve design"],
                ["reject", "reject"],
                ["edit_requested", "needs edit"],
                ["snooze", "snooze"],
              ].map(([action, label]) => (
                <button
                  key={action}
                  className="hg-focusable"
                  onClick={() => onProposalDecision(proposalDetail.proposal, action)}
                  style={buttonStyle}
                >{label}</button>
              ))}
              <button
                className="hg-focusable"
                onClick={() => onProposalDecision(proposalDetail.proposal, "do_not_learn")}
                style={buttonStyle}
              >do not learn</button>
            </div>
            <div style={{ marginTop: 10 }}>
              <DecisionHistory decisions={proposalDetail.decisions || []} />
            </div>
          </div>
        )}

        {blockedCandidates.length > 0 && (
          <div>
            <div style={{
              color: "var(--hg-fg-5)",
              fontFamily: "'Geist Mono', ui-monospace, monospace",
              fontSize: 9.5,
              letterSpacing: "0.12em",
              textTransform: "uppercase",
              marginBottom: 5,
            }}>blocked candidates</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
              {blockedCandidates.slice(0, 6).map((candidate) => (
                <div key={candidate.id} style={{
                  display: "grid",
                  gridTemplateColumns: "minmax(90px, 130px) 1fr auto",
                  gap: 8,
                  alignItems: "baseline",
                  borderTop: "1px solid var(--hg-border-soft)",
                  paddingTop: 7,
                }}>
                  <span style={{
                    color: "var(--hg-fg-3)",
                    fontFamily: "'Geist Mono', ui-monospace, monospace",
                    fontSize: 10.5,
                  }}>{(candidate.zone || "").replace(/_/g, " ")}</span>
                  <span style={{ color: "var(--hg-fg-4)", fontSize: 11, lineHeight: 1.35 }}>
                    {(candidate.blockers || []).slice(0, 2).join("; ") || "readiness gate failed"}
                  </span>
                  <Chip tone="warn">not proposed</Chip>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }

  function ExperimentReview({
    experiments = [],
    experimentDetail,
    sandboxResult,
    sandboxExplanation,
    onRun,
    onRunSandbox,
    onRunSandboxExplain,
    onSelectExperiment,
  }) {
    const inactive = experiments.filter((experiment) => experiment.status === "inactive_design");
    return (
      <div style={{
        background: "var(--hg-bg-1)",
        border: "1px solid var(--hg-border-soft)",
        borderRadius: 6,
        padding: "12px 14px",
      }}>
        <div style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 10,
          marginBottom: 10,
        }}>
          <span style={{
            fontFamily: "'Geist Mono', ui-monospace, monospace",
            fontSize: 9.5,
            letterSpacing: "0.18em",
            textTransform: "uppercase",
            color: "var(--hg-fg-4)",
          }}>experiment designs</span>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <button className="hg-focusable" onClick={onRunSandbox} style={buttonStyle}>sandbox chain</button>
            <button className="hg-focusable" onClick={onRunSandboxExplain} style={buttonStyle}>explain readiness</button>
            <button className="hg-focusable" onClick={onRun} style={buttonStyle}>design experiments</button>
          </div>
        </div>

        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(96px, 1fr))",
          gap: 10,
          marginBottom: 10,
        }}>
          <Metric label="inactive" value={inactive.length} />
          <Metric label="stored" value={experiments.length} />
          <Metric label="activation" value="none" />
          <Metric label="write path" value="none" />
          <Metric label="sandbox" value={sandboxResult?.chain ? "ready" : "-"} />
        </div>

        {sandboxResult?.chain && (
          <div style={{
            borderTop: "1px solid var(--hg-border-soft)",
            paddingTop: 8,
            marginBottom: 10,
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))",
            gap: 10,
          }}>
            <Metric label="candidate" value={(sandboxResult.chain.candidate_status || "-")} />
            <Metric label="review" value={sandboxResult.chain.review_ready ? "ready" : "blocked"} />
            <Metric label="activate" value={sandboxResult.chain.can_activate ? "yes" : "no"} />
            <Metric label="apply" value={sandboxResult.chain.can_apply ? "yes" : "no"} />
            <Metric label="prod" value={sandboxResult.production_unchanged ? "unchanged" : "changed"} />
          </div>
        )}

        {sandboxExplanation?.summary && (
          <div style={{
            borderTop: "1px solid var(--hg-border-soft)",
            paddingTop: 9,
            marginBottom: 10,
            display: "flex",
            flexDirection: "column",
            gap: 8,
          }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
              <Chip tone={sandboxExplanation.ready_now ? "ok" : "warn"}>
                {sandboxExplanation.ready_now ? "ready" : "blocked"}
              </Chip>
              <span style={{ color: "var(--hg-fg-2)", fontSize: 12, lineHeight: 1.35 }}>
                {sandboxExplanation.summary.headline}
              </span>
            </div>
            {(sandboxExplanation.remediation_plan || []).length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                {(sandboxExplanation.remediation_plan || []).slice(0, 6).map((step) => (
                  <div key={step.code} style={{
                    display: "grid",
                    gridTemplateColumns: "minmax(110px, 150px) 1fr",
                    gap: 8,
                    alignItems: "baseline",
                    borderTop: "1px solid var(--hg-border-soft)",
                    paddingTop: 6,
                  }}>
                    <Chip tone={step.blocking ? "warn" : "ok"}>{String(step.phase || "").replace(/_/g, " ")}</Chip>
                    <span style={{ color: "var(--hg-fg-4)", fontSize: 11, lineHeight: 1.35 }}>
                      <span style={{ color: "var(--hg-fg-2)" }}>{step.title}</span>
                      {" · "}{step.next_action}
                    </span>
                  </div>
                ))}
              </div>
            )}
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              <Chip>{sandboxExplanation.next_safe_milestone?.title || "read-only review"}</Chip>
              <Chip tone={sandboxExplanation.production_unchanged ? "ok" : "crit"}>
                prod {sandboxExplanation.production_unchanged ? "unchanged" : "changed"}
              </Chip>
              {(sandboxExplanation.non_actions || []).slice(0, 4).map((item) => (
                <Chip key={item.code} tone="ok">{String(item.code || "").replace(/_/g, " ")}</Chip>
              ))}
            </div>
          </div>
        )}

        {experiments.length === 0 ? (
          <div style={{ color: "var(--hg-fg-4)", fontSize: 12, lineHeight: 1.4, marginBottom: 10 }}>
            no approved proposals have produced inactive experiment designs
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 10 }}>
            {experiments.slice(0, 6).map((experiment) => {
              const body = experiment.body || {};
              const target = body.proposed_rule?.target || {};
              return (
                <button key={experiment.id} className="hg-focusable" onClick={() => onSelectExperiment(experiment)} style={{
                  textAlign: "left",
                  background: experimentDetail?.experiment?.id === experiment.id ? "var(--hg-bg-2)" : "transparent",
                  border: 0,
                  borderTop: "1px solid var(--hg-border-soft)",
                  padding: "9px 0 0",
                  cursor: "pointer",
                  display: "flex",
                  flexDirection: "column",
                  gap: 6,
                }}>
                  <div style={{ color: "var(--hg-fg-2)", fontSize: 12, lineHeight: 1.35 }}>
                    {body.title || experiment.title || experiment.id}
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    <Chip tone="warn">{experiment.status}</Chip>
                    <Chip>{body.affected?.zone || "zone"}</Chip>
                    <Chip>target {target.light_state === "off" ? "off" : fmtPct(target.brightness_pct)}</Chip>
                    <Chip>{(experiment.evidence_ids || []).length} evidence</Chip>
                    <Chip>{fmtTime(experiment.updated_at || experiment.created_at)}</Chip>
                  </div>
                </button>
              );
            })}
          </div>
        )}

        {experimentDetail?.experiment && (
          <div style={{
            borderTop: "1px solid var(--hg-border-soft)",
            paddingTop: 10,
          }}>
            {(() => {
              const evaluation = experimentDetail.evaluation || {};
              const readiness = evaluation.readiness || {};
              const baseline = evaluation.baseline || {};
              const activation = evaluation.activation || {};
              const preflight = experimentDetail.preflight || {};
              const manifest = experimentDetail.manifest || {};
              const missingArtifacts = (preflight.required_artifacts || [])
                .filter((artifact) => String(artifact.status || "").startsWith("missing")).length;
              return (
                <div style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(96px, 1fr))",
                  gap: 10,
                  marginBottom: 10,
                }}>
                  <Metric label="review" value={readiness.ready_for_activation_review ? "ready" : "blocked"} />
                  <Metric label="eligible" value={baseline.eligible_observation_count ?? "-"} />
                  <Metric label="touches" value={baseline.linked_override_count ?? "-"} />
                  <Metric label="days" value={(baseline.distinct_days || []).length} />
                  <Metric label="startable" value={activation.can_start_experiment ? "yes" : "no"} />
                  <Metric label="missing" value={missingArtifacts} />
                  <Metric label="writes" value={(manifest.write_set || []).length || "-"} />
                </div>
              );
            })()}
            <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 8 }}>
              <Chip tone="warn">{experimentDetail.experiment.status}</Chip>
              <span style={{ color: "var(--hg-fg-2)", fontSize: 12, lineHeight: 1.35 }}>
                {experimentDetail.experiment.body?.title || experimentDetail.experiment.id}
              </span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "minmax(180px, 1fr) minmax(220px, 1.2fr)", gap: 12 }}>
              <div style={{ minWidth: 0 }}>
                <ContextChips context={experimentDetail.experiment.body?.context || {}} />
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
                  <Chip>{(experimentDetail.evidence || []).length} evidence</Chip>
                  <Chip>{experimentDetail.experiment.body?.trial_design?.duration_days || "-"} days</Chip>
                  <Chip>{experimentDetail.experiment.body?.trial_design?.minimum_matching_context_observations || "-"} observations</Chip>
                  <Chip>{experimentDetail.experiment.body?.trial_design?.activation_endpoint_present ? "activation endpoint" : "no activation endpoint"}</Chip>
                </div>
              </div>
              <div style={{ color: "var(--hg-fg-4)", fontSize: 11, lineHeight: 1.4 }}>
                <div>hypothesis: {experimentDetail.experiment.body?.hypothesis}</div>
                <div style={{ marginTop: 5 }}>metric: {experimentDetail.experiment.body?.success_metrics?.primary}</div>
                <div style={{ marginTop: 5 }}>rollback: {experimentDetail.experiment.body?.rollback?.condition}</div>
              </div>
            </div>
            {(experimentDetail.evaluation?.readiness?.hard_blockers || []).length > 0 && (
              <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 4 }}>
                {(experimentDetail.evaluation.readiness.hard_blockers || []).slice(0, 5).map((blocker) => (
                  <div key={blocker.code} style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: 6, alignItems: "baseline" }}>
                    <Chip tone="warn">{blocker.code}</Chip>
                    <span style={{ color: "var(--hg-fg-4)", fontSize: 11, lineHeight: 1.35 }}>
                      {blocker.message}
                    </span>
                  </div>
                ))}
              </div>
            )}
            {experimentDetail.evaluation?.activation?.reason && (
              <div style={{ marginTop: 10, color: "var(--hg-fg-4)", fontSize: 11, lineHeight: 1.4 }}>
                activation: {experimentDetail.evaluation.activation.reason}
              </div>
            )}
            {experimentDetail.preflight?.reason && (
              <div style={{ marginTop: 6, color: "var(--hg-fg-4)", fontSize: 11, lineHeight: 1.4 }}>
                preflight: {experimentDetail.preflight.reason}
              </div>
            )}
            {experimentDetail.manifest?.reason && (
              <div style={{ marginTop: 6, color: "var(--hg-fg-4)", fontSize: 11, lineHeight: 1.4 }}>
                manifest: {experimentDetail.manifest.reason}
              </div>
            )}
            {(experimentDetail.preflight?.required_artifacts || []).length > 0 && (
              <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 6 }}>
                {(experimentDetail.preflight.required_artifacts || []).slice(0, 8).map((artifact) => (
                  <Chip
                    key={artifact.artifact}
                    tone={String(artifact.status || "").startsWith("missing") ? "warn" : "ok"}
                  >
                    {artifact.artifact.replace(/_/g, " ")}={artifact.status}
                  </Chip>
                ))}
              </div>
            )}
            {(experimentDetail.manifest?.write_set || []).length > 0 && (
              <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 5 }}>
                {(experimentDetail.manifest.write_set || []).slice(0, 5).map((write) => (
                  <div key={write.id} style={{
                    display: "grid",
                    gridTemplateColumns: "minmax(120px, 180px) 1fr auto",
                    gap: 8,
                    alignItems: "baseline",
                    borderTop: "1px solid var(--hg-border-soft)",
                    paddingTop: 6,
                  }}>
                    <Chip tone="warn">{write.id.replace(/^write:/, "")}</Chip>
                    <span style={{
                      color: "var(--hg-fg-4)",
                      fontFamily: "'Geist Mono', ui-monospace, monospace",
                      fontSize: 10,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}>{write.target?.entity_id || write.kind}</span>
                    <Chip>{write.domain}.{write.service}</Chip>
                  </div>
                ))}
              </div>
            )}
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 10 }}>
              {Object.entries(experimentDetail.experiment.body?.safety || {}).map(([k, v]) => (
                <Chip key={k} tone={v ? "crit" : "ok"}>{k.replace(/_/g, " ")}={String(v)}</Chip>
              ))}
            </div>
            {(experimentDetail.experiment.body?.success_metrics?.guardrails || []).length > 0 && (
              <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 4 }}>
                {(experimentDetail.experiment.body.success_metrics.guardrails || []).slice(0, 4).map((guardrail) => (
                  <span key={guardrail} style={{ color: "var(--hg-fg-4)", fontSize: 11, lineHeight: 1.35 }}>
                    {guardrail}
                  </span>
                ))}
              </div>
            )}
            <div style={{ marginTop: 10 }}>
              <DecisionHistory decisions={experimentDetail.decisions || []} />
            </div>
          </div>
        )}
      </div>
    );
  }

  function ClaimDetail({ detail, onClose, onDecision }) {
    if (!detail?.claim) return null;
    const claim = detail.claim;
    const evidence = detail.evidence || [];
    const candidates = detail.candidates || [];
    const episodes = detail.episodes || [];
    const decisions = detail.decisions || [];
    return (
      <div style={{
        borderTop: "1px solid var(--hg-border-soft)",
        paddingTop: 10,
        display: "flex",
        flexDirection: "column",
        gap: 9,
      }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <Chip tone={claim.review_state === "open_question" ? "warn" : "default"}>{claim.review_state}</Chip>
          <Chip>{fmtRatio(claim.confidence || 0)} conf</Chip>
          <span style={{
            color: "var(--hg-fg-5)",
            fontFamily: "'Geist Mono', ui-monospace, monospace",
            fontSize: 9.5,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}>{claim.id}</span>
          <span style={{ marginLeft: "auto" }} />
          <button className="hg-focusable" onClick={onClose} style={buttonStyle}>close</button>
        </div>
        <div style={{ color: "var(--hg-fg-2)", fontSize: 12, lineHeight: 1.4 }}>
          {claim.claim}
        </div>
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(96px, 1fr))",
          gap: 10,
        }}>
          <Metric label="evidence" value={evidence.length} />
          <Metric label="candidates" value={candidates.length} />
          <Metric label="episodes" value={episodes.length} />
          <Metric label="updated" value={fmtTime(claim.updated_at)} />
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {[
            ["confirm", "confirm"],
            ["reject", "reject"],
            ["correction", "wrong"],
            ["snooze", "snooze"],
          ].map(([decision, label]) => (
            <button
              key={decision}
              className="hg-focusable"
              onClick={() => onDecision(claim, decision)}
              style={buttonStyle}
            >{label}</button>
          ))}
        </div>
        <DecisionHistory decisions={decisions} />
        <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          {evidence.slice(0, 8).map((item) => (
            <div key={item.id} style={{
              display: "flex",
              flexWrap: "wrap",
              gap: 6,
              alignItems: "center",
              color: "var(--hg-fg-3)",
              fontSize: 10.5,
            }}>
              <Chip>{item.kind}</Chip>
              <Chip>{(item.zone || "").replace(/_/g, " ")}</Chip>
              <Chip>{fmtPct(item.actual_brightness_pct)} actual</Chip>
              <Chip>{fmtPct(item.predicted_brightness_pct)} pred</Chip>
              <span style={{
                color: "var(--hg-fg-5)",
                fontFamily: "'Geist Mono', ui-monospace, monospace",
              }}>{item.id}</span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  function MemoryReview({
    journals = [],
    claims = [],
    selectedJournal,
    claimDetail,
    claimFilter,
    onClaimFilter,
    zoneFilter,
    onZoneFilter,
    onSelectJournal,
    onSelectClaim,
    onCloseClaim,
    onClaimDecision,
  }) {
    const latest = selectedJournal || journals[0] || null;
    const allZones = Array.from(new Set(claims.map((claim) => {
      const path = claim.page_path || "";
      const room = path.match(/\/rooms\/([^/]+)\.md$/);
      if (room) return room[1];
      const issue = path.match(/\/issues\/(.+?)-lighting-evidence\.md$/);
      return issue ? issue[1] : "";
    }).filter(Boolean))).sort();
    const filteredClaims = claims.filter((claim) => {
      if (claimFilter !== "all" && claim.review_state !== claimFilter) return false;
      if (zoneFilter) {
        const path = claim.page_path || "";
        if (!path.includes(`/${zoneFilter}.md`) && !path.includes(`/${zoneFilter}-`)) return false;
      }
      return true;
    });
    const openQuestions = claims.filter((claim) => claim.review_state === "open_question");
    return (
      <div style={{
        background: "var(--hg-bg-1)",
        border: "1px solid var(--hg-border-soft)",
        borderRadius: 6,
        padding: "12px 14px",
      }}>
        <div style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 10,
          marginBottom: 10,
        }}>
          <span style={{
            fontFamily: "'Geist Mono', ui-monospace, monospace",
            fontSize: 9.5,
            letterSpacing: "0.18em",
            textTransform: "uppercase",
            color: "var(--hg-fg-4)",
          }}>daily memory</span>
          <span style={{ color: "var(--hg-fg-5)", fontSize: 10.5 }}>
            {latest ? latest.date : "no journal"}
          </span>
        </div>

        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
          gap: 14,
          alignItems: "start",
        }}>
          <div style={{ minWidth: 0 }}>
            <div style={{
              color: "var(--hg-fg-5)",
              fontFamily: "'Geist Mono', ui-monospace, monospace",
              fontSize: 9.5,
              letterSpacing: "0.12em",
              textTransform: "uppercase",
              marginBottom: 5,
            }}>journal</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 7 }}>
              {journals.slice(0, 5).map((journal) => (
                <button
                  key={journal.date}
                  className="hg-focusable"
                  onClick={() => onSelectJournal(journal)}
                  style={{
                    ...buttonStyle,
                    color: latest?.date === journal.date ? "var(--hg-ice-bright)" : buttonStyle.color,
                  }}
                >{journal.date}</button>
              ))}
            </div>
            {latest ? (
              <pre style={{ ...preStyle, maxHeight: 260 }}>{latest.body_md}</pre>
            ) : (
              <div style={{ color: "var(--hg-fg-4)", fontSize: 12, padding: "10px 0" }}>
                no daily journal generated
              </div>
            )}
          </div>

          <div style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 12 }}>
            <div>
              <div style={{
                color: "var(--hg-fg-5)",
                fontFamily: "'Geist Mono', ui-monospace, monospace",
                fontSize: 9.5,
                letterSpacing: "0.12em",
                textTransform: "uppercase",
                marginBottom: 5,
              }}>wiki claims</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 7 }}>
                {["all", "open_question", "unreviewed"].map((state) => (
                  <button
                    key={state}
                    className="hg-focusable"
                    onClick={() => onClaimFilter(state)}
                    style={{
                      ...buttonStyle,
                      color: claimFilter === state ? "var(--hg-ice-bright)" : buttonStyle.color,
                    }}
                  >{state}</button>
                ))}
                <select
                  className="hg-focusable"
                  value={zoneFilter}
                  onChange={(e) => onZoneFilter(e.target.value)}
                  style={{
                    ...buttonStyle,
                    padding: "5px 7px",
                    background: "var(--hg-bg-1)",
                  }}
                >
                  <option value="">all zones</option>
                  {allZones.map((zone) => (
                    <option key={zone} value={zone}>{zone.replace(/_/g, " ")}</option>
                  ))}
                </select>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {filteredClaims.length === 0 ? (
                  <span style={{ color: "var(--hg-fg-5)", fontSize: 10.5 }}>no claims</span>
                ) : filteredClaims.slice(0, 8).map((claim) => (
                  <button key={claim.id} className="hg-focusable" onClick={() => onSelectClaim(claim)} style={{
                    textAlign: "left",
                    background: claimDetail?.claim?.id === claim.id ? "var(--hg-bg-2)" : "transparent",
                    border: 0,
                    borderTop: "1px solid var(--hg-border-soft)",
                    paddingTop: 8,
                    cursor: "pointer",
                    display: "flex",
                    flexDirection: "column",
                    gap: 5,
                  }}>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                      <Chip tone={claim.review_state === "open_question" ? "warn" : "default"}>
                        {claim.review_state}
                      </Chip>
                      <Chip>{claim.claim_type}</Chip>
                      <Chip>{fmtRatio(claim.confidence || 0)} conf</Chip>
                      <Chip>{(claim.evidence_ids || []).length} evidence</Chip>
                    </div>
                    <div style={{ color: "var(--hg-fg-2)", fontSize: 11.5, lineHeight: 1.35 }}>
                      {claim.claim}
                    </div>
                    <div style={{
                      color: "var(--hg-fg-5)",
                      fontFamily: "'Geist Mono', ui-monospace, monospace",
                      fontSize: 9.5,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}>{claim.page_path}</div>
                  </button>
                ))}
              </div>
              <ClaimDetail detail={claimDetail} onClose={onCloseClaim} onDecision={onClaimDecision} />
            </div>

            {openQuestions.length > 0 && (
              <div>
                <div style={{
                  color: "var(--hg-fg-5)",
                  fontFamily: "'Geist Mono', ui-monospace, monospace",
                  fontSize: 9.5,
                  letterSpacing: "0.12em",
                  textTransform: "uppercase",
                  marginBottom: 5,
                }}>open questions</div>
                <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                  {openQuestions.slice(0, 4).map((claim) => (
                    <span key={claim.id} style={{ color: "var(--hg-warn)", fontSize: 11, lineHeight: 1.35 }}>
                      {claim.claim}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  function CandidateDetail({ detail, onClose }) {
    if (!detail || !detail.candidate) return null;
    const candidate = detail.candidate;
    return (
      <div style={{
        background: "var(--hg-bg-1)",
        border: "1px solid var(--hg-border-soft)",
        borderRadius: 6,
        padding: "12px 14px",
      }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 10 }}>
          <span style={{
            fontFamily: "'Geist Mono', ui-monospace, monospace",
            fontSize: 9.5,
            letterSpacing: "0.18em",
            textTransform: "uppercase",
            color: "var(--hg-fg-4)",
          }}>evidence drilldown</span>
          <span style={{ color: "var(--hg-fg-2)", fontSize: 12 }}>
            {(candidate.zone || "").replace(/_/g, " ")}
          </span>
          <span style={{ marginLeft: "auto" }} />
          <button className="hg-focusable" onClick={onClose} style={buttonStyle}>close</button>
        </div>

        <div style={{
          display: "grid",
          gridTemplateColumns: "minmax(180px, 1fr) minmax(220px, 1.2fr)",
          gap: 12,
          marginBottom: 10,
        }}>
          <div>
            <ContextChips context={candidate.context} />
            <div style={{ marginTop: 8, display: "flex", gap: 6, flexWrap: "wrap" }}>
              <Chip tone={candidate.status === "candidate" ? "ok" : "warn"}>{candidate.status}</Chip>
              <Chip>{candidate.evidence_count || 0} evidence</Chip>
              {candidate.evidence_tier && <Chip>{candidate.evidence_tier.replace(/_/g, " ")}</Chip>}
              {(candidate.raw_evidence_count || 0) > (candidate.evidence_count || 0) && (
                <Chip tone="warn">{candidate.raw_evidence_count} raw</Chip>
              )}
              {(candidate.pending_evidence_count || 0) > 0 && (
                <Chip tone="ok">{candidate.pending_evidence_count} pending</Chip>
              )}
              {(candidate.session_intent_count || 0) > 0 && (
                <Chip>{candidate.session_intent_count} intent</Chip>
              )}
              <Chip>{candidate.supporting_evidence_count || 0} support</Chip>
              <Chip>{candidate.linked_override_count || 0} linked touch</Chip>
              {(candidate.burst_event_count || 0) > 0 && (
                <Chip tone="warn">{candidate.burst_event_count} burst collapsed</Chip>
              )}
              <Chip>{candidate.capture_suppressed_total || 0} saved</Chip>
              <Chip>{candidate.dedupe_key_count || 0} dedupe keys</Chip>
              <Chip>{candidate.days_seen || 0} days</Chip>
              {candidate.duplicate_pending_ratio != null && (
                <Chip tone={candidate.duplicate_pending_ratio > 0.35 ? "warn" : "default"}>
                  dup {Math.round(candidate.duplicate_pending_ratio * 100)}%
                </Chip>
              )}
            </div>
          </div>
          <WarningList warnings={candidate.quality_warnings || []} limit={8} />
        </div>

        <ReadinessPanel readiness={detail.readiness} />
        <ProposalPreview preview={detail.proposal_preview} />
        <div style={{ marginBottom: 10 }}>
          <DecisionHistory decisions={detail.decisions || []} />
        </div>

        {(detail.override_sessions || []).length > 0 && (
          <div style={{ marginBottom: 10 }}>
            <div style={{
              fontFamily: "'Geist Mono', ui-monospace, monospace",
              fontSize: 9.5,
              letterSpacing: "0.16em",
              textTransform: "uppercase",
              color: "var(--hg-fg-4)",
              marginBottom: 2,
            }}>override sessions</div>
            {(detail.override_sessions || []).slice(0, 6).map((session) => (
              <OverrideSessionRow key={session.id} session={session} />
            ))}
          </div>
        )}

        {(detail.episodes || []).length > 0 && (
          <div style={{ marginBottom: 10 }}>
            <div style={{
              fontFamily: "'Geist Mono', ui-monospace, monospace",
              fontSize: 9.5,
              letterSpacing: "0.16em",
              textTransform: "uppercase",
              color: "var(--hg-fg-4)",
              marginBottom: 2,
            }}>episodes</div>
            {(detail.episodes || []).slice(0, 5).map((episode) => (
              <EpisodeRow key={episode.id} episode={episode} />
            ))}
          </div>
        )}

        {(detail.evidence || []).map((e) => (
          <EvidenceRow key={e.id} evidence={e} />
        ))}
      </div>
    );
  }

  function HomeIntelligenceOverlay({ open, onClose, metricsBase, endpoint, spatialMode = false }) {
    const base = useMemo(() => intelligenceBaseFrom(metricsBase, endpoint), [metricsBase, endpoint]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [today, setToday] = useState(null);
    const [jobs, setJobs] = useState([]);
    const [scheduler, setScheduler] = useState(null);
    const [evidenceSources, setEvidenceSources] = useState(null);
    const [soak, setSoak] = useState(null);
    const [candidates, setCandidates] = useState([]);
    const [multimodalStatus, setMultimodalStatus] = useState(null);
    const [labelEvalStatus, setLabelEvalStatus] = useState(null);
    const [labelEvalQueue, setLabelEvalQueue] = useState(null);
    const [promptGate, setPromptGate] = useState(null);
    const [trainingManifests, setTrainingManifests] = useState(null);
    const [lightingActivity, setLightingActivity] = useState(null);
    const [packets, setPackets] = useState([]);
    const [map, setMap] = useState(null);
    const [selectedId, setSelectedId] = useState(null);
    const [detail, setDetail] = useState(null);
    const [expandedOpen, setExpandedOpen] = useState(false);
    const [expandedTab, setExpandedTab] = useState("overview");
    const [globalPanel, setGlobalPanel] = useState(null);
    const [trayMode, setTrayMode] = useState("timeline");
    const [labelEditorOpen, setLabelEditorOpen] = useState(false);
    const [undoAudit, setUndoAudit] = useState(null);
    const [lightboxIndex, setLightboxIndex] = useState(null);
    const [autoSelected, setAutoSelected] = useState(false);
    const [infoOpen, setInfoOpen] = useState(false);

    const points = map?.points || [];
    const selectedPacket = detail?.packet || packets.find((packet) => packet.id === selectedId) || null;
    const selectedPoint = points.find((point) => point.id === selectedId) || null;
    const selectedLabels = selectedPacket?.effective_labels || detail?.effective_labels || detail?.latest_label?.labels || selectedPacket?.latest_labels || {};
    const selectedQwenLabels = selectedPacket?.latest_labels || detail?.latest_label?.labels || {};
    const selectedActivity = labelAxis(selectedLabels, "primary_activity");
    const selectedQwenActivity = labelAxis(selectedQwenLabels, "primary_activity");
    const selectedQuality = labelAxis(selectedLabels, "image_quality");
    const selectedPrivacy = labelAxis(selectedLabels, "privacy_sensitivity");
    const selectedLighting = labelAxis(selectedLabels, "room_lighting_visual");
    const selectedScreen = labelAxis(selectedLabels, "screen_visual_state");
    const selectedPresence = labelAxis(selectedLabels, "presence");
    const selectedMotion = labelAxis(selectedLabels, "motion_phase");
    const selectedPosture = labelAxis(selectedLabels, "posture");
    const selectedSurface = labelAxis(selectedLabels, "task_surface");
    const selectedFocus = labelAxis(selectedLabels, "visual_focus");
    const selectedConsistency = labelAxis(selectedLabels, "metadata_consistency");
    const selectedOverride = selectedPacket?.human_label_overrides?.primary_activity || detail?.human_label_overrides?.primary_activity || null;
    const selectedReview = selectedPacket ? packetNeedsReview(selectedPacket) : { primary_activity: false, reasons: [] };
    const selectedNeedsReview = Boolean(selectedReview.primary_activity);
    const promptGateBody = promptGate?.gate || labelEvalStatus?.prompt_gate || {};
    const statusCounts = multimodalStatus?.counts || {};
    const lightingActivitySummary = lightingActivity?.summary || {};
    const evalQueueItems = labelEvalQueue?.items || [];
    const manifestRows = trainingManifests?.manifests || [];
    const privacyPackets = packets.filter((packet) => {
      const label = labelAxis(packet.latest_labels || {}, "privacy_sensitivity").label;
      return label && label !== "normal" && label !== "unknown";
    });
    const privacyReviewCount = privacyPackets.length;
    const multimodalConfig = multimodalStatus?.config || {};
    const ringBuffer = multimodalStatus?.ring_buffer || {};
    const latestPilotJob = jobs.find((job) => job.name === "multimodal_capture_pilot");
    const latestPilot = latestPilotJob?.details?.pilot || {};
    const latestPilotSkipped = latestPilot.not_selected || [];
    const pilotZones = multimodalConfig.pilot_zones || latestPilot.config?.pilot_zones || [];
    const pilotCameras = multimodalConfig.cameras || latestPilot.config?.cameras || [];
    const pilotLookback = multimodalConfig.pilot_lookback_s ?? latestPilot.config?.pilot_lookback_s;
    const reviewPackets = packets.filter((packet) => packetNeedsReview(packet).primary_activity);
    const visiblePackets = trayMode === "needs" ? reviewPackets : packets;

    const refresh = useCallback(async () => {
      if (!open || !base) return;
      setLoading(true);
      setError("");
      try {
        const [
          t,
          jobRows,
          schedule,
          evidenceRows,
          soakRows,
          candidateRows,
          multimodalRows,
          labelEvalRows,
          labelEvalQueueRows,
          promptGateRows,
          trainingManifestRows,
          lightingActivityRows,
          observationRows,
          observationMapRows,
        ] = await Promise.all([
          fetchJson(`${base}/api/intelligence/today`).catch(() => null),
          fetchJson(`${base}/api/intelligence/jobs?limit=8`).catch(() => ({ jobs: [] })),
          fetchJson(`${base}/api/intelligence/scheduler`).catch(() => ({ scheduler: null })),
          fetchJson(`${base}/api/intelligence/evidence-sources`).catch(() => ({ evidence_sources: null })),
          fetchJson(`${base}/api/intelligence/soak-review?hours=24`).catch(() => null),
          fetchJson(`${base}/api/intelligence/candidates?limit=18`).catch(() => ({ candidates: [] })),
          fetchJson(`${base}/api/intelligence/multimodal/status`).catch(() => null),
          fetchJson(`${base}/api/intelligence/label-evals/status`).catch(() => null),
          fetchJson(`${base}/api/intelligence/label-evals/queue?limit=10`).catch(() => null),
          fetchJson(`${base}/api/intelligence/qwen-labels/prompt-gate`).catch(() => null),
          fetchJson(`${base}/api/intelligence/training-clips/manifests?limit=10`).catch(() => ({ manifests: [] })),
          fetchJson(`${base}/api/intelligence/lighting-activity?hours=24&limit=80`).catch(() => null),
          fetchJson(`${base}/api/intelligence/observation-packets?limit=80`).catch(() => ({ observation_packets: [] })),
          fetchJson(`${base}/api/intelligence/observation-packets/map?limit=300`).catch(() => ({ points: [] })),
        ]);
        setToday(t);
        setJobs(jobRows.jobs || []);
        setScheduler(schedule.scheduler || null);
        setEvidenceSources(evidenceRows.evidence_sources || t?.evidence_sources || null);
        setSoak(soakRows);
        setCandidates(candidateRows.candidates || []);
        setMultimodalStatus(multimodalRows);
        setLabelEvalStatus(labelEvalRows);
        setLabelEvalQueue(labelEvalQueueRows);
        setPromptGate(promptGateRows);
        setTrainingManifests(trainingManifestRows);
        setLightingActivity(lightingActivityRows);
        setPackets(observationRows.observation_packets || []);
        setMap(observationMapRows);
      } catch (e) {
        setError(e?.message || String(e));
      } finally {
        setLoading(false);
      }
    }, [base, open]);

    const loadPacket = useCallback(async (packetOrPoint) => {
      if (!base || !packetOrPoint?.id) return;
      const id = packetOrPoint.id;
      setSelectedId(id);
      setLightboxIndex(null);
      setError("");
      try {
        const body = await fetchJson(`${base}/api/intelligence/observation-packets/${encodeURIComponent(id)}`);
        setDetail(body);
      } catch (e) {
        setError(e?.message || String(e));
      }
    }, [base]);

    useEffect(() => {
      if (!open) return undefined;
      refresh();
      const id = setInterval(refresh, 30000);
      return () => clearInterval(id);
    }, [open, refresh]);

    useEffect(() => {
      if (!open) {
        setAutoSelected(false);
        setInfoOpen(false);
        return;
      }
      if (autoSelected || selectedId || packets.length === 0) return;
      setAutoSelected(true);
      loadPacket(packets[0]);
    }, [open, selectedId, packets, loadPacket, autoSelected]);

    // Overlay layer: topmost-only Escape + focus trap via HomeOverlay. The
    // hook keeps onEscape in a fresh ref, so the layered close chain reads
    // current state without a 7-dep resubscribe.
    const atlasRootRef = React.useRef(null);
    window.HomeOverlay.useOverlayLayer({
      key: "intel-root",
      active: !!open,
      onEscape: () => {
        if (lightboxIndex != null) setLightboxIndex(null);
        else if (infoOpen) setInfoOpen(false);
        else if (globalPanel) setGlobalPanel(null);
        else if (expandedOpen) setExpandedOpen(false);
        else if (selectedId) {
          setSelectedId(null);
          setDetail(null);
          setLabelEditorOpen(false);
          setAutoSelected(true);
        }
        else onClose?.();
      },
      rootRef: atlasRootRef,
      trap: true,
      initialFocus: "root",
    });

    const auditPacket = useCallback(async (action, body = {}, note = null, options = {}) => {
      if (!base || !selectedPacket?.id) return;
      setLoading(true);
      setError("");
      try {
        const response = await fetchJson(`${base}/api/intelligence/observation-packets/${encodeURIComponent(selectedPacket.id)}/audits`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action, actor: "home_app_atlas", note, body }),
        });
        if (options.undoable && response?.audit?.id) {
          setUndoAudit({
            packetId: selectedPacket.id,
            auditId: response.audit.id,
            label: options.undoLabel || action,
          });
        }
        await loadPacket(selectedPacket);
        await refresh();
      } catch (e) {
        setError(e?.message || String(e));
      } finally {
        setLoading(false);
      }
    }, [base, selectedPacket, loadPacket, refresh]);

    const correctPrimaryActivity = useCallback((label) => auditPacket(
      "correct_primary_activity",
      {
        axis: "primary_activity",
        label,
        confidence: 1.0,
        source: "human",
        previous_qwen_label: selectedQwenActivity.label,
        previous_qwen_confidence: selectedQwenActivity.confidence || 0,
      },
      null,
      { undoable: true, undoLabel: label.replace(/_/g, " ") },
    ), [auditPacket, selectedQwenActivity]);

    const confirmQwenActivity = useCallback(() => auditPacket(
      "confirm_qwen_activity",
      {
        axis: "primary_activity",
        label: selectedQwenActivity.label,
        confidence: selectedQwenActivity.confidence || 0,
        source: "human_confirmation",
      },
      null,
      { undoable: true, undoLabel: "qwen confirmed" },
    ), [auditPacket, selectedQwenActivity]);

    const confirmUnknownActivity = useCallback(() => auditPacket(
      "confirm_unknown_activity",
      { axis: "primary_activity", label: "unknown", source: "human_confirmation" },
      null,
      { undoable: true, undoLabel: "unknown confirmed" },
    ), [auditPacket]);

    const notLabelableActivity = useCallback((reason) => auditPacket(
      "not_labelable_activity",
      { axis: "primary_activity", reason, source: "human_review" },
      null,
      { undoable: true, undoLabel: `not labelable: ${reason.replace(/_/g, " ")}` },
    ), [auditPacket]);

    const undoLastLabelAudit = useCallback(async () => {
      if (!base || !selectedPacket?.id || !undoAudit?.auditId) return;
      setLoading(true);
      setError("");
      try {
        await fetchJson(`${base}/api/intelligence/observation-packets/${encodeURIComponent(selectedPacket.id)}/audits`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            action: "retract_label_audit",
            actor: "home_app_atlas",
            body: { target_audit_id: undoAudit.auditId },
          }),
        });
        setUndoAudit(null);
        await loadPacket(selectedPacket);
        await refresh();
      } catch (e) {
        setError(e?.message || String(e));
      } finally {
        setLoading(false);
      }
    }, [base, selectedPacket, undoAudit, loadPacket, refresh]);

    const acceptEval = useCallback(async (item) => {
      if (!base || !item?.id) return;
      setLoading(true);
      setError("");
      try {
        await fetchJson(`${base}/api/intelligence/label-evals/items/${encodeURIComponent(item.id)}/accept-qwen`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ actor: "home_app_atlas", note: "accepted from Intelligence Atlas" }),
        });
        await refresh();
      } catch (e) {
        setError(e?.message || String(e));
      } finally {
        setLoading(false);
      }
    }, [base, refresh]);

    const createManifest = useCallback(async () => {
      if (!base || !selectedPacket?.id) return;
      setLoading(true);
      setError("");
      try {
        await fetchJson(`${base}/api/intelligence/training-clips/manifests`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ packet_id: selectedPacket.id, tier: "short_clip", actor: "home_app_atlas" }),
        });
        setGlobalPanel("model");
        await refresh();
      } catch (e) {
        setError(e?.message || String(e));
      } finally {
        setLoading(false);
      }
    }, [base, selectedPacket, refresh]);

    if (!open) return null;

    const statusMode = evidenceSources?.overall_status || evidenceSources?.evidence_sources?.overall_status || "unknown";
    const sourceRows = evidenceSources?.sources || evidenceSources?.evidence_sources?.sources || [];
    const selectedX = Number(selectedPoint?.x);
    const selectedY = Number(selectedPoint?.y);
    const pointX = isFinite(selectedX) ? Math.max(18, Math.min(82, selectedX)) : 62;
    const pointY = isFinite(selectedY) ? Math.max(20, Math.min(76, selectedY)) : 45;
    const popoverTopPct = Math.max(16, Math.min(42, pointY - 6));
    const popoverVertical = {
      top: `${popoverTopPct}%`,
      maxHeight: `calc(${100 - popoverTopPct}% - 24px)`,
    };
    const popoverLeft = pointX > 58
      ? { right: 28, ...popoverVertical }
      : { left: `calc(${pointX}% + 58px)`, ...popoverVertical };
    const connectionStyle = pointX > 58
      ? { left: `${pointX}%`, top: `${pointY}%`, width: 74, transform: "rotate(15deg)" }
      : { left: `${pointX}%`, top: `${pointY}%`, width: 74, transform: "rotate(-15deg)" };
    const frames = detail?.frames || [];
    const summary = detail?.latest_label?.labels?.short_visible_summary || selectedPacket?.short_visible_summary || "Select an observation to inspect frames, labels, and HA context.";
    const ha = detail?.packet?.ha_metadata || selectedPacket?.ha_metadata || {};
    const captureChange = selectedPacket ? captureChangeSummary(selectedPacket, ha) : null;
    const emptyFrames = selectedPacket ? frameEmptyState(selectedPacket, detail?.capture_timing || selectedPacket?.capture_timing || {}) : null;
    const counts = today?.counts || {};
    const readyCandidates = candidates.filter((candidate) => candidate.status === "candidate");
    const blockedCandidates = candidates.filter((candidate) => candidate.status !== "candidate");
    const projectionMode = map?.projection || "deterministic_activity_layout_v1";
    const showReviewEditor = Boolean(selectedPacket && (labelEditorOpen || undoAudit?.packetId === selectedPacket.id));
    const lightboxFrame = lightboxIndex == null ? null : frames[lightboxIndex];
    const compactContextRows = [
      { label: "person", values: [selectedPresence, selectedMotion, selectedPosture] },
      { label: "scene", values: [selectedSurface, selectedFocus, selectedScreen] },
      { label: "capture", values: [selectedLighting, selectedQuality, selectedConsistency] },
    ];
    const sensitiveLightbox = Boolean(
      lightboxFrame
      && (
        (lightboxFrame.privacy_flags || []).some((flag) => flag && flag !== "normal")
        || !["normal", "unknown"].includes(selectedPrivacy.label)
      )
    );
    const expandedTabs = [
      {
        id: "overview",
        label: "Overview",
        short: "why captured",
        description: "The trigger, actual light transition, visual interpretation, confidence, and next action.",
        focus: "Start here when you want to understand what happened and why this dot exists.",
      },
      {
        id: "frames",
        label: "Frames",
        short: "visual proof",
        description: "Camera frames attached to this event, with privacy warnings and large-preview support.",
        focus: "Use this when you want to verify what was actually visible.",
      },
      {
        id: "details",
        label: "Details",
        short: "labels + context",
        description: "Qwen labels, human correction history, Home Assistant context, use/privacy eligibility, and raw evidence.",
        focus: "Use this when you want provenance, audit history, and device truth.",
      },
    ];
    const activeExpandedTab = expandedTabs.find((tab) => tab.id === expandedTab) || expandedTabs[0];
    const globalPanelMeta = {
      observations: {
        title: "Observation Queue",
        deck: "All packets currently loaded into Atlas. Select one to inspect, review, or correct.",
      },
      privacy: {
        title: "Privacy Review",
        deck: "Packets that may contain visible screens, close faces, sensitive context, or uncertain privacy labels. Review these before using them for training or proposals.",
      },
      model: {
        title: "Model Review Gate",
        deck: "Qwen prompt and label-schema changes stay locked until there is enough audited held-out evidence to prove improvement.",
      },
      learning: {
        title: "Learning Readiness",
        deck: "Repeated lighting evidence becomes candidates only after it clears age, variance, conflict, and suppression blockers.",
      },
      system: {
        title: "System Health",
        deck: "Evidence freshness, activity-ledger ingestion, capture coverage, and scheduler jobs for the Intelligence Core.",
      },
    }[globalPanel] || null;

    const overlay = (
      <div
        ref={atlasRootRef}
        className={`intel-atlas${spatialMode ? " spatial" : ""}`}
        data-theme={spatialMode ? "dark" : undefined}
        role="dialog"
        aria-modal="true"
        aria-label="Intelligence Atlas"
        style={spatialMode ? {
          inset: "auto",
          left: 82,
          right: "calc(clamp(352px, 19vw, 388px) + 18px)",
          top: 82,
          bottom: 46,
          border: "1px solid rgba(184,216,255,0.075)",
          borderRadius: 7,
          background: "rgba(5,7,11,0.86)",
          backdropFilter: "blur(16px)",
          boxShadow: "0 18px 56px rgba(0,0,0,0.36)",
        } : undefined}
      >
        <div className="intel-atlas-top">
          <div className="intel-atlas-title">
            <div className="intel-atlas-title-row">
              <h1>intelligence atlas</h1>
              <button
                className="intel-atlas-info hg-focusable"
                type="button"
                aria-expanded={infoOpen ? "true" : "false"}
                aria-controls="intel-atlas-guide"
                aria-label="How the Intelligence Atlas works"
                title="How the Intelligence Atlas works"
                onClick={() => setInfoOpen((open) => !open)}
              >i</button>
            </div>
            <p>observation packets / visual context / learning evidence</p>
          </div>
          <div className="intel-atlas-actions">
            <Chip
              className="intel-atlas-status-chip"
              tone={statusMode === "fresh_idle" || statusMode === "fresh_ingested" ? "ok" : "warn"}
              title={evidenceStatusHelp(statusMode)}
              onClick={() => setGlobalPanel("system")}
            >
              {statusMode === "fresh_idle" || statusMode === "fresh_ingested" ? "synced" : evidenceStatusLabel(statusMode)}
            </Chip>
            <Chip
              className="intel-atlas-status-chip"
              title="Captured home moments linked to events. Each may include frames, Home Assistant metadata, Qwen labels, and human review."
              onClick={() => setGlobalPanel("observations")}
            >
              {packets.length || statusCounts.observation_packets || 0} observations
            </Chip>
            {privacyReviewCount > 0 && (
              <Chip
                className="intel-atlas-status-chip"
                tone="warn"
                title="Observations with uncertain privacy, visible screens, close faces, or potentially sensitive activity. Review before using for training or proposals."
                onClick={() => setGlobalPanel("privacy")}
              >
                {privacyReviewCount} privacy checks
              </Chip>
            )}
            <Chip
              className="intel-atlas-status-chip"
              tone={readyCandidates.length > 0 ? "ok" : blockedCandidates.length > 0 ? "warn" : "default"}
              title="Learning status from repeated override and pending-preference evidence. Opens the proposal/readiness panel."
              onClick={() => setGlobalPanel("learning")}
            >
              {readyCandidates.length > 0 ? `${readyCandidates.length} proposals ready` : `${blockedCandidates.length} patterns waiting`}
            </Chip>
            <Chip
              className="intel-atlas-status-chip"
              tone={promptGateBody.ready ? "ok" : "warn"}
              title={promptGateHelp(promptGateBody, labelEvalStatus)}
              onClick={() => setGlobalPanel("model")}
            >
              {promptGateLabel(promptGateBody, labelEvalStatus)}
            </Chip>
            {error && <Chip role="status" tone="crit">{error}</Chip>}
            {loading && <Chip tone="warn">loading</Chip>}
            <button className="intel-atlas-button hg-focusable" title="Reload Atlas packets, source health, and labeling status." onClick={refresh}>refresh</button>
            <button className="intel-atlas-button hg-focusable" title="Close the Intelligence Atlas. Esc also closes it." onClick={onClose}>close / esc</button>
          </div>
        </div>

        {infoOpen && (
          <>
            <button
              className="intel-atlas-guide-scrim"
              type="button"
              aria-label="Close Atlas guide"
              onClick={() => setInfoOpen(false)}
            />
            <section
              className="intel-atlas-guide"
              id="intel-atlas-guide"
              role="dialog"
              aria-label="How the Intelligence Atlas works"
            >
              <header>
                <div>
                  <span className="intel-atlas-kicker">system guide</span>
                  <h2>How Atlas Works</h2>
                  <p>Event evidence becomes visual context, human review, and eventually safer home intelligence.</p>
                </div>
                <button
                  className="intel-atlas-icon-button hg-focusable"
                  type="button"
                  aria-label="Close Atlas guide"
                  title="Close guide"
                  onClick={() => setInfoOpen(false)}
                >x</button>
              </header>

              <div className="intel-atlas-guide-grid">
                <section className="intel-atlas-guide-card">
                  <h3>What gets captured</h3>
                  <p>Atlas shows event-linked observation packets: lighting changes, Home Assistant state, camera frames when available, Qwen visual labels, and your review history.</p>
                </section>
                <section className="intel-atlas-guide-card">
                  <h3>What the dots mean</h3>
                  <p>Dots are laid out by effective activity label: human correction first, then Qwen, then unknown. This is a deterministic label map, not a learned embedding yet.</p>
                </section>
                <section className="intel-atlas-guide-card">
                  <h3>What can change lights</h3>
                  <p>Manual light changes and pending preferences are treated as preference evidence. Qwen labels are context features only; they do not directly change lighting policy.</p>
                </section>
                <section className="intel-atlas-guide-card">
                  <h3>Why review matters</h3>
                  <p>Your corrections become the held-out audit set. That verifier is what lets us tell whether a future prompt, label schema, or skill edit genuinely improved.</p>
                </section>
                <section className="intel-atlas-guide-card wide">
                  <h3>SkillOpt lessons we borrowed</h3>
                  <ul>
                    <li><b>Strict gates beat self-confidence.</b> Prompt and skill changes stay locked until reviewed evidence shows a real improvement; ties and regressions are rejected.</li>
                    <li><b>Small edits are safer.</b> Future prompt and skill updates should be bounded, inspectable patches, not full rewrites.</li>
                    <li><b>Compact procedure, durable evidence.</b> Skills store how to reason and review. Home facts stay in the ledger, packets, wiki claims, and audit trails.</li>
                    <li><b>Rejected changes still teach.</b> Failed prompt or skill edits become negative evidence for later optimization instead of being silently forgotten.</li>
                  </ul>
                </section>
                <section className="intel-atlas-guide-card wide muted">
                  <h3>Current limitation</h3>
                  <p>The map is intentionally simple until enough reviewed observations exist. Once there is a trustworthy held-out set, we can test embeddings, Qwen prompt changes, and SkillOpt-style procedural skills without shipping slop.</p>
                </section>
              </div>
            </section>
          </>
        )}

        {globalPanelMeta && (
          <>
            <button
              className="intel-atlas-global-scrim"
              type="button"
              aria-label="Close Atlas panel"
              onClick={() => setGlobalPanel(null)}
            />
            <section className="intel-atlas-global-panel" role="dialog" aria-label={globalPanelMeta.title}>
              <header>
                <div>
                  <span className="intel-atlas-kicker">global panel</span>
                  <h2>{globalPanelMeta.title}</h2>
                  <p>{globalPanelMeta.deck}</p>
                </div>
                <button className="intel-atlas-icon-button hg-focusable" type="button" onClick={() => setGlobalPanel(null)}>x</button>
              </header>

              {globalPanel === "observations" && (
                <div className="intel-atlas-global-body">
                  <DrawerSection title="loaded observations">
                    <div className="intel-atlas-stat-grid">
                      <Metric label="loaded" value={packets.length} />
                      <Metric label="need label" value={reviewPackets.length} />
                      <Metric label="privacy" value={privacyReviewCount} />
                      <Metric label="frames" value={statusCounts.observation_frames || "-"} />
                    </div>
                  </DrawerSection>
                  <DrawerSection title="recent packets">
                    {packets.slice(0, 12).map((packet) => {
                      const activity = labelAxis(packet.effective_labels || packet.latest_labels || {}, "primary_activity");
                      return (
                        <button
                          key={packet.id}
                          className="intel-atlas-global-row hg-focusable"
                          type="button"
                          onClick={() => {
                            setGlobalPanel(null);
                            loadPacket(packet);
                          }}
                        >
                          <span>{activity.label.replace(/_/g, " ")}</span>
                          <small>{(packet.zone || "zone").replace(/_/g, " ")} · {fmtTime(packet.ts || packet.created_at)}</small>
                        </button>
                      );
                    })}
                  </DrawerSection>
                </div>
              )}

              {globalPanel === "privacy" && (
                <div className="intel-atlas-global-body">
                  <DrawerSection title="why this matters">
                    <p className="intel-atlas-muted">Privacy review decides whether packets with visible screens, close faces, or uncertain context may be used beyond local inspection. It does not change lighting behavior.</p>
                  </DrawerSection>
                  <DrawerSection title="packets to review">
                    {privacyPackets.length === 0 ? <span className="intel-atlas-muted">No privacy review packets currently loaded.</span> : privacyPackets.slice(0, 12).map((packet) => {
                      const activity = labelAxis(packet.effective_labels || packet.latest_labels || {}, "primary_activity");
                      const privacy = labelAxis(packet.latest_labels || {}, "privacy_sensitivity");
                      return (
                        <button
                          key={packet.id}
                          className="intel-atlas-global-row warn hg-focusable"
                          type="button"
                          onClick={() => {
                            setGlobalPanel(null);
                            loadPacket(packet);
                            setExpandedTab("details");
                            setExpandedOpen(true);
                          }}
                        >
                          <span>{activity.label.replace(/_/g, " ")}</span>
                          <small>{privacy.label.replace(/_/g, " ")} · {(packet.zone || "zone").replace(/_/g, " ")}</small>
                        </button>
                      );
                    })}
                  </DrawerSection>
                </div>
              )}

              {globalPanel === "model" && (
                <div className="intel-atlas-global-body">
                  <DrawerSection title="prompt gate">
                    <div className="intel-atlas-stat-grid">
                      <Metric label="audited" value={`${labelEvalStatus?.audited_items || 0}/${labelEvalStatus?.minimum_audited_items || 50}`} />
                      <Metric label="status" value={promptGateBody.status || "blocked"} />
                      <Metric label="validity" value={promptGateBody.valid_json_rate == null ? "-" : fmtRatio(promptGateBody.valid_json_rate)} />
                      <Metric label="privacy" value={promptGateBody.privacy_recall == null ? "-" : fmtRatio(promptGateBody.privacy_recall)} />
                    </div>
                    <p className="intel-atlas-muted">Prompt optimization is locked until the held-out audit set is large enough to catch regressions. This mirrors the SkillOpt lesson: the validation gate matters more than the self-editing loop.</p>
                  </DrawerSection>
                  <DrawerSection title="next audits">
                    {evalQueueItems.length === 0 ? <span className="intel-atlas-muted">No unaudited eval items currently queued.</span> : evalQueueItems.slice(0, 5).map((item) => {
                      const activity = labelAxis(item.qwen_labels || {}, "primary_activity");
                      return (
                        <div key={item.id} className="intel-atlas-drawer-row">
                          <Chip>{item.packet_summary?.zone || "zone"}</Chip>
                          <Chip>{activity.label} {Math.round((activity.confidence || 0) * 100)}%</Chip>
                          <button className="intel-atlas-button hg-focusable" onClick={() => acceptEval(item)}>accept qwen</button>
                        </div>
                      );
                    })}
                  </DrawerSection>
                  <DrawerSection title="clip manifests">
                    <button className="intel-atlas-button hg-focusable" onClick={createManifest} disabled={!selectedPacket}>create from selected packet</button>
                    {manifestRows.length === 0 ? <p className="intel-atlas-muted">No clip manifests yet. These are future V-JEPA curation references, not active training jobs.</p> : manifestRows.slice(0, 5).map((manifest) => (
                      <div key={manifest.id} className="intel-atlas-drawer-row">
                        <Chip>{manifest.zone || "zone"}</Chip>
                        <Chip tone={manifest.status === "draft_eligible" ? "ok" : "warn"}>{manifest.status}</Chip>
                        <Chip>{manifest.frame_count_tier}</Chip>
                      </div>
                    ))}
                  </DrawerSection>
                </div>
              )}

              {globalPanel === "learning" && (
                <div className="intel-atlas-global-body">
                  <DrawerSection title="candidate state">
                    <div className="intel-atlas-stat-grid">
                      <Metric label="ready" value={readyCandidates.length} />
                      <Metric label="blocked" value={blockedCandidates.length} />
                      <Metric label="preference obs" value={counts.preference_observations || counts.observations || 0} />
                      <Metric label="visual packets" value={counts.observation_packets || statusCounts.observation_packets || 0} />
                    </div>
                  </DrawerSection>
                  <DrawerSection title="why nothing may be ready yet">
                    {blockedCandidates.length === 0 ? <span className="intel-atlas-muted">No blocked candidates currently loaded.</span> : blockedCandidates.slice(0, 8).map((candidate) => (
                      <div key={candidate.id} className="intel-atlas-drawer-row">
                        <Chip>{(candidate.zone || "zone").replace(/_/g, " ")}</Chip>
                        <span>{(candidate.blockers || []).slice(0, 2).join("; ") || candidate.status}</span>
                      </div>
                    ))}
                  </DrawerSection>
                  <DrawerSection title="soak review">
                    <ContextChips context={{
                      observed: soak?.summary?.observation_count,
                      pending: soak?.summary?.pending_count,
                      overrides: soak?.summary?.override_count,
                      m4: soak?.summary?.m4_record_count,
                      saved: soak?.summary?.capture_suppressed_total,
                    }} />
                  </DrawerSection>
                </div>
              )}

              {globalPanel === "system" && (
                <div className="intel-atlas-global-body">
                  <DrawerSection title="evidence sources">
                    {sourceRows.length === 0 ? <span className="intel-atlas-muted">No source rows.</span> : sourceRows.map((source) => (
                      <div key={source.source} className="intel-atlas-drawer-row">
                        <Chip tone={source.status === "fresh_idle" || source.status === "fresh_ingested" ? "ok" : "warn"}>{source.source}</Chip>
                        <Chip>{source.transport}</Chip>
                        <span>lag {fmtBytes(source.lag_bytes)}</span>
                      </div>
                    ))}
                  </DrawerSection>
                  <DrawerSection title="lighting activity ledger">
                    <ContextChips context={{
                      episodes: lightingActivitySummary.episode_count || 0,
                      raw_events: lightingActivitySummary.raw_event_count || 0,
                      collapsed: lightingActivitySummary.events_collapsed || 0,
                      unknown_source: lightingActivitySummary.unknown_source_count || 0,
                    }} />
                    {(lightingActivity?.episodes || []).slice(0, 4).map((episode) => (
                      <div key={episode.id} className="intel-atlas-drawer-row">
                        <Chip>{(episode.zone || "zone").replace(/_/g, " ")}</Chip>
                        <Chip>{String(episode.source_class || "unknown_source").replace(/_/g, " ")}</Chip>
                        <span>{transitionValueText(episode.first_state, "?")} -&gt; {transitionValueText(episode.last_state, "?")}</span>
                      </div>
                    ))}
                  </DrawerSection>
                  <DrawerSection title="capture and jobs">
                    <ContextChips context={{
                      packets: statusCounts.observation_packets || packets.length,
                      frames: statusCounts.observation_frames,
                      cameras: pilotCameras.join(", ") || "none",
                      ring: ringBuffer.running ? "running" : "stopped",
                      evidence_next: fmtTime(scheduler?.next_evidence_pull_at),
                      vision_next: fmtTime(scheduler?.next_multimodal_capture_at),
                    }} />
                    {jobs.slice(0, 5).map((job) => (
                      <div key={job.id} className="intel-atlas-drawer-row">
                        <Chip tone={job.status === "ok" ? "ok" : "warn"}>{job.status}</Chip>
                        <span>{job.name}</span>
                        <span>{fmtTime(job.finished_at || job.started_at)}</span>
                      </div>
                    ))}
                  </DrawerSection>
                </div>
              )}
            </section>
          </>
        )}

        <aside className="intel-atlas-timeline">
          <div className="intel-atlas-timeline-head">
            <span className="intel-atlas-kicker">{trayMode === "needs" ? "needs labels" : "timeline"}</span>
            <Chip>{trayMode === "needs" ? reviewPackets.length : "24h"}</Chip>
          </div>
          <div className="intel-atlas-filters">
            <button
              className={`intel-atlas-button hg-focusable ${trayMode === "timeline" ? "active" : ""}`}
              onClick={() => setTrayMode("timeline")}
              title="Show recent observations in time order."
            >timeline</button>
            <button
              className={`intel-atlas-button hg-focusable ${trayMode === "needs" ? "active" : ""}`}
              onClick={() => setTrayMode("needs")}
              title="Show observations where the visual label needs human review."
            >needs labels {reviewPackets.length}</button>
          </div>
          {trayMode === "needs" && (
            <div className="intel-atlas-review-guide">
              <span title="Correct only what you can see in the captured frames.">Only label what is visible.</span>
              <span title="If the room is empty or no activity is visible, confirm unknown instead of inventing an activity.">Confirm unknown when nothing visible is happening.</span>
              <span title="These labels improve review and clustering; they do not change lighting automations.">Labels do not change automations.</span>
            </div>
          )}
          {visiblePackets.length === 0 ? (
            <div style={{ color: "var(--hg-fg-5)", fontSize: 12 }}>no visual packets yet</div>
          ) : visiblePackets.slice(0, 18).map((packet, index) => {
            const labels = packetDisplayLabels(packet);
            const activity = labelAxis(labels, "primary_activity");
            const quality = labelAxis(labels, "image_quality");
            const privacy = labelAxis(labels, "privacy_sensitivity");
            const review = packetNeedsReview(packet);
            const active = selectedId === packet.id;
            const toneClass = packet.qwen_status === "invalid" ? "warn" : packet.frame_count ? "" : "muted";
            const showDay = index === 0 || new Date(packet.event_ts).toDateString() !== new Date(visiblePackets[index - 1]?.event_ts).toDateString();
            return (
              <React.Fragment key={packet.id}>
                {showDay && <div className="intel-atlas-day">{fmtTime(packet.event_ts).split(",")[0]}</div>}
                <button
                  className={`intel-atlas-event ${active ? "selected" : ""} ${toneClass} ${review.primary_activity ? "needs-review" : ""}`}
                  onClick={() => loadPacket(packet)}
                >
                  <div className="intel-atlas-event-top">
                    <strong>{String(activity.label || "unknown").replace(/_/g, " ")}</strong>
                    <span>{fmtTime(packet.event_ts).replace(/^.*?,\s*/, "")}</span>
                  </div>
                  <div className="intel-atlas-tags">
                    <span>{(packet.zone || "unknown").replace(/_/g, " ")}</span>
                    {activity.confidence ? <span>{Math.round(activity.confidence * 100)}%</span> : null}
                    <span>{packet.frame_count || 0} frames</span>
                    {privacy.label !== "normal" && privacy.label !== "unknown" && <span>{privacy.label}</span>}
                    {quality.label !== "unknown" && <span>{quality.label}</span>}
                    {review.primary_activity && <span>{review.reasons[0] || "review"}</span>}
                    {(packet.human_label_overrides || {}).primary_activity && <span>human</span>}
                  </div>
                </button>
              </React.Fragment>
            );
          })}
        </aside>

        <section className="intel-atlas-stage">
          <div
            className="intel-atlas-projection"
            data-projection={projectionMode}
            title="Dots are placed by effective primary activity label. Human corrections win over Qwen labels; unknown activity falls near the center. This is a deterministic activity layout, not a learned visual embedding yet."
          >
            <span>layout</span>
            <strong>activity labels</strong>
            <em>human &gt; qwen &gt; unknown</em>
            <b>not embedding</b>
          </div>
          <div className="intel-atlas-region region-work"><span>desk work</span></div>
          <div className="intel-atlas-region region-tv"><span>watching tv</span></div>
          <div className="intel-atlas-region region-kitchen"><span>cooking</span></div>
          <span className="intel-atlas-label label-reading">reading</span>
          <span className="intel-atlas-label label-pass">passing through</span>

          {points.length === 0 ? (
            <div className="intel-atlas-empty">no atlas points yet</div>
          ) : points.map((point) => {
            const color = activityColors[point.activity] || activityColors.unknown;
            const active = selectedId === point.id;
            const size = active ? 24 : Math.max(13, Math.min(18, 13 + (point.frame_count || 0) * 1.4));
            const alpha = active ? 1 : Math.max(0.72, Math.min(1, 0.76 + (point.confidence || 0) * 0.18));
            return (
              <button
                key={point.id}
                className={`intel-atlas-point ${active ? "selected" : ""} ${point.privacy_sensitive ? "privacy" : ""} ${point.linked ? "linked" : ""} ${point.human_corrected ? "human" : ""}`}
                title={`${point.activity} · ${point.zone || "unknown"} · ${fmtTime(point.event_ts)}`}
                onClick={() => loadPacket(point)}
                style={{
                  left: `${point.x}%`,
                  top: `${point.y}%`,
                  width: size,
                  height: size,
                  color,
                  opacity: alpha,
                }}
              />
            );
          })}

          {selectedPacket && (
            <>
              <div className="intel-atlas-connector" style={connectionStyle} />
              <article className="intel-atlas-popover" style={popoverLeft}>
                <header>
                  <strong>{(selectedPacket.zone || "unknown").replace(/_/g, " ")} · {fmtTime(selectedPacket.event_ts)}</strong>
                  <div className="intel-atlas-pop-head-actions">
                    <Chip tone={selectedPrivacy.label === "normal" || selectedPrivacy.label === "unknown" ? "default" : "warn"}>
                      {selectedPrivacy.label}
                    </Chip>
                    <button
                      className="intel-atlas-icon-button hg-focusable"
                      title="Close this observation card"
                      aria-label="Close observation card"
                      onClick={() => { setSelectedId(null); setDetail(null); setLabelEditorOpen(false); setAutoSelected(true); }}
                    >x</button>
                  </div>
                </header>
                <div className="intel-atlas-tags">
                  <Chip tone="ok">{selectedActivity.label} {Math.round((selectedActivity.confidence || 0) * 100)}%</Chip>
                  <Chip tone={selectedOverride ? "ok" : "default"}>{selectedOverride ? "human label" : "qwen label"}</Chip>
                  <Chip>{frames.length || selectedPacket.frame_count || 0} frames</Chip>
                  {selectedNeedsReview && <Chip tone="warn">review needed</Chip>}
                </div>
                {captureChange && (
                  <div className="intel-atlas-change">
                    <div className="intel-atlas-change-head">
                      <span className="intel-atlas-kicker">{captureChange.eyebrow}</span>
                      {captureChange.context && <Chip>{captureChange.context}</Chip>}
                    </div>
                    <strong>{captureChange.title}</strong>
                    <div className="intel-atlas-change-flow">
                      <div>
                        <span>{captureChange.beforeLabel}</span>
                        <b>{captureChange.before}</b>
                      </div>
                      <em>-&gt;</em>
                      <div>
                        <span>{captureChange.afterLabel}</span>
                        <b>{captureChange.after}</b>
                      </div>
                    </div>
                    <p>{captureChange.note}</p>
                  </div>
                )}
                <p>{summary}</p>
                <div className="intel-atlas-context-snapshot">
                  <div className="intel-atlas-context-snapshot-head">
                    <span className="intel-atlas-kicker">visual context</span>
                    <Chip>{selectedOverride ? "human > qwen" : "qwen"}</Chip>
                  </div>
                  {compactContextRows.map((row) => (
                    <div key={row.label} className="intel-atlas-context-row">
                      <span>{row.label}</span>
                      <div>
                        {row.values.map((value, index) => (
                          <b key={`${row.label}-${index}`}>{compactLabelText(value)}</b>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
                <div className="intel-atlas-frames">
                  {frames.slice(0, 4).map((frame, index) => (
                    <button
                      key={frame.id}
                      className="intel-atlas-frame hg-focusable"
                      title="Open frame"
                      onClick={() => setLightboxIndex(index)}
                    >
                      {!frame.deleted_at ? (
                        <img src={`${base}${frame.image_url}`} alt={frame.frame_id} />
                      ) : (
                        <div>deleted</div>
                      )}
                      <span>{frame.frame_role}</span>
                    </button>
                  ))}
                  {frames.length === 0 && (
                    <div className="intel-atlas-frame empty">
                      <div className="intel-atlas-frame-empty-copy">
                        <strong>{emptyFrames?.title || "No frames captured"}</strong>
                        <span>{emptyFrames?.detail || "No camera frames are attached to this observation."}</span>
                      </div>
                    </div>
                  )}
                </div>
                {showReviewEditor ? (
                  <div className="intel-atlas-review-panel">
                    <div className="intel-atlas-review-head">
                      <strong>{selectedNeedsReview ? "Review this label" : "Edit label"}</strong>
                      <Chip tone={selectedNeedsReview ? "warn" : "default"}>
                        {(selectedReview.reasons || [])[0] || selectedOverride?.source || "manual"}
                      </Chip>
                    </div>
                    <div className="intel-atlas-review-copy">
                      <span title="Correct only what is visible in the captured frames.">Only label what is visible.</span>
                      <span title="If nobody is doing anything visible, keep the activity unknown.">If no activity is visible, confirm unknown.</span>
                      <span title="These corrections train the label reviewer and clustering view, not lighting automations.">Corrections do not change automations.</span>
                    </div>
                    <div className="intel-atlas-review-source">
                      <span>Qwen: {selectedQwenActivity.label} {Math.round((selectedQwenActivity.confidence || 0) * 100)}%</span>
                      {selectedOverride && <span>Effective: {selectedActivity.label} via {String(selectedOverride.source || "human").replace(/_/g, " ")}</span>}
                    </div>
                    <div className="intel-atlas-activity-grid">
                      {PRIMARY_ACTIVITY_OPTIONS.map(([value, label]) => (
                        <button
                          key={value}
                          className="intel-atlas-button hg-focusable"
                          title={`Choose ${label} when that activity is visible in the frames.`}
                          onClick={() => correctPrimaryActivity(value)}
                        >{label}</button>
                      ))}
                    </div>
                    <div className="intel-atlas-review-actions">
                      <button
                        className="intel-atlas-button hg-focusable"
                        title="Accept Qwen's current activity label as correct."
                        onClick={confirmQwenActivity}
                      >confirm qwen</button>
                      <button
                        className="intel-atlas-button hg-focusable"
                        title="Use when no visible activity is present."
                        onClick={confirmUnknownActivity}
                      >confirm unknown</button>
                    </div>
                    <div className="intel-atlas-review-source">not labelable:</div>
                    <div className="intel-atlas-review-actions">
                      {NOT_LABELABLE_REASONS.map(([value, label]) => (
                        <button
                          key={value}
                          className="intel-atlas-button hg-focusable"
                          title={`Mark this packet not labelable because it is ${label}.`}
                          onClick={() => notLabelableActivity(value)}
                        >{label}</button>
                      ))}
                    </div>
                    {undoAudit?.packetId === selectedPacket.id && (
                      <div className="intel-atlas-undo">
                        <span>saved {undoAudit.label}</span>
                        <button className="intel-atlas-button hg-focusable" onClick={undoLastLabelAudit}>undo</button>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className={`intel-atlas-review-idle ${selectedNeedsReview ? "needs" : ""}`}>
                    <span>
                      {selectedNeedsReview
                        ? "Review activity label"
                        : selectedOverride
                          ? `Human label: ${selectedActivity.label}`
                          : `Qwen label: ${selectedActivity.label}`}
                      {selectedNeedsReview && <small>Qwen is uncertain; correct it only if the frames make the activity visible.</small>}
                    </span>
                    <button
                      className="intel-atlas-button hg-focusable"
                      title="Edit the primary activity label for this observation."
                      onClick={() => setLabelEditorOpen(true)}
                    >{selectedNeedsReview ? "review label" : "edit label"}</button>
                  </div>
                )}
                <div className="intel-atlas-pop-actions">
                  <button className="intel-atlas-button hg-focusable" onClick={() => auditPacket("label_looks_right")}>right</button>
                  <button className="intel-atlas-button hg-focusable" onClick={() => auditPacket("label_looks_wrong")}>wrong</button>
                  <button className="intel-atlas-button hg-focusable" onClick={() => { setExpandedTab("details"); setExpandedOpen(true); }}>advanced</button>
                </div>
              </article>
            </>
          )}

          {expandedOpen && selectedPacket && (
            <>
              <button
                className="intel-atlas-expanded-scrim"
                type="button"
                aria-label="Close expanded observation"
                onClick={() => setExpandedOpen(false)}
              />
              <section className="intel-atlas-expanded-card" role="dialog" aria-label="Expanded observation">
                <header className="intel-atlas-expanded-head">
                  <div>
                    <span className="intel-atlas-kicker">selected observation</span>
                    <h2>{(selectedPacket.zone || "observation").replace(/_/g, " ")} · {fmtTime(selectedPacket.event_ts || selectedPacket.ts || selectedPacket.created_at)}</h2>
                    <p>{selectedNeedsReview ? "Review this packet before it becomes reliable evaluation evidence." : "A larger view of the same observation, with frames, context, and provenance."}</p>
                  </div>
                  <button
                    className="intel-atlas-icon-button hg-focusable"
                    type="button"
                    title="Close expanded observation"
                    aria-label="Close expanded observation"
                    onClick={() => setExpandedOpen(false)}
                  >x</button>
                </header>

                <div className="intel-atlas-expanded-tabs">
                  {expandedTabs.map((tab) => (
                    <button
                      key={tab.id}
                      className={`intel-atlas-expanded-tab hg-focusable ${expandedTab === tab.id ? "active" : ""}`}
                      type="button"
                      title={tab.description}
                      onClick={() => setExpandedTab(tab.id)}
                    >
                      <span>{tab.label}</span>
                      <small>{tab.short}</small>
                    </button>
                  ))}
                </div>
                <div className="intel-atlas-expanded-context">
                  <strong>{activeExpandedTab.label}</strong>
                  <span>{activeExpandedTab.focus}</span>
                </div>

                {expandedTab === "overview" && (
                  <div className="intel-atlas-expanded-body">
                    <DrawerSection title="why captured">
                      {captureChange ? (
                        <div className="intel-atlas-change expanded">
                          <div className="intel-atlas-change-head">
                            <span className="intel-atlas-kicker">{captureChange.eyebrow}</span>
                            {captureChange.context && <Chip>{captureChange.context}</Chip>}
                          </div>
                          <strong>{captureChange.title}</strong>
                          <div className="intel-atlas-change-flow">
                            <div><span>{captureChange.beforeLabel}</span><b>{captureChange.before}</b></div>
                            <em>-&gt;</em>
                            <div><span>{captureChange.afterLabel}</span><b>{captureChange.after}</b></div>
                          </div>
                          <p>{captureChange.note}</p>
                        </div>
                      ) : (
                        <p className="intel-atlas-muted">This packet has no lighting transition summary yet.</p>
                      )}
                    </DrawerSection>
                    <DrawerSection title="interpretation">
                      <ContextChips context={{
                        activity: `${selectedActivity.label} ${Math.round((selectedActivity.confidence || 0) * 100)}%`,
                        source: selectedOverride ? "human correction" : "qwen label",
                        privacy: selectedPrivacy.label,
                        quality: selectedQuality.label,
                        frames: frames.length,
                        review: selectedNeedsReview ? "needed" : "not needed",
                      }} />
                      <p className="intel-atlas-muted">{summary}</p>
                    </DrawerSection>
                    <DrawerSection title="next action">
                      {selectedNeedsReview ? (
                        <button className="intel-atlas-primary-action hg-focusable" type="button" onClick={() => { setLabelEditorOpen(true); setExpandedOpen(false); }}>
                          Review activity label
                        </button>
                      ) : (
                        <button className="intel-atlas-button hg-focusable" type="button" onClick={() => setExpandedTab("frames")}>Open frames</button>
                      )}
                      <p className="intel-atlas-muted">Corrections are append-only supervision data. They do not change lighting policy.</p>
                    </DrawerSection>
                  </div>
                )}

                {expandedTab === "frames" && (
                  <div className="intel-atlas-expanded-body">
                    <DrawerSection title="frames">
                      {frames.length === 0 ? (
                        <div className="intel-atlas-empty-frames-inline">
                          <b>{emptyFrames?.title || "No frames captured"}</b>
                          <span>{emptyFrames?.detail || "No camera frames are attached to this observation."}</span>
                        </div>
                      ) : (
                        <div className="intel-atlas-expanded-frame-grid">
                          {frames.map((frame, index) => (
                            <button
                              key={frame.id}
                              className="intel-atlas-expanded-frame hg-focusable"
                              type="button"
                              title="Open frame"
                              onClick={() => setLightboxIndex(index)}
                            >
                              {!frame.deleted_at ? <img src={`${base}${frame.image_url}`} alt={frame.frame_id} /> : <div>deleted</div>}
                              <span>{frame.frame_role || frame.frame_id}</span>
                            </button>
                          ))}
                        </div>
                      )}
                    </DrawerSection>
                    <DrawerSection title="privacy cue">
                      <ContextChips context={{
                        privacy: selectedPrivacy.label,
                        quality: selectedQuality.label,
                        screen: selectedScreen.label,
                        lighting: selectedLighting.label,
                      }} />
                    </DrawerSection>
                  </div>
                )}

                {expandedTab === "details" && (
                  <div className="intel-atlas-expanded-body">
                    <DrawerSection title="labels">
                      <AxisGrid labels={selectedLabels} />
                    </DrawerSection>
                    {selectedOverride && (
                      <DrawerSection title="human correction history">
                        <ContextChips context={{
                          effective: selectedActivity.label,
                          source: String(selectedOverride.source || "human").replace(/_/g, " "),
                          qwen: `${selectedQwenActivity.label} ${Math.round((selectedQwenActivity.confidence || 0) * 100)}%`,
                          audit: selectedOverride.audit_id,
                        }} />
                      </DrawerSection>
                    )}
                    <DrawerSection title="home assistant context">
                      <ContextChips context={{
                        zone: selectedPacket?.zone,
                        camera: selectedPacket?.camera,
                        event: selectedPacket?.event_type,
                        profile: ha.profile,
                        state: ha.state,
                        tv: ha.tv_playing,
                        actual: fmtPct(ha.actual_brightness_pct),
                        predicted: fmtPct(ha.predicted_brightness_pct),
                        shadow: ha.shadow_mode,
                      }} />
                    </DrawerSection>
                    <DrawerSection title="use and privacy">
                      <ContextChips context={{
                        needs_review: selectedNeedsReview,
                        privacy: selectedPrivacy.label,
                        quality: selectedQuality.label,
                        frames: frames.length,
                        human_label: selectedOverride ? selectedActivity.label : null,
                        training: selectedPrivacy.label === "normal" && !selectedNeedsReview ? "eligible" : "review first",
                      }} />
                      <div className="intel-atlas-expanded-actions">
                        <button className="intel-atlas-button hg-focusable" type="button" onClick={() => auditPacket("ignore_observation")}>ignore observation</button>
                        <button className="intel-atlas-button hg-focusable" type="button" onClick={createManifest} disabled={!selectedPacket}>create clip manifest</button>
                      </div>
                    </DrawerSection>
                    <details className="intel-atlas-advanced-raw">
                      <summary>Advanced raw evidence</summary>
                      <pre style={preStyle}>{JSON.stringify({ packet: detail?.packet, latest_label: detail?.latest_label, audits: detail?.audits }, null, 2)}</pre>
                    </details>
                  </div>
                )}
              </section>
            </>
          )}

        </section>

        {false && (
          <>
            <div className="intel-atlas-rail">
            <button className="intel-atlas-button hg-focusable" onClick={() => { setDrawerTab("summary"); setDrawerOpen(true); }}>inspector</button>
            <button className="intel-atlas-button hg-focusable" onClick={() => setGlobalPanel("privacy")}>privacy queue</button>
            <button className="intel-atlas-button hg-focusable" onClick={() => setGlobalPanel("system")}>system health</button>
            </div>

            <aside className={`intel-atlas-drawer ${drawerOpen ? "open" : ""}`}>
          <div className="intel-atlas-drawer-head">
            <div>
              <span className="intel-atlas-kicker">selected observation</span>
              <h2>Observation Inspector</h2>
            </div>
            <button className="intel-atlas-button hg-focusable" onClick={() => setDrawerOpen(false)}>close</button>
          </div>
          <p className="intel-atlas-drawer-deck">A focused explanation of the selected dot: what happened, what was visible, what Home Assistant knew, and whether the packet is usable.</p>
          <div className="intel-atlas-drawer-tabs">
            {drawerTabs.map((tab) => (
              <button
                key={tab.id}
                className={`intel-atlas-drawer-tab hg-focusable ${drawerTab === tab.id ? "active" : ""}`}
                onClick={() => setDrawerTab(tab.id)}
                title={tab.description}
              >
                <span>{tab.label}</span>
                <small>{tab.short}</small>
              </button>
            ))}
          </div>
          <div className="intel-atlas-drawer-context">
            <strong>{activeDrawerTab.label}</strong>
            <span>{activeDrawerTab.focus}</span>
          </div>

          {drawerTab === "summary" && (
            <div className="intel-atlas-drawer-body">
              <DrawerSection title="what happened">
                {captureChange ? (
                  <div className="intel-atlas-change drawer">
                    <div>
                      <span>{captureChange.eyebrow}</span>
                      {captureChange.context && <Chip>{captureChange.context}</Chip>}
                    </div>
                    <strong>{captureChange.title}</strong>
                    <div className="intel-atlas-change-flow">
                      <div><span>{captureChange.beforeLabel}</span><b>{captureChange.before}</b></div>
                      <em>-&gt;</em>
                      <div><span>{captureChange.afterLabel}</span><b>{captureChange.after}</b></div>
                    </div>
                    <p>{captureChange.note}</p>
                  </div>
                ) : (
                  <p className="intel-atlas-muted">Select a dot to inspect its trigger and evidence.</p>
                )}
              </DrawerSection>
              <DrawerSection title="current interpretation">
                <ContextChips context={{
                  activity: `${selectedActivity.label} ${Math.round((selectedActivity.confidence || 0) * 100)}%`,
                  source: selectedOverride ? "human correction" : "qwen label",
                  privacy: selectedPrivacy.label,
                  quality: selectedQuality.label,
                  frames: frames.length,
                  review: selectedNeedsReview ? "needed" : "not needed",
                }} />
                <p className="intel-atlas-muted">{summary}</p>
              </DrawerSection>
              <DrawerSection title="next action">
                {selectedNeedsReview ? (
                  <button className="intel-atlas-button hg-focusable" onClick={() => { setDrawerTab("labels"); setLabelEditorOpen(true); }}>review label</button>
                ) : (
                  <button className="intel-atlas-button hg-focusable" onClick={() => setDrawerTab("frames")}>inspect frames</button>
                )}
              </DrawerSection>
            </div>
          )}

          {drawerTab === "frames" && (
            <div className="intel-atlas-drawer-body">
              <DrawerSection title="captured frames">
                <div className="intel-atlas-frames drawer">
                  {frames.length === 0 ? (
                    <div className="intel-atlas-frame empty">
                      <div className="intel-atlas-frame-empty-copy">
                        <strong>{emptyFrames?.title || "No frames captured"}</strong>
                        <span>{emptyFrames?.detail || "No camera frames are attached to this observation."}</span>
                      </div>
                    </div>
                  ) : frames.map((frame, index) => (
                    <button
                      key={frame.id}
                      className="intel-atlas-frame hg-focusable"
                      title="Open frame"
                      onClick={() => setLightboxIndex(index)}
                    >
                      {!frame.deleted_at ? <img src={`${base}${frame.image_url}`} alt={frame.frame_id} /> : <div>deleted</div>}
                      <span>{frame.frame_role}</span>
                    </button>
                  ))}
                </div>
              </DrawerSection>
              <DrawerSection title="privacy cue">
                <ContextChips context={{
                  privacy: selectedPrivacy.label,
                  quality: selectedQuality.label,
                  screen: selectedScreen.label,
                  lighting: selectedLighting.label,
                }} />
              </DrawerSection>
            </div>
          )}

          {drawerTab === "labels" && (
            <div className="intel-atlas-drawer-body">
              <DrawerSection title="effective labels">
                <AxisGrid labels={selectedLabels} />
              </DrawerSection>
              {selectedOverride && (
                <DrawerSection title="human correction">
                  <ContextChips context={{
                    effective: selectedActivity.label,
                    source: String(selectedOverride.source || "human").replace(/_/g, " "),
                    qwen: `${selectedQwenActivity.label} ${Math.round((selectedQwenActivity.confidence || 0) * 100)}%`,
                    audit: selectedOverride.audit_id,
                  }} />
                </DrawerSection>
              )}
              <DrawerSection title="edit primary activity">
                <div className="intel-atlas-review-copy">
                  <span>Only label what is visible.</span>
                  <span>Corrections train the label reviewer, not lighting automations.</span>
                </div>
                <button className="intel-atlas-button hg-focusable" onClick={() => setLabelEditorOpen(true)}>open label editor on card</button>
              </DrawerSection>
            </div>
          )}

          {drawerTab === "context" && (
            <div className="intel-atlas-drawer-body">
              <DrawerSection title="home assistant truth">
                <ContextChips context={{
                  zone: selectedPacket?.zone,
                  camera: selectedPacket?.camera,
                  event: selectedPacket?.event_type,
                  profile: ha.profile,
                  state: ha.state,
                  tv: ha.tv_playing,
                  actual: fmtPct(ha.actual_brightness_pct),
                  predicted: fmtPct(ha.predicted_brightness_pct),
                  shadow: ha.shadow_mode,
                }} />
              </DrawerSection>
              <DrawerSection title="capture timing">
                <ContextChips context={{
                  packet: selectedPacket?.id,
                  created: fmtTime(selectedPacket?.created_at || selectedPacket?.ts),
                  trigger: selectedPacket?.trigger || ha.capture_provenance?.capture_trigger,
                  evidence: ha.evidence_id || selectedPacket?.source_event_id,
                  related_override: ha.related_override_context_id,
                }} />
              </DrawerSection>
            </div>
          )}

          {drawerTab === "use" && (
            <div className="intel-atlas-drawer-body">
              <DrawerSection title="can this packet be used?">
                <ContextChips context={{
                  needs_review: selectedNeedsReview,
                  privacy: selectedPrivacy.label,
                  quality: selectedQuality.label,
                  frames: frames.length,
                  human_label: selectedOverride ? selectedActivity.label : null,
                  training: selectedPrivacy.label === "normal" && !selectedNeedsReview ? "eligible" : "review first",
                }} />
              </DrawerSection>
              <DrawerSection title="actions">
                <div className="intel-atlas-drawer-row">
                  <button className="intel-atlas-button hg-focusable" onClick={() => auditPacket("ignore_observation")}>ignore observation</button>
                  <button className="intel-atlas-button hg-focusable" onClick={createManifest} disabled={!selectedPacket}>create clip manifest</button>
                </div>
                <p className="intel-atlas-muted">These actions affect audit/training eligibility only. They do not change lighting policy.</p>
              </DrawerSection>
            </div>
          )}

          {drawerTab === "raw" && (
            <div className="intel-atlas-drawer-body">
              <DrawerSection title="raw evidence">
                <pre style={preStyle}>{JSON.stringify({ packet: detail?.packet, latest_label: detail?.latest_label, audits: detail?.audits }, null, 2)}</pre>
              </DrawerSection>
            </div>
          )}

          {drawerTab === "observation" && (
            <div className="intel-atlas-drawer-body">
              <DrawerSection title="frames">
                <div className="intel-atlas-frames drawer">
                  {frames.length === 0 ? (
                    <div className="intel-atlas-frame empty">
                      <div className="intel-atlas-frame-empty-copy">
                        <strong>{emptyFrames?.title || "No frames captured"}</strong>
                        <span>{emptyFrames?.detail || "No camera frames are attached to this observation."}</span>
                      </div>
                    </div>
                  ) : frames.map((frame, index) => (
                    <button
                      key={frame.id}
                      className="intel-atlas-frame hg-focusable"
                      title="Open frame"
                      onClick={() => setLightboxIndex(index)}
                    >
                      {!frame.deleted_at ? <img src={`${base}${frame.image_url}`} alt={frame.frame_id} /> : <div>deleted</div>}
                      <span>{frame.frame_role}</span>
                    </button>
                  ))}
                </div>
              </DrawerSection>
              <DrawerSection title="labels">
                <AxisGrid labels={selectedLabels} />
              </DrawerSection>
              {selectedOverride && (
                <DrawerSection title="human label provenance">
                  <ContextChips context={{
                    effective: selectedActivity.label,
                    source: String(selectedOverride.source || "human").replace(/_/g, " "),
                    qwen: `${selectedQwenActivity.label} ${Math.round((selectedQwenActivity.confidence || 0) * 100)}%`,
                    audit: selectedOverride.audit_id,
                  }} />
                </DrawerSection>
              )}
              <DrawerSection title="home assistant">
                <ContextChips context={{
                  zone: selectedPacket?.zone,
                  camera: selectedPacket?.camera,
                  event: selectedPacket?.event_type,
                  profile: ha.profile,
                  state: ha.state,
                  tv: ha.tv_playing,
                  actual: fmtPct(ha.actual_brightness_pct),
                  predicted: fmtPct(ha.predicted_brightness_pct),
                  shadow: ha.shadow_mode,
                }} />
              </DrawerSection>
              <DrawerSection title="raw evidence">
                <pre style={preStyle}>{JSON.stringify({ packet: detail?.packet, latest_label: detail?.latest_label, audits: detail?.audits }, null, 2)}</pre>
              </DrawerSection>
            </div>
          )}

          {drawerTab === "training" && (
            <div className="intel-atlas-drawer-body">
              <DrawerSection title="prompt gate">
                <div className="intel-atlas-stat-grid">
                  <Metric label="audited" value={`${labelEvalStatus?.audited_items || 0}/${labelEvalStatus?.minimum_audited_items || 50}`} />
                  <Metric label="status" value={promptGateBody.status || "blocked"} />
                  <Metric label="validity" value={promptGateBody.valid_json_rate == null ? "-" : fmtRatio(promptGateBody.valid_json_rate)} />
                  <Metric label="privacy" value={promptGateBody.privacy_recall == null ? "-" : fmtRatio(promptGateBody.privacy_recall)} />
                </div>
              </DrawerSection>
              <DrawerSection title="eval queue">
                {evalQueueItems.length === 0 ? <span className="intel-atlas-muted">no unaudited eval items</span> : evalQueueItems.slice(0, 4).map((item) => {
                  const labels = item.qwen_labels || {};
                  const activity = labelAxis(labels, "primary_activity");
                  return (
                    <div key={item.id} className="intel-atlas-drawer-row">
                      <Chip>{item.packet_summary?.zone || "zone"}</Chip>
                      <Chip>{activity.label} {Math.round((activity.confidence || 0) * 100)}%</Chip>
                      <button className="intel-atlas-button hg-focusable" onClick={() => acceptEval(item)}>accept qwen</button>
                    </div>
                  );
                })}
              </DrawerSection>
              <DrawerSection title="training manifests">
                <div className="intel-atlas-drawer-row">
                  <button className="intel-atlas-button hg-focusable" onClick={createManifest}>clip manifest</button>
                </div>
                {manifestRows.length === 0 ? <span className="intel-atlas-muted">no clip manifests yet</span> : manifestRows.slice(0, 5).map((manifest) => (
                  <div key={manifest.id} className="intel-atlas-drawer-row">
                    <Chip>{manifest.zone || "zone"}</Chip>
                    <Chip tone={manifest.status === "draft_eligible" ? "ok" : "warn"}>{manifest.status}</Chip>
                    <Chip>{manifest.frame_count_tier}</Chip>
                  </div>
                ))}
              </DrawerSection>
            </div>
          )}

          {drawerTab === "learning" && (
            <div className="intel-atlas-drawer-body">
              <DrawerSection title="candidate state">
                <div className="intel-atlas-stat-grid">
                  <Metric label="ready" value={readyCandidates.length} />
                  <Metric label="blocked" value={blockedCandidates.length} />
                  <Metric label="observations" value={counts.preference_observations || counts.observations || 0} />
                  <Metric label="visual" value={counts.observation_packets || statusCounts.observation_packets || 0} />
                </div>
              </DrawerSection>
              <DrawerSection title="blocked candidates">
                {blockedCandidates.slice(0, 8).map((candidate) => (
                  <div key={candidate.id} className="intel-atlas-drawer-row">
                    <Chip>{(candidate.zone || "zone").replace(/_/g, " ")}</Chip>
                    <span>{(candidate.blockers || []).slice(0, 2).join("; ") || candidate.status}</span>
                  </div>
                ))}
              </DrawerSection>
              <DrawerSection title="soak review">
                <ContextChips context={{
                  observed: soak?.summary?.observation_count,
                  pending: soak?.summary?.pending_count,
                  overrides: soak?.summary?.override_count,
                  m4: soak?.summary?.m4_record_count,
                  saved: soak?.summary?.capture_suppressed_total,
                }} />
              </DrawerSection>
            </div>
          )}

          {drawerTab === "system" && (
            <div className="intel-atlas-drawer-body">
              <DrawerSection title="evidence sources">
                {sourceRows.length === 0 ? <span className="intel-atlas-muted">no source rows</span> : sourceRows.map((source) => (
                  <div key={source.source} className="intel-atlas-drawer-row">
                    <Chip tone={source.status === "fresh_idle" || source.status === "fresh_ingested" ? "ok" : "warn"}>{source.source}</Chip>
                    <Chip>{source.transport}</Chip>
                    <span>lag {fmtBytes(source.lag_bytes)}</span>
                  </div>
                ))}
              </DrawerSection>
              <DrawerSection title="lighting activity ledger">
                <ContextChips context={{
                  episodes: lightingActivitySummary.episode_count || 0,
                  raw_events: lightingActivitySummary.raw_event_count || 0,
                  collapsed: lightingActivitySummary.events_collapsed || 0,
                  unknown_source: lightingActivitySummary.unknown_source_count || 0,
                }} />
                {(lightingActivity?.episodes || []).slice(0, 5).map((episode) => (
                  <div key={episode.id} className="intel-atlas-drawer-row">
                    <Chip>{(episode.zone || "zone").replace(/_/g, " ")}</Chip>
                    <Chip>{String(episode.source_class || "unknown_source").replace(/_/g, " ")}</Chip>
                    <span>{transitionValueText(episode.first_state, "?")} -&gt; {transitionValueText(episode.last_state, "?")}</span>
                  </div>
                ))}
              </DrawerSection>
              <DrawerSection title="capture coverage">
                <ContextChips context={{
                  packets: statusCounts.observation_packets || packets.length,
                  frames: statusCounts.observation_frames,
                  cameras: pilotCameras.join(", ") || "none",
                  zones: pilotZones.join(", ") || "none",
                  lookback: pilotLookback == null ? "-" : `${Math.round(pilotLookback)}s`,
                  max_run: multimodalConfig.pilot_max_packets_per_run ?? latestPilot.config?.pilot_max_packets_per_run,
                  max_hour: multimodalConfig.max_captures_per_hour,
                  ring: ringBuffer.running ? "running" : "stopped",
                  ring_frames: (ringBuffer.cameras || []).map((camera) => `${camera.camera}:${camera.frames}`).join(", ") || "-",
                }} />
                {latestPilotSkipped.length > 0 && (
                  <div className="intel-atlas-drawer-row">
                    <Chip tone="warn">sparse</Chip>
                    <span>{latestPilotSkipped.length} evidence rows skipped by the current pilot; latest reason: {latestPilotSkipped[0]?.reason || "not selected"}</span>
                  </div>
                )}
              </DrawerSection>
              <DrawerSection title="jobs">
                <ContextChips context={{
                  running: scheduler?.running,
                  evidence_next: fmtTime(scheduler?.next_evidence_pull_at),
                  vision_next: fmtTime(scheduler?.next_multimodal_capture_at),
                  memory_next: fmtTime(scheduler?.next_memory_at),
                }} />
                {jobs.slice(0, 5).map((job) => (
                  <div key={job.id} className="intel-atlas-drawer-row">
                    <Chip tone={job.status === "ok" ? "ok" : "warn"}>{job.status}</Chip>
                    <span>{job.name}</span>
                    <span>{fmtTime(job.finished_at || job.started_at)}</span>
                  </div>
                ))}
              </DrawerSection>
            </div>
          )}
        </aside>
            </>
          )}

        {lightboxFrame && (
          /* aria-modal honesty: focus is trapped at the atlas ROOT layer
             (intel-root), not here — this inner dialog must not claim
             modality it doesn't enforce. Escape still closes it first via
             the root layer's chain. */
          <div className="intel-atlas-lightbox" role="dialog" aria-modal="false" aria-label="Observation frame preview">
            <div className="intel-atlas-lightbox-card">
              <div className="intel-atlas-lightbox-head">
                <div>
                  <strong>{lightboxFrame.frame_role || lightboxFrame.frame_id}</strong>
                  <span>{fmtTime(lightboxFrame.captured_at)} / {lightboxFrame.relative_ts_s == null ? "event frame" : `${Number(lightboxFrame.relative_ts_s).toFixed(1)}s`}</span>
                </div>
                <div className="intel-atlas-pop-head-actions">
                  {sensitiveLightbox && <Chip tone="warn">{selectedPrivacy.label || "sensitive"}</Chip>}
                  <button className="intel-atlas-icon-button hg-focusable" title="Close frame preview" onClick={() => setLightboxIndex(null)}>x</button>
                </div>
              </div>
              {sensitiveLightbox && (
                <div className="intel-atlas-lightbox-warning">
                  This frame may contain sensitive visual context. Keep review local and label only what is visible.
                </div>
              )}
              {!lightboxFrame.deleted_at ? (
                <img src={`${base}${lightboxFrame.image_url}`} alt={lightboxFrame.frame_id} />
              ) : (
                <div className="intel-atlas-lightbox-missing">frame deleted</div>
              )}
              <div className="intel-atlas-lightbox-actions">
                <button
                  className="intel-atlas-button hg-focusable"
                  disabled={frames.length <= 1}
                  onClick={() => setLightboxIndex((lightboxIndex + frames.length - 1) % frames.length)}
                >previous</button>
                <Chip>{(lightboxIndex || 0) + 1}/{frames.length}</Chip>
                <button
                  className="intel-atlas-button hg-focusable"
                  disabled={frames.length <= 1}
                  onClick={() => setLightboxIndex((lightboxIndex + 1) % frames.length)}
                >next</button>
              </div>
            </div>
          </div>
        )}

        <style>{`
          @keyframes intel-atlas-fade-in {
            from { opacity: 0; transform: translateY(6px); }
            to { opacity: 1; transform: translateY(0); }
          }
          .intel-atlas {
            position: fixed;
            inset: 0;
            z-index: 1000;
            overflow: hidden;
            background: var(--hg-bg-0);
            color: var(--hg-fg-0);
            font-family: 'Geist', system-ui, sans-serif;
            animation: intel-atlas-fade-in 220ms cubic-bezier(0.16, 1, 0.3, 1);
          }
          .intel-atlas.spatial {
            overflow: hidden;
          }
          .intel-atlas.spatial .intel-atlas-top {
            border-radius: 8px 8px 0 0;
          }
          .intel-atlas.spatial .intel-atlas-expanded-card,
          .intel-atlas.spatial .intel-atlas-global-panel,
          .intel-atlas.spatial .intel-atlas-guide,
          .intel-atlas.spatial .intel-atlas-lightbox-card {
            max-height: calc(100% - 48px);
          }
          .intel-atlas::before {
            content: "";
            position: absolute;
            inset: 0;
            background:
              linear-gradient(90deg, rgba(255,255,255,0.025) 1px, transparent 1px),
              linear-gradient(0deg, rgba(255,255,255,0.018) 1px, transparent 1px);
            background-size: 120px 120px;
            mask-image: linear-gradient(to bottom, transparent, #000 18%, #000 82%, transparent);
            opacity: 0.45;
            pointer-events: none;
          }
          .intel-atlas-top {
            position: absolute;
            z-index: 300;
            left: 0;
            right: 0;
            top: 0;
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 16px;
            min-height: 92px;
            padding: 22px 28px 18px;
            box-sizing: border-box;
            background:
              linear-gradient(to bottom, rgba(0,0,0,0.98) 0%, rgba(0,0,0,0.96) 72%, rgba(0,0,0,0.78) 100%);
            border-bottom: 1px solid rgba(255,255,255,0.045);
            box-shadow: 0 18px 50px rgba(0,0,0,0.5);
          }
          .intel-atlas-title h1 {
            margin: 0;
            font-size: 22px;
            font-weight: 500;
            letter-spacing: 0;
          }
          .intel-atlas-title-row {
            position: relative;
            display: flex;
            align-items: center;
            gap: 8px;
            width: max-content;
            max-width: 100%;
          }
          .intel-atlas-title p {
            margin: 5px 0 0;
            color: var(--hg-fg-4);
            font-size: 12px;
          }
          .intel-atlas-info {
            width: 17px;
            height: 17px;
            display: grid;
            place-items: center;
            border: 1px solid var(--hg-border-soft);
            border-radius: 999px;
            background: rgba(255,255,255,0.035);
            color: var(--hg-fg-4);
            font-family: 'Geist Mono', ui-monospace, monospace;
            font-size: 10px;
            line-height: 1;
            cursor: pointer;
          }
          .intel-atlas-info:hover,
          .intel-atlas-info:focus-visible {
            color: var(--hg-fg-0);
            border-color: var(--hg-border-strong);
            background: rgba(255,255,255,0.07);
          }
          .intel-atlas-guide-scrim {
            position: absolute;
            z-index: 220;
            inset: 0;
            border: 0;
            padding: 0;
            background: rgba(0,0,0,0.62);
            cursor: default;
          }
          .intel-atlas-guide {
            position: absolute;
            z-index: 260;
            left: 322px;
            top: 108px;
            width: min(620px, calc(100vw - 378px));
            max-height: min(640px, calc(100vh - 140px));
            overflow: auto;
            overscroll-behavior: contain;
            scrollbar-width: thin;
            scrollbar-color: var(--hg-border-soft) transparent;
            padding: 16px;
            border: 1px solid var(--hg-border);
            border-radius: 8px;
            background: #070707;
            box-shadow: 0 30px 100px rgba(0,0,0,0.76), 0 0 0 1px rgba(255,255,255,0.025);
            color: var(--hg-fg-3);
            box-sizing: border-box;
          }
          .intel-atlas-guide::-webkit-scrollbar {
            width: 6px;
            height: 6px;
          }
          .intel-atlas-guide::-webkit-scrollbar-track {
            background: transparent;
          }
          .intel-atlas-guide::-webkit-scrollbar-thumb {
            background: var(--hg-border-soft);
            border-radius: 0;
          }
          .intel-atlas-guide::-webkit-scrollbar-thumb:hover {
            background: var(--hg-border);
          }
          .intel-atlas-guide header {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 16px;
            padding-bottom: 13px;
            border-bottom: 1px solid var(--hg-border-soft);
          }
          .intel-atlas-guide h2 {
            margin: 5px 0 0;
            color: var(--hg-fg-0);
            font-size: 18px;
            font-weight: 600;
            letter-spacing: 0;
          }
          .intel-atlas-guide p {
            margin: 7px 0 0;
            color: var(--hg-fg-3);
            font-size: 12px;
            line-height: 1.45;
          }
          .intel-atlas-guide-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 9px;
            margin-top: 13px;
          }
          .intel-atlas-guide-card {
            padding: 12px;
            border: 1px solid var(--hg-border-soft);
            border-radius: 7px;
            background: rgba(255,255,255,0.026);
          }
          .intel-atlas-guide-card.wide {
            grid-column: 1 / -1;
          }
          .intel-atlas-guide-card.muted {
            background: rgba(255,255,255,0.015);
          }
          .intel-atlas-guide-card h3 {
            margin: 0;
            color: var(--hg-fg-0);
            font-size: 12px;
            font-weight: 600;
            letter-spacing: 0;
          }
          .intel-atlas-guide-card ul {
            display: grid;
            gap: 7px;
            margin: 10px 0 0;
            padding: 0;
            list-style: none;
          }
          .intel-atlas-guide-card li {
            position: relative;
            padding-left: 12px;
            color: var(--hg-fg-3);
            font-size: 12px;
            line-height: 1.42;
          }
          .intel-atlas-guide-card li::before {
            content: "";
            position: absolute;
            left: 0;
            top: 0.6em;
            width: 4px;
            height: 4px;
            border-radius: 999px;
            background: var(--hg-ice-bright);
            box-shadow: 0 0 10px rgba(143,215,255,0.42);
          }
          .intel-atlas-guide-card li b {
            color: var(--hg-fg-0);
            font-weight: 600;
          }
          .intel-atlas-global-scrim {
            position: absolute;
            z-index: 215;
            inset: 92px 0 0;
            border: 0;
            padding: 0;
            background: rgba(0,0,0,0.34);
            cursor: default;
          }
          .intel-atlas-global-panel {
            position: absolute;
            z-index: 340;
            top: 108px;
            right: 28px;
            width: min(520px, calc(100vw - 360px));
            max-height: calc(100vh - 136px);
            overflow: auto;
            overscroll-behavior: contain;
            padding: 18px;
            border: 1px solid var(--hg-border);
            border-radius: 8px;
            background: rgba(7,7,7,0.985);
            box-shadow: 0 28px 90px rgba(0,0,0,0.7);
            scrollbar-width: thin;
            scrollbar-color: var(--hg-border-soft) transparent;
          }
          .intel-atlas-global-panel::-webkit-scrollbar {
            width: 6px;
            height: 6px;
          }
          .intel-atlas-global-panel::-webkit-scrollbar-track {
            background: transparent;
          }
          .intel-atlas-global-panel::-webkit-scrollbar-thumb {
            background: var(--hg-border-soft);
            border-radius: 0;
          }
          .intel-atlas-global-panel header {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 16px;
            padding-bottom: 13px;
            border-bottom: 1px solid var(--hg-border-soft);
          }
          .intel-atlas-global-panel h2 {
            margin: 4px 0 0;
            font-size: 18px;
            font-weight: 600;
            letter-spacing: 0;
          }
          .intel-atlas-global-panel p {
            margin: 7px 0 0;
            color: var(--hg-fg-3);
            font-size: 12px;
            line-height: 1.42;
          }
          .intel-atlas-global-body {
            display: grid;
            gap: 14px;
            margin-top: 14px;
          }
          .intel-atlas-global-row {
            display: grid;
            width: 100%;
            gap: 4px;
            padding: 9px 10px;
            border: 1px solid var(--hg-border-soft);
            border-radius: 6px;
            background: rgba(255,255,255,0.018);
            color: var(--hg-fg-2);
            text-align: left;
            cursor: pointer;
          }
          .intel-atlas-global-row.warn {
            border-color: rgba(255,185,77,0.22);
          }
          .intel-atlas-global-row:hover,
          .intel-atlas-global-row:focus-visible {
            border-color: var(--hg-border);
            background: rgba(255,255,255,0.045);
          }
          .intel-atlas-global-row span {
            font-size: 12px;
            font-weight: 600;
          }
          .intel-atlas-global-row small {
            color: var(--hg-fg-5);
            font-family: 'Geist Mono', ui-monospace, monospace;
            font-size: 9px;
          }
          .intel-atlas-actions,
          .intel-atlas-tags,
          .intel-atlas-pop-actions,
          .intel-atlas-pop-head-actions,
          .intel-atlas-rail,
          .intel-atlas-filters,
          .intel-atlas-review-actions,
          .intel-atlas-drawer-row {
            display: flex;
            gap: 7px;
            flex-wrap: wrap;
            align-items: center;
          }
          .intel-atlas-actions { margin-left: auto; justify-content: flex-end; }
          .intel-atlas-status-chip {
            position: relative;
            cursor: pointer;
          }
          .intel-atlas-status-chip::after {
            content: attr(data-tooltip);
            position: absolute;
            z-index: 30;
            right: 0;
            top: calc(100% + 8px);
            width: min(280px, calc(100vw - 40px));
            padding: 9px 10px;
            border: 1px solid var(--hg-border);
            border-radius: 6px;
            background: rgba(8,8,8,0.98);
            box-shadow: 0 22px 70px rgba(0,0,0,0.58);
            color: var(--hg-fg-2);
            font-family: 'Geist', system-ui, sans-serif;
            font-size: 11px;
            line-height: 1.35;
            letter-spacing: 0;
            text-transform: none;
            white-space: normal;
            opacity: 0;
            transform: translateY(-3px);
            pointer-events: none;
            transition: opacity 120ms ease, transform 120ms ease;
          }
          .intel-atlas-status-chip::before {
            content: "";
            position: absolute;
            z-index: 31;
            right: 14px;
            top: calc(100% + 3px);
            width: 8px;
            height: 8px;
            border-left: 1px solid var(--hg-border);
            border-top: 1px solid var(--hg-border);
            background: rgba(8,8,8,0.98);
            transform: rotate(45deg);
            opacity: 0;
            pointer-events: none;
            transition: opacity 120ms ease;
          }
          .intel-atlas-status-chip:hover::after,
          .intel-atlas-status-chip:focus-visible::after {
            opacity: 1;
            transform: translateY(0);
          }
          .intel-atlas-status-chip:hover::before,
          .intel-atlas-status-chip:focus-visible::before {
            opacity: 1;
          }
          .intel-atlas-button {
            min-height: 22px;
            border: 1px solid var(--hg-border-soft);
            border-radius: 3px;
            background: rgba(0, 0, 0, 0.48);
            color: var(--hg-fg-3);
            padding: 3px 8px;
            font-family: 'Geist Mono', ui-monospace, monospace;
            font-size: 10px;
            cursor: pointer;
          }
          .intel-atlas-button.active {
            color: var(--hg-fg-0);
            border-color: var(--hg-border-strong);
          }
          .intel-atlas-button:disabled {
            opacity: 0.36;
            cursor: default;
          }
          .intel-atlas-icon-button {
            width: 22px;
            height: 22px;
            display: grid;
            place-items: center;
            border: 1px solid var(--hg-border-soft);
            border-radius: 999px;
            background: rgba(0,0,0,0.55);
            color: var(--hg-fg-3);
            font-family: 'Geist Mono', ui-monospace, monospace;
            font-size: 11px;
            cursor: pointer;
          }
          .intel-atlas-pop-head-actions {
            margin-left: auto;
            flex-wrap: nowrap;
          }
          .intel-atlas-kicker,
          .intel-atlas-day,
          .intel-atlas-label,
          .intel-atlas-region span,
          .intel-atlas-frame span,
          .intel-atlas-facts span {
            font-family: 'Geist Mono', ui-monospace, monospace;
            font-size: 10px;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            color: var(--hg-fg-5);
          }
          .intel-atlas-timeline {
            position: absolute;
            z-index: 6;
            left: 22px;
            top: 118px;
            bottom: 28px;
            width: 252px;
            padding: 14px;
            border: 1px solid var(--hg-border-soft);
            border-radius: 8px;
            background: rgba(5, 5, 5, 0.68);
            overflow: auto;
            scrollbar-width: thin;
            scrollbar-color: var(--hg-border-soft) transparent;
          }
          .intel-atlas-timeline::-webkit-scrollbar,
          .intel-atlas-popover::-webkit-scrollbar,
          .intel-atlas-drawer::-webkit-scrollbar,
          .intel-atlas-drawer-body::-webkit-scrollbar {
            width: 6px;
            height: 6px;
          }
          .intel-atlas-timeline::-webkit-scrollbar-track,
          .intel-atlas-popover::-webkit-scrollbar-track,
          .intel-atlas-drawer::-webkit-scrollbar-track,
          .intel-atlas-drawer-body::-webkit-scrollbar-track {
            background: transparent;
          }
          .intel-atlas-timeline::-webkit-scrollbar-thumb,
          .intel-atlas-popover::-webkit-scrollbar-thumb,
          .intel-atlas-drawer::-webkit-scrollbar-thumb,
          .intel-atlas-drawer-body::-webkit-scrollbar-thumb {
            background: var(--hg-border-soft);
            border-radius: 0;
          }
          .intel-atlas-timeline::-webkit-scrollbar-thumb:hover,
          .intel-atlas-popover::-webkit-scrollbar-thumb:hover,
          .intel-atlas-drawer::-webkit-scrollbar-thumb:hover,
          .intel-atlas-drawer-body::-webkit-scrollbar-thumb:hover {
            background: var(--hg-border);
          }
          .intel-atlas-timeline::-webkit-scrollbar-corner,
          .intel-atlas-popover::-webkit-scrollbar-corner,
          .intel-atlas-drawer::-webkit-scrollbar-corner,
          .intel-atlas-drawer-body::-webkit-scrollbar-corner {
            background: transparent;
          }
          .intel-atlas-timeline-head {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 14px;
          }
          .intel-atlas-filters { margin-bottom: 18px; }
          .intel-atlas-review-guide {
            display: grid;
            gap: 5px;
            margin: -4px 0 14px;
            padding: 8px;
            border: 1px solid rgba(255,255,255,0.07);
            border-radius: 6px;
            background: rgba(255,255,255,0.025);
            color: var(--hg-fg-4);
            font-size: 11px;
            line-height: 1.35;
          }
          .intel-atlas-day { margin: 18px 0 8px; }
          .intel-atlas-event {
            position: relative;
            width: 100%;
            text-align: left;
            border: 1px solid transparent;
            border-radius: 7px;
            background: transparent;
            color: var(--hg-fg-0);
            padding: 10px 10px 10px 24px;
            margin: 0 0 8px;
            cursor: pointer;
          }
          .intel-atlas-event::before {
            content: "";
            position: absolute;
            left: 9px;
            top: 17px;
            width: 7px;
            height: 7px;
            border-radius: 999px;
            background: var(--hg-blue);
          }
          .intel-atlas-event.selected {
            background: rgba(255, 255, 255, 0.045);
            border-color: rgba(221, 234, 255, 0.72);
          }
          .intel-atlas-event.needs-review {
            border-color: rgba(245, 158, 11, 0.25);
          }
          .intel-atlas-event.warn::before { background: var(--hg-warn); }
          .intel-atlas-event.muted::before { background: var(--hg-fg-5); }
          .intel-atlas-event-top {
            display: flex;
            justify-content: space-between;
            gap: 10px;
            margin-bottom: 7px;
            font-size: 12px;
          }
          .intel-atlas-event-top strong {
            color: var(--hg-fg-2);
            font-weight: 500;
          }
          .intel-atlas-event-top span {
            color: var(--hg-fg-4);
            font-size: 11px;
          }
          .intel-atlas-event .intel-atlas-tags span {
            border: 1px solid var(--hg-border-soft);
            border-radius: 3px;
            padding: 2px 5px;
            color: var(--hg-fg-4);
            font-family: 'Geist Mono', ui-monospace, monospace;
            font-size: 9px;
          }
          .intel-atlas-stage {
            position: absolute;
            left: 292px;
            right: 0;
            top: 92px;
            bottom: 0;
          }
          .intel-atlas-projection {
            position: absolute;
            left: 22px;
            top: 18px;
            z-index: 5;
            display: flex;
            align-items: center;
            gap: 7px;
            max-width: calc(100% - 44px);
            padding: 6px 8px;
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 6px;
            background: rgba(0,0,0,0.46);
            box-shadow: 0 14px 44px rgba(0,0,0,0.26);
            color: var(--hg-fg-3);
            font-family: 'Geist Mono', ui-monospace, monospace;
            font-size: 9.5px;
            line-height: 1;
            white-space: nowrap;
            pointer-events: auto;
          }
          .intel-atlas-projection span,
          .intel-atlas-projection em,
          .intel-atlas-projection b {
            color: var(--hg-fg-5);
            font-style: normal;
            font-weight: 500;
          }
          .intel-atlas-projection strong {
            color: var(--hg-fg-1);
            font-weight: 650;
          }
          .intel-atlas-projection b {
            color: var(--hg-warn);
          }
          .intel-atlas-region {
            position: absolute;
            border: 1px solid rgba(255,255,255,0.11);
            border-radius: 999px;
            pointer-events: none;
          }
          .intel-atlas-region span,
          .intel-atlas-label {
            position: absolute;
            padding: 4px 7px;
            border: 1px solid rgba(255,255,255,0.12);
            border-radius: 999px;
            background: rgba(255,255,255,0.045);
            color: rgba(255,255,255,0.58);
            text-shadow:
              0 0 10px rgba(255,255,255,0.22),
              0 0 24px rgba(143,215,255,0.12);
            box-shadow:
              inset 0 0 0 1px rgba(255,255,255,0.025),
              0 8px 26px rgba(0,0,0,0.22);
            backdrop-filter: blur(8px);
          }
          .region-work { left: 33%; top: 20%; width: 210px; height: 130px; }
          .region-tv { right: 22%; top: 34%; width: 220px; height: 142px; }
          .region-kitchen { left: 36%; bottom: 13%; width: 230px; height: 138px; }
          .region-work span { left: 20px; top: 28px; }
          .region-tv span { right: -90px; top: -42px; }
          .region-kitchen span { left: -16px; top: 60px; }
          .label-reading { left: 22%; top: 36%; }
          .label-pass { right: 18%; bottom: 14%; }
          .intel-atlas-point {
            position: absolute;
            z-index: 2;
            border-radius: 999px;
            transform: translate(-50%, -50%);
            border: 1px solid rgba(255,255,255,0.92);
            background: rgba(255,255,255,0.96);
            box-shadow:
              0 0 0 1px rgba(255,255,255,0.22),
              0 0 7px rgba(255,255,255,0.88),
              0 0 20px currentColor,
              0 0 44px rgba(255,255,255,0.16);
            padding: 0;
            cursor: pointer;
            transition: transform 140ms ease, box-shadow 140ms ease, opacity 140ms ease;
          }
          .intel-atlas-point::after {
            content: "";
            position: absolute;
            inset: -7px;
            border-radius: inherit;
            border: 1px solid currentColor;
            opacity: 0.24;
            pointer-events: none;
          }
          .intel-atlas-point.selected {
            transform: translate(-50%, -50%) scale(1.08);
            box-shadow:
              0 0 0 7px rgba(255,255,255,0.12),
              0 0 12px rgba(255,255,255,0.96),
              0 0 32px currentColor,
              0 0 76px rgba(255,255,255,0.22);
          }
          .intel-atlas-point.linked::after {
            opacity: 0.38;
          }
          .intel-atlas-point.privacy {
            border-style: dashed;
            background: rgba(255,255,255,0.82);
          }
          .intel-atlas-point.privacy::after {
            border-style: dashed;
            opacity: 0.52;
          }
          .intel-atlas-point.human::before {
            content: "";
            position: absolute;
            right: -3px;
            top: -3px;
            width: 6px;
            height: 6px;
            border-radius: 999px;
            background: #fff;
            box-shadow: 0 0 8px rgba(255,255,255,0.82);
          }
          .intel-atlas-connector {
            position: absolute;
            z-index: 1;
            height: 1px;
            background: var(--hg-border);
            transform-origin: left center;
          }
          .intel-atlas-popover {
            position: absolute;
            z-index: 7;
            box-sizing: border-box;
            width: min(316px, calc(100vw - 48px));
            max-height: calc(100% - 24px);
            overflow-y: auto;
            overscroll-behavior: contain;
            scrollbar-gutter: stable;
            scrollbar-width: thin;
            scrollbar-color: var(--hg-border-soft) transparent;
            border: 1px solid var(--hg-border);
            border-radius: 8px;
            background: rgba(7, 7, 7, 0.97);
            box-shadow: 0 26px 90px rgba(0, 0, 0, 0.62);
            padding: 12px;
          }
          .intel-atlas-popover header {
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            gap: 10px;
            margin-bottom: 9px;
          }
          .intel-atlas-popover strong {
            font-size: 13px;
            font-weight: 500;
          }
          .intel-atlas-change {
            margin-top: 10px;
            padding: 9px;
            border: 1px solid rgba(255,255,255,0.11);
            border-radius: 6px;
            background: rgba(255,255,255,0.035);
          }
          .intel-atlas-change-head {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            margin-bottom: 7px;
          }
          .intel-atlas-change > strong {
            display: block;
            color: var(--hg-fg-1);
            font-size: 12px;
            font-weight: 500;
          }
          .intel-atlas-change-flow {
            display: grid;
            grid-template-columns: minmax(0, 1fr) 18px minmax(0, 1fr);
            gap: 6px;
            align-items: center;
            margin: 8px 0;
          }
          .intel-atlas-change-flow div {
            min-width: 0;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 4px;
            padding: 6px;
            background: rgba(0,0,0,0.2);
          }
          .intel-atlas-change-flow span {
            display: block;
            margin-bottom: 3px;
            color: var(--hg-fg-5);
            font-family: 'Geist Mono', ui-monospace, monospace;
            font-size: 8.5px;
            text-transform: uppercase;
          }
          .intel-atlas-change-flow b {
            display: block;
            color: #fff;
            font-size: 12px;
            font-weight: 500;
            white-space: normal;
            overflow-wrap: anywhere;
          }
          .intel-atlas-change-flow em {
            color: var(--hg-ice-bright);
            font-family: 'Geist Mono', ui-monospace, monospace;
            font-size: 11px;
            font-style: normal;
            text-align: center;
          }
          .intel-atlas-change p {
            color: var(--hg-fg-4);
            font-size: 10.5px;
          }
          .intel-atlas-context-snapshot {
            display: grid;
            gap: 7px;
            margin-top: 10px;
            padding: 9px;
            border: 1px solid rgba(255,255,255,0.085);
            border-radius: 6px;
            background: rgba(255,255,255,0.022);
          }
          .intel-atlas-context-snapshot-head,
          .intel-atlas-context-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
          }
          .intel-atlas-context-row {
            align-items: flex-start;
          }
          .intel-atlas-context-row > span {
            flex: 0 0 54px;
            color: var(--hg-fg-5);
            font-family: 'Geist Mono', ui-monospace, monospace;
            font-size: 9px;
            letter-spacing: 0.12em;
            text-transform: uppercase;
          }
          .intel-atlas-context-row > div {
            min-width: 0;
            display: flex;
            flex-wrap: wrap;
            justify-content: flex-end;
            gap: 5px;
          }
          .intel-atlas-context-row b {
            max-width: 145px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            border: 1px solid var(--hg-border-soft);
            border-radius: 3px;
            padding: 2px 5px;
            color: var(--hg-fg-3);
            font-family: 'Geist Mono', ui-monospace, monospace;
            font-size: 9px;
            font-weight: 500;
          }
          .intel-atlas-frames {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(82px, 1fr));
            gap: 6px;
            margin: 10px 0;
          }
          .intel-atlas-frames.drawer {
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }
          .intel-atlas-frame {
            position: relative;
            display: block;
            width: 100%;
            min-height: 70px;
            border: 1px solid var(--hg-border-soft);
            border-radius: 4px;
            overflow: hidden;
            background: var(--hg-bg-1);
            padding: 0;
            text-align: left;
            cursor: pointer;
          }
          .intel-atlas-frame:hover {
            border-color: rgba(255,255,255,0.38);
          }
          .intel-atlas-frame img {
            display: block;
            width: 100%;
            aspect-ratio: 4 / 3;
            object-fit: cover;
          }
          .intel-atlas-frame div {
            height: 70px;
            display: grid;
            place-items: center;
            color: var(--hg-fg-5);
            font-size: 10px;
          }
          .intel-atlas-frame.empty {
            cursor: default;
          }
          .intel-atlas-frame .intel-atlas-frame-empty-copy {
            height: auto;
            min-height: 84px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: flex-start;
            gap: 5px;
            padding: 10px;
            color: var(--hg-fg-4);
            text-align: left;
          }
          .intel-atlas-frame-empty-copy strong,
          .intel-atlas-empty-frames-inline b {
            color: var(--hg-fg-2);
            font-size: 11px;
            font-weight: 500;
          }
          .intel-atlas-frame-empty-copy span,
          .intel-atlas-empty-frames-inline span {
            color: var(--hg-fg-5);
            font-size: 10.5px;
            line-height: 1.35;
          }
          .intel-atlas-empty-frames-inline {
            display: flex;
            flex-direction: column;
            gap: 4px;
            min-height: 74px;
            justify-content: center;
            border: 1px solid var(--hg-border-soft);
            border-radius: 4px;
            padding: 10px;
            background: rgba(255,255,255,0.03);
          }
          .intel-atlas-frame > span {
            position: absolute;
            left: 5px;
            bottom: 4px;
            color: rgba(255,255,255,0.55);
            font-size: 8px;
          }
          .intel-atlas-popover p,
          .intel-atlas-muted,
          .intel-atlas-drawer p {
            margin: 0;
            color: var(--hg-fg-3);
            font-size: 12px;
            line-height: 1.45;
          }
          .intel-atlas-review-panel,
          .intel-atlas-review-idle {
            margin-top: 10px;
            border-top: 1px solid var(--hg-border-soft);
            padding-top: 10px;
          }
          .intel-atlas-review-head,
          .intel-atlas-review-idle,
          .intel-atlas-undo {
            display: flex;
            justify-content: space-between;
            gap: 8px;
            align-items: center;
          }
          .intel-atlas-review-idle.needs {
            align-items: flex-start;
            padding: 9px;
            border: 1px solid rgba(245,158,11,0.22);
            border-radius: 6px;
            background: rgba(245,158,11,0.045);
          }
          .intel-atlas-review-idle span {
            color: var(--hg-fg-2);
            font-size: 12px;
            font-weight: 600;
          }
          .intel-atlas-review-idle small {
            display: block;
            margin-top: 3px;
            color: var(--hg-fg-4);
            font-size: 10.5px;
            font-weight: 400;
            line-height: 1.35;
          }
          .intel-atlas-review-copy {
            display: grid;
            gap: 4px;
            margin: 8px 0;
            color: var(--hg-fg-4);
            font-size: 11px;
            line-height: 1.35;
          }
          .intel-atlas-review-source {
            display: flex;
            gap: 7px;
            flex-wrap: wrap;
            margin: 8px 0;
            color: var(--hg-fg-5);
            font-family: 'Geist Mono', ui-monospace, monospace;
            font-size: 9px;
          }
          .intel-atlas-activity-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 5px;
            margin: 8px 0;
          }
          .intel-atlas-review-actions {
            margin-top: 6px;
          }
          .intel-atlas-undo {
            margin-top: 9px;
            padding: 7px;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 5px;
            color: var(--hg-fg-3);
            font-size: 11px;
          }
          .intel-atlas-facts {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 8px;
            margin-top: 10px;
          }
          .intel-atlas-facts div {
            border-top: 1px solid var(--hg-border-soft);
            padding-top: 7px;
          }
          .intel-atlas-facts b {
            display: block;
            margin-top: 2px;
            color: var(--hg-fg-2);
            font-size: 12px;
            font-weight: 500;
            white-space: nowrap;
          }
          .intel-atlas-pop-actions { margin-top: 11px; }
          .intel-atlas-expanded-scrim {
            position: absolute;
            z-index: 8;
            inset: 0;
            border: 0;
            padding: 0;
            background: rgba(0,0,0,0.34);
            cursor: default;
          }
          .intel-atlas-expanded-card {
            position: absolute;
            z-index: 9;
            right: 28px;
            top: 28px;
            width: min(640px, calc(100% - 56px));
            max-height: calc(100vh - 140px);
            display: flex;
            flex-direction: column;
            overflow: hidden;
            border: 1px solid var(--hg-border);
            border-radius: 10px;
            background: rgba(8, 8, 8, 0.98);
            box-shadow: 0 30px 110px rgba(0,0,0,0.72), 0 0 0 1px rgba(255,255,255,0.025);
          }
          .intel-atlas-expanded-head {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 14px;
            padding: 16px 16px 12px;
            border-bottom: 1px solid var(--hg-border-soft);
          }
          .intel-atlas-expanded-head h2 {
            margin: 4px 0 0;
            color: var(--hg-fg-0);
            font-size: 18px;
            font-weight: 600;
            letter-spacing: 0;
          }
          .intel-atlas-expanded-head p {
            margin: 6px 0 0;
            max-width: 480px;
            color: var(--hg-fg-4);
            font-size: 12px;
            line-height: 1.42;
          }
          .intel-atlas-expanded-tabs {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 7px;
            padding: 12px 16px 0;
          }
          .intel-atlas-expanded-tab {
            min-width: 0;
            display: grid;
            gap: 3px;
            min-height: 48px;
            padding: 8px 9px;
            border: 1px solid var(--hg-border-soft);
            border-radius: 6px;
            background: rgba(255,255,255,0.018);
            color: var(--hg-fg-3);
            text-align: left;
            cursor: pointer;
          }
          .intel-atlas-expanded-tab:hover,
          .intel-atlas-expanded-tab:focus-visible {
            border-color: var(--hg-border);
            background: rgba(255,255,255,0.04);
            color: var(--hg-fg-1);
          }
          .intel-atlas-expanded-tab.active {
            border-color: rgba(255,255,255,0.34);
            background: rgba(255,255,255,0.075);
            color: var(--hg-fg-0);
          }
          .intel-atlas-expanded-tab span {
            font-size: 12px;
            font-weight: 650;
          }
          .intel-atlas-expanded-tab small {
            color: var(--hg-fg-5);
            font-family: 'Geist Mono', ui-monospace, monospace;
            font-size: 9px;
            line-height: 1.2;
          }
          .intel-atlas-expanded-context {
            display: grid;
            gap: 3px;
            margin: 10px 16px 0;
            padding: 9px 10px;
            border: 1px solid var(--hg-border-soft);
            border-radius: 6px;
            background: rgba(255,255,255,0.024);
          }
          .intel-atlas-expanded-context strong {
            color: var(--hg-fg-1);
            font-size: 12px;
            font-weight: 600;
          }
          .intel-atlas-expanded-context span {
            color: var(--hg-fg-4);
            font-size: 11px;
            line-height: 1.35;
          }
          .intel-atlas-expanded-body {
            overflow: auto;
            padding: 0 16px 16px;
            scrollbar-width: thin;
            scrollbar-color: var(--hg-border-soft) transparent;
          }
          .intel-atlas-expanded-body::-webkit-scrollbar {
            width: 6px;
            height: 6px;
          }
          .intel-atlas-expanded-body::-webkit-scrollbar-track {
            background: transparent;
          }
          .intel-atlas-expanded-body::-webkit-scrollbar-thumb {
            background: var(--hg-border-soft);
            border-radius: 0;
          }
          .intel-atlas-expanded-frame-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 9px;
          }
          .intel-atlas-expanded-frame {
            position: relative;
            display: block;
            width: 100%;
            min-height: 160px;
            border: 1px solid var(--hg-border-soft);
            border-radius: 6px;
            overflow: hidden;
            background: var(--hg-bg-1);
            padding: 0;
            cursor: pointer;
          }
          .intel-atlas-expanded-frame:hover,
          .intel-atlas-expanded-frame:focus-visible {
            border-color: rgba(255,255,255,0.42);
          }
          .intel-atlas-expanded-frame img {
            display: block;
            width: 100%;
            aspect-ratio: 4 / 3;
            object-fit: cover;
          }
          .intel-atlas-expanded-frame div {
            min-height: 160px;
            display: grid;
            place-items: center;
            color: var(--hg-fg-5);
            font-size: 11px;
          }
          .intel-atlas-expanded-frame span {
            position: absolute;
            left: 7px;
            bottom: 6px;
            color: rgba(255,255,255,0.7);
            font-family: 'Geist Mono', ui-monospace, monospace;
            font-size: 9px;
            text-transform: uppercase;
          }
          .intel-atlas-expanded-actions {
            display: flex;
            gap: 7px;
            flex-wrap: wrap;
            margin-top: 10px;
          }
          .intel-atlas-primary-action {
            min-height: 30px;
            border: 1px solid rgba(255,185,77,0.75);
            border-radius: 5px;
            background: rgba(245,158,11,0.12);
            color: var(--hg-warn);
            padding: 6px 10px;
            font-family: 'Geist Mono', ui-monospace, monospace;
            font-size: 10px;
            font-weight: 650;
            cursor: pointer;
          }
          .intel-atlas-advanced-raw {
            margin-top: 16px;
            border-top: 1px solid var(--hg-border-soft);
            padding-top: 12px;
          }
          .intel-atlas-advanced-raw summary {
            cursor: pointer;
            color: var(--hg-fg-3);
            font-family: 'Geist Mono', ui-monospace, monospace;
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 0.1em;
          }
          .intel-atlas-rail {
            position: absolute;
            z-index: 6;
            right: 24px;
            bottom: 22px;
          }
          .intel-atlas-empty {
            position: absolute;
            inset: 0;
            display: grid;
            place-items: center;
            color: var(--hg-fg-5);
            font-size: 12px;
          }
          .intel-atlas-lightbox {
            position: absolute;
            z-index: 12;
            inset: 0;
            display: grid;
            place-items: center;
            padding: 28px;
            background: rgba(0,0,0,0.72);
          }
          .intel-atlas-lightbox-card {
            width: min(920px, calc(100vw - 56px));
            max-height: calc(100vh - 56px);
            display: flex;
            flex-direction: column;
            gap: 10px;
            border: 1px solid var(--hg-border);
            border-radius: 8px;
            background: rgba(6,6,6,0.98);
            box-shadow: 0 28px 120px rgba(0,0,0,0.72);
            padding: 12px;
          }
          .intel-atlas-lightbox-card img {
            width: 100%;
            max-height: calc(100vh - 190px);
            object-fit: contain;
            background: #000;
            border-radius: 5px;
          }
          .intel-atlas-lightbox-head,
          .intel-atlas-lightbox-actions {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
          }
          .intel-atlas-lightbox-head strong {
            display: block;
            font-size: 13px;
            font-weight: 500;
          }
          .intel-atlas-lightbox-head span,
          .intel-atlas-lightbox-warning,
          .intel-atlas-lightbox-missing {
            color: var(--hg-fg-4);
            font-size: 11px;
          }
          .intel-atlas-lightbox-warning {
            border: 1px solid rgba(245, 158, 11, 0.25);
            border-radius: 5px;
            padding: 7px;
            color: var(--hg-warn);
          }
          .intel-atlas-lightbox-missing {
            min-height: 320px;
            display: grid;
            place-items: center;
            border: 1px solid var(--hg-border-soft);
            border-radius: 5px;
          }
          .intel-atlas-drawer {
            position: absolute;
            z-index: 9;
            top: 92px;
            right: 0;
            bottom: 0;
            width: 360px;
            border-left: 1px solid var(--hg-border);
            background: rgba(5, 5, 5, 0.98);
            box-shadow: -34px 0 90px rgba(0, 0, 0, 0.64);
            padding: 18px;
            transform: translateX(100%);
            opacity: 0;
            pointer-events: none;
            transition: transform 220ms cubic-bezier(0.16, 1, 0.3, 1), opacity 180ms ease;
            overflow: auto;
            scrollbar-width: thin;
            scrollbar-color: var(--hg-border-soft) transparent;
          }
          .intel-atlas-drawer.open {
            transform: translateX(0);
            opacity: 1;
            pointer-events: auto;
          }
          .intel-atlas-drawer-head {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 10px;
            margin-bottom: 8px;
          }
          .intel-atlas-drawer-head h2 {
            margin: 3px 0 0;
            font-size: 17px;
            font-weight: 600;
            letter-spacing: 0;
          }
          .intel-atlas-drawer-deck {
            margin: 0 0 13px;
            color: var(--hg-fg-3);
            font-size: 12px;
            line-height: 1.42;
          }
          .intel-atlas-drawer-tabs {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 7px;
            margin: 12px 0;
          }
          .intel-atlas-drawer-tab {
            display: grid;
            gap: 3px;
            min-height: 48px;
            padding: 8px 9px;
            border: 1px solid var(--hg-border-soft);
            border-radius: 6px;
            background: rgba(255,255,255,0.018);
            color: var(--hg-fg-3);
            text-align: left;
            cursor: pointer;
          }
          .intel-atlas-drawer-tab:hover,
          .intel-atlas-drawer-tab:focus-visible {
            border-color: var(--hg-border);
            background: rgba(255,255,255,0.04);
            color: var(--hg-fg-1);
          }
          .intel-atlas-drawer-tab.active {
            border-color: rgba(255,255,255,0.32);
            background: rgba(255,255,255,0.07);
            color: var(--hg-fg-0);
          }
          .intel-atlas-drawer-tab span {
            font-size: 12px;
            font-weight: 600;
            letter-spacing: 0;
          }
          .intel-atlas-drawer-tab small {
            color: var(--hg-fg-5);
            font-family: 'Geist Mono', ui-monospace, monospace;
            font-size: 9px;
            line-height: 1.2;
          }
          .intel-atlas-drawer-context {
            display: grid;
            gap: 4px;
            margin: 0 0 14px;
            padding: 10px;
            border: 1px solid var(--hg-border-soft);
            border-radius: 6px;
            background: rgba(255,255,255,0.024);
          }
          .intel-atlas-drawer-context strong {
            color: var(--hg-fg-1);
            font-size: 12px;
            font-weight: 600;
          }
          .intel-atlas-drawer-context span {
            color: var(--hg-fg-4);
            font-size: 11px;
            line-height: 1.35;
          }
          .intel-atlas-section {
            border-top: 1px solid var(--hg-border-soft);
            padding-top: 14px;
            margin-top: 15px;
          }
          .intel-atlas-section h3 {
            margin: 0 0 8px;
            font-family: 'Geist Mono', ui-monospace, monospace;
            color: var(--hg-fg-5);
            font-size: 10px;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            font-weight: 500;
          }
          .intel-atlas-drawer-body {
            display: flex;
            flex-direction: column;
            scrollbar-width: thin;
            scrollbar-color: var(--hg-border-soft) transparent;
          }
          .intel-atlas-drawer-row {
            border-top: 1px solid var(--hg-border-soft);
            padding-top: 7px;
            margin-top: 7px;
            color: var(--hg-fg-3);
            font-size: 11px;
          }
          .intel-atlas-stat-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
          }
          .intel-atlas-axis-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 8px;
          }
          .intel-atlas-axis {
            border-top: 1px solid var(--hg-border-soft);
            padding-top: 7px;
            min-width: 0;
          }
          .intel-atlas-axis span {
            display: block;
            color: var(--hg-fg-5);
            font-family: 'Geist Mono', ui-monospace, monospace;
            font-size: 9px;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            overflow: hidden;
            text-overflow: ellipsis;
          }
          .intel-atlas-axis b {
            display: block;
            margin-top: 3px;
            color: var(--hg-fg-2);
            font-size: 11px;
            font-weight: 500;
            overflow: hidden;
            text-overflow: ellipsis;
          }
          [data-theme="light"] .intel-atlas {
            background:
              radial-gradient(circle at 56% 40%, rgba(255, 255, 255, 0.58), transparent 34%),
              radial-gradient(circle at 78% 72%, rgba(31, 79, 168, 0.07), transparent 28%),
              var(--hg-bg-0);
            color: var(--hg-fg-0);
          }
          [data-theme="light"] .intel-atlas::before {
            background:
              linear-gradient(90deg, rgba(26, 24, 18, 0.035) 1px, transparent 1px),
              linear-gradient(0deg, rgba(26, 24, 18, 0.028) 1px, transparent 1px);
            background-size: 96px 96px;
            opacity: 0.5;
          }
          [data-theme="light"] .intel-atlas-top {
            background:
              linear-gradient(to bottom, rgba(239, 233, 220, 0.98) 0%, rgba(239, 233, 220, 0.94) 72%, rgba(239, 233, 220, 0) 100%);
            border-bottom: 1px solid rgba(26, 24, 18, 0.08);
            box-shadow: 0 18px 46px rgba(76, 62, 32, 0.12);
            backdrop-filter: blur(18px);
          }
          [data-theme="light"] .intel-atlas-title p {
            color: var(--hg-fg-2);
          }
          [data-theme="light"] .intel-atlas-guide-scrim,
          [data-theme="light"] .intel-atlas-global-scrim,
          [data-theme="light"] .intel-atlas-expanded-scrim {
            background: rgba(239, 233, 220, 0.54);
            backdrop-filter: blur(2px);
          }
          [data-theme="light"] .intel-atlas-guide,
          [data-theme="light"] .intel-atlas-global-panel,
          [data-theme="light"] .intel-atlas-popover,
          [data-theme="light"] .intel-atlas-expanded-card,
          [data-theme="light"] .intel-atlas-lightbox-card {
            background: rgba(247, 242, 229, 0.96);
            border-color: rgba(26, 24, 18, 0.14);
            box-shadow:
              0 22px 70px rgba(76, 62, 32, 0.16),
              0 0 0 1px rgba(255, 255, 255, 0.48);
          }
          [data-theme="light"] .intel-atlas-popover,
          [data-theme="light"] .intel-atlas-expanded-card {
            backdrop-filter: blur(18px);
          }
          [data-theme="light"] .intel-atlas-timeline {
            background: rgba(247, 242, 229, 0.76);
            border-color: rgba(26, 24, 18, 0.12);
            box-shadow:
              0 18px 50px rgba(76, 62, 32, 0.10),
              inset 0 1px 0 rgba(255, 255, 255, 0.58);
            backdrop-filter: blur(18px);
            scrollbar-color: rgba(26, 24, 18, 0.22) transparent;
          }
          [data-theme="light"] .intel-atlas-timeline::-webkit-scrollbar-thumb,
          [data-theme="light"] .intel-atlas-popover::-webkit-scrollbar-thumb,
          [data-theme="light"] .intel-atlas-expanded-card::-webkit-scrollbar-thumb,
          [data-theme="light"] .intel-atlas-drawer::-webkit-scrollbar-thumb,
          [data-theme="light"] .intel-atlas-drawer-body::-webkit-scrollbar-thumb {
            background: rgba(26, 24, 18, 0.18);
          }
          [data-theme="light"] .intel-atlas-timeline::-webkit-scrollbar-thumb:hover,
          [data-theme="light"] .intel-atlas-popover::-webkit-scrollbar-thumb:hover,
          [data-theme="light"] .intel-atlas-expanded-card::-webkit-scrollbar-thumb:hover,
          [data-theme="light"] .intel-atlas-drawer::-webkit-scrollbar-thumb:hover,
          [data-theme="light"] .intel-atlas-drawer-body::-webkit-scrollbar-thumb:hover {
            background: rgba(26, 24, 18, 0.30);
          }
          [data-theme="light"] .intel-atlas-button,
          [data-theme="light"] .intel-atlas-icon-button,
          [data-theme="light"] .intel-atlas-info {
            background: rgba(255, 255, 255, 0.42);
            border-color: rgba(26, 24, 18, 0.12);
            color: var(--hg-fg-2);
          }
          [data-theme="light"] .intel-atlas-button:hover,
          [data-theme="light"] .intel-atlas-button.active,
          [data-theme="light"] .intel-atlas-icon-button:hover,
          [data-theme="light"] .intel-atlas-info:hover {
            background: rgba(255, 255, 255, 0.72);
            border-color: rgba(26, 24, 18, 0.24);
            color: var(--hg-fg-0);
          }
          [data-theme="light"] .intel-atlas-status-chip::after,
          [data-theme="light"] .intel-atlas-status-chip::before {
            background: rgba(247, 242, 229, 0.98);
            border-color: rgba(26, 24, 18, 0.12);
            color: var(--hg-fg-1);
            box-shadow: 0 14px 38px rgba(76, 62, 32, 0.16);
          }
          [data-theme="light"] .intel-atlas-projection {
            background: rgba(247, 242, 229, 0.82);
            border-color: rgba(26, 24, 18, 0.10);
            color: var(--hg-fg-2);
            box-shadow: 0 14px 36px rgba(76, 62, 32, 0.10);
          }
          [data-theme="light"] .intel-atlas-projection b {
            color: var(--hg-fg-0);
          }
          [data-theme="light"] .intel-atlas-region {
            border-color: rgba(26, 24, 18, 0.09);
            background: rgba(255, 255, 255, 0.12);
          }
          [data-theme="light"] .intel-atlas-region span,
          [data-theme="light"] .intel-atlas-label {
            background: rgba(255, 255, 255, 0.66);
            border-color: rgba(26, 24, 18, 0.12);
            color: rgba(26, 24, 18, 0.74);
            text-shadow: none;
            box-shadow: 0 10px 26px rgba(76, 62, 32, 0.12);
          }
          [data-theme="light"] .intel-atlas-point {
            background: #fff;
            border-color: rgba(255, 255, 255, 0.96);
            box-shadow:
              0 0 0 1px rgba(26, 24, 18, 0.16),
              0 0 10px rgba(255, 255, 255, 0.95),
              0 0 30px currentColor,
              0 14px 34px rgba(76, 62, 32, 0.18);
          }
          [data-theme="light"] .intel-atlas-point::after {
            opacity: 0.36;
            border-color: currentColor;
          }
          [data-theme="light"] .intel-atlas-point.selected {
            box-shadow:
              0 0 0 7px rgba(255, 255, 255, 0.66),
              0 0 0 9px rgba(26, 24, 18, 0.10),
              0 0 40px currentColor,
              0 18px 50px rgba(76, 62, 32, 0.22);
          }
          [data-theme="light"] .intel-atlas-point.human::before {
            background: var(--hg-blue);
            box-shadow: 0 0 8px rgba(31, 79, 168, 0.45);
          }
          [data-theme="light"] .intel-atlas-event {
            color: var(--hg-fg-1);
          }
          [data-theme="light"] .intel-atlas-event.selected {
            background: rgba(255, 255, 255, 0.58);
            border-color: rgba(26, 24, 18, 0.36);
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.62);
          }
          [data-theme="light"] .intel-atlas-event.needs-review {
            border-color: rgba(185, 106, 29, 0.36);
          }
          [data-theme="light"] .intel-atlas-badge,
          [data-theme="light"] .intel-atlas-tag,
          [data-theme="light"] .intel-atlas-chip,
          [data-theme="light"] .intel-atlas-pill {
            background: rgba(255, 255, 255, 0.44);
            border-color: rgba(26, 24, 18, 0.10);
            color: var(--hg-fg-2);
          }
          [data-theme="light"] .intel-atlas-change,
          [data-theme="light"] .intel-atlas-context-snapshot,
          [data-theme="light"] .intel-atlas-review-idle.needs,
          [data-theme="light"] .intel-atlas-empty-frames-inline,
          [data-theme="light"] .intel-atlas-expanded-context,
          [data-theme="light"] .intel-atlas-guide-card,
          [data-theme="light"] .intel-atlas-global-row,
          [data-theme="light"] .intel-atlas-drawer-context,
          [data-theme="light"] .intel-atlas-review-guide {
            background: rgba(255, 255, 255, 0.40);
            border-color: rgba(26, 24, 18, 0.09);
          }
          [data-theme="light"] .intel-atlas-change-flow div {
            background: rgba(255, 255, 255, 0.56);
            border-color: rgba(26, 24, 18, 0.10);
          }
          [data-theme="light"] .intel-atlas-change-flow b {
            color: var(--hg-fg-0);
          }
          [data-theme="light"] .intel-atlas-change-flow em {
            color: var(--hg-blue);
          }
          [data-theme="light"] .intel-atlas-frame,
          [data-theme="light"] .intel-atlas-expanded-frame {
            background: rgba(255, 255, 255, 0.42);
            border-color: rgba(26, 24, 18, 0.12);
          }
          [data-theme="light"] .intel-atlas-frame:hover,
          [data-theme="light"] .intel-atlas-expanded-frame:hover {
            border-color: rgba(31, 79, 168, 0.40);
          }
          [data-theme="light"] .intel-atlas-frame > span,
          [data-theme="light"] .intel-atlas-expanded-frame span {
            color: rgba(26, 24, 18, 0.68);
            text-shadow: 0 1px 2px rgba(255, 255, 255, 0.86);
          }
          [data-theme="light"] .intel-atlas-pop-actions button,
          [data-theme="light"] .intel-atlas-review-actions button,
          [data-theme="light"] .intel-atlas-activity-grid button,
          [data-theme="light"] .intel-atlas-not-labelable-grid button,
          [data-theme="light"] .intel-atlas-expanded-action,
          [data-theme="light"] .intel-atlas-undo {
            background: rgba(255, 255, 255, 0.42);
            border-color: rgba(26, 24, 18, 0.10);
            color: var(--hg-fg-2);
          }
          [data-theme="light"] .intel-atlas-pop-actions button:hover,
          [data-theme="light"] .intel-atlas-review-actions button:hover,
          [data-theme="light"] .intel-atlas-activity-grid button:hover,
          [data-theme="light"] .intel-atlas-not-labelable-grid button:hover,
          [data-theme="light"] .intel-atlas-expanded-action:hover,
          [data-theme="light"] .intel-atlas-undo:hover {
            background: rgba(255, 255, 255, 0.72);
            border-color: rgba(31, 79, 168, 0.24);
            color: var(--hg-fg-0);
          }
          [data-theme="light"] .intel-atlas-expanded-tab,
          [data-theme="light"] .intel-atlas-drawer-tab {
            background: rgba(255, 255, 255, 0.34);
            border-color: rgba(26, 24, 18, 0.10);
            color: var(--hg-fg-2);
          }
          [data-theme="light"] .intel-atlas-expanded-tab.active,
          [data-theme="light"] .intel-atlas-drawer-tab.active {
            background: rgba(31, 79, 168, 0.08);
            border-color: rgba(31, 79, 168, 0.30);
            color: var(--hg-fg-0);
          }
          [data-theme="light"] .intel-atlas-lightbox {
            background: rgba(26, 24, 18, 0.42);
            backdrop-filter: blur(4px);
          }
          [data-theme="light"] .intel-atlas-lightbox-card img {
            background: #1a1812;
          }
          .intel-atlas.spatial,
          [data-theme="light"] .intel-atlas.spatial {
            background: rgba(3,5,8,0.96);
            color: #fff;
          }
          .intel-atlas.spatial::before,
          [data-theme="light"] .intel-atlas.spatial::before {
            background:
              linear-gradient(90deg, rgba(255,255,255,0.025) 1px, transparent 1px),
              linear-gradient(0deg, rgba(255,255,255,0.018) 1px, transparent 1px);
            opacity: 0.45;
          }
          .intel-atlas.spatial .intel-atlas-top,
          [data-theme="light"] .intel-atlas.spatial .intel-atlas-top {
            background:
              linear-gradient(to bottom, rgba(0,0,0,0.98) 0%, rgba(0,0,0,0.94) 72%, rgba(0,0,0,0.72) 100%);
            border-bottom-color: rgba(255,255,255,0.06);
            box-shadow: 0 18px 50px rgba(0,0,0,0.5);
          }
          @media (max-width: 760px) {
            .intel-atlas-top {
              min-height: 82px;
              padding: 18px;
            }
            .intel-atlas-title h1 { font-size: 18px; }
            .intel-atlas-actions { gap: 5px; }
            .intel-atlas-actions > :nth-child(n+4) { display: none; }
            .intel-atlas-guide {
              left: 14px;
              right: 14px;
              top: 96px;
              width: auto;
              max-height: calc(100vh - 124px);
            }
            .intel-atlas-guide-grid {
              grid-template-columns: 1fr;
            }
            .intel-atlas-global-scrim {
              inset: 82px 0 0;
            }
            .intel-atlas-global-panel {
              left: 14px;
              right: 14px;
              top: 96px;
              width: auto;
              max-height: calc(100vh - 124px);
            }
            .intel-atlas-timeline {
              left: 12px;
              right: 12px;
              top: auto;
              bottom: 14px;
              width: auto;
              height: 180px;
              display: flex;
              gap: 8px;
              overflow-x: auto;
              overflow-y: hidden;
            }
            .intel-atlas-timeline-head,
            .intel-atlas-filters,
            .intel-atlas-day {
              display: none;
            }
            .intel-atlas-event {
              min-width: 218px;
              margin-bottom: 0;
            }
            .intel-atlas-stage {
              left: 0;
              top: 82px;
              bottom: 194px;
            }
            .intel-atlas-popover {
              left: 18px !important;
              right: 18px !important;
              top: 18% !important;
              width: auto;
              max-height: calc(82% - 14px) !important;
            }
            .intel-atlas-expanded-card {
              left: 14px;
              right: 14px;
              top: 14px;
              width: auto;
              max-height: calc(100vh - 318px);
            }
            .intel-atlas-expanded-tabs {
              grid-template-columns: 1fr;
            }
            .intel-atlas-expanded-frame-grid {
              grid-template-columns: 1fr;
            }
            .intel-atlas-drawer {
              top: auto;
              left: 0;
              width: 100%;
              height: 58%;
              border-left: 0;
              border-top: 1px solid var(--hg-border);
              transform: translateY(100%);
            }
            .intel-atlas-drawer.open {
              transform: translateY(0);
            }
            .intel-atlas-rail {
              display: none;
            }
          }
        `}</style>
      </div>
    );

    return typeof document !== "undefined" && document.body
      ? ReactDOM.createPortal(overlay, document.body)
      : overlay;
  }

  function DrawerSection({ title, children }) {
    return (
      <section className="intel-atlas-section">
        <h3>{title}</h3>
        {children}
      </section>
    );
  }

  function AxisGrid({ labels = {} }) {
    const axes = [
      "primary_activity",
      "presence",
      "motion_phase",
      "posture",
      "task_surface",
      "visual_focus",
      "screen_visual_state",
      "room_lighting_visual",
      "image_quality",
      "privacy_sensitivity",
      "metadata_consistency",
    ];
    return (
      <div className="intel-atlas-axis-grid">
        {axes.map((axis) => {
          const value = labelAxis(labels, axis);
          return (
            <div key={axis} className="intel-atlas-axis">
              <span>{axis.replace(/_/g, " ")}</span>
              <b>{value.label} {value.confidence ? `${Math.round(value.confidence * 100)}%` : ""}</b>
            </div>
          );
        })}
      </div>
    );
  }

  function HomeIntelligenceTab({ metricsBase, endpoint }) {
    const base = useMemo(() => intelligenceBaseFrom(metricsBase, endpoint), [metricsBase, endpoint]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [today, setToday] = useState(null);
    const [candidates, setCandidates] = useState([]);
    const [suppressions, setSuppressions] = useState([]);
    const [soak, setSoak] = useState(null);
    const [journals, setJournals] = useState([]);
    const [claims, setClaims] = useState([]);
    const [jobs, setJobs] = useState([]);
    const [scheduler, setScheduler] = useState(null);
    const [evidenceSources, setEvidenceSources] = useState(null);
    const [linkAudit, setLinkAudit] = useState(null);
    const [multimodalStatus, setMultimodalStatus] = useState(null);
    const [labelEvalStatus, setLabelEvalStatus] = useState(null);
    const [labelEvalQueue, setLabelEvalQueue] = useState(null);
    const [promptGate, setPromptGate] = useState(null);
    const [trainingManifests, setTrainingManifests] = useState(null);
    const [observationPackets, setObservationPackets] = useState([]);
    const [observationMap, setObservationMap] = useState(null);
    const [observationDetail, setObservationDetail] = useState(null);
    const [proposals, setProposals] = useState([]);
    const [proposalDetail, setProposalDetail] = useState(null);
    const [experiments, setExperiments] = useState([]);
    const [experimentDetail, setExperimentDetail] = useState(null);
    const [sandboxResult, setSandboxResult] = useState(null);
    const [sandboxExplanation, setSandboxExplanation] = useState(null);
    const [selectedJournal, setSelectedJournal] = useState(null);
    const [claimFilter, setClaimFilter] = useState("all");
    const [zoneFilter, setZoneFilter] = useState("");
    const [claimDetail, setClaimDetail] = useState(null);
    const [detail, setDetail] = useState(null);

    const refresh = useCallback(async () => {
      if (!base) return;
      setLoading(true);
      setError("");
      try {
        const [
          t,
          c,
          s,
          review,
          j,
          w,
          jobRows,
          schedule,
          evidenceRows,
          linkRows,
          multimodalRows,
          labelEvalRows,
          labelEvalQueueRows,
          promptGateRows,
          trainingManifestRows,
          observationRows,
          observationMapRows,
          proposalRows,
          experimentRows,
        ] = await Promise.all([
          fetchJson(`${base}/api/intelligence/today`),
          fetchJson(`${base}/api/intelligence/candidates?limit=30`),
          fetchJson(`${base}/api/intelligence/suppressions`),
          fetchJson(`${base}/api/intelligence/soak-review?hours=24`),
          fetchJson(`${base}/api/intelligence/journals?limit=3`).catch(() => ({ journals: [] })),
          fetchJson(`${base}/api/intelligence/wiki-claims?limit=50`).catch(() => ({ claims: [] })),
          fetchJson(`${base}/api/intelligence/jobs?limit=8`).catch(() => ({ jobs: [] })),
          fetchJson(`${base}/api/intelligence/scheduler`).catch(() => ({ scheduler: null })),
          fetchJson(`${base}/api/intelligence/evidence-sources`).catch(() => ({ evidence_sources: null })),
          fetchJson(`${base}/api/intelligence/evidence-link-audit?hours=24&limit=50`).catch(() => null),
          fetchJson(`${base}/api/intelligence/multimodal/status`).catch(() => null),
          fetchJson(`${base}/api/intelligence/label-evals/status`).catch(() => null),
          fetchJson(`${base}/api/intelligence/label-evals/queue?limit=10`).catch(() => null),
          fetchJson(`${base}/api/intelligence/qwen-labels/prompt-gate`).catch(() => null),
          fetchJson(`${base}/api/intelligence/training-clips/manifests?limit=10`).catch(() => ({ manifests: [] })),
          fetchJson(`${base}/api/intelligence/observation-packets?limit=60`).catch(() => ({ observation_packets: [] })),
          fetchJson(`${base}/api/intelligence/observation-packets/map?limit=300`).catch(() => ({ points: [] })),
          fetchJson(`${base}/api/intelligence/proposals?limit=20`).catch(() => ({ proposals: [] })),
          fetchJson(`${base}/api/intelligence/experiments?limit=20`).catch(() => ({ experiments: [] })),
        ]);
        setToday(t);
        setCandidates(c.candidates || []);
        setSuppressions((s.suppressions || []).filter((x) => x.active));
        setSoak(review);
        setJournals(j.journals || []);
        setClaims(w.claims || []);
        setJobs(jobRows.jobs || []);
        setScheduler(schedule.scheduler || null);
        setEvidenceSources(evidenceRows.evidence_sources || t.evidence_sources || null);
        setLinkAudit(linkRows);
        setMultimodalStatus(multimodalRows);
        setLabelEvalStatus(labelEvalRows);
        setLabelEvalQueue(labelEvalQueueRows);
        setPromptGate(promptGateRows);
        setTrainingManifests(trainingManifestRows);
        setObservationPackets(observationRows.observation_packets || []);
        setObservationMap(observationMapRows);
        setProposals(proposalRows.proposals || []);
        setExperiments(experimentRows.experiments || []);
        setSelectedJournal((current) => current || (j.journals || [])[0] || null);
      } catch (e) {
        setError(e?.message || String(e));
      } finally {
        setLoading(false);
      }
    }, [base]);

    useEffect(() => {
      refresh();
      if (!base) return undefined;
      const id = setInterval(refresh, 30000);
      return () => clearInterval(id);
    }, [base, refresh]);

    const runIngest = useCallback(async () => {
      if (!base) return;
      setLoading(true);
      setError("");
      try {
        await fetchJson(`${base}/api/intelligence/ingest/run`, { method: "POST" });
        await refresh();
      } catch (e) {
        setError(e?.message || String(e));
        setLoading(false);
      }
    }, [base, refresh]);

    const runMemory = useCallback(async () => {
      if (!base) return;
      setLoading(true);
      setError("");
      try {
        await fetchJson(`${base}/api/intelligence/memory/run`, { method: "POST" });
        await refresh();
      } catch (e) {
        setError(e?.message || String(e));
        setLoading(false);
      }
    }, [base, refresh]);

    const runProposals = useCallback(async () => {
      if (!base) return;
      setLoading(true);
      setError("");
      try {
        await fetchJson(`${base}/api/intelligence/proposals/run`, { method: "POST" });
        await refresh();
      } catch (e) {
        setError(e?.message || String(e));
        setLoading(false);
      }
    }, [base, refresh]);

    const runExperiments = useCallback(async () => {
      if (!base) return;
      setLoading(true);
      setError("");
      try {
        await fetchJson(`${base}/api/intelligence/experiments/design/run`, { method: "POST" });
        await refresh();
      } catch (e) {
        setError(e?.message || String(e));
        setLoading(false);
      }
    }, [base, refresh]);

    const runSandbox = useCallback(async () => {
      if (!base) return;
      setLoading(true);
      setError("");
      try {
        const body = await fetchJson(`${base}/api/intelligence/sandbox/experiment-chain/run`, { method: "POST" });
        setSandboxResult(body);
        setSandboxExplanation(null);
      } catch (e) {
        setError(e?.message || String(e));
      } finally {
        setLoading(false);
      }
    }, [base]);

    const runSandboxExplain = useCallback(async () => {
      if (!base) return;
      setLoading(true);
      setError("");
      try {
        const body = await fetchJson(`${base}/api/intelligence/sandbox/experiment-chain/explain`, { method: "POST" });
        setSandboxExplanation(body);
        if (body?.chain) {
          setSandboxResult(body);
        }
      } catch (e) {
        setError(e?.message || String(e));
      } finally {
        setLoading(false);
      }
    }, [base]);

    const loadJournal = useCallback(async (journal) => {
      if (!base || !journal?.date) return;
      setLoading(true);
      setError("");
      try {
        const date = encodeURIComponent(journal.date);
        const body = await fetchJson(`${base}/api/intelligence/journals/${date}`);
        setSelectedJournal(body.journal || journal);
      } catch (e) {
        setError(e?.message || String(e));
      } finally {
        setLoading(false);
      }
    }, [base]);

    const loadClaim = useCallback(async (claim) => {
      if (!base || !claim?.id) return;
      setLoading(true);
      setError("");
      try {
        const id = encodeURIComponent(claim.id);
        const body = await fetchJson(`${base}/api/intelligence/wiki-claims/${id}`);
        setClaimDetail(body);
      } catch (e) {
        setError(e?.message || String(e));
      } finally {
        setLoading(false);
      }
    }, [base]);

    const suppress = useCallback(async (candidate) => {
      if (!base || !candidate) return;
      setLoading(true);
      setError("");
      try {
        const id = encodeURIComponent(candidate.id);
        await fetchJson(`${base}/api/intelligence/candidates/${id}/decisions`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            decision: "do_not_learn",
            actor: "home_app",
            reason: "suppressed from Home app",
          }),
        });
        await refresh();
      } catch (e) {
        setError(e?.message || String(e));
        setLoading(false);
      }
    }, [base, refresh]);

    const loadProposal = useCallback(async (proposal) => {
      if (!base || !proposal?.id) return;
      setLoading(true);
      setError("");
      try {
        const id = encodeURIComponent(proposal.id);
        const body = await fetchJson(`${base}/api/intelligence/proposals/${id}`);
        setProposalDetail(body);
      } catch (e) {
        setError(e?.message || String(e));
      } finally {
        setLoading(false);
      }
    }, [base]);

    const loadExperiment = useCallback(async (experiment) => {
      if (!base || !experiment?.id) return;
      setLoading(true);
      setError("");
      try {
        const id = encodeURIComponent(experiment.id);
        const [detailBody, evaluationBody, preflightBody, manifestBody] = await Promise.all([
          fetchJson(`${base}/api/intelligence/experiments/${id}`),
          fetchJson(`${base}/api/intelligence/experiments/${id}/evaluation`),
          fetchJson(`${base}/api/intelligence/experiments/${id}/preflight`),
          fetchJson(`${base}/api/intelligence/experiments/${id}/manifest`),
        ]);
        setExperimentDetail({
          ...detailBody,
          evaluation: evaluationBody.evaluation,
          preflight: preflightBody.preflight,
          manifest: manifestBody.manifest,
        });
      } catch (e) {
        setError(e?.message || String(e));
      } finally {
        setLoading(false);
      }
    }, [base]);

    const loadObservationPacket = useCallback(async (packetOrPoint) => {
      if (!base || !packetOrPoint?.id) return;
      setLoading(true);
      setError("");
      try {
        const id = encodeURIComponent(packetOrPoint.id);
        const body = await fetchJson(`${base}/api/intelligence/observation-packets/${id}`);
        setObservationDetail(body);
      } catch (e) {
        setError(e?.message || String(e));
      } finally {
        setLoading(false);
      }
    }, [base]);

    const runQwenLabels = useCallback(async () => {
      if (!base) return;
      setLoading(true);
      setError("");
      try {
        await fetchJson(`${base}/api/intelligence/qwen-labels/run?limit=1`, { method: "POST" });
        await refresh();
      } catch (e) {
        setError(e?.message || String(e));
        setLoading(false);
      }
    }, [base, refresh]);

    const runMultimodalPilot = useCallback(async () => {
      if (!base) return;
      setLoading(true);
      setError("");
      try {
        await fetchJson(`${base}/api/intelligence/multimodal/pilot/run`, { method: "POST" });
        await refresh();
      } catch (e) {
        setError(e?.message || String(e));
        setLoading(false);
      }
    }, [base, refresh]);

    const seedLabelEval = useCallback(async () => {
      if (!base) return;
      setLoading(true);
      setError("");
      try {
        await fetchJson(`${base}/api/intelligence/label-evals/seed`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: "heldout_v1",
            target_size: 50,
            require_valid_labels: false,
          }),
        });
        await refresh();
      } catch (e) {
        setError(e?.message || String(e));
        setLoading(false);
      }
    }, [base, refresh]);

    const acceptLabelEval = useCallback(async (item) => {
      if (!base || !item?.id) return;
      setLoading(true);
      setError("");
      try {
        const id = encodeURIComponent(item.id);
        await fetchJson(`${base}/api/intelligence/label-evals/items/${id}/accept-qwen`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            actor: "home_app",
            note: "accepted from Observation Explorer",
          }),
        });
        await refresh();
      } catch (e) {
        setError(e?.message || String(e));
        setLoading(false);
      }
    }, [base, refresh]);

    const createTrainingManifest = useCallback(async (packet) => {
      if (!base || !packet?.id) return;
      setLoading(true);
      setError("");
      try {
        await fetchJson(`${base}/api/intelligence/training-clips/manifests`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            packet_id: packet.id,
            tier: "short_clip",
            actor: "home_app",
          }),
        });
        await refresh();
      } catch (e) {
        setError(e?.message || String(e));
        setLoading(false);
      }
    }, [base, refresh]);

    const auditObservationPacket = useCallback(async (packet, action) => {
      if (!base || !packet?.id) return;
      setLoading(true);
      setError("");
      try {
        const id = encodeURIComponent(packet.id);
        await fetchJson(`${base}/api/intelligence/observation-packets/${id}/audits`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action, actor: "home_app" }),
        });
        await loadObservationPacket(packet);
        await refresh();
      } catch (e) {
        setError(e?.message || String(e));
        setLoading(false);
      }
    }, [base, loadObservationPacket, refresh]);

    const deleteObservationFrames = useCallback(async (packet) => {
      if (!base || !packet?.id) return;
      setLoading(true);
      setError("");
      try {
        const id = encodeURIComponent(packet.id);
        await fetchJson(`${base}/api/intelligence/observation-packets/${id}/frames`, { method: "DELETE" });
        await loadObservationPacket(packet);
        await refresh();
      } catch (e) {
        setError(e?.message || String(e));
        setLoading(false);
      }
    }, [base, loadObservationPacket, refresh]);

    const recordProposalDecision = useCallback(async (proposal, decision) => {
      if (!base || !proposal?.id) return;
      setLoading(true);
      setError("");
      try {
        const id = encodeURIComponent(proposal.id);
        await fetchJson(`${base}/api/intelligence/proposals/${id}/decisions`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            decision,
            actor: "home_app",
            reason: `Home app ${decision}`,
          }),
        });
        await refresh();
        await loadProposal(proposal);
      } catch (e) {
        setError(e?.message || String(e));
        setLoading(false);
      }
    }, [base, loadProposal, refresh]);

    const recordClaimDecision = useCallback(async (claim, decision) => {
      if (!base || !claim?.id) return;
      setLoading(true);
      setError("");
      try {
        const id = encodeURIComponent(claim.id);
        await fetchJson(`${base}/api/intelligence/wiki-claims/${id}/decisions`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            decision,
            actor: "home_app",
            reason: `Home app ${decision}`,
          }),
        });
        await refresh();
        await loadClaim(claim);
      } catch (e) {
        setError(e?.message || String(e));
        setLoading(false);
      }
    }, [base, loadClaim, refresh]);

    const loadDetail = useCallback(async (candidate) => {
      if (!base || !candidate?.id) return;
      setLoading(true);
      setError("");
      try {
        const id = encodeURIComponent(candidate.id);
        const [d, r, p] = await Promise.all([
          fetchJson(`${base}/api/intelligence/candidates/${id}`),
          fetchJson(`${base}/api/intelligence/candidates/${id}/readiness`),
          fetchJson(`${base}/api/intelligence/proposal-preview/${id}`),
        ]);
        setDetail({
          ...d,
          readiness: r.readiness,
          proposal_preview: p.proposal_preview,
        });
      } catch (e) {
        setError(e?.message || String(e));
      } finally {
        setLoading(false);
      }
    }, [base]);

    const counts = today?.counts || {};
    const lastJob = today?.last_job || null;
    const blockedForProposals = candidates.filter((candidate) => candidate.status !== "candidate");

    if (!base) {
      return (
        <div style={{ color: "var(--hg-fg-4)", fontSize: 12 }}>
          intelligence sidecar base unavailable
        </div>
      );
    }

    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{
            fontFamily: "'Geist Mono', ui-monospace, monospace",
            color: "var(--hg-fg-4)",
            fontSize: 10.5,
            marginRight: "auto",
          }}>
            {base} {lastJob ? `last ${lastJob.status} at ${fmtTime(lastJob.finished_at || lastJob.started_at)}` : ""}
          </div>
          {error && <Chip role="status" tone="crit">{error}</Chip>}
          {loading && <Chip tone="warn">loading</Chip>}
          <button className="hg-focusable" onClick={refresh} style={buttonStyle}>refresh</button>
          <button className="hg-focusable" onClick={runIngest} style={buttonStyle}>ingest</button>
          <button className="hg-focusable" onClick={async () => {
            if (!base) return;
            setLoading(true);
            setError("");
            try {
              await fetchJson(`${base}/api/intelligence/evidence/pull/run`, { method: "POST" });
              await refresh();
            } catch (e) {
              setError(e?.message || String(e));
              setLoading(false);
            }
          }} style={buttonStyle}>pull evidence</button>
          <button className="hg-focusable" onClick={runMemory} style={buttonStyle}>memory</button>
        </div>

        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
          gap: 10,
        }}>
          <SummaryCard title="observations" value={counts.observations || 0} meta="override and pending preference evidence" />
          <SummaryCard title="visual packets" value={counts.observation_packets || 0} meta={`${counts.qwen_labels || 0} local Qwen labels`} />
          <SummaryCard title="candidates" value={counts.candidates || 0} meta={`${counts.ready_candidates || 0} ready, ${counts.blocked_candidates || 0} blocked`} />
          <SummaryCard title="episodes" value={counts.episodes || 0} meta="touches linked to final captures" />
          <SummaryCard title="suppressed" value={counts.suppression_rules || suppressions.length || 0} meta="contexts excluded from future proposals" />
          <SummaryCard title="proposals" value={counts.proposals || proposals.filter((p) => p.status === "draft").length || 0} meta="drafts only, no activation path" />
          <SummaryCard title="experiments" value={counts.experiments || experiments.filter((e) => e.status === "inactive_design").length || 0} meta="inactive designs, no activation path" />
          <SummaryCard title="decisions" value={counts.decisions || 0} meta="human review ledger" />
        </div>

        <EvidenceSourceReview evidenceSources={evidenceSources} />
        <EvidenceLinkAudit audit={linkAudit} />
        <JobReview scheduler={scheduler} jobs={jobs} />
        <ObservationExplorer
          base={base}
          status={multimodalStatus}
          packets={observationPackets}
          map={observationMap}
          detail={observationDetail}
          evalStatus={labelEvalStatus}
          evalQueue={labelEvalQueue}
          promptGate={promptGate}
          manifests={trainingManifests}
          onSelect={loadObservationPacket}
          onRunPilot={runMultimodalPilot}
          onLabelNext={runQwenLabels}
          onSeedEval={seedLabelEval}
          onAcceptEval={acceptLabelEval}
          onCreateManifest={createTrainingManifest}
          onAudit={auditObservationPacket}
          onDeleteFrames={deleteObservationFrames}
        />
        <ProposalReview
          proposals={proposals}
          blockedCandidates={blockedForProposals}
          proposalDetail={proposalDetail}
          onRun={runProposals}
          onSelectProposal={loadProposal}
          onProposalDecision={recordProposalDecision}
          onSuppressCandidate={suppress}
        />
        <ExperimentReview
          experiments={experiments}
          experimentDetail={experimentDetail}
          sandboxResult={sandboxResult}
          sandboxExplanation={sandboxExplanation}
          onRun={runExperiments}
          onRunSandbox={runSandbox}
          onRunSandboxExplain={runSandboxExplain}
          onSelectExperiment={loadExperiment}
        />
        <SoakReview soak={soak} />
        <MemoryReview
          journals={journals}
          claims={claims}
          selectedJournal={selectedJournal}
          claimDetail={claimDetail}
          claimFilter={claimFilter}
          onClaimFilter={setClaimFilter}
          zoneFilter={zoneFilter}
          onZoneFilter={setZoneFilter}
          onSelectJournal={loadJournal}
          onSelectClaim={loadClaim}
          onCloseClaim={() => setClaimDetail(null)}
          onClaimDecision={recordClaimDecision}
        />

        <div style={{
          background: "var(--hg-bg-1)",
          border: "1px solid var(--hg-border-soft)",
          borderRadius: 6,
          padding: "12px 14px 4px",
        }}>
          <div style={{
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
            gap: 10,
            marginBottom: 4,
          }}>
            <span style={{
              fontFamily: "'Geist Mono', ui-monospace, monospace",
              fontSize: 9.5,
              letterSpacing: "0.18em",
              textTransform: "uppercase",
              color: "var(--hg-fg-4)",
            }}>preference candidates</span>
            <span style={{ color: "var(--hg-fg-5)", fontSize: 10.5 }}>
              evidence-linked, inactive rules
            </span>
          </div>
          {candidates.length === 0 ? (
            <div style={{ color: "var(--hg-fg-4)", padding: "18px 0", fontSize: 12 }}>
              no candidates yet
            </div>
          ) : candidates.map((candidate) => (
            <CandidateRow
              key={candidate.id}
              candidate={candidate}
              onSuppress={suppress}
              onSelect={loadDetail}
              selected={detail?.candidate?.id === candidate.id}
            />
          ))}
        </div>

        <CandidateDetail detail={detail} onClose={() => setDetail(null)} />
      </div>
    );
  }

  const buttonStyle = {
    background: "transparent",
    border: "1px solid var(--hg-border-soft)",
    color: "var(--hg-fg-3)",
    borderRadius: 4,
    padding: "5px 8px",
    cursor: "pointer",
    fontFamily: "'Geist Mono', ui-monospace, monospace",
    fontSize: 10,
  };

  const preStyle = {
    margin: 0,
    maxHeight: 220,
    overflow: "auto",
    background: "var(--hg-bg-0)",
    border: "1px solid var(--hg-border-soft)",
    borderRadius: 4,
    padding: 8,
    color: "var(--hg-fg-3)",
    fontFamily: "'Geist Mono', ui-monospace, monospace",
    fontSize: 10,
    lineHeight: 1.4,
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
  };

  window.HomeIntelligenceTab = HomeIntelligenceTab;
  window.HomeIntelligenceOverlay = HomeIntelligenceOverlay;
})();

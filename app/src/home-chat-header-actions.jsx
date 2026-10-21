/* Conversation presentation and a bounded local text projection. The controller
 * supplies current feed events and existing guarded operations. No DOM, storage,
 * household service or clipboard API is read directly by this leaf. */
(function () {
  "use strict";

  const transcriptLabels = Object.freeze({ user: "You", voice: "You", home: "Home", assistant: "Home", external: "External" });

  function buildText(events, { excludedIds = [] } = {}) {
    const excluded = new Set(Array.isArray(excludedIds) ? excludedIds : []);
    return (Array.isArray(events) ? events : []).filter(event => event && typeof event === "object"
      && Object.prototype.hasOwnProperty.call(transcriptLabels, event.kind)
      && !event.onboarding && !event.privateCameraContext && !event.backgroundPerception && !event.streaming
      && !excluded.has(event.id) && typeof event.text === "string" && event.text.trim().length > 0)
      .map(event => `${transcriptLabels[event.kind]}: ${event.text}`).join("\n");
  }

  async function copy({ events, excludedIds, writeText } = {}) {
    const text = buildText(events, { excludedIds });
    if (!text) return { ok: false, reason: "empty" };
    if (typeof writeText !== "function") return { ok: false, reason: "unavailable" };
    try {
      const result = await writeText(text);
      return result === false ? { ok: false, reason: "failed" } : { ok: true };
    } catch (_) {
      return { ok: false, reason: "failed" };
    }
  }

  // Paths from the supplied ui-kit.jsx; keep the handoff's icon vocabulary.
  const actionPaths = { explain: "M8 7v5M8 4.5V5M8 15A7 7 0 1 0 8 1a7 7 0 0 0 0 14z",
    copy: "M5 5h8v8H5zM3 11V3h8", new: "M8 3v10M3 8h10" };
  function HomeChatHeaderActions({ onExplain, onCopy, onNewConversation, compact = false,
    explainDisabledReason, copyDisabledReason, newConversationDisabledReason }) {
    const actions = [
      { key: "explain", label: "Explain the last answer", text: "Explain", callback: onExplain,
        reason: explainDisabledReason, unavailable: "Explanation is unavailable." },
      { key: "copy", label: "Copy transcript", text: "Copy", callback: onCopy,
        reason: copyDisabledReason, unavailable: "Transcript copying is unavailable." },
      { key: "new", label: "New conversation", text: "New", callback: onNewConversation,
        reason: newConversationDisabledReason, unavailable: "Starting a new conversation is unavailable." },
    ];
    return <span className="apt-ui-chat-conversation-actions" data-compact={compact ? "true" : undefined} role="group" aria-label="Conversation actions">
      {actions.map(action => {
        const disabled = typeof action.callback !== "function" || !!action.reason;
        const description = disabled ? action.reason || action.unavailable
          : compact && action.key === "copy" ? "Conversation text only" : undefined;
        return <button key={action.key} type="button" className="apt-ui-button is-small"
          data-chat-header-action={action.key} aria-label={action.label} aria-description={description}
          title={compact && description ? `${action.label} · ${description}` : description || action.label} disabled={disabled}
          onClick={disabled ? undefined : action.callback}>{compact ? <svg aria-hidden="true" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d={actionPaths[action.key]} /></svg> : action.text}</button>;
      })}
      {!compact && <span className="apt-ui-chat-transcript-scope">Conversation text only</span>}
    </span>;
  }

  window.HomeChatHeaderActions = HomeChatHeaderActions;
  window.HomeChatTranscript = Object.freeze({ buildText, copy });
})();

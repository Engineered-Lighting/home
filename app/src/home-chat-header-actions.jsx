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

  function HomeChatHeaderActions({ onExplain, onCopy, onNewConversation,
    explainDisabledReason, copyDisabledReason, newConversationDisabledReason }) {
    const actions = [
      { key: "explain", label: "Explain the last answer", text: "Explain", callback: onExplain,
        reason: explainDisabledReason, unavailable: "Explanation is unavailable." },
      { key: "copy", label: "Copy transcript", text: "Copy", callback: onCopy,
        reason: copyDisabledReason, unavailable: "Transcript copying is unavailable." },
      { key: "new", label: "New conversation", text: "New", callback: onNewConversation,
        reason: newConversationDisabledReason, unavailable: "Starting a new conversation is unavailable." },
    ];
    return <span className="apt-ui-chat-conversation-actions" role="group" aria-label="Conversation actions">
      {actions.map(action => {
        const disabled = typeof action.callback !== "function" || !!action.reason;
        const description = disabled ? action.reason || action.unavailable : undefined;
        return <button key={action.key} type="button" className="apt-ui-button is-small"
          data-chat-header-action={action.key} aria-label={action.label} aria-description={description}
          title={description || action.label} disabled={disabled}
          onClick={disabled ? undefined : action.callback}>{action.text}</button>;
      })}
      <span className="apt-ui-chat-transcript-scope">Conversation text only</span>
    </span>;
  }

  window.HomeChatHeaderActions = HomeChatHeaderActions;
  window.HomeChatTranscript = Object.freeze({ buildText, copy });
})();

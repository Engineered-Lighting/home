import { Bot, Send, SlidersHorizontal, X } from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";
import type { ChatMessage } from "../lib/home-core/types";
import type { HAConnectionState } from "../lib/home-core/ha";

type Props = {
  messages: ChatMessage[];
  connection: HAConnectionState;
  busy: boolean;
  open: boolean;
  onClose: () => void;
  onSend: (text: string) => void;
  onOpenSettings: () => void;
};

export function ChatDrawer({ messages, connection, busy, open, onClose, onSend, onOpenSettings }: Props) {
  const [draft, setDraft] = useState("");
  const scrollerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    scrollerRef.current?.scrollTo({ top: scrollerRef.current.scrollHeight });
  }, [messages]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    setDraft("");
    onSend(text);
  };

  return (
    <aside className={`chat-drawer ${open ? "is-open" : ""}`} aria-label="Chat">
      <div className="drawer-titlebar">
        <div>
          <div className="drawer-title">chat</div>
          <div className={`connection-label is-${connection.state}`}>
            <span />
            {connection.state === "online" ? "local models" : connection.state.replace("_", " ")}
          </div>
        </div>
        <div className="drawer-actions">
          <button className="icon-button" type="button" aria-label="Chat settings" title="Chat settings" onClick={onOpenSettings}>
            <SlidersHorizontal size={17} />
          </button>
          <button className="icon-button" type="button" aria-label="Close chat" title="Close chat" onClick={onClose}>
            <X size={18} />
          </button>
        </div>
      </div>
      <div className="message-list" ref={scrollerRef}>
        {messages.length === 0 ? (
          <div className="empty-thread">
            <Bot size={18} />
            <span>Everything looks normal.</span>
          </div>
        ) : (
          messages.map((message) => (
            <article className={`message-row is-${message.role}`} key={message.id}>
              <div className="message-meta">
                <span>{message.role}</span>
                <time>{formatTime(message.at)}</time>
              </div>
              <p>{message.text || (message.streaming ? "..." : "")}</p>
            </article>
          ))
        )}
      </div>
      <form className="chat-composer" onSubmit={submit}>
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="type or /command"
          aria-label="Chat command"
          disabled={busy}
        />
        <button className="send-button" type="submit" aria-label="Send" title="Send" disabled={!draft.trim() || busy}>
          <Send size={16} />
        </button>
      </form>
    </aside>
  );
}

function formatTime(value: number) {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  }).format(new Date(value));
}

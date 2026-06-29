const KEEPALIVE_MS = 30_000;
const RUN_TIMEOUT_MS = 120_000;

export type HAConnectionState =
  | { state: "idle" }
  | { state: "connecting"; url?: string }
  | { state: "online"; haVersion?: string }
  | { state: "offline"; error?: string }
  | { state: "auth_invalid"; message?: string };

export type PipelineUpdate = {
  textDelta?: string;
  finalText?: string;
  conversationId?: string | null;
  raw?: unknown;
};

type RunState = {
  onEvent: (event: unknown) => void;
  resolve: (result: { elapsedMs: number }) => void;
  reject: (error: Error) => void;
  timeout: number;
};

export function haUrlToWs(baseUrl: string): string {
  if (!baseUrl) return "";
  if (baseUrl.startsWith("/")) {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const path = baseUrl.endsWith("/api/websocket") ? baseUrl : `${baseUrl.replace(/\/+$/, "")}/api/websocket`;
    return `${protocol}//${window.location.host}${path}`;
  }
  let url = baseUrl.trim().replace(/\/+$/, "");
  if (url.startsWith("https://")) url = `wss://${url.slice("https://".length)}`;
  else if (url.startsWith("http://")) url = `ws://${url.slice("http://".length)}`;
  else if (!url.startsWith("ws")) url = `ws://${url}`;
  if (!/\/api\/websocket$/.test(url)) url += "/api/websocket";
  return url;
}

export class HomeAssistantClient {
  private socket: WebSocket | null = null;
  private messageId = 1;
  private connectionState: HAConnectionState = { state: "idle" };
  private listeners = new Set<(state: HAConnectionState) => void>();
  private authPromise: Promise<void> | null = null;
  private keepalive: number | null = null;
  private runs = new Map<number, RunState>();
  private url = "";
  private token = "";

  onConnection(listener: (state: HAConnectionState) => void): () => void {
    this.listeners.add(listener);
    listener(this.connectionState);
    return () => this.listeners.delete(listener);
  }

  connect(baseUrl: string, token: string): Promise<void> {
    if (!token.trim()) {
      this.setConnection({ state: "offline", error: "missing token" });
      return Promise.reject(new Error("missing token"));
    }
    if (this.socket?.readyState === WebSocket.OPEN && this.url === baseUrl && this.token === token) {
      return Promise.resolve();
    }
    if (this.authPromise) return this.authPromise;

    this.url = baseUrl;
    this.token = token;
    const wsUrl = haUrlToWs(baseUrl);
    this.setConnection({ state: "connecting", url: wsUrl });

    const authPromise = new Promise<void>((resolve, reject) => {
      let settled = false;
      let socket: WebSocket;
      try {
        socket = new WebSocket(wsUrl);
      } catch (error) {
        const message = error instanceof Error ? error.message : "could not open websocket";
        this.setConnection({ state: "offline", error: message });
        this.authPromise = null;
        reject(new Error(message));
        return;
      }

      this.socket = socket;

      socket.onmessage = (event) => {
        let message: Record<string, unknown>;
        try {
          message = JSON.parse(String(event.data)) as Record<string, unknown>;
        } catch {
          return;
        }

        if (message.type === "auth_required") {
          socket.send(JSON.stringify({ type: "auth", access_token: token }));
          return;
        }
        if (message.type === "auth_ok") {
          settled = true;
          this.setConnection({ state: "online", haVersion: String(message.ha_version || "") || undefined });
          this.startKeepalive();
          resolve();
          return;
        }
        if (message.type === "auth_invalid") {
          settled = true;
          const text = String(message.message || "auth invalid");
          this.setConnection({ state: "auth_invalid", message: text });
          reject(new Error(text));
          socket.close();
          return;
        }
        this.handleMessage(message);
      };

      socket.onerror = () => {
        if (!settled) reject(new Error("websocket error"));
        this.setConnection({ state: "offline", error: "websocket error" });
      };

      socket.onclose = () => {
        this.stopKeepalive();
        this.authPromise = null;
        this.socket = null;
        for (const [id, run] of this.runs) {
          window.clearTimeout(run.timeout);
          run.reject(new Error("websocket closed"));
          this.runs.delete(id);
        }
        if (!settled) reject(new Error("websocket closed"));
        this.setConnection({ state: "offline", error: "closed" });
      };
    }).finally(() => {
      this.authPromise = null;
    });

    this.authPromise = authPromise;
    return authPromise;
  }

  disconnect(): void {
    this.stopKeepalive();
    try {
      this.socket?.close();
    } catch {
      // Ignore close races.
    }
    this.socket = null;
    this.authPromise = null;
  }

  async runPipeline(text: string, conversationId: string | null, onUpdate: (update: PipelineUpdate) => void): Promise<void> {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) throw new Error("not connected");
    const id = this.messageId++;
    const start = performance.now();
    const payload: Record<string, unknown> = {
      id,
      type: "assist_pipeline/run",
      start_stage: "intent",
      end_stage: "intent",
      input: { text }
    };
    if (conversationId) payload.conversation_id = conversationId;

    await new Promise<void>((resolve, reject) => {
      const timeout = window.setTimeout(() => {
        this.runs.delete(id);
        reject(new Error("pipeline timeout"));
      }, RUN_TIMEOUT_MS);
      this.runs.set(id, {
        timeout,
        onEvent: (event) => {
          const update = extractPipelineUpdate(event);
          if (update) onUpdate({ ...update, raw: event });
        },
        resolve: () => resolve(),
        reject
      });
      try {
        this.socket?.send(JSON.stringify(payload));
      } catch (error) {
        this.runs.delete(id);
        window.clearTimeout(timeout);
        reject(error instanceof Error ? error : new Error("pipeline send failed"));
      }
    });
    onUpdate({ raw: { elapsedMs: Math.round(performance.now() - start) } });
  }

  private handleMessage(message: Record<string, unknown>): void {
    const id = typeof message.id === "number" ? message.id : null;
    if (!id || !this.runs.has(id)) return;
    const run = this.runs.get(id);
    if (!run) return;

    if (message.type === "event") {
      run.onEvent(message.event);
      const event = message.event as Record<string, unknown> | undefined;
      if (event?.type === "run-end") {
        window.clearTimeout(run.timeout);
        this.runs.delete(id);
        run.resolve({ elapsedMs: 0 });
      }
      if (event?.type === "error") {
        window.clearTimeout(run.timeout);
        this.runs.delete(id);
        run.reject(new Error("pipeline error"));
      }
      return;
    }

    if (message.type === "result" && message.success === false) {
      window.clearTimeout(run.timeout);
      this.runs.delete(id);
      const error = message.error as { message?: string } | undefined;
      run.reject(new Error(error?.message || "pipeline rejected"));
    }
  }

  private startKeepalive(): void {
    this.stopKeepalive();
    this.keepalive = window.setInterval(() => {
      if (this.socket?.readyState !== WebSocket.OPEN) return;
      this.socket.send(JSON.stringify({ id: this.messageId++, type: "ping" }));
    }, KEEPALIVE_MS);
  }

  private stopKeepalive(): void {
    if (this.keepalive != null) window.clearInterval(this.keepalive);
    this.keepalive = null;
  }

  private setConnection(state: HAConnectionState): void {
    this.connectionState = state;
    for (const listener of this.listeners) listener(state);
  }
}

function extractPipelineUpdate(event: unknown): PipelineUpdate | null {
  if (!event || typeof event !== "object") return null;
  const typed = event as { type?: string; data?: Record<string, unknown> };
  if (typed.type === "intent-progress") {
    const delta = typed.data?.chat_log_delta as { content?: unknown } | undefined;
    if (typeof delta?.content === "string" && delta.content) return { textDelta: delta.content };
    if (typeof typed.data?.delta === "string") return { textDelta: typed.data.delta };
  }
  if (typed.type === "intent-end") {
    const data = typed.data || {};
    const intent = data.intent_output as Record<string, unknown> | undefined;
    const response = intent?.response as Record<string, unknown> | undefined;
    const speech = response?.speech as Record<string, unknown> | undefined;
    const plain = speech?.plain as Record<string, unknown> | undefined;
    const ssml = speech?.ssml as Record<string, unknown> | undefined;
    const finalText = typeof plain?.speech === "string"
      ? plain.speech
      : typeof ssml?.speech === "string"
        ? ssml.speech
        : "";
    return {
      finalText,
      conversationId: typeof intent?.conversation_id === "string" ? intent.conversation_id : null
    };
  }
  return null;
}

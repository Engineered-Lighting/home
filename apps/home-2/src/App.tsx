import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MessageSquare } from "lucide-react";
import { getApartmentModel } from "./lib/home-core/apartment";
import { defaultConfig, loadConfig, saveConfig, type Home2Config } from "./lib/home-core/config";
import { HomeAssistantClient, type HAConnectionState } from "./lib/home-core/ha";
import { getMetrics, probeHealth } from "./lib/home-core/metrics";
import { connectTracks } from "./lib/home-core/tracker";
import type { ApartmentDevice, ApartmentModel, ChatMessage, ServiceHealth, ServiceState, Track } from "./lib/home-core/types";
import { SpatialScene, type AnchorPosition, type SceneMode } from "./scene/SpatialScene";
import { BottomDrawer } from "./ui/BottomDrawer";
import { ChatDrawer } from "./ui/ChatDrawer";
import { SettingsPanel } from "./ui/SettingsPanel";
import { TopRail } from "./ui/TopRail";

const emptyModel: ApartmentModel = {
  schema_version: 1,
  exists: false,
  zones: [],
  devices: []
};

const serviceSpecs = [
  ["metrics", "metrics", "metricsBaseUrl"],
  ["intelligence", "intelligence", "intelligenceBaseUrl"],
  ["tracker", "tracker", "trackerBaseUrl"],
  ["supervisor", "supervisor", "supervisorBaseUrl"],
  ["video-labeler", "video labeler", "videoLabelerBaseUrl"],
  ["vision", "vision", "visionBaseUrl"]
] as const;

export default function App() {
  const [config, setConfig] = useState<Home2Config>(() => safeLoadConfig());
  const [model, setModel] = useState<ApartmentModel>(emptyModel);
  const [track, setTrack] = useState<Track | null>(null);
  const [trackerState, setTrackerState] = useState<ServiceState>("connecting");
  const [services, setServices] = useState<ServiceHealth[]>(() => initialServices("offline"));
  const [metricsText, setMetricsText] = useState("");
  const [mode, setMode] = useState<SceneMode>("hybrid");
  const [daylight, setDaylight] = useState(false);
  const [chatOpen, setChatOpen] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [bottomOpen, setBottomOpen] = useState(false);
  const [activeTab, setActiveTab] = useState("health");
  const [selectedCamera, setSelectedCamera] = useState<ApartmentDevice | null>(null);
  const [anchor, setAnchor] = useState<AnchorPosition>({ left: 0, top: 0, visible: false });
  const [haConnection, setHaConnection] = useState<HAConnectionState>({ state: "idle" });
  const [chatBusy, setChatBusy] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "boot",
      role: "assistant",
      text: "Everything looks normal.",
      at: Date.now()
    }
  ]);
  const conversationIdRef = useRef<string | null>(null);
  const haClientRef = useRef(new HomeAssistantClient());

  useEffect(() => haClientRef.current.onConnection(setHaConnection), []);

  useEffect(() => {
    const controller = new AbortController();
    getApartmentModel(config.apartmentAssetBase, controller.signal).then(setModel);
    return () => controller.abort();
  }, [config.apartmentAssetBase]);

  useEffect(() => {
    if (!model.zones.length) return;
    const connection = connectTracks(
      config.trackerBaseUrl,
      (frame) => {
        const primary = frame.tracks[0] || null;
        setTrack(primary);
      },
      (state) => setTrackerState(state === "live" ? "online" : state === "sim" ? "sim" : state),
      { model }
    );
    return () => connection.close();
  }, [config.trackerBaseUrl, model]);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    const poll = async () => {
      const measured = await Promise.all(
        serviceSpecs.map(([id, label, key]) => probeHealth(id, label, config[key], controller.signal))
      );
      if (!cancelled) setServices(withHaService(measured, haConnection));
    };

    poll();
    const interval = window.setInterval(poll, 10_000);
    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(interval);
    };
  }, [config, haConnection]);

  useEffect(() => {
    const controller = new AbortController();
    const pollMetrics = async () => {
      try {
        const snapshot = await getMetrics(config.metricsBaseUrl, controller.signal);
        setMetricsText(snapshot.text || "");
      } catch {
        setMetricsText("");
      }
    };
    pollMetrics();
    const interval = window.setInterval(pollMetrics, 6_000);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [config.metricsBaseUrl]);

  useEffect(() => {
    if (!config.haToken) return;
    haClientRef.current.connect(config.haBaseUrl, config.haToken).catch(() => {
      // Connection state is surfaced by the client.
    });
  }, [config.haBaseUrl, config.haToken]);

  const activeRoomId = track?.room || selectedCamera?.room_id || "kitchen";

  const selectedCameraWithDefault = useMemo(() => {
    if (selectedCamera) return selectedCamera;
    return model.devices.find((device) => device.type === "camera" && device.room_id === activeRoomId) || null;
  }, [activeRoomId, model.devices, selectedCamera]);

  const handleSaveConfig = (next: Home2Config) => {
    const saved = saveConfig(next);
    setConfig(saved);
    setSettingsOpen(false);
  };

  const openSurface = (surface: string) => {
    setActiveTab(surface);
    setBottomOpen(true);
  };

  const handleCameraSelect = useCallback((device: ApartmentDevice) => {
    setSelectedCamera(device);
    setActiveTab("cameras");
    setBottomOpen(true);
  }, []);

  const sendChat = async (text: string) => {
    if (text.startsWith("/")) {
      handleSlashCommand(text);
      return;
    }
    const now = Date.now();
    setMessages((current) => [
      ...current,
      { id: `u-${now}`, role: "user", text, at: now },
      { id: `a-${now}`, role: "assistant", text: "", at: now + 1, streaming: true }
    ]);
    if (!config.haToken) {
      finishAssistantMessage(now, "Home Assistant token is not configured.");
      return;
    }
    setChatBusy(true);
    try {
      await haClientRef.current.connect(config.haBaseUrl, config.haToken);
      await haClientRef.current.runPipeline(text, conversationIdRef.current, (update) => {
        if (update.conversationId) conversationIdRef.current = update.conversationId;
        if (update.textDelta) appendAssistantDelta(now, update.textDelta);
        if (update.finalText) finishAssistantMessage(now, update.finalText);
      });
      setMessages((current) => current.map((message) => (
        message.id === `a-${now}` ? { ...message, streaming: false, text: message.text || "Done." } : message
      )));
    } catch (error) {
      finishAssistantMessage(now, error instanceof Error ? error.message : "Chat failed.");
    } finally {
      setChatBusy(false);
    }
  };

  const handleSlashCommand = (command: string) => {
    const key = command.slice(1).split(/\s+/)[0]?.toLowerCase() || "help";
    const map: Record<string, string> = {
      help: "help",
      status: "health",
      metrics: "metrics",
      atlas: "atlas",
      people: "people",
      person: "people",
      cameras: "cameras",
      camera: "cameras",
      lights: "lights",
      explain: "atlas"
    };
    setActiveTab(map[key] || "help");
    setBottomOpen(true);
    const now = Date.now();
    setMessages((current) => [
      ...current,
      { id: `u-${now}`, role: "user", text: command, at: now },
      { id: `s-${now}`, role: "system", text: `Opened ${map[key] || "help"}.`, at: now + 1 }
    ]);
  };

  const appendAssistantDelta = (turnId: number, delta: string) => {
    setMessages((current) => current.map((message) => (
      message.id === `a-${turnId}` ? { ...message, text: `${message.text}${delta}` } : message
    )));
  };

  const finishAssistantMessage = (turnId: number, text: string) => {
    setMessages((current) => current.map((message) => (
      message.id === `a-${turnId}` ? { ...message, text, streaming: false } : message
    )));
  };

  return (
    <div className={`app-shell ${daylight ? "is-daylight" : ""}`}>
      <TopRail
        trackerState={trackerState}
        daylight={daylight}
        onToggleDaylight={() => setDaylight((value) => !value)}
        onOpenSettings={() => setSettingsOpen(true)}
        onOpenSurface={openSurface}
      />

      <main className="spatial-stage">
        <SpatialScene
          assetBase={config.apartmentAssetBase}
          model={model}
          activeRoomId={activeRoomId}
          track={track}
          mode={mode}
          daylight={daylight}
          selectedCameraId={selectedCamera?.id || null}
          onCameraSelect={handleCameraSelect}
          onAnchorChange={setAnchor}
        />
        <InferenceCard track={track} anchor={anchor} activeRoomId={activeRoomId} selectedCamera={selectedCameraWithDefault} />
        <ModeSwitch mode={mode} onModeChange={setMode} />
        <button className="chat-fab" type="button" aria-label="Open chat" title="Open chat" onClick={() => setChatOpen(true)}>
          <MessageSquare size={18} />
        </button>
      </main>

      <ChatDrawer
        messages={messages}
        connection={haConnection}
        busy={chatBusy}
        open={chatOpen}
        onClose={() => setChatOpen(false)}
        onSend={sendChat}
        onOpenSettings={() => setSettingsOpen(true)}
      />

      <BottomDrawer
        open={bottomOpen}
        activeTab={activeTab}
        services={services}
        metricsText={metricsText}
        model={model}
        track={track}
        selectedCamera={selectedCameraWithDefault}
        onToggle={() => setBottomOpen((value) => !value)}
        onTabChange={setActiveTab}
        onCameraSelect={handleCameraSelect}
      />

      <SettingsPanel
        config={config}
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onSave={handleSaveConfig}
      />
    </div>
  );
}

function InferenceCard({
  track,
  anchor,
  activeRoomId,
  selectedCamera
}: {
  track: Track | null;
  anchor: AnchorPosition;
  activeRoomId: string | null;
  selectedCamera: ApartmentDevice | null;
}) {
  const visible = anchor.visible && track;
  return (
    <div
      className={`inference-card ${visible ? "is-visible" : ""}`}
      style={{ left: anchor.left + 28, top: anchor.top - 34 }}
    >
      <strong>{track?.activity?.replace("_", " ") || "present"}</strong>
      <span>{activeRoomId?.replace("_", " ") || "home"} · {Math.round((track?.activity_conf || track?.conf || 0) * 100)}%</span>
      <span>{selectedCamera?.camera?.frigate_name || selectedCamera?.name || "local vision"} · {track?.conf_reason || "ready"}</span>
      <i aria-hidden="true" />
    </div>
  );
}

function ModeSwitch({ mode, onModeChange }: { mode: SceneMode; onModeChange: (mode: SceneMode) => void }) {
  const modes: SceneMode[] = ["cloud", "photo", "mesh", "hybrid"];
  return (
    <div className="mode-switch" role="tablist" aria-label="Scene mode">
      {modes.map((item) => (
        <button
          key={item}
          type="button"
          className={mode === item ? "is-active" : ""}
          onClick={() => onModeChange(item)}
        >
          {item}
        </button>
      ))}
    </div>
  );
}

function safeLoadConfig() {
  try {
    return loadConfig();
  } catch {
    return defaultConfig;
  }
}

function initialServices(state: ServiceState): ServiceHealth[] {
  return [
    { id: "ha", label: "home assistant", state },
    ...serviceSpecs.map(([id, label]) => ({ id, label, state }))
  ];
}

function withHaService(services: ServiceHealth[], connection: HAConnectionState): ServiceHealth[] {
  const state: ServiceState = connection.state === "online"
    ? "online"
    : connection.state === "connecting"
      ? "connecting"
      : connection.state === "auth_invalid"
        ? "degraded"
        : "offline";
  return [
    {
      id: "ha",
      label: "home assistant",
      state,
      detail: connection.state === "auth_invalid" ? connection.message : connection.state
    },
    ...services
  ];
}

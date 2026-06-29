import {
  Activity,
  Camera,
  ChevronDown,
  ChevronUp,
  CircleHelp,
  Lightbulb,
  RadioTower,
  Sparkles,
  UserRound
} from "lucide-react";
import type { ApartmentDevice, ApartmentModel, ServiceHealth, Track } from "../lib/home-core/types";

type Props = {
  open: boolean;
  activeTab: string;
  services: ServiceHealth[];
  metricsText: string;
  model: ApartmentModel;
  track: Track | null;
  selectedCamera: ApartmentDevice | null;
  onToggle: () => void;
  onTabChange: (tab: string) => void;
  onCameraSelect: (device: ApartmentDevice) => void;
};

const tabs = [
  { id: "health", label: "health", icon: RadioTower },
  { id: "metrics", label: "metrics", icon: Activity },
  { id: "atlas", label: "atlas", icon: Sparkles },
  { id: "people", label: "people", icon: UserRound },
  { id: "cameras", label: "cameras", icon: Camera },
  { id: "lights", label: "lights", icon: Lightbulb },
  { id: "help", label: "help", icon: CircleHelp }
];

export function BottomDrawer({
  open,
  activeTab,
  services,
  metricsText,
  model,
  track,
  selectedCamera,
  onToggle,
  onTabChange,
  onCameraSelect
}: Props) {
  const online = services.filter((service) => service.state === "online").length;
  return (
    <section className={`bottom-drawer ${open ? "is-open" : ""}`} aria-label="Operations">
      <button className="bottom-strip" type="button" onClick={onToggle}>
        <span className="strip-status">
          <span className="status-dot" />
          {online}/{services.length} systems
        </span>
        <span className="strip-room">{track?.person || "someone"} · {track?.room?.replace("_", " ") || "home"}</span>
        {open ? <ChevronDown size={16} /> : <ChevronUp size={16} />}
      </button>
      <div className="bottom-panel">
        <div className="tab-list">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                className={`tab-button ${activeTab === tab.id ? "is-active" : ""}`}
                key={tab.id}
                type="button"
                onClick={() => onTabChange(tab.id)}
              >
                <Icon size={15} />
                {tab.label}
              </button>
            );
          })}
        </div>
        <div className="tab-body">
          {activeTab === "health" && <HealthTab services={services} />}
          {activeTab === "metrics" && <MetricsTab metricsText={metricsText} />}
          {activeTab === "atlas" && <AtlasTab model={model} track={track} />}
          {activeTab === "people" && <PeopleTab track={track} />}
          {activeTab === "cameras" && <CamerasTab model={model} selectedCamera={selectedCamera} onCameraSelect={onCameraSelect} />}
          {activeTab === "lights" && <LightsTab model={model} />}
          {activeTab === "help" && <HelpTab />}
        </div>
      </div>
    </section>
  );
}

function HealthTab({ services }: { services: ServiceHealth[] }) {
  return (
    <div className="surface-grid">
      {services.map((service) => (
        <article className="surface-card" key={service.id}>
          <div className="surface-card-top">
            <span>{service.label}</span>
            <span className={`state-chip is-${service.state}`}>{service.state}</span>
          </div>
          <p>{service.detail || "waiting"}</p>
          <small>{service.latencyMs ? `${service.latencyMs} ms` : "no response"}</small>
        </article>
      ))}
    </div>
  );
}

function MetricsTab({ metricsText }: { metricsText: string }) {
  const lines = metricsText.split("\n").filter((line) => line && !line.startsWith("#")).slice(0, 10);
  return (
    <div className="metrics-readout">
      {lines.length ? lines.map((line) => <code key={line}>{line}</code>) : <span>metrics offline</span>}
    </div>
  );
}

function AtlasTab({ model, track }: { model: ApartmentModel; track: Track | null }) {
  const rooms = model.zones.map((zone) => ({
    ...zone,
    active: zone.id === track?.room
  }));
  return (
    <div className="surface-grid">
      {rooms.map((room) => (
        <article className="surface-card" key={room.id}>
          <div className="surface-card-top">
            <span>{room.name}</span>
            <span className={`state-chip ${room.active ? "is-online" : ""}`}>{room.active ? "active" : "quiet"}</span>
          </div>
          <p>{room.frigate_camera || "no camera"}</p>
          <small>{room.floor_polygon.length} anchors</small>
        </article>
      ))}
    </div>
  );
}

function PeopleTab({ track }: { track: Track | null }) {
  return (
    <div className="surface-grid">
      <article className="surface-card">
        <div className="surface-card-top">
          <span>{track?.person || "someone"}</span>
          <span className={`state-chip ${track?.pos ? "is-online" : "is-degraded"}`}>{track?.pos ? "precise" : "room"}</span>
        </div>
        <p>{track?.activity?.replace("_", " ") || "present"}</p>
        <small>{track?.conf ? `${Math.round(track.conf * 100)}% confidence` : "waiting"}</small>
      </article>
    </div>
  );
}

function CamerasTab({
  model,
  selectedCamera,
  onCameraSelect
}: {
  model: ApartmentModel;
  selectedCamera: ApartmentDevice | null;
  onCameraSelect: (device: ApartmentDevice) => void;
}) {
  const cameras = model.devices.filter((device) => device.type === "camera");
  return (
    <div className="surface-grid">
      {cameras.map((camera) => {
        const calibration = camera.camera?.calibration?.status || (camera.confidence && camera.confidence >= 0.95 ? "calibrated" : "approximate");
        return (
          <button
            className={`surface-card camera-card ${selectedCamera?.id === camera.id ? "is-selected" : ""}`}
            key={camera.id}
            type="button"
            onClick={() => onCameraSelect(camera)}
          >
            <div className="surface-card-top">
              <span>{camera.name}</span>
              <span className={`state-chip is-${calibration === "calibrated" ? "online" : "degraded"}`}>{calibration}</span>
            </div>
            <p>{camera.room_id?.replace("_", " ") || "unplaced"}</p>
            <small>{camera.ha_entity_id || camera.camera?.frigate_name || "camera"}</small>
          </button>
        );
      })}
    </div>
  );
}

function LightsTab({ model }: { model: ApartmentModel }) {
  const lights = model.devices.filter((device) => device.type === "light");
  return (
    <div className="surface-grid">
      {lights.slice(0, 10).map((light) => (
        <article className="surface-card" key={light.id}>
          <div className="surface-card-top">
            <span>{light.name}</span>
            <span className="state-chip is-degraded">read only</span>
          </div>
          <p>{light.room_id?.replace("_", " ") || "unplaced"}</p>
          <small>{light.ha_entity_id || "light"}</small>
        </article>
      ))}
    </div>
  );
}

function HelpTab() {
  const commands = ["/help", "/status", "/metrics", "/atlas", "/people", "/cameras", "/lights", "/explain"];
  return (
    <div className="command-grid">
      {commands.map((command) => (
        <code key={command}>{command}</code>
      ))}
    </div>
  );
}

import { baseToWsUrl } from "./config";
import type { ApartmentModel, TracksFrame } from "./types";
import { roomCenter } from "./apartment";

export type TrackConnection = {
  close: () => void;
};

export function connectTracks(
  trackerBaseUrl: string,
  onFrame: (frame: TracksFrame) => void,
  onStatus: (state: "live" | "offline" | "sim" | "connecting") => void,
  options: { replay?: string; model?: ApartmentModel } = {}
): TrackConnection {
  const wsUrl = `${baseToWsUrl(trackerBaseUrl, "/ws/tracks")}${options.replay ? `?replay=${encodeURIComponent(options.replay)}` : ""}`;
  let closed = false;
  let socket: WebSocket | null = null;
  let retryMs = 1000;
  let simTimer: number | null = null;

  const startSim = () => {
    if (simTimer != null) return;
    onStatus("sim");
    const startedAt = performance.now();
    simTimer = window.setInterval(() => {
      onFrame(makeSimFrame(options.model, startedAt));
    }, 240);
  };

  const connect = () => {
    if (closed) return;
    onStatus("connecting");
    try {
      socket = new WebSocket(wsUrl);
    } catch {
      startSim();
      return;
    }
    socket.onopen = () => {
      retryMs = 1000;
      onStatus("live");
    };
    socket.onmessage = (message) => {
      try {
        const frame = JSON.parse(String(message.data)) as TracksFrame;
        if (frame?.type === "tracks" && Array.isArray(frame.tracks)) onFrame(frame);
      } catch {
        // Ignore malformed tracker frames; the next good frame wins.
      }
    };
    socket.onerror = () => {
      try {
        socket?.close();
      } catch {
        // Already closing.
      }
    };
    socket.onclose = () => {
      if (closed) return;
      onStatus("offline");
      window.setTimeout(connect, retryMs);
      retryMs = Math.min(retryMs * 1.7, 10_000);
      window.setTimeout(startSim, 1800);
    };
  };

  connect();

  return {
    close: () => {
      closed = true;
      if (simTimer != null) window.clearInterval(simTimer);
      try {
        socket?.close();
      } catch {
        // Ignore close races.
      }
    }
  };
}

function makeSimFrame(model: ApartmentModel | undefined, startedAt: number): TracksFrame {
  const elapsed = ((performance.now() - startedAt) / 1000) % 26;
  const kitchen = roomCenter(model || { zones: [], devices: [] }, "kitchen") || [5.2, 4.2, 0];
  const living = roomCenter(model || { zones: [], devices: [] }, "living_room") || [10.5, 3.8, 0];
  const dining = roomCenter(model || { zones: [], devices: [] }, "dining_room") || [5.2, 1.6, 0];
  const points = [living, dining, kitchen, living];
  const segment = Math.floor((elapsed / 26) * (points.length - 1));
  const local = ((elapsed / 26) * (points.length - 1)) % 1;
  const a = points[segment];
  const b = points[Math.min(segment + 1, points.length - 1)];
  const smooth = 0.5 - Math.cos(local * Math.PI) * 0.5;
  const pos: [number, number, number] = [
    a[0] + (b[0] - a[0]) * smooth,
    a[1] + (b[1] - a[1]) * smooth,
    0
  ];
  const room = elapsed > 14 && elapsed < 20 ? "kitchen" : elapsed > 7 && elapsed < 13 ? "dining_room" : "living_room";
  const roomOnly = elapsed > 18 && elapsed < 21;
  return {
    type: "tracks",
    ts: Date.now() / 1000,
    tracks: [
      {
        id: "home2-sim",
        person: "marcelo",
        state: roomOnly ? "room_only" : "active",
        pos: roomOnly ? null : pos,
        room,
        source_cams: room === "kitchen" ? ["kitchen"] : ["living_room"],
        conf: roomOnly ? 0.62 : 0.88,
        conf_reason: roomOnly ? "room confidence" : "precise",
        activity: room === "kitchen" ? "prepping coffee" : room === "dining_room" ? "passing through" : "home",
        activity_conf: room === "kitchen" ? 0.86 : 0.72,
        activity_source: "simulation"
      }
    ]
  };
}

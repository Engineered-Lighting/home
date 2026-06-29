export type Vec3 = [number, number, number];
export type Vec2 = [number, number];

export type RoomZone = {
  id: string;
  name: string;
  color?: string;
  ceiling_height_m?: number;
  frigate_camera?: string;
  floor_polygon: Vec2[];
};

export type ApartmentDevice = {
  id: string;
  type: "light" | "camera" | "speaker" | "tv" | string;
  name: string;
  ha_entity_id?: string;
  pos?: Vec3;
  yaw_rad?: number;
  room_id?: string;
  confidence?: number;
  source?: string;
  controllable?: boolean;
  camera?: {
    frigate_name?: string;
    calibration?: CameraCalibration;
  };
};

export type CameraCalibration = {
  status?: "calibrated" | "approximate" | "missing";
  fov_deg?: number;
  intrinsics?: number[];
  extrinsics?: number[];
  error_px?: number;
};

export type ApartmentModel = {
  schema_version?: number;
  revision?: number;
  exists?: boolean;
  seeded?: boolean;
  meta?: Record<string, unknown>;
  zones: RoomZone[];
  devices: ApartmentDevice[];
};

export type Track = {
  id: string;
  person?: string;
  state?: string;
  pos?: Vec3 | null;
  vel?: Vec3 | null;
  room?: string | null;
  zone?: string | null;
  source_cams?: string[];
  conf?: number;
  conf_reason?: string;
  activity?: string | null;
  activity_conf?: number | null;
  activity_source?: string | null;
};

export type TracksFrame = {
  type: "tracks";
  ts?: number;
  tracks: Track[];
};

export type ServiceState = "online" | "offline" | "degraded" | "connecting" | "sim";

export type ServiceHealth = {
  id: string;
  label: string;
  state: ServiceState;
  detail?: string;
  latencyMs?: number;
};

export type ChatMessage = {
  id: string;
  role: "assistant" | "user" | "system";
  text: string;
  at: number;
  streaming?: boolean;
};

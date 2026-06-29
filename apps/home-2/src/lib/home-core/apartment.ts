import type { ApartmentModel } from "./types";

const emptyModel: ApartmentModel = {
  schema_version: 1,
  exists: false,
  zones: [],
  devices: []
};

export async function getApartmentModel(assetBase: string, signal?: AbortSignal): Promise<ApartmentModel> {
  const base = assetBase.replace(/\/+$/, "");
  try {
    const response = await fetch(`${base}/seed-model.json`, { cache: "no-store", signal });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const model = await response.json() as ApartmentModel;
    return {
      ...emptyModel,
      ...model,
      zones: model.zones || [],
      devices: model.devices || []
    };
  } catch {
    return emptyModel;
  }
}

export async function getFrameMetadata(assetBase: string, signal?: AbortSignal): Promise<Record<string, unknown> | null> {
  const base = assetBase.replace(/\/+$/, "");
  try {
    const response = await fetch(`${base}/frame.json`, { cache: "no-store", signal });
    if (!response.ok) return null;
    return await response.json() as Record<string, unknown>;
  } catch {
    return null;
  }
}

export function roomCenter(model: ApartmentModel, roomId?: string | null): [number, number, number] | null {
  const room = model.zones.find((zone) => zone.id === roomId);
  if (!room || room.floor_polygon.length === 0) return null;
  const sum = room.floor_polygon.reduce((acc, point) => [acc[0] + point[0], acc[1] + point[1]], [0, 0]);
  return [sum[0] / room.floor_polygon.length, sum[1] / room.floor_polygon.length, 0];
}

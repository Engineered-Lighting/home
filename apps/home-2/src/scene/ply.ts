export type ParsedPly = {
  positions: Float32Array;
  colors: Float32Array;
  count: number;
  bounds: {
    min: [number, number, number];
    max: [number, number, number];
  };
};

type Property = {
  type: "float" | "uchar";
  name: string;
};

const typeSize: Record<Property["type"], number> = {
  float: 4,
  uchar: 1
};

export function parseBinaryLittleEndianPly(buffer: ArrayBuffer, maxPoints = 120_000): ParsedPly {
  const bytes = new Uint8Array(buffer);
  const headerEnd = findHeaderEnd(bytes);
  const header = new TextDecoder().decode(bytes.slice(0, headerEnd));
  if (!header.includes("format binary_little_endian 1.0")) {
    throw new Error("Only binary_little_endian PLY files are supported");
  }

  const lines = header.split(/\r?\n/);
  const vertexLine = lines.find((line) => line.startsWith("element vertex "));
  const vertexCount = Number(vertexLine?.split(/\s+/)[2] || 0);
  if (!Number.isFinite(vertexCount) || vertexCount <= 0) throw new Error("PLY missing vertex count");

  const properties: Property[] = [];
  let readingVertex = false;
  for (const line of lines) {
    if (line.startsWith("element vertex ")) {
      readingVertex = true;
      continue;
    }
    if (readingVertex && line.startsWith("element ")) break;
    if (!readingVertex || !line.startsWith("property ")) continue;
    const [, rawType, name] = line.split(/\s+/);
    if (rawType === "float" || rawType === "uchar") properties.push({ type: rawType, name });
  }

  const stride = properties.reduce((sum, property) => sum + typeSize[property.type], 0);
  const xOffset = propertyOffset(properties, "x");
  const yOffset = propertyOffset(properties, "y");
  const zOffset = propertyOffset(properties, "z");
  const intensityOffset = propertyOffset(properties, "intensity");
  if (xOffset == null || yOffset == null || zOffset == null) throw new Error("PLY missing xyz properties");

  const sampleStride = Math.max(1, Math.ceil(vertexCount / maxPoints));
  const count = Math.ceil(vertexCount / sampleStride);
  const positions = new Float32Array(count * 3);
  const colors = new Float32Array(count * 3);
  const view = new DataView(buffer);
  const min: [number, number, number] = [Infinity, Infinity, Infinity];
  const max: [number, number, number] = [-Infinity, -Infinity, -Infinity];
  let out = 0;
  const dataStart = headerEnd;

  for (let i = 0; i < vertexCount; i += sampleStride) {
    const base = dataStart + i * stride;
    const x = view.getFloat32(base + xOffset, true);
    const y = view.getFloat32(base + yOffset, true);
    const z = view.getFloat32(base + zOffset, true);
    positions[out * 3] = x;
    positions[out * 3 + 1] = y;
    positions[out * 3 + 2] = z;
    min[0] = Math.min(min[0], x);
    min[1] = Math.min(min[1], y);
    min[2] = Math.min(min[2], z);
    max[0] = Math.max(max[0], x);
    max[1] = Math.max(max[1], y);
    max[2] = Math.max(max[2], z);

    const intensity = intensityOffset == null ? 120 : view.getUint8(base + intensityOffset);
    const level = Math.max(0.08, Math.min(0.68, intensity / 255 * 0.55 + 0.08));
    colors[out * 3] = level * 0.72;
    colors[out * 3 + 1] = level * 0.78;
    colors[out * 3 + 2] = level * 0.88;
    out += 1;
  }

  return {
    positions: out === count ? positions : positions.slice(0, out * 3),
    colors: out === count ? colors : colors.slice(0, out * 3),
    count: out,
    bounds: { min, max }
  };
}

function findHeaderEnd(bytes: Uint8Array): number {
  const marker = new TextEncoder().encode("end_header\n");
  for (let i = 0; i <= bytes.length - marker.length; i += 1) {
    let found = true;
    for (let j = 0; j < marker.length; j += 1) {
      if (bytes[i + j] !== marker[j]) {
        found = false;
        break;
      }
    }
    if (found) return i + marker.length;
  }
  const crlfMarker = new TextEncoder().encode("end_header\r\n");
  for (let i = 0; i <= bytes.length - crlfMarker.length; i += 1) {
    let found = true;
    for (let j = 0; j < crlfMarker.length; j += 1) {
      if (bytes[i + j] !== crlfMarker[j]) {
        found = false;
        break;
      }
    }
    if (found) return i + crlfMarker.length;
  }
  throw new Error("PLY missing header terminator");
}

function propertyOffset(properties: Property[], name: string): number | null {
  let offset = 0;
  for (const property of properties) {
    if (property.name === name) return offset;
    offset += typeSize[property.type];
  }
  return null;
}

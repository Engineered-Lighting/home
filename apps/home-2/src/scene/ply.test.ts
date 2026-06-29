import { describe, expect, it } from "vitest";
import { parseBinaryLittleEndianPly } from "./ply";

describe("parseBinaryLittleEndianPly", () => {
  it("parses xyz and intensity from a small binary ply", () => {
    const header = [
      "ply",
      "format binary_little_endian 1.0",
      "element vertex 2",
      "property float x",
      "property float y",
      "property float z",
      "property uchar intensity",
      "end_header",
      ""
    ].join("\n");
    const headerBytes = new TextEncoder().encode(header);
    const buffer = new ArrayBuffer(headerBytes.length + 2 * 13);
    const bytes = new Uint8Array(buffer);
    bytes.set(headerBytes);
    const view = new DataView(buffer);
    let offset = headerBytes.length;
    view.setFloat32(offset, 1, true);
    view.setFloat32(offset + 4, 2, true);
    view.setFloat32(offset + 8, 3, true);
    view.setUint8(offset + 12, 128);
    offset += 13;
    view.setFloat32(offset, -1, true);
    view.setFloat32(offset + 4, -2, true);
    view.setFloat32(offset + 8, -3, true);
    view.setUint8(offset + 12, 255);

    const parsed = parseBinaryLittleEndianPly(buffer);
    expect(parsed.count).toBe(2);
    expect(Array.from(parsed.positions.slice(0, 6))).toEqual([1, 2, 3, -1, -2, -3]);
    expect(parsed.bounds.min).toEqual([-1, -2, -3]);
    expect(parsed.bounds.max).toEqual([1, 2, 3]);
  });
});

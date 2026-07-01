import { createReadStream, promises as fs } from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..", "app", "data", "apartment");
const host = process.env.HOME_APARTMENT_ASSET_HOST || "127.0.0.1";
const port = Number(process.env.HOME_APARTMENT_ASSET_PORT || 5190);
const allowOrigin = process.env.HOME_APARTMENT_ASSET_ORIGIN || "*";

const types = {
  ".json": "application/json; charset=utf-8",
  ".glb": "model/gltf-binary",
  ".ply": "application/octet-stream",
  ".spz": "application/octet-stream",
  ".las": "application/octet-stream",
};

const HEAVY_CACHE = "private, max-age=604800, stale-while-revalidate=86400";
const METADATA_CACHE = "private, no-cache";

const assetGroups = [
  { key: "points", required: true, files: ["points.ply"] },
  { key: "scan", required: true, files: ["apartment.spz", "apartment.ply"] },
  { key: "scanMobile", required: false, files: ["apartment.mobile.spz", "apartment.mobile.ply"] },
  { key: "mesh", required: true, files: ["mesh.glb", "collision.glb"] },
  { key: "meshMobile", required: false, files: ["mesh.mobile.glb"] },
  { key: "manifest", required: true, files: ["manifest.json"] },
  { key: "frame", required: true, files: ["frame.json"] },
  { key: "floor", required: true, files: ["floor.json"] },
  { key: "seedModel", required: true, files: ["seed-model.json"] },
];

async function fileInfo(file) {
  try {
    const stat = await fs.stat(path.join(root, file));
    return {
      file,
      present: stat.isFile(),
      size: stat.isFile() ? stat.size : 0,
    };
  } catch {
    return { file, present: false, size: 0 };
  }
}

async function assetHealth() {
  const groups = {};
  let assetsReady = true;
  for (const group of assetGroups) {
    const files = await Promise.all(group.files.map(fileInfo));
    const present = files.some((item) => item.present);
    groups[group.key] = {
      required: group.required,
      present,
      files,
    };
    if (group.required && !present) assetsReady = false;
  }
  return {
    ok: true,
    service: "home-apartment-assets",
    root,
    assetsReady,
    assets: groups,
    ts: new Date().toISOString(),
  };
}

function sendHeaders(res, status, headers = {}) {
  res.writeHead(status, {
    "Access-Control-Allow-Origin": allowOrigin,
    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
    "Access-Control-Allow-Headers": "Range, Content-Type",
    "Cross-Origin-Resource-Policy": "cross-origin",
    ...headers,
  });
}

function safePath(urlPath) {
  const decoded = decodeURIComponent(urlPath.split("?")[0] || "/");
  const cleaned = decoded.replace(/^\/+/, "");
  const full = path.resolve(root, cleaned);
  if (!full.startsWith(root + path.sep) && full !== root) return null;
  return full;
}

function fileEtag(stat) {
  return `"${stat.size.toString(16)}-${Math.floor(stat.mtimeMs).toString(16)}"`;
}

function normalizeEtag(value) {
  return String(value || "").trim().replace(/^W\//, "").replace(/^"|"$/g, "");
}

function clientHasFreshCopy(req, stat, etag) {
  const inm = String(req.headers["if-none-match"] || "");
  if (inm && inm.split(",").some((v) => normalizeEtag(v) === normalizeEtag(etag))) return true;
  const ims = req.headers["if-modified-since"];
  if (!ims) return false;
  const since = Date.parse(String(ims));
  return Number.isFinite(since) && Math.floor(stat.mtimeMs / 1000) <= Math.floor(since / 1000);
}

function cachePolicy(file) {
  const ext = path.extname(file).toLowerCase();
  if (ext === ".json") return METADATA_CACHE;
  if ([".ply", ".spz", ".glb", ".wasm", ".jpg", ".jpeg", ".png", ".webp"].includes(ext)) return HEAVY_CACHE;
  return METADATA_CACHE;
}

const server = http.createServer(async (req, res) => {
  if (req.method === "OPTIONS") {
    sendHeaders(res, 204);
    res.end();
    return;
  }
  if (req.method !== "GET" && req.method !== "HEAD") {
    sendHeaders(res, 405, { Allow: "GET, HEAD, OPTIONS" });
    res.end("method not allowed");
    return;
  }

  const pathname = (req.url || "/").split("?")[0] || "/";
  if (pathname === "/healthz") {
    const body = JSON.stringify(await assetHealth());
    sendHeaders(res, 200, {
      "Content-Type": "application/json; charset=utf-8",
      "Content-Length": Buffer.byteLength(body),
      "Cache-Control": "no-store",
    });
    if (req.method === "HEAD") res.end();
    else res.end(body);
    return;
  }

  const file = safePath(req.url || "/");
  if (!file) {
    sendHeaders(res, 403);
    res.end("forbidden");
    return;
  }

  try {
    const stat = await fs.stat(file);
    if (!stat.isFile()) throw new Error("not a file");
    const ext = path.extname(file).toLowerCase();
    const etag = fileEtag(stat);
    const cache = cachePolicy(file);
    const headers = {
      "Content-Type": types[ext] || "application/octet-stream",
      "Content-Length": stat.size,
      "Cache-Control": cache,
      "ETag": etag,
      "Last-Modified": stat.mtime.toUTCString(),
      "Accept-Ranges": "bytes",
    };
    if (clientHasFreshCopy(req, stat, etag)) {
      sendHeaders(res, 304, {
        "Cache-Control": headers["Cache-Control"],
        "ETag": etag,
        "Last-Modified": headers["Last-Modified"],
      });
      res.end();
      return;
    }
    sendHeaders(res, 200, headers);
    if (req.method === "HEAD") {
      res.end();
      return;
    }
    createReadStream(file).pipe(res);
  } catch {
    sendHeaders(res, 404);
    res.end("not found");
  }
});

server.listen(port, host, () => {
  console.log(`apartment assets: http://${host}:${port}/ -> ${root}`);
});

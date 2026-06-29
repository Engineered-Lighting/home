import { createReadStream, promises as fs } from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..", "app", "data", "apartment");
const host = "127.0.0.1";
const port = Number(process.env.HOME_APARTMENT_ASSET_PORT || 5190);

const types = {
  ".json": "application/json; charset=utf-8",
  ".glb": "model/gltf-binary",
  ".ply": "application/octet-stream",
  ".spz": "application/octet-stream",
  ".las": "application/octet-stream",
};

function sendHeaders(res, status, headers = {}) {
  res.writeHead(status, {
    "Access-Control-Allow-Origin": "http://127.0.0.1:5180",
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
    sendHeaders(res, 200, {
      "Content-Type": types[ext] || "application/octet-stream",
      "Content-Length": stat.size,
      "Cache-Control": "no-store",
    });
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

import path from "node:path";

function safeFile(root, urlPath) {
  let decoded;
  try {
    decoded = decodeURIComponent(String(urlPath || "").split("?")[0]);
  } catch {
    return null;
  }
  if (
    decoded.includes("\\") || decoded.startsWith("//") ||
    /^[A-Za-z]:/.test(decoded) || /[\u0000-\u001f\u007f]/.test(decoded)
  ) return null;
  const resolvedRoot = path.resolve(root);
  const normalized = path.normalize(decoded).replace(/^[/\\]+/, "");
  const full = path.resolve(resolvedRoot, normalized);
  const relative = path.relative(resolvedRoot, full);
  if (
    path.isAbsolute(relative) || relative === ".." ||
    relative.startsWith(`..${path.sep}`)
  ) return null;
  return full;
}

export { safeFile };

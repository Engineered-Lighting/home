function parseAgentOriginBoundary(raw, { allowInsecure = false } = {}) {
  const values = String(raw || "").split(",").map((value) => value.trim()).filter(Boolean);
  if (!values.length) {
    return { configured: false, valid: false, origins: [], byHost: new Map(), error: "missing" };
  }
  const origins = [];
  const byHost = new Map();
  for (const value of values) {
    try {
      const parsed = new URL(value);
      const protocolAllowed = parsed.protocol === "https:" ||
        (allowInsecure && parsed.protocol === "http:");
      const host = parsed.host.toLowerCase();
      if (
        !protocolAllowed || value !== parsed.origin || parsed.username || parsed.password ||
        parsed.pathname !== "/" || parsed.search || parsed.hash || !host || byHost.has(host)
      ) {
        return { configured: true, valid: false, origins: [], byHost: new Map(), error: "invalid" };
      }
      origins.push(parsed.origin);
      byHost.set(host, parsed.origin);
    } catch {
      return { configured: true, valid: false, origins: [], byHost: new Map(), error: "invalid" };
    }
  }
  return { configured: true, valid: true, origins: Object.freeze(origins), byHost, error: null };
}

function classifyAgentOrigin(headers, boundary) {
  const host = String(headers?.host || "").trim().toLowerCase();
  const expectedOrigin = boundary?.valid ? boundary.byHost.get(host) : undefined;
  if (!expectedOrigin) {
    return { agentHost: false, originAllowed: false, expectedOrigin: null };
  }
  const suppliedOrigin = String(headers?.origin || "").trim();
  return {
    agentHost: true,
    originAllowed: !suppliedOrigin || suppliedOrigin === expectedOrigin,
    expectedOrigin,
  };
}

export { classifyAgentOrigin, parseAgentOriginBoundary };

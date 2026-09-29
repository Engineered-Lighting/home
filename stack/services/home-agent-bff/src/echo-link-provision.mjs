import fs from "node:fs";
import path from "node:path";

function read(file, maximum) {
  if (typeof file !== "string" || !path.isAbsolute(file)) throw new Error("link_provision_absolute_file_required");
  const fd = fs.openSync(file, "r");
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.size > maximum) throw new Error("link_provision_file_rejected");
    const bytes = Buffer.alloc(maximum + 1);
    const count = fs.readSync(fd, bytes, 0, bytes.length, 0);
    if (count > maximum) throw new Error("link_provision_file_rejected");
    return bytes.subarray(0, count).toString("utf8").trim();
  } finally { fs.closeSync(fd); }
}

// Operator-owned file, never a browser payload. Secrets are separate mounted
// files, so configuration receipts can omit their contents entirely.
export function loadEchoLinkProvision(file) {
  const value = JSON.parse(read(file, 8192));
  const fields = ["version", "commitmentKeyFile", "journalKeyFile", "pairingDbPath",
    "proofDbPath", "revocationDbPath", "sessionRevocation", "proofIngress",
    "linkIssuance", "victoriaHandoff", "linkReview", "victoriaBrowserOrigin"];
  if (!value || typeof value !== "object" || Array.isArray(value) || value.version !== 1 ||
      !fields.every(name=>Object.hasOwn(value,name)) ||
      Object.keys(value).some(name=>!fields.includes(name) && !["personalMemory","personalMemoryHomeOrigins","lighting"].includes(name))) throw new Error("link_provision_shape_rejected");
  const key = name => {
    const encoded = read(value[name], 128), bytes = Buffer.from(encoded, "base64url");
    if (!/^[A-Za-z0-9_-]{43}$/.test(encoded) || bytes.length !== 32 ||
        bytes.toString("base64url") !== encoded) throw new Error("link_provision_key_rejected");
    return bytes;
  };
  const transport = (name, field = "endpoint") => {
    const entry = value[name];
    if (!entry || typeof entry !== "object" || Array.isArray(entry) ||
        Object.keys(entry).sort().join() !== [field, "credentialFile"].sort().join()) {
      throw new Error("link_provision_transport_rejected");
    }
    const credential = read(entry.credentialFile, 128);
    if (!/^[a-f0-9]{64}$/.test(credential)) throw new Error("link_provision_credential_rejected");
    return { [field]: entry[field], credential };
  };
  return {
    commitmentKey: key("commitmentKeyFile"), journalKey: key("journalKeyFile"),
    pairingDbPath: value.pairingDbPath, proofDbPath: value.proofDbPath,
    revocationDbPath: value.revocationDbPath, victoriaBrowserOrigin: value.victoriaBrowserOrigin,
    sessionRevocation: transport("sessionRevocation"), proofIngress: transport("proofIngress"),
    linkIssuance: transport("linkIssuance"), victoriaHandoff: transport("victoriaHandoff"),
    linkReview: transport("linkReview", "origin"),
    ...(Object.hasOwn(value,"personalMemory") ? {personalMemory:transport("personalMemory","origin")} : {}),
    ...(Object.hasOwn(value,"lighting") ? {lighting:transport("lighting","origin")} : {}),
    personalMemoryHomeOrigins:value.personalMemoryHomeOrigins ?? [],
  };
}

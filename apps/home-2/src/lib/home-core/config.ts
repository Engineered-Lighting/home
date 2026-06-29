export const HOME2_CONFIG_KEY = "home2.config.v1";
const LEGACY_PREFS_KEY = "hg-prefs";

export type Home2Config = {
  haBaseUrl: string;
  haToken: string;
  metricsBaseUrl: string;
  intelligenceBaseUrl: string;
  trackerBaseUrl: string;
  supervisorBaseUrl: string;
  supervisorToken: string;
  videoLabelerBaseUrl: string;
  visionBaseUrl: string;
  apartmentAssetBase: string;
  importedLegacyPrefsAt?: string;
};

export const defaultConfig: Home2Config = {
  haBaseUrl: "/proxy/ha",
  haToken: "",
  metricsBaseUrl: "/proxy/metrics",
  intelligenceBaseUrl: "/proxy/intelligence",
  trackerBaseUrl: "/proxy/tracker",
  supervisorBaseUrl: "/proxy/supervisor",
  supervisorToken: "",
  videoLabelerBaseUrl: "/proxy/video-labeler",
  visionBaseUrl: "/proxy/vision",
  apartmentAssetBase: "/assets/apartment"
};

type StorageLike = Pick<Storage, "getItem" | "setItem">;

function safeParse(value: string | null): Record<string, unknown> | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

function firstString(source: Record<string, unknown>, keys: string[]): string | undefined {
  for (const key of keys) {
    const value = source[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return undefined;
}

export function importLegacyPrefs(storage: StorageLike = window.localStorage): Partial<Home2Config> | null {
  const prefs = safeParse(storage.getItem(LEGACY_PREFS_KEY));
  if (!prefs) return null;

  const haBaseUrl = firstString(prefs, ["endpoint", "haUrl", "haBaseUrl", "url"]);
  const haToken = firstString(prefs, ["token", "haToken", "accessToken"]);
  const trackerBaseUrl = firstString(prefs, ["apartment3d.trackerBase", "trackerBaseUrl"]);

  const imported: Partial<Home2Config> = {};
  if (haBaseUrl) imported.haBaseUrl = haBaseUrl.replace(/\/+$/, "");
  if (haToken) imported.haToken = haToken;
  if (trackerBaseUrl) imported.trackerBaseUrl = trackerBaseUrl.replace(/\/+$/, "");

  return Object.keys(imported).length ? imported : null;
}

export function loadConfig(storage: StorageLike = window.localStorage): Home2Config {
  const stored = safeParse(storage.getItem(HOME2_CONFIG_KEY));
  if (stored) return { ...defaultConfig, ...stored } as Home2Config;

  const imported = importLegacyPrefs(storage);
  const config = {
    ...defaultConfig,
    ...(imported || {}),
    importedLegacyPrefsAt: imported ? new Date().toISOString() : undefined
  };
  storage.setItem(HOME2_CONFIG_KEY, JSON.stringify(config));
  return config;
}

export function saveConfig(config: Home2Config, storage: StorageLike = window.localStorage): Home2Config {
  const normalized: Home2Config = {
    ...defaultConfig,
    ...config,
    haBaseUrl: config.haBaseUrl.replace(/\/+$/, ""),
    metricsBaseUrl: config.metricsBaseUrl.replace(/\/+$/, ""),
    intelligenceBaseUrl: config.intelligenceBaseUrl.replace(/\/+$/, ""),
    trackerBaseUrl: config.trackerBaseUrl.replace(/\/+$/, ""),
    supervisorBaseUrl: config.supervisorBaseUrl.replace(/\/+$/, ""),
    videoLabelerBaseUrl: config.videoLabelerBaseUrl.replace(/\/+$/, ""),
    visionBaseUrl: config.visionBaseUrl.replace(/\/+$/, ""),
    apartmentAssetBase: config.apartmentAssetBase.replace(/\/+$/, "")
  };
  storage.setItem(HOME2_CONFIG_KEY, JSON.stringify(normalized));
  return normalized;
}

export function baseToWsUrl(baseUrl: string, path = ""): string {
  const cleanPath = path.startsWith("/") ? path : `/${path}`;
  if (baseUrl.startsWith("/")) {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}${baseUrl}${cleanPath}`;
  }
  const url = new URL(baseUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = `${url.pathname.replace(/\/+$/, "")}${cleanPath}`;
  return url.toString();
}

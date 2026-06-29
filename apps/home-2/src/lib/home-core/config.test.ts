import { describe, expect, it } from "vitest";
import { HOME2_CONFIG_KEY, defaultConfig, importLegacyPrefs, loadConfig, saveConfig } from "./config";

class MemoryStorage implements Pick<Storage, "getItem" | "setItem"> {
  private map = new Map<string, string>();

  getItem(key: string): string | null {
    return this.map.get(key) || null;
  }

  setItem(key: string, value: string): void {
    this.map.set(key, value);
  }
}

describe("home2 config", () => {
  it("creates an isolated home2 config key from defaults", () => {
    const storage = new MemoryStorage();
    const config = loadConfig(storage);
    expect(config.metricsBaseUrl).toBe("/proxy/metrics");
    expect(storage.getItem(HOME2_CONFIG_KEY)).toContain("/proxy/tracker");
    expect(storage.getItem("hg-prefs")).toBeNull();
  });

  it("imports compatible legacy prefs without rewriting them", () => {
    const storage = new MemoryStorage();
    storage.setItem("hg-prefs", JSON.stringify({ endpoint: "http://ha.local:8123/", token: "abc" }));
    const imported = importLegacyPrefs(storage);
    expect(imported).toEqual({ haBaseUrl: "http://ha.local:8123", haToken: "abc" });
    const config = loadConfig(storage);
    expect(config.haBaseUrl).toBe("http://ha.local:8123");
    expect(storage.getItem("hg-prefs")).toContain("abc");
  });

  it("normalizes saved base urls", () => {
    const storage = new MemoryStorage();
    const saved = saveConfig({ ...defaultConfig, metricsBaseUrl: "/proxy/metrics/" }, storage);
    expect(saved.metricsBaseUrl).toBe("/proxy/metrics");
  });
});

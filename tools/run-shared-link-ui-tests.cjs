"use strict";
// Browser rendering of the actual shipped panel/API with isolated HTTP fixtures.
// No HA, Core, model or home endpoint is contacted.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");
const root = path.resolve(__dirname, "..");
const id = "00000000-0000-0000-0000-000000000001";
const gesture = "00000000-0000-0000-0000-000000000002";
const digest = "a".repeat(64);
const authority = { version: 1, site_id: "echo", ha_issuer_id: "home-assistant:echo" };
const screenshot = path.join(root, ".tmp/shared-link-review.png");

(async () => {
  const browser = await chromium.launch({ headless: true });
  let passed = 0;
  async function scenario(name, overrides, run) {
    const context = await browser.newContext({ viewport: { width: 1050, height: 950 } });
    const page = await context.newPage();
    const calls = [], errors = [];
    let confirmed = false, sharingOperation = null;
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/*", async (route) => {
      const url = new URL(route.request().url());
      if (url.origin !== "https://agent.test") throw new Error("unexpected outbound origin");
      if (url.pathname.startsWith("/home-agent/")) {
        const name = url.pathname === "/home-agent/" ? "index.html" : url.pathname.split("/").at(-1);
        assert.ok(["index.html", "api.js", "panel.js", "panel.css"].includes(name));
        return route.fulfill({ body: fs.readFileSync(path.join(root, "app/src/home-agent", name)),
          contentType: name.endsWith("css") ? "text/css" : name.endsWith("html") ? "text/html" : "text/javascript" });
      }
      calls.push({ path: url.pathname, body: route.request().postDataJSON(), headers: route.request().headers() });
      const reply = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
      if (url.pathname.endsWith("auth/session")) return reply({ authenticated: true, user_id: "owner",
        csrf_token: "csrf", authority, shared_link_review_enabled: overrides.enabled !== false,
        personal_memory_enabled: overrides.sharing === true, lighting_enabled: overrides.lighting === true });
      if (url.pathname.endsWith("onboarding/status")) return reply({ state: "bound" });
      if (url.pathname.endsWith("snapshot")) return reply({ rollout_mode: "shadow", capabilities: {}, preferences: {} });
      if (url.pathname.endsWith("auth/logout")) return reply({ ok: true });
      if (url.pathname.endsWith("personal-memory/sharing-propose")) {
        sharingOperation=route.request().postDataJSON().operation_id;
        return reply({version:1,result:{version:1,operation_id:sharingOperation,source:"core.personal-preferences.v1",
          applies_to:"both_homes",effect:"read_and_manage_confirmed_preferences",reviewed_digest:digest,
          grants_expire_at:new Date(Date.now()+86400000).toISOString(),expires_at:new Date(Date.now()+60000).toISOString()}});
      }
      if (url.pathname.endsWith("personal-memory/sharing-confirm") || url.pathname.endsWith("personal-memory/sharing-outcome")) {
        if(overrides.uncertain && url.pathname.endsWith("sharing-confirm")) return reply({error:"unknown"},503);
        return reply({version:1,result:{version:1,operation_id:sharingOperation,status:"committed"}});
      }
      if (url.pathname.endsWith("lighting/consent-propose")) {
        sharingOperation=route.request().postDataJSON().operation_id;
        return reply({version:1,result:{version:1,operation_id:sharingOperation,source:"core.lighting.v1",
          applies_to:"both_homes",effect:"switch_allowlisted_lights_after_each_confirmation",reviewed_digest:digest,
          grants_expire_at:new Date(Date.now()+86400000).toISOString(),expires_at:new Date(Date.now()+60000).toISOString()}});
      }
      if (url.pathname.endsWith("lighting/consent-confirm") || url.pathname.endsWith("lighting/consent-outcome")) {
        return reply({version:1,result:{version:1,operation_id:sharingOperation,status:"committed"}});
      }
      if (url.pathname.endsWith("shared-identity/review")) return reply({ version: 1, ceremony_id: id,
        gesture_id: gesture, reviewed_digest: digest,
        expires_at: new Date(Date.now()+(overrides.expiresMs || 60_000)).toISOString(), accounts: [
          { site_id: "echo", issuer_id: "home-assistant:echo", subject: "owner" },
          { site_id: "victoria", issuer_id: overrides.wrongIssuer ? "home-assistant:echo" : "home-assistant:victoria",
            subject: overrides.longSubject ? "v".repeat(64) : "victoria-owner" },
        ] });
      if (url.pathname.endsWith("shared-identity/confirm")) {
        confirmed = true;
        if (overrides.uncertain) return reply({ error: "linking_unavailable" }, 503);
      }
      if (url.pathname.endsWith("shared-identity/outcome") || url.pathname.endsWith("shared-identity/confirm")) {
        return confirmed ? reply({ version: 1, status: "confirmed", ceremony_id: id }) : reply({ error: "unavailable" }, 503);
      }
      return reply({});
    });
    try {
      await page.goto(`https://agent.test/home-agent/#shared-link/${id}`);
      await page.getByRole("button", { name: "Sign out", exact: true }).waitFor();
      await run(page, calls);
      assert.deepEqual(errors, []);
      passed++;
      process.stdout.write(`PASS ${name}\n`);
    } finally { await context.close(); }
  }
  try {
    await scenario("explicit account review and confirmation", {}, async (page, calls) => {
      assert.equal(calls.some((c) => c.path.endsWith("shared-identity/confirm")), false);
      await page.getByRole("button", { name: "Review both accounts", exact: true }).click();
      await page.getByText("victoria-owner", { exact: true }).waitFor();
      const button = page.getByRole("button", { name: "Link these two accounts", exact: true });
      assert.equal(await button.isDisabled(), true);
      await page.screenshot({ path: screenshot, fullPage: true });
      await page.getByRole("checkbox", { name: "These are both my accounts." }).check();
      await button.click();
      await page.getByText("Your two accounts are linked.", { exact: true }).waitFor();
      const confirmations = calls.filter((c) => c.path.endsWith("shared-identity/confirm"));
      assert.equal(confirmations.length, 1);
      assert.deepEqual(confirmations[0].body, { ceremony_id: id, gesture_id: gesture, reviewed_digest: digest });
      assert.equal(confirmations[0].headers["x-csrf-token"], "csrf");
    });
    await scenario("uncertain confirmation offers lookup only", { uncertain: true }, async (page, calls) => {
      await page.getByRole("button", { name: "Review both accounts", exact: true }).click();
      await page.getByRole("checkbox").check();
      await page.getByRole("button", { name: "Link these two accounts", exact: true }).click();
      await page.getByText(/The confirmation outcome is unknown/).waitFor();
      assert.equal(await page.getByRole("button", { name: "Link these two accounts", exact: true }).count(), 0);
      await page.getByRole("button", { name: "Check confirmation status", exact: true }).click();
      await page.getByText("Your two accounts are linked.", { exact: true }).waitFor();
      assert.equal(calls.filter((c) => c.path.endsWith("shared-identity/confirm")).length, 1);
      assert.equal(calls.filter((c) => c.path.endsWith("shared-identity/outcome")).length, 1);
    });
    await scenario("expired review disables approval", { expiresMs: 1200 }, async (page, calls) => {
      await page.getByRole("button", { name: "Review both accounts", exact: true }).click();
      await page.getByText(/This review expired/).waitFor();
      assert.equal(await page.getByRole("button", { name: "Link these two accounts", exact: true }).isDisabled(), true);
      assert.equal(calls.some((c) => c.path.endsWith("shared-identity/confirm")), false);
    });
    await scenario("wrong issuer cannot become an approval", { wrongIssuer: true }, async (page) => {
      await page.getByRole("button", { name: "Review both accounts", exact: true }).click();
      await page.getByText(/This account review is unavailable/).waitFor();
      assert.equal(await page.getByRole("checkbox").count(), 0);
    });
    await scenario("capability absent keeps ceremony screen hidden", { enabled: false }, async (page, calls) => {
      assert.equal(await page.getByRole("heading", { name: "Connect your two homes" }).count(), 0);
      assert.equal(calls.some((c) => c.path.includes("shared-identity")), false);
    });
    await scenario("sign-out clears private reviewed accounts", {}, async (page) => {
      await page.getByRole("button", { name: "Review both accounts", exact: true }).click();
      await page.getByText("victoria-owner", { exact: true }).waitFor();
      await page.getByRole("button", { name: "Sign out", exact: true }).click();
      await page.getByRole("heading", { name: "Authentication required" }).waitFor();
      assert.equal(await page.getByText("victoria-owner", { exact: true }).count(), 0);
    });
    await scenario("narrow review wraps long account IDs", { longSubject: true }, async (page) => {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.getByRole("button", { name: "Review both accounts", exact: true }).click();
      await page.getByText("v".repeat(64), { exact: true }).waitFor();
      const card = page.locator(".agent-shared-link");
      assert.equal(await card.evaluate((node) => node.scrollWidth <= node.clientWidth), true);
      await card.screenshot({ path: path.join(root, ".tmp/shared-link-review-mobile.png") });
    });
    await scenario("phone preference sharing requires separate explicit consent", {sharing:true}, async(page,calls)=>{
      await page.setViewportSize({width:390,height:844});
      await page.getByRole("button",{name:"Review preference sharing",exact:true}).click();
      const confirm=page.getByRole("button",{name:"Confirm preference sharing",exact:true});
      assert.equal(await confirm.isDisabled(),true);
      assert.equal(calls.some(c=>c.path.endsWith("sharing-confirm")),false);
      await page.getByRole("checkbox",{name:"Allow Home to read and manage this shared preference."}).check();
      const card=page.locator(".agent-preference-sharing");
      assert.equal(await card.evaluate(node=>node.scrollWidth<=node.clientWidth),true);
      await card.screenshot({path:path.join(root,".tmp/preference-sharing-mobile.png")});
      await confirm.click();
      await page.getByText(/Preference sharing was confirmed/).waitFor();
      const sent=calls.filter(c=>c.path.endsWith("sharing-confirm"));
      assert.equal(sent.length,1);assert.equal(sent[0].headers["x-csrf-token"],"csrf");
    });
    await scenario("uncertain preference sharing uses status lookup only", {sharing:true,uncertain:true}, async(page,calls)=>{
      await page.getByRole("button",{name:"Review preference sharing",exact:true}).click();
      await page.getByRole("checkbox",{name:"Allow Home to read and manage this shared preference."}).check();
      await page.getByRole("button",{name:"Confirm preference sharing",exact:true}).click();
      await page.getByRole("button",{name:"Check sharing status",exact:true}).click();
      await page.getByText(/Preference sharing was confirmed/).waitFor();
      assert.equal(calls.filter(c=>c.path.endsWith("sharing-confirm")).length,1);
      assert.equal(calls.filter(c=>c.path.endsWith("sharing-outcome")).length,1);
    });
    await scenario("lighting control requires its own explicit consent", {lighting:true}, async(page,calls)=>{
      await page.setViewportSize({width:390,height:844});
      await page.getByRole("button",{name:"Review lighting control",exact:true}).click();
      const confirm=page.getByRole("button",{name:"Confirm lighting control",exact:true});
      assert.equal(await confirm.isDisabled(),true);
      assert.equal(calls.some(c=>c.path.endsWith("lighting/consent-confirm")),false);
      await page.getByRole("checkbox",{name:"Allow Home to switch these lights after I confirm each change."}).check();
      const card=page.locator(".agent-lighting-consent");
      assert.equal(await card.evaluate(node=>node.scrollWidth<=node.clientWidth),true);
      await card.screenshot({path:path.join(root,".tmp/lighting-consent-mobile.png")});
      await confirm.click();
      await page.getByText(/Lighting control was allowed/).waitFor();
      const sent=calls.filter(c=>c.path.endsWith("lighting/consent-confirm"));
      assert.equal(sent.length,1);assert.equal(sent[0].headers["x-csrf-token"],"csrf");
      assert.deepEqual(Object.keys(sent[0].body).sort(),["operation_id","reviewed_digest","version"]);
    });
    await scenario("lighting consent is hidden unless the session enables lighting", {}, async(page)=>{
      assert.equal(await page.getByRole("heading",{name:"Lighting control between homes"}).count(),0);
    });
    process.stdout.write(`${passed} browser scenarios passed; screenshot ${screenshot}\n`);
  } finally { await browser.close(); }
})().catch((error) => { process.stderr.write(String(error.stack || error)+"\n"); process.exitCode = 1; });

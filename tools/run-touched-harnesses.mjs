#!/usr/bin/env node
/* ===================================================================== * run-touched-harnesses.mjs — run the test harnesses that guard the files
 * touched by a diff.
 *
 * The tools/run-*.js harnesses and explicitly mapped Node test suites guard
 * specific app/src surfaces. This script maps changed files to guarding
 * harnesses and runs exactly that set (plus
 * check-jsx for any app/src change), so a PR that touches a surface runs the
 * surface's guards.
 *
 * Usage:
 *   node tools/run-touched-harnesses.mjs --base origin/main
 *   node tools/run-touched-harnesses.mjs --files app/src/home-app.jsx,...
 *   node tools/run-touched-harnesses.mjs --all
 *   node tools/run-touched-harnesses.mjs --all --list
 *   node tools/run-touched-harnesses.mjs --base origin/main --shard 2/3 \
 *     --receipt .tmp/touched-harnesses/shard-2-of-3.json
 *   node tools/run-touched-harnesses.mjs --base origin/main \
 *     --verify-receipts .tmp/touched-harnesses --shards 3
 *
 * --shard i/n runs only the plan items a deterministic partition assigns to
 * worker i. Every worker computes the same full plan first, so the n shards
 * are disjoint and their union is exactly the unsharded plan (proved by
 * tools/test-touched-shards.mjs). --receipt records the full plan, the
 * shard's plan and each result; --verify-receipts recomputes the plan for the
 * same base and fails unless the n receipts cover it exactly once, all green.
 * ========================================================================= */
"use strict";

import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const TOOLS = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(TOOLS, "..");

/* file-pattern → harnesses that pin or exercise it. Patterns are tested with
 * RegExp.test against repo-relative POSIX paths. Keep this map in sync with
 * new harnesses (a harness with no row here simply never auto-runs). */
const MAP = [
  [/^(?:app\/src\/(?:home-app\.jsx|home-tokens\.css|home-apartment(?:\.jsx|-den-perception\.jsx|-camera\.jsx|-camera-source\.js|-perception-(?:source|player|view|dock-model)\.js)|home-den-lab-stream\.js|home-feature-loader\.js|home-perception-lab(?:\.jsx|-graph\.jsx|-data\.js|-transport\.js)|home-lighting-brain-(?:client|follower)\.js|home-3d\/apartment-perception-layer\.mjs)|tools\/(?:test-apartment-connected-view-browser\.mjs|fixtures\/apartment-connected-view\.json))$/, ["test-apartment-connected-view-browser.mjs"]],
  [/^(?:app\/src\/home-apartment-perception-view\.js|tools\/test-home-apartment-perception-view\.cjs)$/, ["test-home-apartment-perception-view.cjs"]],
  [/^(?:web-gateway\/(?:server|perception-lab-route)\.mjs|tools\/test-web-gateway-(?:perception-lab|lab-mounted)\.mjs)$/, ["test-web-gateway-perception-lab.mjs", "test-web-gateway-lab-mounted.mjs"]],
  [/^(?:app\/src\/(?:home-apartment-perception-source|home-perception-lab-transport)\.js|tools\/test-(?:apartment-perception-source\.cjs|perception-lab-transport\.mjs))$/, ["test-apartment-perception-source.cjs", "test-perception-lab-transport.mjs"]],
  [/^(?:app\/src\/(?:home-app\.jsx|home-metrics(?:-lab)?\.jsx)|tools\/test-home-full-ui-metrics-browser\.mjs)$/, ["test-home-full-ui-metrics-browser.mjs"]],
  [/^(?:app\/src\/(?:home-app|home-people)\.jsx|tools\/test-home-full-ui-people-browser\.mjs)$/, ["test-home-full-ui-people-browser.mjs"]],
  [/^(?:app\/src\/(?:home-app|home-intelligence)\.jsx|tools\/test-home-full-ui-intelligence-browser\.mjs)$/, ["test-home-full-ui-intelligence-browser.mjs"]],
  [/^(?:app\/src\/(?:home-app|home-perception-lab)\.jsx|tools\/test-home-full-ui-lab-browser\.mjs)$/, ["test-home-full-ui-lab-browser.mjs"]],
  [/^tools\/fixtures\/home-full-ui-harness\.mjs$/, ["test-home-full-ui-metrics-browser.mjs", "test-home-full-ui-people-browser.mjs", "test-home-full-ui-intelligence-browser.mjs", "test-home-full-ui-lab-browser.mjs", "test-apartment-connected-view-browser.mjs"]],
  [/^(?:web-gateway\/(?:server|content-pack-route|equipment-registration|lighting-layer)\.mjs|tools\/test-web-gateway-(?:content-pack-route|installed-content|lighting-layer)\.mjs)$/, ["test-web-gateway-content-pack-route.mjs", "test-web-gateway-installed-content.mjs", "test-web-gateway-lighting-layer.mjs"]],
  [/^app\/src\/(?:home-app\.jsx|home-apartment-data\.js)$/, ["test-chat-output-refusal.cjs", "test-chat-service-envelope.cjs", "run-control-card-tests.js", "run-simulation-control-tests.js"]],
  [/^tools\/test-chat-(?:output-refusal|service-envelope)\.cjs$/, ["test-chat-output-refusal.cjs", "test-chat-service-envelope.cjs"]],
  [/^app\/src\/(?:home-apartment-aim\.jsx|home-tokens\.css)$/, ["test-apartment-aim-theme-browser.mjs"]],
  [/^tools\/test-apartment-aim-theme-browser\.mjs$/, ["test-apartment-aim-theme-browser.mjs"]],
  [/^app\/src\/(?:home-apartment\.jsx|home-tokens\.css)$/, ["test-apartment-photo-recovery.cjs", "test-apartment-photo-recovery-browser.mjs"]],
  [/^tools\/test-apartment-photo-recovery(?:-browser\.mjs|\.cjs)$/, ["test-apartment-photo-recovery.cjs", "test-apartment-photo-recovery-browser.mjs"]],
  [/^app\/src\/(?:home-tokens\.css|home-app\.jsx|home-apartment(?:-ui|-cards|-edit)?\.jsx|home-chat-header-actions\.jsx)$/, ["test-apartment-shell-audit-browser.mjs", "test-apartment-shell-audit-echo-browser.mjs", "test-apartment-shell-audit-behaviour-browser.mjs"]],
  [/^tools\/(?:test-apartment-shell-audit(?:-echo|-behaviour)?-browser|fixtures\/apartment-shell-audit(?:-server|-probes)?)\.mjs$/, ["test-apartment-shell-audit-browser.mjs", "test-apartment-shell-audit-echo-browser.mjs", "test-apartment-shell-audit-behaviour-browser.mjs"]],
  [/^app\/src\/home-3d\//, ["test-apartment-picking.mjs", "test-apartment-performance.mjs"]],
  [/^tools\/test-apartment-(?:picking|performance)\.mjs$/, ["test-apartment-picking.mjs", "test-apartment-performance.mjs"]],
  [/^(?:app\/src\/home-3d\/(?:picking|engine)\.js|tools\/run-apartment-cutaway-pick-tests\.mjs)$/, ["run-apartment-cutaway-pick-tests.mjs"]],
  [/^(?:app\/src\/home-3d\/(?:picking|engine)\.js|tools\/(?:run-apartment-beam-path-tests|test-apartment-beam-path-browser)\.mjs)$/, ["run-apartment-beam-path-tests.mjs", "test-apartment-beam-path-browser.mjs"]],
  [/^app\/src\/home-3d\/(?:engine\.js|picking\.js|apartment-ui-overlay-bridge\.mjs)$/, ["test-apartment-ui-overlay-bridge.mjs", "test-apartment-overlay-bridge-browser.mjs"]],
  [/^tools\/test-apartment-(?:ui-overlay-bridge|overlay-bridge-browser)\.mjs$/, ["test-apartment-ui-overlay-bridge.mjs", "test-apartment-overlay-bridge-browser.mjs"]],
  [/^(?:app\/src\/(?:home-3d\/(?:rig|engine)\.js|home-apartment\.jsx|home-tokens\.css)|tools\/(?:run-apartment-rig-fit-tests|test-apartment-rig-fit-browser)\.mjs)$/, ["run-apartment-rig-fit-tests.mjs", "run-apartment-edit-orbit-tests.mjs", "test-apartment-rig-fit-browser.mjs"]],
  [/^tools\/(?:lib\/home-frontend-inventory|test-home-frontend-inventory)\.cjs$/, ["run-bootstrap-tests.js", "run-lab-tests.js", "test-home-frontend-inventory.cjs"]],

  [/^app\/src\/home-app\.jsx$/, [
    "run-chat-dedupe-tests", "run-slash-command-tests", "run-recovery-scenarios-tests",
    "run-bootstrap-tests", "run-lights-drawer-tests", "run-chat-ui-regression",
    "run-lab-tests", "run-capabilities-tests", "run-simulation-command-tests",
    "run-app-frigate-label-tests",
    "run-apartment-shell-tests.js", "test-home-app-source-contracts.cjs", "test-apartment-chat-presenter.cjs",
    "test-apartment-unread-state.cjs", "test-apartment-unread-integration.mjs",
    "test-apartment-chat-header-actions.cjs", "test-apartment-chat-header-browser.mjs",
  ]],
  [/^app\/src\/home-events\.jsx$/, ["run-events-tests", "run-chat-ui-regression", "run-slash-command-tests"]],
  [/^app\/src\/home-chat-header-actions\.jsx$/, ["test-apartment-chat-header-actions.cjs", "test-apartment-chat-header-browser.mjs"]],
  [/^tools\/test-apartment-chat-header-(?:actions\.cjs|browser\.mjs)$/, ["test-apartment-chat-header-actions.cjs", "test-apartment-chat-header-browser.mjs"]],
  [/^app\/src\/home-lights\.jsx$/, ["run-lights-drawer-tests", "run-recovery-scenarios-tests"]],
  [/^app\/src\/home-people(\.jsx|-helpers\.js|-prewarm\.js)$/, ["run-people-tests"]],
  [/^app\/src\/home-intelligence\.jsx$/, ["run-intelligence-tests"]],
  [/^app\/src\/home-apartment/, ["run-apartment-3d-contract-tests", "run-apartment-aiming-tests", "run-apartment-data-tests", "test-apartment-travel-guard.cjs"]],
  [/^app\/src\/home-apartment(?:-ui|-aim|-cards)?\.jsx$/, ["run-apartment-ui-tests.js", "test-apartment-ui-browser.mjs"]],
  [/^tools\/(?:run-apartment-ui-tests\.js|test-apartment-ui-browser\.mjs)$/, ["run-apartment-ui-tests.js", "test-apartment-ui-browser.mjs"]],
  [/^app\/src\/home-3d\//, ["run-apartment-3d-contract-tests"]],
  [/^(?:app\/src\/(?:home-3d\/(?:lit-(?:view|model)\.mjs|engine\.js)|home-apartment\.jsx)|tools\/test-apartment-lit-(?:view|browser)\.mjs)$/, ["test-apartment-lit-view.mjs", "test-apartment-lit-browser.mjs"]],
  [/^(?:app\/src\/home-3d\/lit-(?:model|view)\.mjs|tools\/test-lit-model\.mjs)$/, ["test-lit-model.mjs", "test-apartment-lit-view.mjs", "test-apartment-lit-browser.mjs"]],
  [/^(?:app\/src\/home-3d\/lit-(?:bvh-bake|bvh-worker|view)\.mjs|tools\/(?:test-lit-bvh-bake(?:-browser)?\.mjs|fixtures\/lit-bake-scenes\.mjs))$/, ["test-lit-bvh-bake.mjs", "test-lit-bvh-bake-browser.mjs", "test-apartment-lit-view.mjs", "test-apartment-lit-browser.mjs"]],
  // L2: Lit's exposure and tone policy, and Lit on the WebGPU path (?renderer=webgpu-webgl).
  [/^(?:app\/src\/home-3d\/lit-exposure\.mjs|tools\/test-lit-exposure\.mjs)$/, ["test-lit-exposure.mjs", "test-apartment-lit-view.mjs", "test-apartment-lit-browser.mjs", "test-apartment-lit-webgpu-browser.mjs", "test-apartment-lit-webgpu-browser-part2.mjs", "test-apartment-lit-webgpu-browser-part3.mjs"]],
  [/^(?:app\/src\/(?:index\.html|vendor\/three-0\.186\.1\/.+|home-3d\/(?:lit-(?:model|view)\.mjs|engine\.js|webgpu-[\w-]+\.mjs|dollhouse-cutaway(?:-tsl)?\.mjs))|tools\/(?:test-apartment-lit-webgpu-browser(?:-part[23])?|fixtures\/apartment-lit-webgpu-browser-proof)\.mjs)$/, ["test-apartment-lit-webgpu-browser.mjs", "test-apartment-lit-webgpu-browser-part2.mjs", "test-apartment-lit-webgpu-browser-part3.mjs"]],
  [/^app\/src\/home-metrics-lab/, ["run-lab-tests"]],
  [/^app\/src\/home-metrics\.jsx$/, ["run-metrics-tests"]],
  [/^app\/src\/home-ai-stack\.jsx$/, ["run-ai-stack-card-tests"]],
  [/^app\/src\/home-stack-actions\.jsx$/, ["run-stack-actions-tests"]],
  [/^app\/src\/home-control\.jsx$/, ["run-control-card-tests"]],
  [/^app\/src\/home-vision\.jsx$/, ["run-vision-tests"]],
  [/^app\/src\/home-proactive\.jsx$/, ["run-proactive-tests"]],
  [/^app\/src\/home-tauri\.jsx$/, ["run-tauri-glue-tests"]],
  [/^app\/src\/home-security\.js$/, ["run-home-security-tests"]],
  [/^app\/src\/home-services\.js$/, ["run-home-services-tests"]],
  [/^app\/src\/home-lighting-events\.jsx$/, ["run-lighting-events-tests"]],
  [/^app\/src\/home-explain/, ["run-explain-tests"]],
  [/^app\/src\/home-external\.jsx$/, ["run-external-tests"]],
  [/^app\/src\/home-look\.jsx$/, ["run-look-tests"]],
  [/^app\/src\/home-natural-look\.js$/, ["run-natural-look-routing-tests"]],
  [/^app\/src\/home-lighting-intent\.js$/, ["run-lighting-intent-tests"]],
  [/^(?:app\/src\/(?:home-lighting-control\.js|home-app\.jsx)|tools\/test-home-lighting-control\.cjs)$/, ["test-home-lighting-control.cjs"]],
  [/^app\/src\/home-worldstate/, ["run-worldstate-tests"]],
  [/^app\/src\/home-spatial/, ["run-spatial-tests"]],
  [/^app\/src\/home-s2s\.jsx$/, ["run-s2s-tests"]],
  [/^app\/src\/home-sse-fetch\.js$/, ["run-sse-fetch-tests"]],
  [/^app\/src\/home-ha\.jsx$/, ["run-ha-client-tests"]],
  [/^app\/src\/home-frigate-perception\.js$/, ["run-frigate-perception-tests"]],
  [/^app\/src\/home-video-labeler-data\.js$/, ["run-video-labeler-data-tests"]],
  [/^app\/src\/(home-fetch-with-retry\.js|home-service-worker\.js)$/, ["run-resilience-tests"]],
  [/^app\/src\/simulation-cameras\.jsx$/, ["run-simulation-camera-tests"]],
  [/^app\/src\/simulation-controls\.jsx$/, ["run-simulation-control-tests"]],
  [/^app\/src\/simulation(-data)?\.jsx$/, ["run-simulation-scenario-tests"]],
  [/^app\/src\/index\.html$/, ["run-lab-tests", "run-bootstrap-tests", "run-resilience-tests"]],
  [/^app\/src\/home-tokens\.css$/, ["run-focus-visibility-tests", "run-apartment-ui-tests.js", "test-apartment-ui-browser.mjs", "test-apartment-chat-header-browser.mjs"]],
  [/^app\/src\/home-overlay\.js$/, ["run-overlay-stack-tests", "test-apartment-escape-stack.cjs", "test-apartment-escape-browser.mjs"]],
  [/^app\/src\/home-apartment(?:-edit)?\.jsx$/, ["test-apartment-escape-stack.cjs", "test-apartment-escape-browser.mjs"]],
  [/^app\/src\/home-apartment\.jsx$/, ["test-apartment-room-selection.mjs", "test-apartment-room-selection-browser.mjs"]],
  [/^tools\/test-apartment-room-selection(?:-browser)?\.mjs$/, ["test-apartment-room-selection.mjs", "test-apartment-room-selection-browser.mjs"]],
  [/^app\/src\/home-apartment(?:-cards|-edit)?\.jsx$/, ["test-apartment-room-inspector.mjs", "test-apartment-room-inspector-browser.mjs"]],
  [/^tools\/test-apartment-room-inspector(?:-browser)?\.mjs$/, ["test-apartment-room-inspector.mjs", "test-apartment-room-inspector-browser.mjs"]],
  [/^(?:app\/src\/home-apartment(?:-setup|-edit)?\.jsx|app\/src\/home-apartment-setup-links\.js|tools\/(?:test-apartment-setup-rail-browser|fixtures\/apartment-setup-fixtures)\.mjs)$/, ["test-apartment-setup-rail-browser.mjs"]],
  [/^(?:app\/src\/(?:home-apartment-(?:setup|edit)\.jsx|home-apartment-data\.js|home-equipment-registration\.js)|web-gateway\/equipment-registration\.mjs|tools\/(?:test-apartment-setup-saves-browser|fixtures\/apartment-setup-fixtures)\.mjs)$/, ["test-apartment-setup-saves-browser.mjs"]],
  [/^(?:app\/src\/(?:home-apartment(?:-setup|-edit)?\.jsx|home-app\.jsx|home-equipment-registration\.js|home-overlay\.js|home-tokens\.css)|tools\/(?:test-apartment-setup-read-only-browser|fixtures\/apartment-setup-fixtures)\.mjs)$/, ["test-apartment-setup-read-only-browser.mjs"]],
  [/^app\/src\/(?:home-apartment-(?:echo\.jsx|quick\.js|ui\.jsx)|home-tokens\.css)$/, ["test-apartment-quick.cjs", "test-apartment-echo-inspector-browser.mjs"]],
  [/^app\/src\/home-apartment-(?:echo\.jsx|quick\.js)$/, ["test-apartment-room-inspector-browser.mjs"]],
  [/^tools\/test-apartment-(?:quick\.cjs|echo-inspector-browser\.mjs)$/, ["test-apartment-quick.cjs", "test-apartment-echo-inspector-browser.mjs"]],
  // APT-F06/S02: the shared data states, the status rows that read them, and Quick, which delegates to them.
  [/^(?:app\/src\/(?:home-apartment-(?:states\.js|status\.jsx|quick\.js|ui\.jsx)|home-tokens\.css)|tools\/(?:test-apartment-states\.cjs|test-apartment-status-rows-browser\.mjs|fixtures\/den-contracts\/.+))$/,
    ["test-apartment-states.cjs", "test-apartment-status-rows-browser.mjs"]],
  [/^app\/src\/home-apartment-states\.js$/, ["test-apartment-quick.cjs", "test-apartment-echo-inspector-browser.mjs"]],
  [/^(?:app\/src\/home-3d\/apartment-light-pools\.mjs|tools\/test-apartment-light-pools\.mjs)$/, ["test-apartment-light-pools.mjs", "test-apartment-room-inspector-browser.mjs"]],
  [/^(?:app\/src\/home-3d\/(?:apartment-echo-glyphs\.mjs|apartment-light-pools\.mjs|markers\.js)|tools\/test-apartment-echo-glyphs\.mjs)$/, ["test-apartment-echo-glyphs.mjs"]],
  [/^(?:app\/src\/(?:home-apartment(?:-room-overlay)?\.jsx|home-3d\/(?:markers\.js|apartment-aim-presentation\.mjs))|tools\/test-apartment-(?:equipment-tags|one-label-browser)\.mjs)$/, ["test-apartment-equipment-tags.mjs", "test-apartment-one-label-browser.mjs"]],
  [/^app\/src\/home-apartment(?:-directional\.js|-cards\.jsx|\.jsx)$/, ["test-apartment-directional.mjs", "test-apartment-directional-integration.mjs", "test-apartment-directional-browser.mjs", "test-apartment-directional-browser-part2.mjs"]],
  [/^tools\/(?:test-apartment-directional(?:-integration|-browser(?:-part2)?)?\.mjs|fixtures\/apartment-directional-browser-proof\.mjs)$/, ["test-apartment-directional.mjs", "test-apartment-directional-integration.mjs", "test-apartment-directional-browser.mjs", "test-apartment-directional-browser-part2.mjs"]],
  [/^tools\/(?:fixtures\/apartment-directional-native-mouse|test-apartment-directional-native-mouse)\.mjs$/, ["test-apartment-directional-native-mouse.mjs", "test-apartment-directional-browser.mjs", "test-apartment-directional-browser-part2.mjs"]],
  [/^tools\/(?:fixtures\/apartment-directional-readiness|test-apartment-directional-readiness)\.mjs$/, ["test-apartment-directional-readiness.mjs", "test-apartment-directional-browser.mjs", "test-apartment-directional-browser-part2.mjs"]],
  [/^(?:app\/src\/home-3d\/apartment-directional-presentation\.mjs|tools\/test-apartment-directional-presentation\.mjs)$/, ["test-apartment-directional-presentation.mjs", "test-apartment-directional-browser.mjs", "test-apartment-directional-browser-part2.mjs"]],
  [/^(?:app\/src\/(?:home-apartment(?:-directional\.js|-cards\.jsx|\.jsx)|home-3d\/apartment-directional-presentation\.mjs)|tools\/test-apartment-composer-comfort-browser\.mjs)$/, ["test-apartment-composer-comfort-browser.mjs"]],
  [/^(?:app\/src\/home-3d\/(?:equipment-virtual-fixtures\.mjs|markers\.js)|tools\/test-apartment-radial-visual\.mjs)$/, ["test-apartment-radial-visual.mjs"]],
  // W2 aim wiring: the mounted overlay, its store and presentation, and the hand-change switch point.
  [/^(?:app\/src\/(?:home-apartment(?:-room-overlay|-cards)?\.jsx|home-apartment-aim-proposals\.js|home-app\.jsx|home-3d\/(?:apartment-aim-presentation\.mjs|engine\.js))|tools\/(?:test-apartment-(?:aim-wiring(?:-(?:drag|keys|room|phone))?-browser|aim-reticle-drag-browser|manual-intent-contract)\.mjs|fixtures\/apartment-aim-wiring-harness\.mjs))$/,
    ["test-apartment-aim-wiring-browser.mjs", "test-apartment-aim-wiring-drag-browser.mjs", "test-apartment-aim-wiring-keys-browser.mjs",
      "test-apartment-aim-wiring-room-browser.mjs", "test-apartment-aim-wiring-phone-browser.mjs", "test-apartment-aim-reticle-drag-browser.mjs", "test-apartment-manual-intent-contract.mjs",
      "run-apartment-aim-presentation-tests.mjs", "run-apartment-aim-lifecycle-browser.mjs", "run-apartment-aim-proposals-tests.mjs"]],
  // Named aims (aim library) and Echo's engineered aim on the shared proposal path.
  [/^(?:app\/src\/(?:home-apartment(?:-aim-library|-echo-aim|-chat-aim|-aim-proposals)\.js|home-apartment(?:-aim-library|-room-overlay|-aim)?\.jsx|home-app\.jsx|home-3d\/apartment-aim-presentation\.mjs)|tools\/test-apartment-(?:aim-library(?:-browser|-shell-browser)?|chat-aim)\.mjs)$/,
    ["test-apartment-aim-library.mjs", "test-apartment-aim-library-browser.mjs", "test-apartment-aim-library-shell-browser.mjs", "test-apartment-chat-aim.mjs"]],
  [/^(?:app\/src\/home-3d\/(?:equipment-virtual-fixtures|equipment-spotlight-visual)\.mjs|tools\/test-apartment-aim-sweep\.mjs)$/, ["test-apartment-aim-sweep.mjs"]],
  // Every GLSL site and its WebGPU twin (?renderer=webgpu) move together.
  [/^(?:app\/src\/home-3d\/(?:webgpu-[\w-]+\.mjs|dollhouse-cutaway(?:-tsl|-material)?\.m?js|pointcloud\.js|modes\.js|photo-floor-support\.js|equipment-spotlight-visual\.mjs|equipment-virtual-fixtures\.mjs|apartment-directional-presentation\.mjs)|tools\/test-apartment-webgpu-ports\.mjs)$/, ["test-apartment-webgpu-ports.mjs"]],
  [/^(?:app\/src\/(?:index\.html|vendor\/three-0\.186\.1\/.+|home-3d\/(?:engine\.js|webgpu-[\w-]+\.mjs|dollhouse-cutaway-tsl\.mjs|gaussian-splat-[\w-]+\.mjs|splat-cutaway-tsl\.mjs))|tools\/test-apartment-webgpu-(?:renderer|photo)-browser\.mjs)$/, ["test-apartment-webgpu-renderer-browser.mjs", "test-apartment-webgpu-photo-browser.mjs"]],
  // The WebGPU splat pipeline (Photo on ?renderer=webgpu) and the Spark cutaway it mirrors.
  [/^(?:app\/src\/home-3d\/(?:gaussian-splat-[\w-]+\.mjs|splat-cutaway(?:-tsl)?\.m?js|splat-exterior-cleanup\.mjs|modes\.js|adaptive-performance\.mjs)|tools\/test-apartment-gaussian-splat\.mjs)$/, ["test-apartment-gaussian-splat.mjs"]],
  [/^(?:app\/src\/(?:vendor\/(?:three-0\.186\.1|spark-2\.1\.0)\/.+|home-3d\/(?:gaussian-splat-[\w-]+\.mjs|splat-cutaway(?:-tsl)?\.m?js|splat-exterior-cleanup\.mjs|dollhouse-cutaway(?:-tsl)?\.mjs))|tools\/test-apartment-webgpu-splat-browser\.mjs)$/, ["test-apartment-webgpu-splat-browser.mjs"]],
  [/^app\/src\/home-3d\/(?:splat-cutaway\.js|splat-exterior-cleanup\.mjs)$/, ["test-apartment-webgpu-ports.mjs"]],
  [/^tools\/(?:run-overlay-stack-tests\.js|test-apartment-escape-(?:stack\.cjs|browser\.mjs))$/, ["run-overlay-stack-tests", "test-apartment-escape-stack.cjs", "test-apartment-escape-browser.mjs"]],
  [/^(?:app\/src\/home-apartment-room-guide\.jsx|tools\/test-apartment-room-guide(?:\.cjs|-browser\.mjs))$/, ["test-apartment-room-guide.cjs", "test-apartment-room-guide-browser.mjs"]],
  [/^app\/src\/(?:home-apartment\.jsx|index\.html|home-feature-loader\.js|home-3d\/(?:rig\.js|markers\.js|equipment-virtual-fixtures\.mjs|apartment-perception-layer\.mjs|registered-desk-target\.mjs))$/, ["test-apartment-room-guide.cjs"]],
  [/^app\/src\/home-apartment(?:-ui)?\.jsx$/, ["test-apartment-room-guide-browser.mjs"]],
  [/^(?:app\/src\/(?:home-apartment(?:-ui|-room-guide)?\.jsx|home-app\.jsx|home-perception-lab\.jsx|home-tokens\.css|home-3d\/(?:modes\.js|asset-byte-cache\.mjs|rig\.js))|tools\/(?:test-apartment-m2-(?:shell|links)-browser|fixtures\/apartment-m2-shell-harness|fixtures\/apartment-home-shell-fixtures)\.mjs)$/, ["test-apartment-m2-shell-browser.mjs", "test-apartment-m2-links-browser.mjs"]],
  [/^app\/src\/.*\.(jsx|js)$/, ["run-focus-visibility-tests"]],
  [/^web-gateway\//, [
    "run-resilience-tests", "run-web-gateway-grounded-vision-tests",
    "run-web-gateway-stack-token-tests", "run-native-agent-gateway-tests",
    "run-native-agent-security-tests", "run-web-gateway-lighting-brain-tests.mjs",
  ]],
  [/^tools\/run-web-gateway-lighting-brain-tests\.mjs$/, ["run-web-gateway-lighting-brain-tests.mjs"]],
  // Ahead-of-time JSX: the loaders, the build, the gateway's verified
  // manifest and the hooks that run the build on deploy and installer builds.
  [/^(?:app\/src\/(?:index\.html|home-feature-loader\.js|home-precompiled-jsx\.js|vendor\/babel-[^/]+\/.*)|web-gateway\/(?:server|precompiled-jsx)\.mjs|tools\/(?:build-app-jsx|lib\/precompiled-jsx|test-precompiled-jsx)\.mjs|tools\/deploy-home-web\.sh|app\/src-tauri\/tauri\.conf\.json)$/, ["test-precompiled-jsx.mjs"]],
  [/^tools\/lib\/home-app-source-contracts\.cjs$/, ["run-bootstrap-tests.js", "run-simulation-command-tests.js", "test-home-app-source-contracts.cjs"]],
  [/^tools\/(?:run-bootstrap-tests|run-simulation-command-tests)\.js$/, ["test-home-app-source-contracts.cjs"]],
  [/^tools\/test-home-app-source-contracts\.cjs$/, ["test-home-app-source-contracts.cjs"]],
  [/^tools\/test-apartment-chat-presenter\.cjs$/, ["test-apartment-chat-presenter.cjs"]],
  [/^app\/src\/(?:home-app\.jsx|home-apartment\.jsx|home-apartment-chat-context\.js|home-tokens\.css|index\.html)$/,
    ["test-apartment-chat-context.cjs", "test-apartment-chat-dispatch.cjs", "test-apartment-chat-chips-browser.mjs", "test-apartment-chat-room-browser.mjs"]],
  [/^tools\/test-apartment-chat-(?:context\.cjs|dispatch\.cjs|chips-browser\.mjs|room-browser\.mjs)$/,
    ["test-apartment-chat-context.cjs", "test-apartment-chat-dispatch.cjs", "test-apartment-chat-chips-browser.mjs", "test-apartment-chat-room-browser.mjs"]],
  [/^tools\/test-apartment-unread-(?:state\.cjs|integration\.mjs)$/, ["test-apartment-unread-state.cjs", "test-apartment-unread-integration.mjs"]],
  [/^tools\/test-apartment-travel-guard\.cjs$/, ["test-apartment-travel-guard.cjs"]],
  [/^(?:app\/src\/(?:home-apartment-setup-links|home-apartment-data)\.js|tools\/test-apartment-setup-links\.cjs)$/, ["test-apartment-setup-links.cjs"]],
  [/^(?:app\/src\/home-apartment-(?:named-aims\.(?:js|jsx)|aiming\.js)|tools\/test-apartment-named-aims\.cjs)$/, ["test-apartment-named-aims.cjs"]],
  [/^(?:app\/src\/home-apartment-setup-devices\.js|tools\/test-apartment-setup-devices\.cjs)$/, ["test-apartment-setup-devices.cjs"]],
  [/^(?:app\/src\/home-apartment-(?:setup-devices\.(?:js|jsx)|setup\.jsx|edit\.jsx)|tools\/(?:test-apartment-setup-proposal-browser|fixtures\/apartment-setup-fixtures)\.mjs)$/, ["test-apartment-setup-proposal-browser.mjs"]],
  [/^(?:app\/src\/(?:home-apartment-named-aims\.(?:js|jsx)|home-apartment-(?:setup|edit)\.jsx|home-3d\/(?:picking\.js|apartment-ui-overlay-bridge\.mjs|markers\.js))|tools\/(?:test-apartment-named-aims-browser|fixtures\/apartment-setup-fixtures)\.mjs)$/, ["test-apartment-named-aims-browser.mjs"]],
  [/^(?:app\/src\/(?:home-apartment(?:-aim|-command-ladder)?\.jsx|home-apartment-aiming\.js|index\.html|home-feature-loader\.js|home-service-worker\.js)|ha-config\/packages\/living_lights_manual_detection\.yaml)$/, ["run-apartment-command-ladder-tests.mjs"]],
  // APT-P01..P06 camera dock: its own suite plus Den's two suites that mount the dock JSX.
  [/^(?:app\/src\/(?:home-apartment-den-perception\.jsx|home-apartment-perception-(?:dock-model|view|player)\.js|home-den-reason-vocabulary\.js|home-lighting-brain-panel\.jsx|home-perception-lab\.jsx|home-apartment\.jsx|home-app\.jsx|home-feature-loader\.js)|docs\/contracts\/den\/(?:reason-vocabulary\.json|fixtures\/[^/]+\.json)|tools\/test-(?:apartment-perception-dock|home-apartment-perception-ui|apartment-desk-preview-ui)\.cjs)$/,
    ["test-apartment-perception-dock.cjs", "test-home-apartment-perception-ui.cjs", "test-apartment-desk-preview-ui.cjs"]],
  // A new or changed top-level source must stay reachable from the loaders.
  [/^app\/src\/[^/]+\.(?:jsx|js)$/, ["run-bootstrap-tests", "run-lab-tests"]],
  [/^tools\/run-touched-harnesses\.mjs$/, ["run-apartment-shell-tests.js", "run-apartment-data-tests.js", "test-apartment-travel-guard.cjs", "test-home-app-source-contracts.cjs"]],
];

const harnessFileName = name => /\.(?:cjs|mjs|js)$/.test(name) ? name : `${name}.js`;

/* Hosted-CI seconds per plan item, used only to balance --shard partitions.
 * Measured on run 39214584656 (#293 touched job, 7m22s of harnesses) and the
 * #287/#289/#290 sibling runs 39174968832, 39177569496 and 39176598325,
 * rounded up, and the composer-comfort suite's first hosted run 39307352122.
 * The shell audit ran whole in 85 s on run 39323737291 and passed its 145 s
 * deadline on a slow runner in run 39326017829, so it runs as three parts,
 * each a page load plus its share (local 18-21 s, about 3.4x hosted).
 * The two directional parts are that run's setup plus their own stages.
 * test-precompiled-jsx.mjs is twice its 45 s local run until hosted-measured.
 * The two WebGPU Lit parts are their hosted stage times (run 39349207506) rounded up.
 * An unmeasured browser suite counts as its 145 s deadline and
 * any other unmeasured item as 1 s. Weights never add, drop or reorder a
 * harness; they only decide which worker runs it. The aim-wiring, chat,
 * shell-audit, room-guide, WebGPU-renderer, Lit and M2 browser weights are
 * hosted times from run 39350126935 (L5, #347), rounded up. */
const SHARD_WEIGHT_S = {
  "test-apartment-directional-browser.mjs": 80,
  "test-apartment-directional-browser-part2.mjs": 90,
  "test-precompiled-jsx.mjs": 90,
  "test-apartment-room-selection-browser.mjs": 111,
  "test-apartment-overlay-bridge-browser.mjs": 100,
  "test-apartment-beam-path-browser.mjs": 40,
  "test-apartment-room-inspector-browser.mjs": 80,
  "test-apartment-m2-links-browser.mjs": 90,
  "test-apartment-m2-shell-browser.mjs": 38,
  "test-apartment-lit-browser.mjs": 120,
  "test-lit-bvh-bake-browser.mjs": 20,
  "test-apartment-lit-webgpu-browser.mjs": 135,
  "test-apartment-lit-webgpu-browser-part2.mjs": 130,
  "test-apartment-composer-comfort-browser.mjs": 55,
  "test-apartment-setup-saves-browser.mjs": 110,
  "test-apartment-setup-rail-browser.mjs": 70,
  "test-apartment-setup-read-only-browser.mjs": 60,
  "test-apartment-named-aims-browser.mjs": 45,
  "test-apartment-setup-proposal-browser.mjs": 80,
  "test-apartment-rig-fit-browser.mjs": 45,
  "test-apartment-shell-audit-browser.mjs": 72,
  "test-apartment-shell-audit-echo-browser.mjs": 68,
  "test-apartment-shell-audit-behaviour-browser.mjs": 66,
  "test-apartment-aim-theme-browser.mjs": 8,
  "test-apartment-aim-library-browser.mjs": 10,
  "test-apartment-photo-recovery-browser.mjs": 8,
  "test-apartment-ui-browser.mjs": 6,
  "test-apartment-chat-header-browser.mjs": 5,
  "test-apartment-unread-integration.mjs": 5,
  "test-apartment-escape-browser.mjs": 4,
  "test-apartment-status-rows-browser.mjs": 6,
  "test-apartment-echo-inspector-browser.mjs": 4,
  "test-apartment-directional-integration.mjs": 4,
  "check-jsx.js": 4,
  "run-resilience-tests.js": 3,
  "test-apartment-escape-stack.cjs": 3,
  "test-apartment-aim-wiring-room-browser.mjs": 110,
  "test-apartment-aim-wiring-drag-browser.mjs": 105,
  "test-apartment-webgpu-renderer-browser.mjs": 100,
  "test-apartment-shell-audit-browser.mjs": 70,
  "test-apartment-chat-room-browser.mjs": 66,
  "test-apartment-aim-wiring-browser.mjs": 63,
  "test-apartment-aim-wiring-keys-browser.mjs": 56,
  "test-apartment-aim-wiring-phone-browser.mjs": 48,
  "test-apartment-chat-chips-browser.mjs": 7,
  "test-apartment-room-guide-browser.mjs": 8,
};
const MAX_SHARDS = 32;
const RECEIPT_SCHEMA = "touched-harness-shard-receipt/v1";

function shardWeight(name) {
  if (Object.hasOwn(SHARD_WEIGHT_S, name)) return SHARD_WEIGHT_S[name];
  return /-browser\.mjs$/.test(name) ? 145 : 1;
}

/* Longest-first greedy partition with total, input-independent tie-breaks
 * (weight, then name; then the lowest-loaded, lowest-index worker). Each
 * worker's items keep the unsharded plan order. */
// Parts of one split suite run on different workers whenever there are at
// least as many workers as parts, so no worker carries the whole suite.
const SHARD_APART = [["test-apartment-directional-browser.mjs", "test-apartment-directional-browser-part2.mjs"],
  ["test-apartment-lit-webgpu-browser.mjs", "test-apartment-lit-webgpu-browser-part2.mjs", "test-apartment-lit-webgpu-browser-part3.mjs"],
  ["test-apartment-shell-audit-browser.mjs", "test-apartment-shell-audit-echo-browser.mjs", "test-apartment-shell-audit-behaviour-browser.mjs"]];
function partitionPlan(plan, count) {
  const loads = Array(count).fill(0), owner = new Map();
  const order = [...plan].sort((a, b) => shardWeight(b) - shardWeight(a) || (a < b ? -1 : a > b ? 1 : 0));
  for (const name of order) {
    const group = SHARD_APART.find(names => names.includes(name)) || [name];
    const taken = new Set(group.filter(other => other !== name && owner.has(other)).map(other => owner.get(other)));
    const open = count > taken.size ? loads.map((_, i) => i).filter(i => !taken.has(i)) : loads.map((_, i) => i);
    let target = open[0];
    for (const i of open) if (loads[i] < loads[target]) target = i;
    owner.set(name, target); loads[target] += shardWeight(name);
  }
  return loads.map((_, index) => plan.filter(name => owner.get(name) === index));
}

function usage(message) {
  console.error(`[touched-harnesses] ${message}`);
  process.exit(2);
}

function parseCount(value, flag) {
  if (!/^[1-9]\d*$/.test(value || "") || Number(value) > MAX_SHARDS) usage(`${flag} needs a worker count from 1 to ${MAX_SHARDS}`);
  return Number(value);
}

function parseArgs(argv) {
  const out = { base: null, files: null, all: false, list: false, shard: null, receipt: null, verifyReceipts: null, shards: null };
  const value = (i, flag) => {
    if (i >= argv.length || /^--/.test(argv[i])) usage(`${flag} needs a value`);
    return argv[i];
  };
  for (let i = 2; i < argv.length; i++) {
    if (argv[i] === "--base") out.base = value(++i, "--base");
    else if (argv[i] === "--files") out.files = value(++i, "--files").split(",").map((s) => s.trim()).filter(Boolean);
    else if (argv[i] === "--all") out.all = true;
    else if (argv[i] === "--list") out.list = true;
    else if (argv[i] === "--shard") {
      const match = /^([1-9]\d*)\/([1-9]\d*)$/.exec(value(++i, "--shard"));
      if (!match) usage("--shard needs i/n, for example 2/3");
      const count = parseCount(match[2], "--shard"), index = Number(match[1]);
      if (index > count) usage(`--shard ${index}/${count} names a worker outside 1..${count}`);
      out.shard = { index, count };
    } else if (argv[i] === "--receipt") out.receipt = value(++i, "--receipt");
    else if (argv[i] === "--verify-receipts") out.verifyReceipts = value(++i, "--verify-receipts");
    else if (argv[i] === "--shards") out.shards = parseCount(value(++i, "--shards"), "--shards");
    else usage(`unknown argument ${JSON.stringify(argv[i])}`);
  }
  if (out.all && (out.files || out.base)) usage("--all cannot be combined with --base or --files");
  if (out.files && out.base) usage("--files cannot be combined with --base");
  if (out.verifyReceipts && (out.shard || out.receipt || out.list || !out.shards)) usage("--verify-receipts needs --shards n and no --shard, --receipt or --list");
  if (out.shards && !out.verifyReceipts) usage("--shards is only for --verify-receipts");
  if (out.receipt && out.list) usage("--receipt records executed results and cannot be combined with --list");
  return out;
}

function changedFiles(base) {
  const range = `${base}...HEAD`;
  const stdout = execFileSync("git", ["diff", "--name-only", range], { cwd: REPO, encoding: "utf8" });
  return stdout.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
}

const git = argv => execFileSync("git", argv, { cwd: REPO, encoding: "utf8" }).trim();
const args = parseArgs(process.argv);
let files = [];
let harnesses = new Set();
const addHarness = name => harnesses.add(harnessFileName(name));

if (args.all) {
  for (const f of fs.readdirSync(TOOLS)) {
    if (/^run-.*\.js$/.test(f)) addHarness(f);
  }
  // Include mapped .cjs/.mjs suites without sweeping optional browser, GPU or
  // service workloads from every test-* file in the repository.
  for (const [, names] of MAP) names.forEach(addHarness);
} else {
  files = args.files || changedFiles(args.base || "origin/main");
  for (const file of files) {
    const posix = file.replace(/\\/g, "/");
    for (const [pattern, names] of MAP) {
      if (pattern.test(posix)) names.forEach(addHarness);
    }
    // An edit to a harness exercises that harness even without an app edit.
    // The dispatcher itself is mapped above instead of recursively running it.
    const ownHarness = /^tools\/(run-[^/]+\.(?:js|cjs|mjs))$/.exec(posix);
    if (ownHarness && ownHarness[1] !== "run-touched-harnesses.mjs") addHarness(ownHarness[1]);
  }
}

const touchedAppSrc = args.all || files.some((f) => f.replace(/\\/g, "/").startsWith("app/src/"));
const list = [...harnesses].sort();
// The full plan: every harness, then check-jsx (which also runs the ESM checks).
const plan = touchedAppSrc ? [...list, "check-jsx.js"] : list;
const viaBase = !args.all && !args.files;
const source = {
  mode: args.all ? "all" : args.files ? "files" : "base",
  base: viaBase ? args.base || "origin/main" : null,
  merge_base: viaBase ? git(["merge-base", args.base || "origin/main", "HEAD"]) : null,
  head: git(["rev-parse", "HEAD"]),
  changed_files: files.map((f) => f.replace(/\\/g, "/")).sort(),
};

console.log(`[touched-harnesses] ${args.all ? "(--all)" : files.length + " changed files"} → ${list.length} harnesses${touchedAppSrc ? " + check-jsx" : ""}`);

if (args.verifyReceipts) process.exit(verifyReceipts(args.verifyReceipts, args.shards));

const shardPlan = args.shard ? partitionPlan(plan, args.shard.count)[args.shard.index - 1] : plan;
if (args.shard) {
  const weight = items => items.reduce((sum, name) => sum + shardWeight(name), 0);
  console.log(`[touched-harnesses] shard ${args.shard.index}/${args.shard.count} → ${shardPlan.length} of ${plan.length} plan items (~${weight(shardPlan)}s of ~${weight(plan)}s measured)`);
}
const results = [];
function finish(code) {
  if (args.receipt) {
    const receipt = { schema: RECEIPT_SCHEMA, ...source, plan,
      shard: args.shard || { index: 1, count: 1 }, shard_plan: shardPlan, results };
    fs.mkdirSync(path.dirname(path.resolve(REPO, args.receipt)), { recursive: true });
    fs.writeFileSync(path.resolve(REPO, args.receipt), `${JSON.stringify(receipt, null, 2)}\n`);
    console.log(`[touched-harnesses] receipt ${args.receipt}`);
  }
  process.exit(code);
}

if (!plan.length) {
  console.log("[touched-harnesses] nothing mapped — done");
  finish(0);
}
if (!shardPlan.length) {
  console.log("[touched-harnesses] this shard has no plan items — done");
  finish(0);
}

let failed = [];
function record(name, status, started) {
  results.push({ name, status, duration_ms: Date.now() - started });
  if (status !== "ok") failed.push(name);
}
for (const name of shardPlan.filter(item => item !== "check-jsx.js")) {
  const file = path.join(TOOLS, name), started = Date.now();
  if (!fs.existsSync(file)) {
    console.log(`  FAIL ${name} (mapped harness file missing)`);
    record(name, "missing", started);
    continue;
  }
  if (args.list) { console.log(`  PLAN ${name}`); continue; }
  process.stdout.write(`  RUN  ${name} ... `);
  const nodeArgs = /^test-.*\.(?:cjs|mjs|js)$/.test(name) ? ["--test", file] : [file];
  const res = spawnSync(process.execPath, nodeArgs, { cwd: REPO, encoding: "utf8", timeout: 180000 });
  if (res.status === 0) {
    console.log("ok");
    record(name, "ok", started);
  } else {
    console.log("FAIL");
    const tail = `${res.stdout || ""}\n${res.stderr || ""}`.trim().split(/\r?\n/).slice(-15).join("\n");
    console.log(tail.replace(/^/gm, "       "));
    record(name, "fail", started);
  }
}

const runCheckJsx = shardPlan.includes("check-jsx.js");
if (runCheckJsx && args.list) console.log("  PLAN check-jsx.js");
if (runCheckJsx && !args.list) {
  const started = Date.now();
  let status = "ok";
  process.stdout.write("  RUN  check-jsx ... ");
  const res = spawnSync(process.execPath, [path.join(TOOLS, "check-jsx.js")], { cwd: REPO, encoding: "utf8", timeout: 180000 });
  if (res.status === 0) console.log("ok");
  else {
    console.log("FAIL");
    console.log(`${res.stdout || ""}\n${res.stderr || ""}`.trim().split(/\r?\n/).slice(-15).join("\n").replace(/^/gm, "       "));
    failed.push("check-jsx");
    status = "fail";
  }

  // home-3d/ and home-agent/ are real ES modules — check-jsx's Function-based
  // parse can't read them; node --check can.
  const esmFiles = files
    .map((f) => f.replace(/\\/g, "/"))
    .filter((f) => /^app\/src\/(home-3d|home-agent)\/.*\.js$/.test(f))
    .filter((f) => fs.existsSync(path.join(REPO, f)));
  for (const f of esmFiles) {
    process.stdout.write(`  RUN  esm-check ${f} ... `);
    const src = fs.readFileSync(path.join(REPO, f), "utf8");
    const r = spawnSync(process.execPath, ["--input-type=module", "--check"], { cwd: REPO, encoding: "utf8", timeout: 60000, input: src });
    if (r.status === 0) console.log("ok");
    else {
      console.log("FAIL");
      console.log(`${r.stderr || ""}`.trim().split(/\r?\n/).slice(-6).join("\n").replace(/^/gm, "       "));
      failed.push(`node-check:${f}`);
      status = "fail";
    }
  }
  results.push({ name: "check-jsx.js", status, duration_ms: Date.now() - started });
}

if (failed.length) {
  console.log(`\n[touched-harnesses] FAILED: ${failed.join(", ")}`);
  finish(1);
}
console.log(args.list ? "\n[touched-harnesses] plan complete (no harnesses executed)" : "\n[touched-harnesses] all green");
finish(0);

/* The aggregate gate: recompute the plan for the same base, then require the
 * workers' receipts to be exactly shards 1..n of that plan, each item run once
 * and green, so the shards are disjoint and their union is the unsharded plan.
 * Returns the exit code. */
function verifyReceipts(dir, count) {
  const problems = [], expected = partitionPlan(plan, count), ranIn = new Map(), present = new Set();
  const absolute = path.resolve(REPO, dir);
  const entries = fs.existsSync(absolute) ? fs.readdirSync(absolute).filter(name => /^shard-.*\.json$/.test(name)).sort() : [];
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  for (const name of entries) {
    const match = /^shard-([1-9]\d*)-of-([1-9]\d*)\.json$/.exec(name);
    if (!match || Number(match[2]) !== count || Number(match[1]) > count) { problems.push(`${name}: not a receipt for shards 1..${count}`); continue; }
    let receipt;
    try { receipt = JSON.parse(fs.readFileSync(path.join(absolute, name), "utf8")); } catch (error) { problems.push(`${name}: unreadable (${error.message})`); continue; }
    const index = Number(match[1]), label = `shard ${index}/${count}`;
    present.add(index);
    if (receipt?.schema !== RECEIPT_SCHEMA) problems.push(`${label}: schema ${JSON.stringify(receipt?.schema)}`);
    if (!same(receipt?.shard, { index, count })) problems.push(`${label}: receipt names shard ${JSON.stringify(receipt?.shard)}`);
    for (const key of ["mode", "base", "merge_base", "head", "changed_files"]) {
      if (!same(receipt?.[key], source[key])) problems.push(`${label}: ${key} ${JSON.stringify(receipt?.[key])} differs from ${JSON.stringify(source[key])}`);
    }
    if (!same(receipt?.plan, plan)) problems.push(`${label}: full plan differs from the unsharded plan`);
    if (!same(receipt?.shard_plan, expected[index - 1])) problems.push(`${label}: shard plan differs from the partition`);
    const ran = Array.isArray(receipt?.results) ? receipt.results : [];
    if (!same(ran.map(result => result?.name), expected[index - 1])) problems.push(`${label}: results do not match its shard plan one to one`);
    for (const result of ran) {
      if (result?.status !== "ok") problems.push(`${label}: ${result?.name} ${result?.status}`);
      if (ranIn.has(result?.name)) problems.push(`${label}: ${result?.name} also ran in shard ${ranIn.get(result?.name)}`);
      else ranIn.set(result?.name, index);
    }
  }
  for (let index = 1; index <= count; index++) if (!present.has(index)) problems.push(`shard ${index}/${count}: receipt missing`);
  const missing = plan.filter(name => !ranIn.has(name)), extra = [...ranIn.keys()].filter(name => !plan.includes(name));
  if (missing.length || extra.length) problems.push(`union differs from the unsharded plan: missing ${JSON.stringify(missing)}, extra ${JSON.stringify(extra)}`);
  for (const problem of problems) console.log(`  FAIL ${problem}`);
  if (problems.length) {
    console.log(`\n[touched-harnesses] receipts REJECTED: ${problems.length} problem(s)`);
    return 1;
  }
  console.log(`  OK   ${count} receipts, disjoint, union = unsharded plan (${plan.length} items), all green`);
  console.log("\n[touched-harnesses] receipts verified");
  return 0;
}

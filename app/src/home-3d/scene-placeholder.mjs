/* home-3d/scene-placeholder.mjs — a disclosed "scan pending" interior, told
 * apart from a scan.
 *
 * WHY THIS EXISTS. Victoria has no interior scan, and the journey still has to
 * arrive somewhere: the particle transition and the interior zoom are the same
 * grammar for both homes. The owner's answer is a GENERIC house (12 x 9 m, two
 * storeys, gable roof; tools/hero-pipeline/make_placeholder_interior.py) shipped
 * as a point cloud in its own pack revision. It is only acceptable while it is
 * never mistaken for the real building, so the renderer has to know which
 * interiors are placeholders and say so on screen.
 *
 * WHERE THE FLAG LIVES. frame.json, not content-pack.json. The native manifest
 * struct (app/src-tauri/src/content_packs.rs) is deny_unknown_fields, so a new
 * manifest field would make every native import of the pack fail; frame.json is
 * an asset the manifest already hashes, and the generator writes
 *   "placeholder": { "kind": "scan-pending", "measured": false, ... }
 * The plan also spelled it as the bare string "placeholder": "scan-pending";
 * both are read.
 *
 * FAIL CLOSED. Anything other than an absent, null or false flag is a
 * placeholder. An unrecognised shape still gets the badge and the points-only
 * rule: the expensive mistake is presenting a placeholder as the home, not the
 * reverse.
 *
 * POINTS ONLY. The pack carries a tiny splat purely because a pack with
 * sceneAvailable:true must (validateManifest), and it has no mesh at all.
 * "photo" would show 2048 grey blobs and "mesh" has nothing to load, so both
 * are refused rather than offered and failed.
 */

export const SCAN_PENDING = 'scan-pending';
/* What the badge says after the home's name, joined by a middle dot:
 * "Victoria · scan pending". The journey audit looks for this text. */
export const SCAN_PENDING_TEXT = 'scan pending';
export const PLACEHOLDER_MODES = Object.freeze(['points']);
/* The error a refused mode change rejects with. */
export const POINTS_ONLY_ERROR = 'scene_points_only';

const KIND = /^[a-z0-9][a-z0-9-]{0,31}$/;
/* frame.json is a file on disk and files get edited: text from it reaches the
 * chrome only with control and bidi characters out and its length clamped,
 * exactly as the pack's review note does in home-apartment.jsx. */
const UNSAFE_TEXT = /[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g;

export function cleanPlaceholderText(value, max = 240) {
    if (typeof value !== 'string') return null;
    const text = value.replace(UNSAFE_TEXT, ' ').replace(/\s+/g, ' ').trim().slice(0, max);
    return text || null;
}

/** The placeholder frame.json declares, as a frozen descriptor; null for a
 * real scan (or no frame at all). */
export function scenePlaceholderFromFrame(frame) {
    if (!frame || typeof frame !== 'object' || Array.isArray(frame)) return null;
    const raw = frame.placeholder;
    if (raw === undefined || raw === null || raw === false) return null;
    const fields = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
    const kind = typeof raw === 'string' ? raw : fields.kind;
    return Object.freeze({
        kind: typeof kind === 'string' && KIND.test(kind) ? kind : SCAN_PENDING,
        label: cleanPlaceholderText(fields.label, 64),
        model: cleanPlaceholderText(fields.model, 64),
        note: cleanPlaceholderText(fields.note, 240),
        statusText: SCAN_PENDING_TEXT,
        pointsOnly: true,
    });
}

/** "Victoria · scan pending"; null when the interior is a real scan. */
export function scenePlaceholderBadgeText(siteName, placeholder) {
    if (!placeholder) return null;
    const name = cleanPlaceholderText(siteName, 64) || 'This home';
    return `${name} \u00b7 ${SCAN_PENDING_TEXT}`;
}

export function modeAllowedForScene(mode, placeholder) {
    return !placeholder?.pointsOnly || PLACEHOLDER_MODES.includes(mode);
}

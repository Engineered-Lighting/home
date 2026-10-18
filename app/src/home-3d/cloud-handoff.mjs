/**
 * The cloud pass-through: how a hyper-real still hands over to the live map
 * without the viewer ever seeing it happen.
 *
 * WHY THIS EXISTS, AND WHY IT REPLACES A CROSS-DISSOLVE. Dissolving a generated
 * still into the live surface requires the two to agree pixel for pixel, and
 * they never quite do: a generative rebuild holds LAYOUT well but repaints
 * material, so a dissolve shows one roof sliding and shimmering into another.
 * An opaque occluder removes the requirement entirely. Nothing has to match
 * while nothing is visible.
 *
 * EVERYTHING HERE IS A FUNCTION OF RANGE, NOT TIME. The clouds are driven by
 * how far out the camera is, which is driven by scroll. That is the whole point:
 * a timed cloud desynchronises the moment the viewer scrolls faster or slower
 * than the animation assumed, and this project has already shipped one bug of
 * exactly that shape -- two clocks in one flight, and a ground track that
 * stopped matching the descent. One clock here, and it is the range.
 *
 * THE HAZARD THAT SHAPES THE API. Because the viewer drives the rate, the
 * viewer can also flick the wheel hard enough to jump from "clouds gathering"
 * to "clouds gone" between two frames, skipping the opaque window and showing
 * the swap. Coverage alone cannot prevent that, so this module also states the
 * largest step that stays hidden and offers a predicate the caller can use to
 * clamp. A design that only computed opacity would look complete and would fail
 * on the first impatient scroll.
 */

/* Where the cloud band sits, as multiples of the handoff range.
 *
 * Multiples rather than metres because the handoff range is a per-site choice --
 * a tighter capture hands over lower -- and every part of the band has to move
 * with it. Spread deliberately wide: the opaque window is the safety margin,
 * and narrowing it to save a few hundred metres of travel buys nothing and
 * risks the one artefact this whole mechanism exists to prevent. */
export const CLOUD_ENTER = 0.55;
export const CLOUD_FULL_START = 0.85;
export const CLOUD_FULL_END = 1.18;
export const CLOUD_CLEAR = 1.9;

/** Coverage at or above this counts as hiding whatever is behind it. */
export const OPAQUE_ENOUGH = 0.995;

function clamp01(value) {
    if (!Number.isFinite(value)) return 0;
    if (value < 0) return 0;
    if (value > 1) return 1;
    return value;
}

/* Zero first and second derivative at both ends, so the cloud has no edge where
 * it begins and none where it ends. The same curve the flight and the hero
 * dissolve use, for the same reason. */
function smootherstep(edge0, edge1, value) {
    if (!(edge1 > edge0)) return value >= edge1 ? 1 : 0;
    const x = clamp01((value - edge0) / (edge1 - edge0));
    return x * x * x * (x * (x * 6 - 15) + 10);
}

function band(handoffRangeM) {
    const handoff = Number(handoffRangeM);
    if (!Number.isFinite(handoff) || !(handoff > 0)) return null;
    return {
        enter: handoff * CLOUD_ENTER,
        fullStart: handoff * CLOUD_FULL_START,
        fullEnd: handoff * CLOUD_FULL_END,
        clear: handoff * CLOUD_CLEAR,
    };
}

/**
 * How much of the frame the cloud covers at this range, 0 to 1.
 *
 * Rises from nothing, holds at fully opaque across the window the swap happens
 * in, then disperses to nothing again. Computed in LOG range, like every other
 * rate in this project, because that is what makes a zoom feel constant.
 */
export function cloudCoverage(rangeM, handoffRangeM) {
    const edges = band(handoffRangeM);
    const range = Number(rangeM);
    if (!edges || !Number.isFinite(range) || !(range > 0)) return 0;
    if (range <= edges.enter) return 0;
    if (range >= edges.clear) return 0;
    if (range >= edges.fullStart && range <= edges.fullEnd) return 1;
    if (range < edges.fullStart) {
        return smootherstep(Math.log(edges.enter), Math.log(edges.fullStart), Math.log(range));
    }
    return 1 - smootherstep(Math.log(edges.fullEnd), Math.log(edges.clear), Math.log(range));
}

/** The range at which the still is dropped and the live map takes over. */
export function swapRange(handoffRangeM) {
    const edges = band(handoffRangeM);
    if (!edges) return null;
    /* The geometric middle of the opaque window, so there is as much cloud on
     * each side of the swap as the band allows -- equal margin in log range,
     * which is the space the camera actually moves through. */
    return Math.sqrt(edges.fullStart * edges.fullEnd);
}

/**
 * Which side of the swap this range is on.
 *
 * The caller uses this to decide what to draw, and it is deliberately a pure
 * function of range rather than a latched state: a latch would have to be
 * reset on the way back in, and forgetting to reset it is how the return
 * journey ends up showing the wrong layer.
 */
export function showsLiveMap(rangeM, handoffRangeM) {
    const swap = swapRange(handoffRangeM);
    const range = Number(rangeM);
    if (swap === null || !Number.isFinite(range)) return false;
    return range >= swap;
}

/** Named phase, for diagnostics and for the caller's own branching. */
export function cloudPhase(rangeM, handoffRangeM) {
    const edges = band(handoffRangeM);
    const range = Number(rangeM);
    if (!edges || !Number.isFinite(range) || !(range > 0)) return 'clear';
    if (range <= edges.enter) return 'clear';
    if (range < edges.fullStart) return 'gathering';
    if (range <= edges.fullEnd) return 'opaque';
    if (range < edges.clear) return 'dispersing';
    return 'gone';
}

/**
 * Whether a step from one range to the next kept the swap hidden.
 *
 * THE POINT OF THE WHOLE MODULE. Scroll-linked means the viewer sets the rate,
 * so a hard flick can carry the camera from "gathering" clear past "dispersing"
 * in a single frame. Coverage would be low at both ends of that step, the swap
 * would happen somewhere in the middle of it, and the viewer would see the
 * still replaced by the map in the open. This reports it so the caller can
 * clamp the step rather than discovering it in a bug report.
 *
 * A step that does not cross the swap at all is trivially hidden.
 */
export function crossingIsHidden(previousRangeM, nextRangeM, handoffRangeM) {
    const swap = swapRange(handoffRangeM);
    const from = Number(previousRangeM);
    const to = Number(nextRangeM);
    /* Both ends must be real camera ranges. A zero or negative range is not a
     * pose the camera can be in, and answering "hidden" for one is worse than
     * refusing: the caller would take it as permission not to clamp. */
    if (swap === null || !(from > 0) || !(to > 0)) return false;
    const low = Math.min(from, to);
    const high = Math.max(from, to);
    if (high < swap || low > swap) return true;
    /* It crosses. Both ends of the step must already be under cloud, because
     * those are the frames the viewer actually sees -- nothing is rendered
     * strictly between them. */
    return cloudCoverage(from, handoffRangeM) >= OPAQUE_ENOUGH
        && cloudCoverage(to, handoffRangeM) >= OPAQUE_ENOUGH;
}

/**
 * The largest per-frame range ratio that is guaranteed to stay hidden.
 *
 * The opaque window spans fullEnd/fullStart in range. A step that crosses the
 * swap is safe only if it fits inside that window, so this is the ratio the
 * caller must clamp to while the cloud band is in play.
 */
export function maxHiddenStepRatio() {
    return CLOUD_FULL_END / CLOUD_FULL_START;
}

/**
 * The largest per-frame step in LOG range that is safe to take, unconditionally.
 *
 * Half the window, not the whole window, and the halving is the entire point.
 * A step equal to the full window can start a hair below full cover and land a
 * hair below the far edge -- crossing the swap with its first endpoint not yet
 * opaque. At half the window, the frame that finally crosses the swap must have
 * begun inside the window, because the swap sits exactly half a window in from
 * its near edge. So both endpoints are opaque without the caller having to
 * reason about where it is.
 *
 * This is what the zoom integrator clamps to while the cloud band is in play.
 */
export function maxHiddenLogStep() {
    return Math.log(maxHiddenStepRatio()) / 2;
}

/**
 * How far the cloud layer has swept past the camera, as a monotonic distance.
 *
 * The clouds are meant to move PAST the viewer rather than fade in place, which
 * is what sells them as weather instead of a transition effect. This gives the
 * renderer one number that only ever increases with range, so puffs can be
 * translated and scaled along the view axis from it. Log-based so the sweep
 * feels the same at every altitude, and zeroed at the band entry so nothing
 * moves before there is anything to see.
 */
export function cloudParallax(rangeM, handoffRangeM) {
    const edges = band(handoffRangeM);
    const range = Number(rangeM);
    if (!edges || !Number.isFinite(range) || !(range > 0)) return 0;
    if (range <= edges.enter) return 0;
    const span = Math.log(edges.clear) - Math.log(edges.enter);
    if (!(span > 0)) return 0;
    return Math.min(1, (Math.log(range) - Math.log(edges.enter)) / span);
}

/**
 * Where to put the cloud deck so the camera flies THROUGH it at the swap.
 *
 * The ambient cloud volume in this project sits at 2,400-4,800 m, which is
 * weather seen from a distance. The pass-through is a different job: the deck
 * has to straddle the altitude the camera occupies while the swap happens, or
 * the camera passes under a ceiling instead of into cloud.
 *
 * Altitude, not range: the camera is on a slant, so at range R and pitch p it
 * sits anchorHeight + R*sin|p| above the ground. The deck is placed around the
 * altitudes of the opaque window's two ends, with a margin so the camera is
 * inside the volume rather than clipping its surface.
 */
export function cloudDeck(handoffRangeM, anchorHeightM = 0, pitchDeg = -35) {
    const edges = band(handoffRangeM);
    const anchor = Number(anchorHeightM);
    const pitch = Number(pitchDeg);
    if (!edges || !Number.isFinite(anchor) || !Number.isFinite(pitch)) return null;
    const rise = Math.abs(Math.sin(pitch * Math.PI / 180));
    /*
     * Spanned across the WHOLE band, entry to clear -- not just the opaque
     * window.
     *
     * Two reasons, both measured. The camera must be inside the volume for
     * every frame the cloud is drawn: sized to the opaque window only, the deck
     * began above the camera at band entry, and the shader's below-the-deck
     * branch then enters at the far side of the planet shell, ~12,700 km away,
     * so every sample is antipodal and discarded. Second, optical depth is
     * density times PATH LENGTH, so a thin deck is faint however dense it is --
     * a 125 m deck reaches a peak opacity of about 0.13, which is invisible.
     */
    const low = anchor + edges.enter * rise;
    const high = anchor + edges.clear * rise;
    const margin = Math.max(20, (high - low) * 0.25);
    return Object.freeze({ baseM: low - margin, topM: high + margin });
}

/**
 * How far the field slides sideways per doubling of range.
 *
 * Large on purpose. At 900 the field moved about 1.6 km across the whole band,
 * which against a 260 m feature cell is a drift you have to be told about
 * rather than one you see. The clouds are supposed to sweep PAST the camera.
 */
export const CLOUD_DRIFT_M_PER_DOUBLING = 2400;

/**
 * How far the cloud field has slid sideways, in metres, at this range.
 *
 * The clouds must MOVE ACROSS the view as the viewer scrolls, not merely
 * thicken in place: a field that only changes density reads as a dissolve with
 * texture on it, while a field that translates reads as flying through weather.
 * The existing volume already accepts a world-space drift offset for wind, so
 * this is the same lever driven by range instead of by a clock -- which keeps
 * the motion tied to the scroll, stopping dead when the viewer stops.
 *
 * Grows with the pull-back rather than with the parallax curve so the slide
 * keeps going while the cloud disperses, instead of freezing at the far edge.
 */
export function cloudDriftM(rangeM, handoffRangeM, metresPerDoubling = CLOUD_DRIFT_M_PER_DOUBLING) {
    const edges = band(handoffRangeM);
    const range = Number(rangeM);
    const rate = Number(metresPerDoubling);
    if (!edges || !(range > 0) || !Number.isFinite(rate)) return 0;
    if (range <= edges.enter) return 0;
    /* Doublings of range since the band was entered: a constant slide per
     * doubling is what makes the motion feel the same at every altitude, for
     * the same reason every other rate here is logarithmic. */
    return rate * (Math.log(range) - Math.log(edges.enter)) / Math.LN2;
}

/** Everything one frame needs, or null when there is no cloud to draw. */
export function cloudFrame(rangeM, handoffRangeM) {
    const coverage = cloudCoverage(rangeM, handoffRangeM);
    if (!(coverage > 0.001)) return null;
    return Object.freeze({
        coverage,
        parallax: cloudParallax(rangeM, handoffRangeM),
        phase: cloudPhase(rangeM, handoffRangeM),
        showsLiveMap: showsLiveMap(rangeM, handoffRangeM),
    });
}

/**
 * Where the cloud band sits, given the range the still was captured at.
 *
 * THESE ARE TWO DIFFERENT RANGES AND COLLAPSING THEM IS A BUG. The still is
 * keyed to the range it was captured at, because that is the only range at
 * which it is the same size as the thing it depicts. The cloud is keyed to
 * where the exchange happens, which has to be somewhere the still has already
 * finished being useful. Passing the capture range as the handoff range puts
 * `cloudCoverage` at its maximum exactly where the still is at native size, so
 * the world opens inside an opaque cloud and the still is never seen at all --
 * observed as "it goes straight from the apartment view to the cloud".
 *
 * The factor is derived, not tuned: dividing by CLOUD_ENTER places the first
 * wisp of cloud exactly at the capture range, so the cloud begins at the moment
 * the still stops being enlarged and starts to shrink. Everything inward of
 * that is clear sky and a still at full size; everything outward is the
 * handover. One rule, and it reads off the band constants rather than being a
 * number somebody liked.
 */
export function cloudHandoffRangeFor(captureRangeM) {
    const capture = Number(captureRangeM);
    if (!Number.isFinite(capture) || !(capture > 0)) return null;
    /*
     * Divide by CLOUD_FULL_START, so the cloud is FULLY opaque exactly at the
     * capture range -- not merely beginning there.
     *
     * The capture range is the last range at which the still is valid. Inward
     * of it the still is enlarged and fills the frame; outward it cannot fill
     * the frame at all, and the live map shows around its edges. Reported from
     * the running app as "we can see the edges of the AI map image and the real
     * Google map hiding behind it".
     *
     * So the handover has to be COMPLETE by then, which means the band is
     * placed by its opaque edge rather than by its leading one. Cloud first
     * appears at CLOUD_ENTER/CLOUD_FULL_START = 0.65 of the capture range and
     * is total by the capture range itself, so every range where the still has
     * shrunk below fill is already hidden. The whole approach -- everywhere the
     * still is enlarged, which is where a viewer is looking at the image rather
     * than leaving it -- stays completely clear.
     */
    return capture / CLOUD_FULL_START;
}

/**
 * Coverage, held opaque while the destination is still streaming in.
 *
 * WHY. The cloud is keyed to RANGE, because scroll drives range and a timed
 * cloud desynchronises the moment the viewer scrolls at a speed the animation
 * did not assume. That is right, and it has one consequence nobody chose: the
 * cloud clears exactly on schedule whether or not there is anything behind it
 * yet. Descending toward a home, the camera routinely outruns the tiles, so the
 * cloud parts onto a half-built world and the detail snaps in afterwards.
 *
 * The cloud is already an opaque curtain over an exchange. This lets it also be
 * the curtain over the LOADING, which costs nothing extra and is the same
 * gesture: weather that happens to clear when the world beneath is ready.
 *
 * TWO THINGS KEEP IT HONEST.
 *
 * It only holds where cloud already belongs -- inside the band, on the live-map
 * side of the swap. Outside the band there is no cloud to hold and conjuring
 * one would be a loading screen wearing weather as a disguise.
 *
 * And it can only hold as long as the caller's gate allows. The gate that feeds
 * this has a bounded budget and reports `timedOut`, at which point `hold` goes
 * false and the range-driven clearing resumes. A provider that never settles
 * therefore costs a few hundred milliseconds of thicker cloud, not a permanent
 * white screen -- the failure this would otherwise introduce is far worse than
 * the pop-in it removes.
 */
export function cloudCoverageHeld(rangeM, handoffRangeM, { hold = false } = {}) {
    const coverage = cloudCoverage(rangeM, handoffRangeM);
    if (hold !== true) return coverage;
    /* Only past the swap: before it the still is on screen and there is nothing
     * being waited for, so holding would hide the very thing it exists to show. */
    if (!showsLiveMap(rangeM, handoffRangeM)) return coverage;
    const edges = band(handoffRangeM);
    if (!edges) return coverage;
    /* And only where the band actually reaches. Past `clear` the journey has
     * left the cloud behind entirely and dragging it back would be a new
     * cloud, not a held one. */
    if (!(Number(rangeM) < edges.clear)) return coverage;
    return Math.max(coverage, OPAQUE_ENOUGH);
}

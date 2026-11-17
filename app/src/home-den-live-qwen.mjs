/* The live den view's Qwen line: what den-live's Qwen ask last said, in plain words, for the den-live chip.
 *
 * den-live (inference plan, step 2) asks Qwen only while Frigate sees someone in the den and den-live sees them seated.
 * Its people events carry health.qwen {state, why, last_reply_at, reply_s_median, ...} and, on a track it asked about,
 * activity {word, close_up, at}. That activity is shown only when den-live was sure whose answer it was.
 * qwenLine(event, sinceEventS) gives one line, or '' when the event says nothing about Qwen (den-live without the ask).
 * An answer's age is counted on den-live's own clock (the event's emitted_ts less the answer's at), plus how long
 * ago this page received that event, so the device's clock never matters.
 * An answer expires 10 s after its newest still (contract 3's valid_until_mono), and den-live drops it from its events
 * then. Should the activity carry its own expiry as valid_for_s (how long it stays valid after emitted_ts), the line
 * goes when that runs out; else 10 s after the answer. Never later than 10 s after it: Home never extends an expiry.
 * Pure: no DOM, no network. */
export const QWEN_ANSWER_S = 10;
const WORDS = Object.freeze({ reading: 'reading', hobby: 'a hobby', phone_use: 'on the phone', computer_work: 'at a computer',
    resting: 'resting', transition: 'moving', other: 'something else', unclear: 'unclear' });
const finite = value => typeof value === 'number' && Number.isFinite(value);
const short = text => (typeof text === 'string' ? text : '').replace(/\s+/g, ' ').trim().slice(0, 80);

export function qwenLine(event, sinceEventS = 0) {
    const qwen = event?.health?.qwen;
    if (!qwen || typeof qwen !== 'object') return '';
    let newest = null;
    for (const track of Array.isArray(event.tracks) ? event.tracks : []) {
        const a = track?.activity;
        if (a && typeof a.word === 'string' && finite(a.at) && (!newest || a.at > newest.at)) newest = a;
    }
    const since = Math.max(0, sinceEventS);
    const age = newest && finite(event.emitted_ts) ? Math.max(0, event.emitted_ts - newest.at + since) : null;
    const own = finite(newest?.valid_for_s) && newest.valid_for_s >= 0 ? newest.valid_for_s : Infinity;
    if (newest && age != null && age < QWEN_ANSWER_S && since < own) {
        const word = Object.hasOwn(WORDS, newest.word) ? WORDS[newest.word] : 'unclear';
        return `Qwen: ${word}${newest.close_up === true ? ' (close-up work)' : ''} · ${Math.round(age)} s ago`;
    }
    if (qwen.state === 'asking') return 'Qwen: asking…';
    if (qwen.state === 'unavailable') return `Qwen: unavailable${short(qwen.why) ? ` (${short(qwen.why)})` : ''}`;
    return `Qwen: off${short(qwen.why) ? ` (${short(qwen.why)})` : ''}`;
}

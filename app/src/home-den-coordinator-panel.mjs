/* The den's lighting coordinator in Home: a section of the live den view's chip (home-den-live-view.mjs).
 *
 * It reads den-live's coordinator state (window.HomeDenLive.coordinator, from the gateway) every 2 s while the den's
 * view shows, and puts it in words: on or off, the situation, why each spot does what it does and which fixtures it
 * refused, the bulb plan, and what it has learned.
 * Signed in, it also has the switch, the desk test, Clear and the settings. Each is one POST through the gateway, with
 * the gateway's CSRF header (web-gateway/den-live-route.mjs). Look-only shows the same state with no buttons, and
 * sends nothing but its GET.
 * When den-live runs no coordinator (its route answers 404 or 501), the section stays hidden and asks again every 30 s.
 * Everything den-live says is written with textContent. */

export const POLL_MS = 2000;
export const ABSENT_POLL_MS = 30000;
export const CSRF_HEADER = 'X-Home-Den-Live-CSRF';
const CLEAR_ARM_MS = 4000;

const ACTIVITY = {
    gaming: 'Gaming', task: 'A close-up task', sitting: 'Sitting', present: 'Someone is here', vacant: 'Nobody is here',
    unknown: 'Not sure yet',
};
const REASON = {
    in_front_of_reader: 'in front of you', beam_through_head: 'its beam would cross a head', too_far: 'too far away',
    out_of_range: "can't turn that far", no_registered_position: 'not placed yet', paused: 'paused',
    no_head: "can't see the head", no_facing: "can't tell which way you face", behind_the_piece: 'behind the artwork',
    through_a_seated_head: 'its beam would cross a seated head', no_target: 'nothing to aim at',
    no_fixture: 'no fixture can reach', no_free_fixture: 'no free fixture',
};
const ACTUATION = {
    shadow: 'Watching only: nothing moves while it is off or a Stop holds',
    virtual: 'Moving the virtual spots in Home',
    live: 'Moving the real fixtures',
};

/* The settings, as den-live's coordinator names them, with words and kinds. Any other setting den-live reports is
 * shown with its own name, so a new one is never hidden. */
export const SETTINGS = [
    { key: 'move_style', label: 'While a spot moves', kind: 'choice', options: [['lit', 'stays lit'], ['dim', 'dims'], ['dark', 'goes dark']] },
    { key: 'move_dim_level', label: 'Dim to, while moving (0–1)', kind: 'number', step: 0.05, max: 1 },
    { key: 'fade_in_s', label: 'Fade in (s)', kind: 'number', step: 0.1 },
    { key: 'fade_out_s', label: 'Fade out (s)', kind: 'number', step: 0.1 },
    { key: 'shift_m', label: 'Follow a shift of at least (m)', kind: 'number', step: 0.01 },
    { key: 'shift_hold_s', label: 'once it has held for (s)', kind: 'number', step: 0.1 },
    { key: 'phone_patience_s', label: 'Phone patience (s)', kind: 'number', step: 1 },
    { key: 'pre_aim', label: 'Point a dark spot at you ahead of time', kind: 'flag' },
    { key: 'accent_level', label: 'Artwork light (0–1)', kind: 'number', step: 0.05, max: 1 },
    { key: 'follow_walkers', label: 'Follow people walking', kind: 'flag' },
];

const words = value => String(value ?? '').replace(/_/g, ' ');
export const lightName = entity => words(String(entity || '').replace(/^light\./, ''));
export const percent = brightness => Number.isFinite(brightness) ? `${Math.round(Math.max(0, Math.min(255, brightness)) / 2.55)}%` : '';

export function situationWords(situation) {
    const key = typeof situation === 'string' ? situation : situation?.key;
    const [act, part] = String(key || '').split('/');
    if (!act) return 'No decision yet';
    const people = Number.isFinite(situation?.people) && situation.people > 1 ? ` · ${situation.people} people` : '';
    return `${ACTIVITY[act] || words(act)}${part ? `, ${words(part)}` : ''}${people}`;
}

export function reasonWords(reason) {
    const raw = String(reason || '');
    return REASON[raw] || (raw.startsWith('pan_tilt_infeasible') ? "can't turn that far" : words(raw) || 'no reason given');
}

function jobWords(job, level) {
    const at = Number.isFinite(level) && level > 0 ? `, at ${Math.round(level * 100)}%` : '';
    if (!job) return 'rests';
    if (job.kind === 'task') return `lights the close-up task${at}`;
    if (job.kind === 'pre_aim') return 'points at you, dark, ready';
    if (job.kind === 'accent') return `lights ${words(job.who)}${at}`;
    return `${words(job.kind)}${at}`;
}

/* "Why", from the latest Decision: each spot's job, then each fixture it refused and why, in words. */
export function whyLines(decision) {
    if (!decision || typeof decision !== 'object') return ['No decision yet'];
    const lines = [];
    if (ACTUATION[decision.actuation]) lines.push(ACTUATION[decision.actuation]);
    const spots = decision.spots && typeof decision.spots === 'object' ? decision.spots : {};
    for (const [fixture, spot] of Object.entries(spots).sort(([a], [b]) => a.localeCompare(b))) {
        lines.push(`${fixture} ${jobWords(spot?.job, spot?.level)}`);
    }
    if (!Object.keys(spots).length) lines.push('No change to the spots');
    const refused = new Set();
    for (const note of Array.isArray(decision.notes) ? decision.notes : []) {
        if (!note || typeof note !== 'object') continue;
        refused.add(note.fixture ? `${note.fixture} not used: ${reasonWords(note.reason)}` : `No spot: ${reasonWords(note.reason)}`);
    }
    return [...lines, ...[...refused].sort()];
}

function sceneWords(scene) {
    if (!scene || typeof scene !== 'object') return '';
    if (scene.on === false) return 'off';
    return [percent(scene.brightness), Number.isFinite(scene.kelvin) ? `${Math.round(scene.kelvin)} K` : ''].filter(Boolean).join(' · ') || 'on';
}

/* The bulb plan den-live last sent den_lighting. An empty one hands every bulb back to den_lighting's own rules. */
export function planLines(plan) {
    const scenes = plan?.data?.scenes;
    if (!plan || !scenes || typeof scenes !== 'object') return ['No plan sent yet'];
    const lights = Object.entries(scenes).sort(([a], [b]) => a.localeCompare(b));
    if (!lights.length) return ["None: the bulbs follow den_lighting's own rules"];
    return lights.map(([light, scene]) => `${lightName(light)}: ${sceneWords(scene)}`);
}

/* What it learned: per situation and light, the scene and how many of your changes it rests on. */
export function learnedLines(learned) {
    const lines = [];
    for (const [key, lights] of Object.entries(learned && typeof learned === 'object' ? learned : {}).sort(([a], [b]) => a.localeCompare(b))) {
        for (const [light, row] of Object.entries(lights && typeof lights === 'object' ? lights : {}).sort(([a], [b]) => a.localeCompare(b))) {
            const n = Number.isFinite(row?.n) ? row.n : 0;
            lines.push(`${situationWords(key)} · ${lightName(light)}: ${sceneWords(row)} (${n} ${n === 1 ? 'change' : 'changes'})`);
        }
    }
    return lines.length ? lines : ['Nothing yet'];
}

export function summaryWords(state) {
    if (!state) return 'Lighting coordinator';
    return `Lighting coordinator · ${state.on ? 'On' : 'Off'}${state.stopped ? ' · Stop holds' : ''} · ${situationWords(state.situation)}`;
}

/* The settings den-live changed, as a POST body: only the ones that differ from what it has. */
export function changedSettings(current, form) {
    const out = {};
    for (const [key, value] of Object.entries(form)) {
        if (!(key in (current || {}))) continue;
        if (current[key] !== value) out[key] = value;
    }
    return out;
}

function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}
function fillList(list, lines) {
    const now = [...list.children].map(node => node.textContent);
    if (now.length === lines.length && now.every((text, i) => text === lines[i])) return;
    list.replaceChildren(...lines.map(line => element('li', '', line)));
}
function setText(node, text) { if (node.textContent !== text) node.textContent = text; }

/* The section. tick() polls when one is due; pause() stops polling until the next tick. */
export function createCoordinatorPanel({ url, lookOnly = false, fetchImpl = (...args) => window.fetch(...args), clock = () => performance.now(),
    wallClock = () => Date.now() } = {}) {
    let state = null, kind = 'none', lastPoll = -Infinity, inflight = false, polls = 0, posts = 0, problem = '', busy = false;
    let clearArmedAt = -Infinity, settingsShown = '', dirty = false;
    const root = element('details', 'home-den-live-coordinator');
    root.dataset.denCoordinator = '1';
    root.hidden = true;
    const summary = element('summary', 'home-den-live-coordinator-summary', 'Lighting coordinator');
    const body = element('div', 'home-den-live-coordinator-body');
    root.append(summary, body);

    const controls = element('div', 'home-den-live-coordinator-controls');
    const switchButton = element('button', 'home-den-live-coordinator-button', 'Turn on');
    switchButton.type = 'button'; switchButton.dataset.denCoordinatorSwitch = '1';
    const deskButton = element('button', 'home-den-live-coordinator-button', 'Start desk test (3 h)');
    deskButton.type = 'button'; deskButton.dataset.denCoordinatorDesk = '1';
    controls.append(switchButton, deskButton);
    const facts = element('p', 'home-den-live-coordinator-facts');
    const error = element('p', 'home-den-live-coordinator-error');
    error.setAttribute('role', 'status');
    const whyHead = element('p', 'home-den-live-coordinator-head', 'Why');
    const why = element('ul', 'home-den-live-coordinator-list'); why.dataset.denCoordinatorWhy = '1';
    const planHead = element('p', 'home-den-live-coordinator-head', 'Bulbs');
    const plan = element('ul', 'home-den-live-coordinator-list'); plan.dataset.denCoordinatorPlan = '1';
    const learnedHead = element('p', 'home-den-live-coordinator-head', 'Learned');
    const clearButton = element('button', 'home-den-live-coordinator-button home-den-live-coordinator-inline', 'Clear');
    clearButton.type = 'button'; clearButton.dataset.denCoordinatorClear = '1';
    const learned = element('ul', 'home-den-live-coordinator-list'); learned.dataset.denCoordinatorLearned = '1';
    const settingsHead = element('p', 'home-den-live-coordinator-head', 'Settings');
    const settings = element('form', 'home-den-live-coordinator-settings'); settings.dataset.denCoordinatorSettings = '1';
    // Steps and limits only set the arrows' increments: any value goes to den-live, which says what it refuses.
    settings.noValidate = true;
    if (!lookOnly) learnedHead.append(clearButton);
    body.append(...(lookOnly ? [] : [controls]), facts, error, whyHead, why, planHead, plan, learnedHead, learned,
        ...(lookOnly ? [] : [settingsHead, settings]));

    async function post(path, payload, label) {
        if (lookOnly || busy) return null;
        busy = true; posts += 1; render();
        try {
            const response = await fetchImpl(`${url}/${path}`, { method: 'POST', credentials: 'same-origin', cache: 'no-store',
                headers: { 'Content-Type': 'application/json', Accept: 'application/json', [CSRF_HEADER]: '1' }, body: JSON.stringify(payload) });
            const answer = await response.json().catch(() => null);
            if (!response.ok || answer?.ok === false) throw new Error(String(answer?.error || `HTTP ${response.status}`).slice(0, 200));
            problem = '';
            return answer;
        } catch (failure) {
            problem = `${label} didn't go through: ${failure?.message || failure}`;
            return null;
        } finally {
            busy = false; lastPoll = -Infinity;
            render();
            void poll();
        }
    }
    switchButton.addEventListener('click', () => { if (state) void post('switch', { on: !state.on }, state.on ? 'Turning it off' : 'Turning it on'); });
    deskButton.addEventListener('click', () => {
        const on = !deskOn();
        void post('desk', { on }, on ? 'Starting the desk test' : 'Stopping the desk test');
    });
    clearButton.addEventListener('click', event => {
        event.preventDefault();
        if (clock() - clearArmedAt > CLEAR_ARM_MS) { clearArmedAt = clock(); render(); return; }
        clearArmedAt = -Infinity;
        void post('learned/clear', {}, 'Clearing what it learned');
    });
    settings.addEventListener('input', () => { dirty = true; renderSettingsButton(); });
    settings.addEventListener('submit', async event => {
        event.preventDefault();
        const changes = changedSettings(state?.settings, readForm());
        if (!Object.keys(changes).length) { dirty = false; renderSettingsButton(); return; }
        const answer = await post('settings', changes, 'Saving the settings');
        if (answer) { dirty = false; settingsShown = ''; }
    });

    function deskOn() {
        return Number.isFinite(state?.desk_until) && state.desk_until * 1000 > wallClock();
    }
    function readForm() {
        const out = {};
        for (const input of settings.querySelectorAll('[data-setting]')) {
            const key = input.dataset.setting, kindOf = input.dataset.kind;
            if (kindOf === 'flag') out[key] = input.checked;
            else if (kindOf === 'number') { if (input.value !== '' && Number.isFinite(Number(input.value))) out[key] = Number(input.value); }
            else out[key] = input.value;
        }
        return out;
    }
    function renderSettings(values) {
        const shown = JSON.stringify(values || {});
        const focused = document.activeElement;
        if (dirty || shown === settingsShown || (settings.contains(focused) && focused.matches('input, select'))) return;
        settingsShown = shown;
        const known = new Map(SETTINGS.map(row => [row.key, row]));
        const rows = [...SETTINGS.filter(row => row.key in (values || {})),
            ...Object.keys(values || {}).filter(key => !known.has(key)).sort().map(key => ({ key, label: words(key),
                kind: typeof values[key] === 'boolean' ? 'flag' : typeof values[key] === 'number' ? 'number' : 'text', step: 'any' }))];
        settings.replaceChildren(...rows.map(row => {
            const label = element('label', `home-den-live-coordinator-setting${row.kind === 'flag' ? ' is-flag' : ''}`);
            let input;
            if (row.kind === 'choice') {
                input = element('select');
                for (const [value, text] of row.options) { const option = element('option', '', text); option.value = value; input.append(option); }
                input.value = String(values[row.key]);
            } else {
                input = element('input');
                if (row.kind === 'flag') { input.type = 'checkbox'; input.checked = values[row.key] === true; }
                else if (row.kind === 'number') {
                    input.type = 'number'; input.min = '0'; input.step = String(row.step ?? 'any'); if (row.max != null) input.max = String(row.max);
                    input.value = String(values[row.key]);
                } else { input.type = 'text'; input.value = String(values[row.key] ?? ''); }
            }
            input.dataset.setting = row.key; input.dataset.kind = row.kind;
            label.append(element('span', '', row.label), input);
            return label;
        }), element('button', 'home-den-live-coordinator-button', 'Save'));
        const save = settings.querySelector('button');
        save.type = 'submit'; save.dataset.denCoordinatorSave = '1';
        renderSettingsButton();
    }
    function renderSettingsButton() {
        const save = settings.querySelector('[data-den-coordinator-save]');
        if (save) save.disabled = busy || !dirty;
    }

    function render() {
        root.hidden = kind === 'none' || kind === 'absent';
        root.dataset.state = kind;
        setText(summary, kind === 'unreachable' && !state ? 'Lighting coordinator · not answering' : summaryWords(state));
        if (!state) {
            setText(facts, kind === 'unreachable' ? problem : '');
            return;
        }
        root.dataset.on = state.on ? '1' : '0';
        const ha = state.home_assistant || {};
        const lines = [];
        if (kind === 'unreachable') lines.push('den-live is not answering; this is what it last said');
        if (state.stopped) lines.push('A Stop holds the spots until Resume');
        if (deskOn()) lines.push(`Desk test on until ${new Date(state.desk_until * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`);
        if (ha.fresh === false) {
            lines.push(`Home Assistant ${Number.isFinite(ha.age_s) ? `hasn't answered for ${Math.round(ha.age_s)} s` : "hasn't answered yet"}`
                + `${ha.problem ? `: ${String(ha.problem).slice(0, 160)}` : ''}`);
        } else if (ha.problem) lines.push(String(ha.problem).slice(0, 160));
        setText(facts, lines.join(' · '));
        setText(error, problem);
        fillList(why, whyLines(state.decision));
        why.title = typeof state.decision?.why === 'string' ? state.decision.why.slice(0, 600) : '';
        fillList(plan, planLines(state.plan));
        fillList(learned, learnedLines(state.learned));
        if (!lookOnly) {
            setText(switchButton, state.on ? 'Turn off' : 'Turn on');
            switchButton.setAttribute('aria-pressed', state.on ? 'true' : 'false');
            setText(deskButton, deskOn() ? 'Stop desk test' : 'Start desk test (3 h)');
            const armed = clock() - clearArmedAt <= CLEAR_ARM_MS;
            setText(clearButton, armed ? 'Clear: sure?' : 'Clear');
            clearButton.dataset.armed = armed ? '1' : '';
            for (const button of [switchButton, deskButton, clearButton]) button.disabled = busy;
            clearButton.hidden = learnedLines(state.learned)[0] === 'Nothing yet' && !armed;
            renderSettings(state.settings);
            renderSettingsButton();
        }
    }

    async function poll() {
        if (inflight) return;
        inflight = true; lastPoll = clock(); polls += 1;
        try {
            const response = await fetchImpl(url, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } });
            if (response.status === 404 || response.status === 501) { kind = 'absent'; state = null; return; }
            const answer = await response.json().catch(() => null);
            if (!response.ok || !answer || typeof answer !== 'object') throw new Error(`HTTP ${response.status}`);
            state = answer; kind = 'running';
        } catch (failure) {
            kind = 'unreachable';
            if (!state) problem = `den-live didn't answer: ${failure?.message || failure}`;
        } finally {
            inflight = false;
            render();
        }
    }

    return {
        element: root,
        tick() {
            if (clock() - lastPoll >= (kind === 'absent' ? ABSENT_POLL_MS : POLL_MS)) void poll();
            else if (state && !lookOnly) render();
        },
        pause() { lastPoll = -Infinity; },
        status: () => ({ kind, on: state ? state.on === true : null, situation: state ? situationWords(state.situation) : null,
            polls, posts, problem, hidden: root.hidden, open: root.open, buttons: !lookOnly }),
    };
}

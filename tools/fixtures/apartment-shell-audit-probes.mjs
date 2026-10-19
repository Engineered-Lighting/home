// In-page, read-only probes for the Home shell audit. Each export is
// serialized into the authored page with page.evaluate, so none may close over
// module state. They read computed style and layout only; they never click,
// dispatch input or call application setters.

// Text contrast, faint-token and small-text inventory for every visible text
// run inside the unified shell, composited over its declared backgrounds.
export function auditText() {
  const shell = document.querySelector('.apt-home-shell[data-home-layout="unified"]');
  if (!shell) throw Error('unified shell is not mounted');
  // Sample settled colours: finite entrance fades end now; loops keep running.
  for (const animation of document.getAnimations()) if (animation.effect?.getComputedTiming().iterations !== Infinity) animation.finish();
  const ctx = new OffscreenCanvas(1, 1).getContext('2d', { willReadFrequently: true });
  const rgba = color => {
    ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = 'rgba(0,0,0,0)'; ctx.fillStyle = color; ctx.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data; return [r, g, b, a / 255];
  };
  const over = (top, under) => {
    const a = top[3] + under[3] * (1 - top[3]);
    if (a <= 0) return [0, 0, 0, 0];
    return [0, 1, 2].map(i => (top[i] * top[3] + under[i] * under[3] * (1 - top[3])) / a).concat(a);
  };
  const lum = c => c.slice(0, 3).map(v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; })
    .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05); };
  function background(element) {
    const layers = [];
    for (let node = element; node; node = node.parentElement) {
      const style = getComputedStyle(node), color = rgba(style.backgroundColor);
      if (color[3] > 0) layers.push(color);
      if (color[3] >= .999) break;
    }
    let result = rgba(getComputedStyle(document.documentElement).backgroundColor);
    if (result[3] < 1) result = [255, 255, 255, 1];
    for (const layer of layers.reverse()) result = over(layer, result);
    return result;
  }
  const opacity = element => { let value = 1; for (let node = element; node; node = node.parentElement) value *= +getComputedStyle(node).opacity; return value; };
  const describe = element => {
    const name = element.getAttribute('aria-label') || element.textContent.trim().replace(/\s+/g, ' ').slice(0, 48);
    const classes = typeof element.className === 'string' ? element.className.trim().split(/\s+/).filter(Boolean).slice(0, 2).join('.') : '';
    return element.tagName.toLowerCase() + (classes ? '.' + classes : '') + (name ? ' "' + name + '"' : '');
  };
  const rows = [];
  const walker = document.createTreeWalker(shell, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  for (let text = walker.nextNode(); text; text = walker.nextNode()) {
    const element = text.parentElement;
    // Only words and numbers inform; glyph-only runs (boot art dots) are decoration.
    if (!element || seen.has(element) || !/[\p{L}\p{N}]/u.test(text.data)) continue;
    seen.add(element);
    if (element.closest('[aria-hidden="true"], script, style, noscript, canvas, svg, option, #authored-shell-proof-note')) continue;
    const style = getComputedStyle(element), rect = element.getBoundingClientRect();
    if (!element.getClientRects().length || rect.width < 2 || rect.height < 2 || style.visibility !== 'visible') continue;
    if (/rect\(0px,? 0px,? 0px,? 0px\)/.test(style.clip) || (rect.width <= 1 && rect.height <= 1)) continue;
    const range = document.createRange(); range.selectNodeContents(text);
    const box = range.getBoundingClientRect();
    if (box.width < 1 || box.height < 1) continue;
    const disabled = !!element.closest(':disabled, [aria-disabled="true"], fieldset:disabled');
    const fade = opacity(element);
    if (fade < .05) continue;
    const bg = background(element), fg = rgba(style.color);
    const shown = over([fg[0], fg[1], fg[2], fg[3] * fade], bg);
    const size = parseFloat(style.fontSize), weight = +style.fontWeight || 400;
    const large = size >= 24 || (size >= 18.66 && weight >= 700);
    const faint = rgba(style.getPropertyValue('--hg-fg-5').trim() || 'transparent');
    rows.push({ element: describe(element), text: text.data.trim().slice(0, 60), ratio: +ratio(shown, bg).toFixed(2),
      need: large ? 3 : 4.5, size, disabled, fg5: faint[3] > 0 && fg.slice(0, 3).every((v, i) => Math.abs(v - faint[i]) <= 1),
      stage: !!element.closest('.apt-home-stage'), color: style.color, background: 'rgb(' + bg.slice(0, 3).map(Math.round).join(',') + ')' });
  }
  return rows;
}

// Every visible, enabled target in the unified shell, with the size of the
// area that actually receives a pointer at its centre lines.
export function auditTargets() {
  const shell = document.querySelector('.apt-home-shell[data-home-layout="unified"]');
  const selector = 'button, a[href], input:not([type="hidden"]), select, textarea, summary, [role="button"], [role="tab"], [role="switch"], [role="menuitem"], [role="slider"], [role="checkbox"], [tabindex]:not([tabindex="-1"])';
  const owns = (element, hit) => !!hit && (hit === element || element.contains(hit)
    || (element.matches('input') && hit.closest('label') && hit.closest('label').contains(element))
    || (hit.matches('input') && element.contains(hit)));
  const describe = element => {
    const name = element.getAttribute('aria-label') || element.getAttribute('title') || element.textContent.trim().replace(/\s+/g, ' ').slice(0, 40);
    const classes = typeof element.className === 'string' ? element.className.trim().split(/\s+/).filter(Boolean).slice(0, 2).join('.') : '';
    return element.tagName.toLowerCase() + (classes ? '.' + classes : '') + (name ? ' "' + name + '"' : '');
  };
  const rows = [];
  for (const control of shell.querySelectorAll(selector)) {
    if (control.closest('[hidden], [inert], [aria-hidden="true"]') || control.matches(':disabled, [aria-disabled="true"]')) continue;
    // A checkbox or radio wrapped in its label is hit through the whole label.
    const element = control.matches('input[type="checkbox"], input[type="radio"]') && control.closest('label:not(.apt-ui-switch)') || control;
    if (element.tagName === 'CANVAS') continue;
    const style = getComputedStyle(element);
    if (style.visibility !== 'visible' || style.pointerEvents === 'none' || !element.getClientRects().length) continue;
    // An opacity-0 native input that a visible label draws is measured through that label.
    let rect = element.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) continue;
    // Inline links inside running text are exempt (WCAG 2.5.8 inline exception).
    if (element.matches('a') && style.display === 'inline' && element.parentElement && /\S/.test(element.parentElement.textContent.replace(element.textContent, ''))) continue;
    const scroller = element.closest('.apt-home-controls, .apt-home-camera, .apt-ui-chat-feed, [role="log"]');
    const before = scroller ? scroller.scrollTop : 0;
    if (rect.bottom < 0 || rect.top > innerHeight || rect.right < 0 || rect.left > innerWidth) {
      element.scrollIntoView({ block: 'center', inline: 'nearest' }); rect = element.getBoundingClientRect();
    }
    const cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2, reach = 21.5;
    const probe = (x, y) => x >= 0 && y >= 0 && x < innerWidth && y < innerHeight && owns(element, document.elementFromPoint(x, y));
    const centre = probe(cx, cy);
    const cross = (x, y) => probe(x, y) && probe(x - reach, y) && probe(x + reach, y) && probe(x, y - reach) && probe(x, y + reach);
    let wide = rect.width >= 43.5 || (probe(cx - reach, cy) && probe(cx + reach, cy));
    let tall = rect.height >= 43.5 || (probe(cx, cy - reach) && probe(cx, cy + reach));
    // A non-rectangular target (an SVG ring sector) passes when a 44 px cross
    // fits anywhere inside its own hit area.
    if (!(wide && tall) && rect.width >= 43.5 && rect.height >= 43.5 && element instanceof SVGElement) {
      search: for (let i = 1; i < 10; i++) for (let j = 1; j < 10; j++)
        if (cross(rect.left + rect.width * i / 10, rect.top + rect.height * j / 10)) { wide = tall = true; break search; }
    }
    if (scroller) scroller.scrollTop = before;
    const hit = centre ? null : document.elementFromPoint(cx, cy);
    rows.push({ element: describe(element), width: +rect.width.toFixed(1), height: +rect.height.toFixed(1),
      covered: !centre, coveredBy: hit ? describe(hit) : null,
      label: element.closest('label') ? element.closest('label').getBoundingClientRect().height : null, ok: wide && tall, stage: !!element.closest('.apt-home-stage') });
  }
  return rows;
}

// Computed surfaces and type against DIRECTION.md › Visual direction.
export function auditSurfaces() {
  const shell = document.querySelector('.apt-home-shell[data-home-layout="unified"]');
  const panel = shell.querySelector('.apt-home-panel'), panelStyle = getComputedStyle(panel);
  const token = name => getComputedStyle(panel).getPropertyValue(name).trim();
  const ctx = new OffscreenCanvas(1, 1).getContext('2d', { willReadFrequently: true });
  const rgba = color => { ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = 'rgba(0,0,0,0)'; ctx.fillStyle = color; ctx.fillRect(0, 0, 1, 1); return Array.from(ctx.getImageData(0, 0, 1, 1).data).join(','); };
  const first = selector => [...shell.querySelectorAll(selector)].find(element => element.getClientRects().length);
  const pick = (selector, fields) => {
    const element = first(selector); if (!element) return null;
    const style = getComputedStyle(element);
    return Object.fromEntries(fields.map(field => [field, style[field]]));
  };
  const narrow = shell.getBoundingClientRect().width < 900;
  return {
    narrow,
    fonts: { geist: document.fonts.check('13px "Geist"'), mono: document.fonts.check('12px "Geist Mono"') },
    column: { background: rgba(panelStyle.backgroundColor), token: rgba(token('--hg-bg-1')),
      borderLeft: panelStyle.borderLeftWidth + ' ' + panelStyle.borderLeftStyle, borderTop: panelStyle.borderTopWidth + ' ' + panelStyle.borderTopStyle,
      radius: panelStyle.borderTopLeftRadius },
    section: pick('.apt-ui-header, .apt-ui-section:not(:last-child)', ['borderBottomWidth', 'borderBottomStyle', 'borderRadius', 'backgroundColor']),
    title: pick('.apt-ui-title', ['fontSize', 'fontWeight', 'fontFamily']),
    heading: pick('.apt-ui-heading', ['fontSize', 'fontWeight']),
    copy: pick('.apt-ui-copy, .apt-ui-sub', ['fontSize']),
    value: pick('.apt-ui-value', ['fontSize', 'fontFamily']),
    button: pick('.apt-ui-button:not(.is-small)', ['borderTopLeftRadius']),
    segment: pick('.apt-ui-seg', ['borderTopLeftRadius']),
    group: pick('.apt-ui-channel, .apt-ui-callout', ['borderTopLeftRadius']),
    badge: pick('.apt-ui-badge', ['fontSize', 'fontFamily', 'borderTopLeftRadius']),
  };
}

// Where a keyboard user is after each Tab, and whether a ring shows it.
export function describeFocus() {
  const element = document.activeElement;
  if (!element || element === document.body) return null;
  const style = getComputedStyle(element), rect = element.getBoundingClientRect();
  const label = element.closest('label');
  const ring = (s) => (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) >= 1) || /\d+px/.test(s.boxShadow) && s.boxShadow !== 'none';
  const region = element.closest('.apt-home-topbar') ? 'header' : element.closest('.apt-home-stage') ? 'stage'
    : element.closest('.apt-home-tabs, .apt-home-sheet-grip') ? 'tabs' : element.closest('.apt-home-controls') ? 'inspector'
      : element.closest('.apt-home-camera') ? 'dock' : element.closest('.apt-home-chat') ? 'chat' : 'other';
  return { region, name: element.getAttribute('aria-label') || element.textContent.trim().replace(/\s+/g, ' ').slice(0, 40),
    tag: element.tagName.toLowerCase(), visible: rect.width > 0 && rect.height > 0,
    ring: ring(style) || (!!label && ring(getComputedStyle(label))) };
}

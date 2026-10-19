/* The lighting brain's explanation in the Victoria view: a compact "why" line,
 * brain online/offline, an expandable last-20 decision log and an Auto/Paused
 * marker per virtual fixture. Display only; the follower owns every change. */
(function (global) {
  'use strict';
  const words = value => String(value || '').replaceAll('_', ' ');
  const clock = at => {
    const date = Number.isFinite(at) ? new Date(at * 1000) : null;
    return date && Number.isFinite(date.getTime()) ? date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
  };
  const statusLabel = brain => brain.online ? 'Brain online'
    : brain.status === 'connecting' ? 'Connecting to the lighting brain…'
    : brain.status === 'stopped' ? 'Brain not connected' : 'Brain offline · holding the last state';
  const note = { fontSize: 10, opacity: .7, lineHeight: 1.45 };
  const chip = auto => ({ fontSize: 10, padding: '1px 6px', border: '1px solid', borderRadius: 3,
    color: auto ? '#b8d8ff' : '#e9bd7e', borderColor: auto ? 'rgba(184,216,255,.35)' : '#8d6d39' });

  function HomeLightingBrainPanel({ brain, labels = {}, onReturnToAuto }) {
    if (!brain) return null;
    const fixtures = brain.fixtures || [], decisions = brain.decisions || [];
    return <section aria-label="Lighting brain" data-lighting-brain="1" data-lighting-brain-status={brain.status}
      style={{ marginTop: 10, paddingTop: 8, borderTop: '1px solid rgba(184,216,255,.18)' }}>
      <div role="status" aria-live="polite" data-lighting-brain-why="1" style={{ fontSize: 12, lineHeight: 1.4 }}>
        {brain.why || (brain.online ? 'Following the lighting brain' : 'No lighting decision yet')}
      </div>
      <div data-lighting-brain-online={brain.online ? '1' : '0'}
        style={{ ...note, opacity: 1, color: brain.online ? 'var(--hg-fg-3, #a8b5c0)' : '#e9bd7e' }}>
        {statusLabel(brain)}{brain.mode ? ` · ${words(brain.mode)}` : ''}
        {brain.refused_reason ? ` · refused ${words(brain.refused_reason)}` : ''}
      </div>
      {brain.notice && <div role="status" style={{ ...note, color: '#e9bd7e' }}>{brain.notice}</div>}
      {fixtures.length > 0 && <ul aria-label="Fixture automation" style={{ listStyle: 'none', margin: '7px 0 0', padding: 0 }}>
        {fixtures.map(row => <li key={row.fixture_id} data-lighting-brain-fixture={row.fixture_id}
          data-lighting-brain-auto={row.auto ? '1' : '0'}
          style={{ display: 'flex', alignItems: 'center', gap: 6, minHeight: 26, fontSize: 11 }}>
          <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{labels[row.fixture_id] || row.fixture_id}</span>
          <span style={chip(row.auto)} title={row.auto ? 'Following the lighting brain' : `Paused${row.pause_reason ? ` · ${words(row.pause_reason)}` : ''}`}>
            {row.auto ? 'Auto' : 'Paused'}</span>
          {!row.auto && onReturnToAuto && <button type="button" onClick={() => onReturnToAuto(row.fixture_id)}
            aria-label={`Return ${labels[row.fixture_id] || row.fixture_id} to Auto`} style={{ minHeight: 26, fontSize: 10 }}>Return to Auto</button>}
        </li>)}
      </ul>}
      {fixtures.some(row => row.override_error) && <div style={{ ...note, color: '#e9bd7e' }}>
        A manual pause was kept here but not confirmed by the brain.</div>}
      <details data-lighting-brain-log="1" style={{ marginTop: 6 }}>
        <summary style={{ cursor: 'pointer', minHeight: 24, fontSize: 11 }}>Decisions ({decisions.length})</summary>
        {decisions.length ? <ol style={{ margin: '4px 0 0', paddingLeft: 16, maxHeight: 180, overflowY: 'auto' }}>
          {decisions.map((row, index) => <li key={`${row.at}:${index}`} data-lighting-brain-decision={row.rule}
            style={{ fontSize: 10, lineHeight: 1.45, marginBottom: 3 }}>
            <span style={{ opacity: .65 }}>{clock(row.at)} · {words(row.rule)}</span>
            <div>{row.why}{row.change ? ` · ${row.change}` : ''}</div>
          </li>)}
        </ol> : <div style={note}>No decisions yet.</div>}
      </details>
      <div style={note}>Virtual fixtures only · a manual change pauses that fixture</div>
    </section>;
  }
  global.HomeLightingBrainPanel = HomeLightingBrainPanel;
})(window);

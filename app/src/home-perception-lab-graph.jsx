/* Perception Lab graph workspace (design v8) over the read-only
 * perception-lab-graph-1 contract (docs/PERCEPTION-LAB-GRAPH-CONTRACT.md).
 * Every value shown comes from sessions.graph / sessions.timeline /
 * sessions.replay or the existing live status; absent data renders as the
 * contract's own status. Nothing here runs inference, writes, or actuates. */
(function (global) {
  "use strict";

  const {useState, useEffect, useRef, useMemo, useCallback} = React;
  const Data = () => global.HomePerceptionLabData || {};

  // ------------------------------------------------------------------ model
  const HEADS = [['src', 'SOURCES · NATIVE OUTPUT'], ['p1', 'PREPARATION'], ['p2', 'RELATIONS'], ['ev', 'EVIDENCE ASSEMBLY'], ['jev', 'CONTEXTUAL REASONING · JEV'], ['bel', 'BELIEF'], ['pol', 'DECISION POLICY'], ['act', 'PROPOSED ACTION'], ['gate', 'AUTHORIZATION'], ['exec', 'EXECUTION'], ['ver', 'VERIFICATION'], ['fb', 'HUMAN REVIEW']];
  const GEOM = {
    detailed: {src: [60, 380], p1: [560, 340], p2: [1000, 340], ev: [1440, 400], jev: [1940, 400], bel: [2440, 280], pol: [2820, 300], act: [3220, 280], gate: [3600, 280], exec: [3980, 260], ver: [4340, 260], fb: [4700, 300]},
    simple: {src: [60, 300], p1: [440, 280], p2: [800, 280], ev: [1160, 300], jev: [1540, 300], bel: [1920, 240], pol: [2240, 260], act: [2580, 240], gate: [2900, 240], exec: [3220, 230], ver: [3530, 230], fb: [3840, 270]}
  };
  const GROUPS = [['visual', 'VISUAL'], ['spatial_temporal', 'SPATIAL · TEMPORAL'], ['home_device', 'HOME ASSISTANT · DEVICES'], ['environment_time', 'ENVIRONMENT · TIME'], ['personal', 'PERSONAL'], ['operational', 'OPERATIONAL'], ['other', 'OTHER']];
  // Future stages are recorded as {status:'inactive', reason:'actuation_deferred'}.
  const FUTURE = {
    contextual_beliefs: ['bel', 'Contextual belief over time', 'Not implemented'],
    policy: ['pol', 'Decision policy', 'Not implemented'],
    proposed_action: ['act', 'Proposed action', 'Not implemented'],
    authorization: ['gate', 'Authorization + gating', 'Not implemented'],
    execution: ['exec', 'Execution', 'Disabled in Lab'],
    verification: ['ver', 'Outcome verification', 'Not connected'],
    human_review: ['fb', 'Human review', 'Awaiting review']
  };
  const PER_ARM_FUTURE = ['contextual_beliefs', 'policy', 'proposed_action'];
  const SHARED_FUTURE = ['authorization', 'execution', 'verification', 'human_review'];
  const SETUP_FIELDS = ['input', 'detector', 'pose', 'masks', 'reasoner', 'device', 'precision', 'localization', 'room_map', 'history', 'sampling_hz', 'ha_evidence', 'caption_evidence', 'ambient_evidence', 'qwen_scene_evidence', 'temporal_encoder_evidence'];
  const SPECIALIST_FIELDS = new Set(['input', 'detector', 'pose', 'masks', 'device', 'precision', 'sampling_hz', 'profile.profile_id', 'profile.model_version', 'profile.artifact_sha256']);
  const CONSUMED_WORD = {consumed: 'in request', not_in_request: 'not in request', no_request: 'no request', withheld_by_setup: 'withheld by setup',
    unknown_older_than_scan_bound: 'unknown · scan bound', other_arm: '—', not_a_jev_input: 'not a JEV input', excluded_by_policy: 'excluded by policy', not_implemented: 'not implemented'};
  const EVIDENCE_KIND_NODE = {'perception.output': arm => 'src:specialist_' + arm, 'ha.observation': () => 'src:ha_snapshot', 'caption.receipt': () => 'src:caption_receipt',
    historical_qwen: () => 'src:caption_receipt', 'room.observation': () => 'src:room_observation', 'qwen.observation': () => 'src:qwen_scene',
    'vjepa.observation': () => 'src:vjepa_scene'};

  const isObj = v => v && typeof v === 'object' && !Array.isArray(v);
  const finite = v => typeof v === 'number' && Number.isFinite(v);
  const hum = x => x == null ? '' : String(x).replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
  const plural = (n, word) => finite(n) ? n + ' ' + word + (n === 1 ? '' : 's') : word + 's not recorded';
  const fmt = s => {
    if (!finite(s)) return '—';
    const sign = s < 0 ? '-' : '', a = Math.abs(s), m = Math.floor(a / 60), x = a - m * 60;
    return sign + String(m).padStart(2, '0') + ':' + (x < 10 ? '0' : '') + x.toFixed(1);
  };
  const ago = s => finite(s) ? (s < 10 ? s.toFixed(1) : Math.round(s)) + ' s' : '—';
  const pnum = v => finite(v) ? v.toFixed(2) : 'not recorded';
  const armOf = id => id.startsWith('a:') ? 'a' : id.startsWith('b:') ? 'b' : null;
  const stageOf = (graph, arm, name) => (graph?.arms?.[arm]?.chain || []).find(s => s?.stage === name) || null;
  const sessionSeconds = (graph, epoch) => finite(epoch) && finite(graph?.session?.started_at) ? epoch - graph.session.started_at : NaN;

  // Contract revision 2 fields. A response without contract_revision comes from
  // a revision 1 controller: its missing fields are "not supplied", never "none".
  const rev2 = x => finite(x?.contract_revision) && x.contract_revision >= 2;
  const words = x => x == null ? '' : String(x).replace(/_/g, ' ');
  const SECTIONS = [['pose_geometry', 'pose'], ['wrist_object_relations', 'relations'], ['observations', 'observations'], ['context_facts', 'context'], ['historical_caption', 'caption']];
  function sectionText(sec) {
    if (!sec) return 'not supplied';
    if (sec.status === 'not_recorded' || !isObj(sec.snippet) && !finite(sec.items)) return 'not recorded';
    // Saved by the request with zero items: recorded as empty, not missing.
    if (sec.status === 'recorded' && sec.items === 0) return 'none recorded';
    return (finite(sec.items) ? sec.items : 'count not recorded') + (sec.items_truncated ? ' (more existed)' : '') + (sec.snippet?.truncated ? ' · snippet truncated' : '');
  }
  function exclusionsText(x, supplied = true) {
    if (x === undefined) return supplied ? 'not recorded' : 'not supplied by this controller';
    if (x === 'not_recorded') return 'not recorded by the ledger';
    if (isObj(x) && x.status === 'recorded') return 'recorded · ' + (x.basis ? words(x.basis) : 'basis not recorded') + (x.snippet?.truncated ? ' · snippet truncated' : '');
    if (typeof x === 'string') return words(x);
    return 'recorded in an unrecognised shape';
  }
  function coverageText(c) {
    if (c == null) return 'not recorded';
    const e = Object.entries(c), pick = v => e.filter(([, x]) => x === v).map(([k]) => words(k));
    const other = e.filter(([, x]) => x !== 'supplied' && x !== 'not_supplied').map(([k, x]) => words(k) + ' ' + words(x));
    return [pick('supplied').length ? 'supplied ' + pick('supplied').join(', ') : '', pick('not_supplied').length ? 'not supplied ' + pick('not_supplied').join(', ') : '', other.join(', ')].filter(Boolean).join(' · ') || 'no sources listed';
  }
  function freshnessText(f) {
    if (!f) return 'not supplied';
    if (f.basis === 'not_recorded' || !isObj(f.counts)) return 'not recorded';
    const c = Object.entries(f.counts).map(([k, n]) => words(k) + ' ' + n).join(' · ') || 'none counted';
    return c + (finite(f.latest_visual_age_seconds) ? ' · latest visual ' + f.latest_visual_age_seconds.toFixed(1) + ' s old' : '');
  }
  function lifecycleText(lc, supplied) {
    if (lc === undefined) return supplied ? 'none recorded' : 'not supplied by this controller';
    if (lc === null) return 'none recorded';
    if (lc.status === 'unknown') return 'unknown · older than scan bound';
    return 'newest pending request ' + (words(lc.state) || 'state not recorded') + (lc.replaced_by ? ' by ' + lc.replaced_by : '') + (lc.reason ? ' · ' + words(lc.reason) : '');
  }
  function frameAgeLines(frameAge, supplied) {
    if (frameAge === undefined) return supplied ? null : ['Frame age not supplied by this controller'];
    if (!isObj(frameAge)) return null;
    return ['a', 'b'].map(arm => {
      const x = frameAge.arms?.[arm], A = arm.toUpperCase();
      if (!x) return A + ': frame age not recorded';
      if (finite(x.behind_camera_s)) return A + ': newest analyzed frame is ' + x.behind_camera_s.toFixed(1) + ' s older than the newest camera frame (controller clock)';
      return A + ': not compared · ' + (words(x.comparison_reason || x.reason) || 'reason not recorded');
    });
  }
  // Graph reads pause while the graph is covered by the expanded dock: the
  // comparison shown is the last read's, and says so.
  function pausedAgeLines(lines, paused) {
    if (!paused || !lines) return lines;
    return [...lines, 'Graph reads paused while the graph is not visible · as of the last read'];
  }
  function variedFields(graph) {
    const a = graph?.arms?.a, b = graph?.arms?.b, out = [];
    if (a?.setup && b?.setup) for (const k of SETUP_FIELDS) {
      if (a.setup_basis?.[k] === 'not_recorded' && b.setup_basis?.[k] === 'not_recorded') continue;
      if (JSON.stringify(a.setup[k] ?? null) !== JSON.stringify(b.setup[k] ?? null)) out.push(k);
    }
    if (isObj(a?.profile) && isObj(b?.profile)) for (const k of ['profile_id', 'model_version', 'artifact_sha256'])
      if (a.profile[k] !== b.profile[k]) out.push('profile.' + k);
    return out;
  }
  function variedNodes(fields) {
    const set = new Set(), has = f => fields.includes(f);
    if (fields.some(f => SPECIALIST_FIELDS.has(f))) ['src:specialist_a', 'src:specialist_b', 'a:specialist_output', 'b:specialist_output'].forEach(x => set.add(x));
    if (has('ha_evidence')) set.add('src:ha_snapshot');
    if (has('caption_evidence')) set.add('src:caption_receipt');
    if (has('ambient_evidence')) set.add('src:ambient_context');
    if (has('qwen_scene_evidence')) set.add('src:qwen_scene');
    if (has('temporal_encoder_evidence')) set.add('src:vjepa_scene');
    if (has('localization') || has('room_map')) set.add('src:room_observation');
    if (has('history')) ['a:prepared_evidence', 'b:prepared_evidence'].forEach(x => set.add(x));
    if (has('reasoner')) ['a:consumed_request', 'b:consumed_request', 'a:result', 'b:result'].forEach(x => set.add(x));
    return set;
  }

  // One state per node, from contract fields only.
  function stateOf(node) {
    const p = node.raw, S = (label, tone, why) => ({label, tone, why: why || null});
    if (node.kind === 'inactive') return S(FUTURE[node.stage]?.[2] || 'Inactive', 'fut', hum(p?.reason));
    if (node.kind === 'proposed') return S(p.status === 'not_provisioned' ? 'Not provisioned' : p.status === 'excluded_by_policy' ? 'Excluded by policy' : 'Proposed', 'fut', p.reason);
    if (node.kind === 'ledger') {
      if (p.status === 'review_only') return S('Review only', 'fut', p.reason);
      if (p.status === 'failed') return p.failure?.kind === 'perception.expired' ? S('Expired', 'warn', p.failure.reason) : S('Unavailable', 'unav', (p.failure?.kind || '') + (p.failure?.reason ? ' · ' + p.failure.reason : ''));
      if (p.provisioned === false) return S('Unavailable', 'unav', hum(p.provisioned_basis));
      if (p.status === 'stale') return S('Stale', 'warn', 'valid_until passed at the cursor');
      if (p.status === 'producing') return S('Current', 'live');
      if (p.status === 'idle') return p.freshness === 'unknown' ? S('Static', 'static', 'no validity recorded') : S('No change', 'event', 'still valid · nothing new within ' + '10 s');
      if (p.status === 'no_recent_records') return S('Unknown', 'muted', 'older than scan bound');
      if (p.status === 'no_records') return S('No records', 'muted', p.reason);
      return S(hum(p.status) || 'Unknown', 'muted');
    }
    const st = p?.status;
    if (st === 'unknown' || st === 'unknown_older_than_scan_bound') return S('Unknown', 'muted', 'older than scan bound');
    switch (node.stage) {
      case 'specialist_output':
        if (st === 'fresh') return S('Current', 'live');
        if (st === 'stale') return p.stale_reason === 'expired' ? S('Expired', 'warn', 'valid_until passed') : S('Stale', 'warn', hum(p.stale_reason));
        if (p?.failure) return S('Unavailable', 'unav', p.failure.kind);
        return S('No output', 'muted');
      case 'relations':
        if (node.prep) {
          const pr = node.prep, sec = pr.sections?.wrist_object_relations;
          if (pr.status === 'not_saved') return S('Not saved', 'muted', hum(pr.reason));
          if (pr.status !== 'saved') return S(hum(pr.status) || 'Unknown', 'muted', hum(pr.reason));
          if (!sec || sec.status === 'not_recorded') return S('Not recorded', 'muted', 'the admitted request saved no wrist–object facts');
          return S('Current', 'live');
        }
      // falls through: revision 1 reads state.semantic from prepared evidence
      case 'prepared_evidence':
        if (st === 'saved') return node.stage === 'relations' && !p.semantic_snippet ? S('Not recorded', 'muted', 'no state.semantic in the admitted request') : S('Current', 'live');
        if (st === 'not_saved') return S('Not saved', 'muted', hum(p.reason));
        return S(hum(st) || 'Unknown', 'muted');
      case 'consumed_request':
        if (st === 'admitted') return S('Current', 'live');
        return S('No request', 'muted');
      case 'result': {
        if (p?.in_flight?.status === 'awaiting_result' && !p.output_event_id) return S('Request in flight', 'flight', 'admitted ' + ago(p.in_flight.age_s) + ' ago');
        if (st === 'ready') return p.freshness === 'stale' ? S('Stale', 'warn', 'valid_until passed at the cursor') : S('Current', 'live');
        if (st === 'abstain') return S('Abstained', 'event', 'provider abstained');
        if (st === 'stale') return S('Stale', 'warn');
        if (st === 'expired') return S('Expired', 'warn', p.failure_reason);
        if (st === 'missing_evidence') return S('Missing evidence', 'warn', p.failure_reason);
        if (st === 'failed' || st === 'indeterminate') return S(st === 'failed' ? 'Unavailable' : 'Indeterminate', 'unav', p.failure_reason);
        if (st === 'no_result_recorded') return S('No result recorded', 'muted');
        if (st === 'none') return S('No result', 'muted');
        return S(hum(st), 'muted');
      }
    }
    return S(hum(st) || 'Unknown', 'muted');
  }

  function distribution(result) {
    return (Array.isArray(result?.distribution) ? result.distribution : []).map(d => ({
      label: d?.label ?? d?.activity ?? d?.choice ?? d?.category ?? null,
      p: finite(d?.p) ? d.p : finite(d?.probability) ? d.probability : null
    })).filter(d => d.label != null);
  }
  const activityText = x => (Data().activityLabel ? Data().activityLabel(x) : hum(x)) || 'activity not recorded';

  function simpleOf(node, graph) {
    const p = node.raw, c = p?.counts || {};
    if (node.kind === 'inactive') {
      const pr = node.stage === 'policy' && !stageOf(graph, node.arm, 'result')?.predictions_hidden ? stageOf(graph, node.arm, 'result')?.policy_result : undefined;
      return {v: FUTURE[node.stage][2], sub: pr != null ? 'recorded policy_result · ' + (typeof pr === 'string' ? pr : JSON.stringify(pr)).slice(0, 80) : p?.note || hum(p?.reason)};
    }
    if (node.kind === 'proposed') return {v: stateOf(node).label, sub: p.reason || ''};
    if (node.kind === 'ledger') {
      const fail = p.failure ? hum(p.failure.kind) + (p.failure.reason ? ' · ' + p.failure.reason : '') : '';
      switch (p.id) {
        case 'rgb_camera': return {v: finite(p.last_available_at) ? 'Last image ' + ago(p.age_s) + ' before cursor' : 'No images recorded', sub: plural(c.images, 'image') + ' · ' + plural(c.video_objects, 'video object')};
        case 'depth_bundle': return {v: finite(p.last_available_at) ? 'Last bundle ' + ago(p.age_s) + ' before cursor' : 'No bundles recorded', sub: plural(c.bundles, 'bundle') + (p.native?.content_type ? ' · ' + p.native.content_type : '')};
        case 'specialist_a': case 'specialist_b': {
          const s = stageOf(graph, p.id.slice(-1), 'specialist_output'), k = s?.counts || {};
          if (!s || s.status === 'none') return {v: 'No output recorded', sub: fail || (p.reason ? hum(p.reason) : 'nothing by the cursor')};
          const parts = [['objects', 'object'], ['pose_instances', 'pose'], ['masks', 'mask'], ['tracks', 'track']].filter(([f]) => finite(k[f])).map(([f, w]) => plural(k[f], w));
          return {v: parts.join(' · ') || 'Output without counted arrays', sub: 'frame ' + (s.frame_id ?? '—') + ' · ' + (s.model_version || p.version?.model_version || 'model not recorded')};
        }
        case 'shared_detector': return {v: finite(c.crops) ? plural(c.crops, 'crop') : 'No crop bundle', sub: fail || (p.version ? 'reference ' + (typeof p.version === 'string' ? p.version : JSON.stringify(p.version)).slice(0, 60) : 'shared by both pose arms when recorded')};
        case 'ha_snapshot': return {v: finite(c.entities) ? c.known + ' of ' + plural(c.entities, 'entity').replace('entitys', 'entities') + ' known' : p.provisioned === false ? 'HA source invalid' : 'No snapshot recorded',
          sub: fail || (finite(c.camera_derived) && c.camera_derived ? c.camera_derived + ' camera-derived · stays correlated' : 'freshness ' + p.freshness)};
        case 'caption_receipt': return {v: p.version?.model ? 'Receipt · ' + p.version.model : 'No receipt recorded', sub: 'caption prose is never copied' + (finite(p.valid_until) && finite(graph?.cursor?.cutoff) ? ' · ' + (p.valid_until >= graph.cursor.cutoff ? 'valid ' + ago(p.valid_until - graph.cursor.cutoff) + ' more' : 'expired') : '')};
        case 'qwen_scene': {
          // Coverage per arm and the last projection's tokens; never digests or reply text.
          const cov = p.version?.coverage || {}, last = p.native?.snippet?.json?.last_projection, latest = isObj(last?.latest) ? last.latest : null;
          const act = latest?.facts?.activity_hypothesis;
          return {v: last ? 'Scene · ' + words(last.status) + (act ? ' · ' + words(act) : '') : finite(p.last_available_at) ? 'Receipt ' + ago(p.age_s) + ' before cursor' : 'No scene recorded',
            sub: 'A ' + words(cov.a || 'not recorded') + ' · B ' + words(cov.b || 'not recorded') + ' · same camera, unvalidated'};
        }
        case 'vjepa_scene': {
          // Coverage per arm and the last projection's bucket words; never probabilities, boxes or digests.
          const cov = p.version?.coverage || {}, last = p.native?.snippet?.json?.last_projection, latest = isObj(last?.latest) ? last.latest : null;
          const top = latest?.facts?.posture?.top;
          return {v: last ? 'Clip buckets · ' + words(last.status) + (top ? ' · ' + words(top) : '') : finite(p.last_available_at) ? 'Receipt ' + ago(p.age_s) + ' before cursor' : 'No clip recorded',
            sub: 'A ' + words(cov.a || 'not recorded') + ' · B ' + words(cov.b || 'not recorded') + ' · same camera, uncalibrated'};
        }
        case 'room_observation': return {v: p.version?.map_version ? 'Map ' + p.version.map_version : 'No room observation', sub: p.version?.coordinate_frame ? 'frame ' + p.version.coordinate_frame : p.native?.kind || 'validated stereo-surface path only'};
        case 'processing_health': {
          const keys = isObj(p.native?.snippet?.json) ? Object.keys(p.native.snippet.json).filter(k => !k.startsWith('__')) : [];
          return {v: keys.length ? 'Delivery receipts · ' + keys.map(k => k.toUpperCase()).join(' + ') : 'No delivery receipts', sub: 'latest perception.delivery per arm'};
        }
        case 'human_labels': return {v: finite(c.labels) ? plural(c.labels, 'label') + ' · ' + (c.reviewed ?? 0) + ' reviewed' : 'Labels not counted', sub: 'review only · never a model input'};
      }
      return {v: stateOf(node).label, sub: fail || hum(p.reason)};
    }
    switch (node.stage) {
      case 'specialist_output':
        if (!p || p.status === 'none') return {v: 'No output', sub: p?.failure ? hum(p.failure.kind) : p?.reason ? hum(p.reason) : 'nothing by the cursor'};
        return {v: 'Frame ' + (p.frame_id ?? '—'), sub: hum(p.status) + (p.stale_reason ? ' · ' + hum(p.stale_reason) : '') + ' · ' +
          (p.consumed_by_latest_request === true ? 'in latest request' : p.consumed_by_latest_request === false ? 'not in latest request' : 'no request recorded')};
      case 'relations': {
        if (node.prep) {
          const pr = node.prep, sec = pr.sections?.wrist_object_relations;
          if (pr.status !== 'saved') return {v: stateOf(node).label, sub: hum(pr.reason) || ''};
          if (!sec || sec.status === 'not_recorded') return {v: 'Not recorded', sub: 'no wrist–object facts in the admitted request'};
          return {v: plural(sec.items, 'relation fact') + (sec.items_truncated ? '+' : ''), sub: 'image-plane nearness, never contact' + (sec.items_truncated ? ' · more existed' : '') + (sec.snippet?.truncated ? ' · snippet truncated' : '')};
        }
        const j = p?.semantic_snippet?.json;
        if (p?.status !== 'saved') return {v: stateOf(node).label, sub: hum(p?.reason) || 'image-plane facts live in state.semantic'};
        if (!p.semantic_snippet) return {v: 'Not recorded', sub: 'admitted request has no state.semantic'};
        return {v: 'Semantic ' + (p.semantic_version || 'version not recorded'), sub: (isObj(j) ? Object.keys(j).length + ' fields' : 'bounded snippet') + ' · ' + p.semantic_snippet.bytes + ' B' + (p.semantic_snippet.truncated ? ' · truncated' : '')};
      }
      case 'prepared_evidence': {
        if (p?.status !== 'saved') return {v: stateOf(node).label, sub: hum(p?.reason) || p?.note || ''};
        const by = Object.entries(p.by_source || {}).map(([k, n]) => k + ' ' + n).join(' · ');
        const cut = sessionSeconds(graph, p.observation_cutoff);
        return {v: plural(p.records, 'record'), sub: 'cutoff ' + (finite(cut) ? fmt(cut) : 'not recorded'),
          sx: [{k: 'by source', v: by || 'none'}, {k: 'exclusions', v: exclusionsText(p.exclusions), warn: !isObj(p.exclusions)},
            {k: 'evidence', v: (p.evidence?.length ?? 0) + ' listed' + (p.evidence_truncated ? ' · truncated at 64' : '')}].concat(node.prep ? [
            {k: 'coverage', v: coverageText(node.prep.source_coverage), muted: node.prep.source_coverage == null},
            {k: 'freshness', v: freshnessText(node.prep.freshness), muted: !isObj(node.prep.freshness?.counts)},
            {k: 'sections', v: SECTIONS.map(([key, short]) => short + ' ' + sectionText(node.prep.sections?.[key])).join(' · '), warn: SECTIONS.some(([key]) => node.prep.sections?.[key]?.items_truncated || node.prep.sections?.[key]?.snippet?.truncated)}] :
            [{k: 'preparation', v: rev2(graph) ? 'not recorded' : 'not supplied by this controller', muted: true}])};
      }
      case 'consumed_request':
        if (p?.status !== 'admitted') return {v: stateOf(node).label, sub: hum(p?.reason) || 'no admitted request by the cursor'};
        return {v: 'Admitted · ' + (p.bytes ?? '—') + ' B', sub: (p.model || 'model not recorded') + ' · ' + plural(p.question_ids?.length, 'question')};
      case 'result': {
        const life = {k: 'lifecycle', v: lifecycleText(p?.latest_lifecycle, rev2(graph)), muted: !(p?.latest_lifecycle?.status === 'recorded'), warn: p?.latest_lifecycle?.status === 'recorded' && p.latest_lifecycle.state !== 'replaced'};
        if (p?.predictions_hidden) return {v: 'Prediction hidden', sub: hum(p.status) + ' · show predictions to compare', sx: [life]};
        const dist = distribution(p).sort((x, y) => (y.p ?? -1) - (x.p ?? -1));
        const flight = p?.in_flight?.status === 'awaiting_result';
        if (p?.output_event_id != null || p?.activity != null || dist.length) {
          const v = p.abstained ? 'Abstained' : activityText(p.activity) + (finite(p.probability) ? ' ' + p.probability.toFixed(2) : '');
          return {v, sub: flight ? 'next request in flight' : finite(p.probability) ? 'returned probability' : 'probability not recorded',
            sx: dist.slice(0, 3).map(d => ({k: activityText(d.label), v: d.p == null ? 'not recorded' : d.p.toFixed(2), w: d.p == null ? null : Math.round(d.p * 100)}))
              .concat([{k: 'confidence', v: finite(p.confidence) ? p.confidence.toFixed(2) + ' · provider-reported, not a probability' : 'not recorded', muted: true}, life])};
        }
        return {v: stateOf(node).label, sub: p?.failure_reason || (p?.in_flight ? 'request ' + p.in_flight.request_id + ' · ' + hum(p.in_flight.status) : p?.latest_lifecycle?.status === 'recorded' ? lifecycleText(p.latest_lifecycle, true) : ''), sx: [life]};
      }
    }
    return {v: stateOf(node).label, sub: ''};
  }

  // Hide predictions: a copy of the graph without any returned judgment fields.
  const PREDICTION_FIELDS = ['activity', 'probability', 'confidence', 'distribution', 'answers', 'policy_result', 'provider_status', 'abstained', 'usage'];
  function maskPredictions(graph) {
    if (!graph?.arms) return graph;
    const arms = {};
    for (const [k, arm] of Object.entries(graph.arms)) arms[k] = arm && {...arm, chain: (arm.chain || []).map(st => st?.stage === 'result' ?
      {...Object.fromEntries(Object.entries(st).filter(([f]) => !PREDICTION_FIELDS.includes(f))), predictions_hidden: true} : st)};
    return {...graph, arms};
  }
  // Exact contract objects behind a node, keyed by their location in the response.
  function payloadOf(node) { return node.payload; }

  function buildModel(graph) {
    if (!graph) return null;
    const nodes = [], edges = [];
    const fields = variedFields(graph), varied = variedNodes(fields);
    const add = n => { n.varied = varied.has(n.id); nodes.push(n); return n; };
    (graph.sources || []).forEach((s, i) => {
      if (!isObj(s) || typeof s.id !== 'string') return;
      const proposed = s.kind === 'proposed';
      const lane = s.id === 'specialist_a' ? 'A' : s.id === 'specialist_b' ? 'B' : 'S';
      const group = GROUPS.some(g => g[0] === s.group) ? s.group : 'other';
      add({id: 'src:' + s.id, sid: s.id, col: s.id === 'human_labels' ? 'fb' : 'src', lane, group, kind: proposed ? 'proposed' : 'ledger', title: s.title || s.id, raw: s,
        ident: (proposed ? 'family ' + (s.families || []).map(n => String(n).padStart(2, '0')).join(', ') : s.id + (s.families?.length ? ' · families ' + s.families.join(', ') : '')) + ' · ' + (s.catalog_status || '?'),
        tag: proposed ? (s.status === 'not_provisioned' ? 'NOT PROVISIONED' : s.status === 'excluded_by_policy' ? 'EXCLUDED' : 'PROPOSED') : s.implemented === false ? 'NOT IN BUILD' : 'RECORDED',
        payload: {['graph.sources[' + i + ']']: s}, snippet: s.native?.snippet || null, rid: s.native?.event_id != null ? 'event ' + s.native.event_id : s.native?.media_id ? 'media ' + s.native.media_id : '—',
        ver: isObj(s.version) ? Object.entries(s.version).filter(([, v]) => v != null && typeof v !== 'object').map(([k, v]) => k + ' ' + v).join(' · ').slice(0, 80) : ''});
    });
    for (const arm of ['a', 'b']) {
      const A = arm.toUpperCase(), chain = graph.arms?.[arm]?.chain || [], at = name => chain.findIndex(s => s?.stage === name);
      const stage = (name, col, title, extra = {}) => {
        const i = at(name), raw = i >= 0 ? chain[i] : null;
        return add({id: arm + ':' + name, stage: name, arm, col, lane: A, kind: FUTURE[name] ? 'inactive' : 'stage', title, raw: raw || {stage: name, status: 'not_in_response'},
          ident: 'arms.' + arm + '.chain[' + name + ']', tag: FUTURE[name] ? 'INACTIVE' : 'RECORDED',
          payload: {['graph.arms.' + arm + '.chain[' + (i >= 0 ? i : name) + ']']: raw}, snippet: null,
          rid: raw?.request_id ? 'request ' + raw.request_id : raw?.event_id != null ? 'event ' + raw.event_id : '—', ver: '', ...extra});
      };
      const spec = stage('specialist_output', 'p1', 'Specialist output · arm ' + A);
      spec.ver = spec.raw?.model_version || '';
      const prep = chain[at('prepared_evidence')] || null;
      // Revision 2: the saved preparation block; revision 1 falls back to state.semantic.
      const preparation = isObj(graph.arms?.[arm]?.preparation) ? graph.arms[arm].preparation : undefined;
      const relSection = preparation?.sections?.wrist_object_relations;
      add({id: arm + ':relations', stage: 'relations', arm, col: 'p2', lane: A, kind: 'stage', title: (preparation ? 'Relations · wrist–object facts · arm ' : 'Relations · state.semantic · arm ') + A,
        raw: prep || {stage: 'prepared_evidence', status: 'not_in_response'}, prep: preparation, ident: preparation ? 'preparation.sections.wrist_object_relations' : 'prepared_evidence.semantic_snippet', tag: 'RECORDED',
        payload: preparation ? {['graph.arms.' + arm + '.preparation.sections.wrist_object_relations']: relSection ?? null} :
          {['graph.arms.' + arm + '.chain[' + (at('prepared_evidence') >= 0 ? at('prepared_evidence') : 'prepared_evidence') + '].semantic_snippet']: prep ? prep.semantic_snippet ?? null : null},
        snippet: preparation ? relSection?.snippet || null : prep?.semantic_snippet || null, rid: (preparation?.request_id || prep?.request_id) ? 'request ' + (preparation?.request_id || prep.request_id) : '—',
        ver: preparation?.semantic_version || prep?.semantic_version || ''});
      const ev = stage('prepared_evidence', 'ev', 'Evidence assembly · arm ' + A);
      ev.snippet = ev.raw?.snippet || null; ev.ver = ev.raw?.semantic_version || '';
      if (preparation) { ev.prep = preparation; ev.payload = {...ev.payload, ['graph.arms.' + arm + '.preparation']: preparation}; }
      const req = stage('consumed_request', 'jev', 'JEV request · arm ' + A);
      req.snippet = req.raw?.snippet || null; req.ver = [req.raw?.question_version, req.raw?.projection_version].filter(Boolean).join(' · ') || (req.raw?.versions_basis ? hum(req.raw.versions_basis) : '');
      const res = stage('result', 'jev', 'JEV result · arm ' + A);
      res.snippet = res.raw?.answers || null; res.ver = res.raw?.model_version || '';
      PER_ARM_FUTURE.forEach(name => stage(name, FUTURE[name][0], FUTURE[name][1] + ' · arm ' + A));
      // Chain edges: native → prepared → request → result → inactive stages.
      edges.push({f: 'src:specialist_' + arm, t: arm + ':specialist_output', lane: A, kind: 'chain'});
      edges.push({f: arm + ':specialist_output', t: arm + ':relations', lane: A, kind: prep?.status === 'saved' ? 'chain' : 'idle'});
      edges.push({f: arm + ':relations', t: arm + ':prepared_evidence', lane: A, kind: (preparation ? relSection && relSection.status !== 'not_recorded' : prep?.semantic_snippet) ? 'chain' : 'idle'});
      edges.push({f: arm + ':specialist_output', t: arm + ':prepared_evidence', lane: A, kind: spec.raw?.consumed_by_latest_request === true ? 'consumed' : spec.raw?.consumed_by_latest_request === false ? 'excluded' : 'idle',
        label: spec.raw?.consumed_by_latest_request === false ? 'newer output · not in request' : null});
      edges.push({f: arm + ':prepared_evidence', t: arm + ':consumed_request', lane: A, kind: req.raw?.status === 'admitted' ? 'chain' : 'idle'});
      edges.push({f: arm + ':consumed_request', t: arm + ':result', lane: A, kind: res.raw?.status && !['none', 'unknown'].includes(res.raw.status) ? 'chain' : 'idle'});
      edges.push({f: arm + ':result', t: arm + ':contextual_beliefs', lane: A, kind: 'fut'});
      edges.push({f: arm + ':result', t: arm + ':policy', lane: A, kind: 'fut'});
      edges.push({f: arm + ':contextual_beliefs', t: arm + ':policy', lane: A, kind: 'fut'});
      edges.push({f: arm + ':policy', t: arm + ':proposed_action', lane: A, kind: 'fut'});
      edges.push({f: arm + ':proposed_action', t: 's:authorization', lane: A, kind: 'fut'});
    }
    SHARED_FUTURE.forEach(name => {
      const pa = stageOf(graph, 'a', name), pb = stageOf(graph, 'b', name);
      add({id: 's:' + name, stage: name, arm: null, col: FUTURE[name][0], lane: 'S', kind: 'inactive', title: FUTURE[name][1], raw: pa || pb || {stage: name, status: 'not_in_response'},
        ident: 'arms.a/b.chain[' + name + ']', tag: 'INACTIVE', payload: {['graph.arms.a.chain[' + name + ']']: pa, ['graph.arms.b.chain[' + name + ']']: pb}, snippet: null, rid: 'no live events', ver: ''});
    });
    edges.push({f: 's:authorization', t: 's:execution', lane: 'S', kind: 'fut'}, {f: 's:execution', t: 's:verification', lane: 'S', kind: 'fut'}, {f: 's:verification', t: 's:human_review', lane: 'S', kind: 'fut'});
    // Source routing: drawn once, to each arm by consumed_by (never by capability).
    for (const n of nodes) {
      if (n.kind === 'proposed' || !n.sid) continue;
      const s = n.raw;
      if (n.sid === 'rgb_camera') ['a', 'b'].forEach(arm => { if (s.declared_by_setup?.[arm] !== false) edges.push({f: n.id, t: arm + ':specialist_output', lane: 'S', kind: 'declared', arm}); });
      if (n.sid === 'human_labels') { edges.push({f: n.id, t: 's:human_review', lane: 'S', kind: 'review'}); continue; }
      if (n.sid === 'specialist_a' || n.sid === 'specialist_b') continue;
      for (const arm of ['a', 'b']) {
        const v = s.consumed_by?.[arm];
        if (!v || ['not_a_jev_input', 'other_arm', 'not_implemented', 'excluded_by_policy'].includes(v)) continue;
        edges.push({f: n.id, t: arm + ':prepared_evidence', lane: 'S', arm, kind: v === 'consumed' ? 'consumed' : v === 'withheld_by_setup' || v === 'not_in_request' ? 'excluded' : 'idle',
          label: v === 'consumed' ? null : CONSUMED_WORD[v] || hum(v)});
      }
    }
    const byId = Object.fromEntries(nodes.map(n => [n.id, n]));
    nodes.forEach(n => { n.state = stateOf(n); n.simple = simpleOf(n, graph); });
    const focus = {
      all: null,
      shared: nodes.filter(n => n.lane === 'S' && n.col === 'src').map(n => n.id),
      A: nodes.filter(n => n.lane !== 'B').map(n => n.id),
      B: nodes.filter(n => n.lane !== 'A').map(n => n.id),
      decision: nodes.filter(n => ['bel', 'pol', 'act', 'gate', 'exec', 'ver', 'fb'].includes(n.col)).map(n => n.id)
    };
    return {graph, nodes, byId, edges: edges.filter(e => byId[e.f] && byId[e.t]), varied: fields, focus, revision: finite(graph.contract_revision) ? graph.contract_revision : null};
  }

  // What Focus frames: an arm's own chain (the shared sources stay visible but dimmed less).
  function focusFit(model, id) {
    if (id === 'A' || id === 'B') { const a = id.toLowerCase(); return model.nodes.filter(n => n.arm === a || n.id === 'src:specialist_' + a).map(n => n.id); }
    return model.focus[id];
  }
  // Trace: the exact records a JEV request consumed, per contract refs.
  function traceOf(model, arm) {
    if (!model || !arm) return null;
    const prep = stageOf(model.graph, arm, 'prepared_evidence'), req = stageOf(model.graph, arm, 'consumed_request');
    if (req?.status !== 'admitted' && prep?.status !== 'saved') return {arm, available: false, reason: 'No admitted request by the cursor; nothing was consumed.'};
    const ids = new Set([arm + ':specialist_output', arm + ':relations', arm + ':prepared_evidence', arm + ':consumed_request', arm + ':result']);
    const records = {};
    for (const n of model.nodes) if (n.kind === 'ledger' && n.raw.consumed_by?.[arm] === 'consumed') ids.add(n.id);
    if (model.byId['src:specialist_' + arm]?.raw?.consumed_by?.[arm] === 'consumed') ids.add('src:specialist_' + arm);
    const evidence = Array.isArray(prep?.evidence) ? prep.evidence : [];
    for (const item of evidence) {
      const target = EVIDENCE_KIND_NODE[item?.source_kind || 'perception.output']?.(arm);
      if (!target) continue;
      ids.add(target);
      (records[target] ||= []).push(item.id);
    }
    // Is each source's current record the one consumed?
    const recordNote = {};
    for (const [id, list] of Object.entries(records)) {
      const current = model.byId[id]?.raw?.native?.event_id;
      recordNote[id] = current != null && list.map(String).includes(String(current)) ? 'consumed record #' + current : 'consumed #' + list.slice(-3).join(', #') + (current != null ? ' · shown #' + current + ' is newer' : '');
    }
    return {arm, available: true, ids, records, recordNote, request_id: req?.request_id || prep?.request_id, count: prep?.records ?? evidence.length,
      by_source: prep?.by_source || {}, cutoff: sessionSeconds(model.graph, prep?.observation_cutoff), evidence_truncated: !!prep?.evidence_truncated};
  }

  function flatten(value, path, out, depth = 0) {
    if (isObj(value) && depth < 6) { for (const k of Object.keys(value)) flatten(value[k], path ? path + '.' + k : k, out, depth + 1); }
    else out[path] = JSON.stringify(value);
    return out;
  }
  // A/B diff over what the contract provides: prepared evidence and returned probabilities.
  function diffOf(model) {
    if (!model) return null;
    const g = model.graph, pa = stageOf(g, 'a', 'prepared_evidence'), pb = stageOf(g, 'b', 'prepared_evidence');
    const ra = stageOf(g, 'a', 'result'), rb = stageOf(g, 'b', 'result');
    const input = [], truncated = [];
    if (pa?.status === 'saved' && pb?.status === 'saved') {
      const cmp = (path, a, b) => { if (JSON.stringify(a ?? null) !== JSON.stringify(b ?? null)) input.push({path, a: JSON.stringify(a ?? null), b: JSON.stringify(b ?? null)}); };
      cmp('records', pa.records, pb.records);
      const kinds = new Set([...Object.keys(pa.by_source || {}), ...Object.keys(pb.by_source || {})]);
      kinds.forEach(k => cmp('by_source.' + k, pa.by_source?.[k] ?? 0, pb.by_source?.[k] ?? 0));
      cmp('semantic_version', pa.semantic_version, pb.semantic_version);
      const qa = g.arms?.a?.preparation, qb = g.arms?.b?.preparation;
      if (isObj(qa) && isObj(qb)) {
        // Revision 2: coverage, freshness counts and the saved preparation sections.
        const keys = new Set([...Object.keys(qa.source_coverage || {}), ...Object.keys(qb.source_coverage || {})]);
        if (qa.source_coverage == null || qb.source_coverage == null) cmp('source_coverage', qa.source_coverage ?? 'not recorded', qb.source_coverage ?? 'not recorded');
        else keys.forEach(k => cmp('source_coverage.' + k, qa.source_coverage[k] ?? 'absent', qb.source_coverage[k] ?? 'absent'));
        cmp('freshness.counts', isObj(qa.freshness?.counts) ? qa.freshness.counts : 'not recorded', isObj(qb.freshness?.counts) ? qb.freshness.counts : 'not recorded');
        for (const [key] of SECTIONS) {
          const sa = qa.sections?.[key], sb = qb.sections?.[key];
          cmp('sections.' + key + '.items', sectionText(sa), sectionText(sb));
          if (sa?.snippet || sb?.snippet) {
            if (sa?.snippet?.truncated || sb?.snippet?.truncated || sa?.items_truncated || sb?.items_truncated) truncated.push(key);
            const fa = flatten(sa?.snippet?.json ?? null, 'sections.' + key, {}), fb = flatten(sb?.snippet?.json ?? null, 'sections.' + key, {});
            new Set([...Object.keys(fa), ...Object.keys(fb)]).forEach(k => { if (fa[k] !== fb[k]) input.push({path: k, a: fa[k] ?? 'absent', b: fb[k] ?? 'absent'}); });
          }
        }
        cmp('exclusions', exclusionsText(qa.exclusions), exclusionsText(qb.exclusions));
      } else {
        const fa = flatten(pa.semantic_snippet?.json ?? null, 'state.semantic', {}), fb = flatten(pb.semantic_snippet?.json ?? null, 'state.semantic', {});
        new Set([...Object.keys(fa), ...Object.keys(fb)]).forEach(k => { if (fa[k] !== fb[k]) input.push({path: k, a: fa[k] ?? 'absent', b: fb[k] ?? 'absent'}); });
      }
    }
    const da = distribution(ra), db = distribution(rb), labels = [...new Set([...da.map(d => d.label), ...db.map(d => d.label)])];
    const output = labels.map(label => {
      const a = da.find(d => d.label === label)?.p, b = db.find(d => d.label === label)?.p;
      return {label, a: finite(a) ? a : null, b: finite(b) ? b : null, d: finite(a) && finite(b) ? b - a : null};
    });
    const judged = r => r && (r.activity != null || r.abstained);
    const summary = judged(ra) && judged(rb) ? (ra.activity === rb.activity && !ra.abstained && !rb.abstained ?
      'Both arms returned ' + activityText(ra.activity) + ' · A ' + pnum(ra.probability) + ' · B ' + pnum(rb.probability) :
      'Arms differ · A ' + (ra.abstained ? 'abstained' : activityText(ra.activity) + ' ' + pnum(ra.probability)) + ' · B ' + (rb.abstained ? 'abstained' : activityText(rb.activity) + ' ' + pnum(rb.probability))) :
      'Waiting for both arms to record a result.';
    return {summary, inputComparable: pa?.status === 'saved' && pb?.status === 'saved', inputReason: pa?.status !== 'saved' || pb?.status !== 'saved' ? 'Prepared evidence is ' + hum(pa?.status || 'absent') + ' (A) / ' + hum(pb?.status || 'absent') + ' (B).' : null,
      input, truncated, preparation: isObj(g.arms?.a?.preparation) && isObj(g.arms?.b?.preparation) ? 'revision_2' : rev2(g) ? 'not_recorded' : 'not_supplied', output, confidence: {a: finite(ra?.confidence) ? ra.confidence : null, b: finite(rb?.confidence) ? rb.confidence : null}, requests: {a: pa?.request_id, b: pb?.request_id}};
  }

  // ------------------------------------------------------------------ layout
  const simpleH = n => n.col === 'src' || n.col === 'fb' && n.sid ? (n.kind === 'proposed' ? 72 : 88) : n.stage === 'prepared_evidence' ? 232 : n.stage === 'result' ? 206 : 96;
  const detailedH = n => n.col === 'src' || n.col === 'fb' && n.sid ? (n.kind === 'proposed' ? 120 : 260) : n.stage === 'prepared_evidence' ? 420 : n.stage === 'result' ? 360 :
    n.stage === 'consumed_request' ? 250 : n.stage === 'specialist_output' ? 280 : n.stage === 'relations' ? 240 : 176;
  function layout(model, mode) {
    const G = GEOM[mode], P = {}, gbox = [], simple = mode === 'simple', H = simple ? simpleH : detailedH;
    if (!model) return {P, gbox, bands: [], WW: G.fb[0] + G.fb[1] + 160, WH: 800, cols: G};
    let y = 110;
    for (const [g, label] of GROUPS) {
      const list = model.nodes.filter(n => n.col === 'src' && n.group === g);
      if (!list.length) continue;
      const gy = y;
      y += simple ? 38 : 34;
      list.forEach(n => { P[n.id] = {x: G.src[0], w: G.src[1], y, h: H(n)}; y += P[n.id].h + (simple ? 10 : 12); });
      gbox.push({x: G.src[0] - 14, y: gy, w: G.src[1] + 28, h: y - gy + 2, label: label + ' · ' + list.length});
      y += simple ? 30 : 40;
    }
    const top = {a: 150}, bands = [];
    for (const arm of ['a', 'b']) {
      const y0 = arm === 'a' ? top.a : top.b, place = (id, col, yy) => { const n = model.byId[id]; if (n) P[id] = {x: G[col][0], w: G[col][1], y: yy, h: H(n)}; };
      place(arm + ':specialist_output', 'p1', y0);
      place(arm + ':relations', 'p2', y0);
      place(arm + ':prepared_evidence', 'ev', y0);
      place(arm + ':consumed_request', 'jev', y0);
      place(arm + ':result', 'jev', y0 + (P[arm + ':consumed_request']?.h || 0) + 24);
      PER_ARM_FUTURE.forEach(name => place(arm + ':' + name, FUTURE[name][0], y0));
      const ids = Object.keys(P).filter(id => id.startsWith(arm + ':'));
      const bottom = Math.max(y0 + 100, ...ids.map(id => P[id].y + P[id].h));
      const x0 = G.p1[0] - 28, x1 = G.act[0] + G.act[1] + 28;
      bands.push({arm, x: x0, y: y0 - 44, w: x1 - x0, h: bottom - y0 + 72});
      if (arm === 'a') top.b = bottom + 150;
    }
    const mid = (bands[0].y + bands[1].y + bands[1].h) / 2 - 60;
    SHARED_FUTURE.forEach(name => { const n = model.byId['s:' + name]; if (n) P[n.id] = {x: G[FUTURE[name][0]][0], w: G[FUTURE[name][0]][1], y: mid, h: H(n)}; });
    const labels = model.byId['src:human_labels'];
    if (labels && P['s:human_review']) P[labels.id] = {x: G.fb[0], w: G.fb[1], y: P['s:human_review'].y + P['s:human_review'].h + (simple ? 110 : 90), h: H(labels)};
    const all = Object.values(P);
    return {P, gbox, bands, WW: G.fb[0] + G.fb[1] + 160, WH: Math.max(...all.map(p => p.y + p.h), y) + 140, cols: G};
  }
  function edgePaths(model, L) {
    if (!model) return [];
    const inc = {};
    const list = model.edges.filter(e => L.P[e.f] && L.P[e.t]).map(e => ({...e}));
    list.forEach(e => (inc[e.t] ||= []).push(e));
    Object.values(inc).forEach(arr => {
      arr.sort((a, b) => (L.P[a.f].y + L.P[a.f].h / 2) - (L.P[b.f].y + L.P[b.f].h / 2));
      const t = L.P[arr[0].t], n = arr.length, span = Math.min(t.h - 32, Math.max(0, (n - 1) * 16)), start = t.y + Math.min(40, t.h / 3);
      arr.forEach((e, i) => { e.y2 = start + (n > 1 ? i * span / (n - 1) : 0); });
    });
    return list.map(e => {
      const s = L.P[e.f], t = L.P[e.t];
      if (Math.abs(s.x - t.x) < 4) {
        const up = t.y < s.y, x = s.x + s.w / 2, y1 = up ? s.y : s.y + s.h, y2 = up ? t.y + t.h : t.y;
        return {...e, d: 'M' + x + ',' + y1 + ' L' + x + ',' + y2, x2: x, y2};
      }
      const x1 = s.x + s.w, y1 = s.y + Math.min(s.h / 2, 60), x2 = t.x, y2 = e.y2, dx = Math.max(60, (x2 - x1) * .5);
      return {...e, d: 'M' + x1 + ',' + y1 + ' C' + (x1 + dx) + ',' + y1 + ' ' + (x2 - dx) + ',' + y2 + ' ' + x2 + ',' + y2, x1, y1, x2, y2};
    });
  }

  // ------------------------------------------------------------------ JSON lines
  const TRUNC = /__truncated__/;
  function tok(s, c) { return {s, c}; }
  function prim(v) {
    if (v === null || v === undefined) return tok('null', 'nul');
    if (typeof v === 'boolean') return tok(String(v), 'bool');
    if (typeof v === 'number') return tok(String(v), 'num');
    const s = JSON.stringify(String(v));
    if (TRUNC.test(v)) return tok(s, 'trunc');
    if (v === '[redacted]') return tok(s, 'red');
    if (['stale', 'expired', 'failed', 'not_saved', 'missing_evidence', 'indeterminate', 'not_in_request', 'withheld_by_setup', 'not_recorded'].includes(v)) return tok(s, 'warn');
    if (['inactive', 'proposed', 'not_provisioned', 'actuation_deferred', 'not_implemented', 'unknown', 'none'].includes(v)) return tok(s, 'mut');
    return tok(s, 'str');
  }
  function inline(v, budget) {
    if (!v || typeof v !== 'object') return [prim(v)];
    const out = [];
    let len = 0;
    const push = t => { out.push(t); len += t.s.length; return len <= budget; };
    if (Array.isArray(v)) {
      if (!push(tok('[', 'p'))) return null;
      for (let i = 0; i < v.length; i++) { const t = inline(v[i], budget - len); if (!t) return null; for (const x of t) if (!push(x)) return null; if (i < v.length - 1 && !push(tok(', ', 'p'))) return null; }
      return push(tok(']', 'p')) ? out : null;
    }
    const ks = Object.keys(v);
    if (!push(tok('{', 'p'))) return null;
    for (let i = 0; i < ks.length; i++) {
      if (!push(tok(JSON.stringify(ks[i]), ks[i] === '__truncated__' ? 'trunc' : 'key')) || !push(tok(': ', 'p'))) return null;
      const t = inline(v[ks[i]], budget - len); if (!t) return null; for (const x of t) if (!push(x)) return null;
      if (i < ks.length - 1 && !push(tok(', ', 'p'))) return null;
    }
    return push(tok('}', 'p')) ? out : null;
  }
  function jsonLines(root, {max = 52, lim = 3, exp = {}} = {}) {
    const out = [];
    function ser(v, ind, pre, comma, path, depth) {
      const push = (toks, tog, open) => out.push({toks, tog, open, path});
      const sp = tok('  '.repeat(ind), 'p'), cm = comma ? [tok(',', 'p')] : [];
      if (v === null || v === undefined || typeof v !== 'object') { push([sp, ...pre, prim(v), ...cm]); return; }
      if (depth > 0) { const t = inline(v, max - ind * 2 - pre.reduce((a, x) => a + x.s.length, 0) - 1); if (t) { push([sp, ...pre, ...t, ...cm]); return; } }
      if (Array.isArray(v)) {
        if (!v.length) { push([sp, ...pre, tok('[]', 'p'), ...cm]); return; }
        const open = !!exp[path], cut = !open && v.length > lim + 1, n = cut ? lim : v.length;
        push([sp, ...pre, tok('[', 'p'), tok('  // ' + v.length + ' items', 'cmt')]);
        for (let i = 0; i < n; i++) ser(v[i], ind + 1, [], i < v.length - 1, path + '[' + i + ']', depth + 1);
        if (cut) push([tok('  '.repeat(ind + 1), 'p'), tok('▸ ' + (v.length - n) + ' more', 'tog')], path, false);
        else if (open) push([tok('  '.repeat(ind + 1), 'p'), tok('▾ collapse', 'tog')], path, true);
        push([sp, tok(']', 'p'), ...cm]);
        return;
      }
      const ks = Object.keys(v);
      if (depth === 0) { ks.forEach((k, i) => ser(v[k], ind, [tok(JSON.stringify(k), k === '__truncated__' ? 'trunc' : 'key'), tok(': ', 'p')], i < ks.length - 1, path + '.' + k, depth + 1)); return; }
      push([sp, ...pre, tok('{', 'p')]);
      ks.forEach((k, i) => ser(v[k], ind + 1, [tok(JSON.stringify(k), k === '__truncated__' ? 'trunc' : 'key'), tok(': ', 'p')], i < ks.length - 1, path + '.' + k, depth + 1));
      push([sp, tok('}', 'p'), ...cm]);
    }
    ser(root, 0, [], false, '', 0);
    return out;
  }
  const lineText = l => l.toks.map(t => t.s).join('');
  // What Detailed shows: the node's recorded fields plus its bounded snippet.
  function detailOf(node) {
    const p = node.raw || {};
    if (node.kind === 'ledger') {
      const o = {status: p.status, freshness: p.freshness, provisioned: p.provisioned, consumed_by: p.consumed_by, age_s: p.age_s};
      if (p.failure) o.failure = p.failure;
      if (p.counts) o.counts = p.counts;
      if (p.reason) o.reason = p.reason;
      if (p.native) o.native = p.native.snippet ? {kind: p.native.kind, ...(p.native.event_id != null ? {event_id: p.native.event_id} : {}), ...(p.native.media_id ? {media_id: p.native.media_id} : {}), json: p.native.snippet.json} : p.native;
      return o;
    }
    if (node.kind === 'proposed') return {status: p.status, reason: p.reason, catalog_status: p.catalog_status, implemented: p.implemented, provisioned: p.provisioned, freshness: p.freshness};
    if (node.stage === 'relations') {
      if (node.prep) {
        const sec = node.prep.sections?.wrist_object_relations;
        if (node.prep.status !== 'saved') return {status: node.prep.status, reason: node.prep.reason ?? null};
        if (!sec || sec.status === 'not_recorded') return {status: 'not_recorded', basis: sec?.basis ?? null};
        return {basis: sec.basis, items: sec.items, items_truncated: sec.items_truncated, snippet_truncated: !!sec.snippet?.truncated, json: sec.snippet?.json ?? null};
      }
      return p.semantic_snippet ? p.semantic_snippet.json : {status: p.status, reason: p.reason ?? 'no state.semantic recorded'};
    }
    if (node.stage === 'prepared_evidence' && p.status === 'saved') {
      const {snippet, semantic_snippet, ...rest} = p;
      const out = {...rest, snippet: snippet ? snippet.json : null};
      if (node.prep) out.preparation = {source_coverage: node.prep.source_coverage ?? null, freshness: node.prep.freshness ?? null, exclusions: node.prep.exclusions,
        sections: Object.fromEntries(SECTIONS.map(([key]) => { const sec = node.prep.sections?.[key]; return [key, !sec || sec.status === 'not_recorded' ? 'not_recorded' :
          {items: sec.items, items_truncated: sec.items_truncated, snippet_truncated: !!sec.snippet?.truncated, json: sec.snippet?.json ?? null}]; }))};
      return out;
    }
    if (node.stage === 'consumed_request' && p.status === 'admitted') { const {snippet, ...rest} = p; return {...rest, packet: snippet ? snippet.json : null}; }
    if (node.stage === 'result') { const {answers, ...rest} = p; return answers ? {...rest, answers: answers.json} : rest; }
    return p;
  }

  // ------------------------------------------------------------------ UI bits
  const TONE_CLASS = {chg: 'chg', live: 'live', event: 'event', warn: 'warn', unav: 'unav', flight: 'flight', static: 'static', fut: 'fut', muted: 'fut'};
  function Tokens({line}) { return line.toks.map((t, i) => <span key={i} className={'pl-g-t-' + t.c}>{t.s}</span>); }
  function JsonView({value, expKey, exp, onToggle, max, lim, hot, dataAttr}) {
    const lines = jsonLines(value, {max, lim, exp});
    return <div className="pl-g-json" data-json={dataAttr}>{lines.map((l, i) => <div key={i} className={'pl-g-line' + (hot?.[i] ? ' pl-g-hot' : '') + (l.tog != null ? ' pl-g-tog' : '')}
      onClick={l.tog != null ? e => { e.stopPropagation(); onToggle?.(expKey + '|' + l.tog); } : undefined}><Tokens line={l} /></div>)}</div>;
  }
  function StateDot({state}) { return <span className={'pl-g-dot pl-g-dot-' + TONE_CLASS[state.tone]} aria-hidden="true" />; }

  function NodeCard({node, box, mode, changed, hotLines, dim, traced, traceNote, focused, exp, onToggle, onClick}) {
    const st = changed && node.state.tone === 'live' ? {label: 'Changed', tone: 'chg', why: null} : node.state;
    const fut = node.kind === 'inactive' || node.kind === 'proposed';
    const cls = 'pl-g-node pl-g-lane-' + node.lane + (fut ? ' pl-g-future' : '') + (node.varied ? ' pl-g-varied' : '') + (dim ? ' pl-g-dim' : '') + (traced ? ' pl-g-traced' : '') + (focused ? ' pl-g-focused' : '') + (changed ? ' pl-g-changed' : '');
    const laneT = node.lane === 'S' ? 'SHARED' : node.lane;
    const cons = node.kind === 'ledger' && node.raw.consumed_by ? ['a', 'b'].map(arm => {
      const v = node.raw.consumed_by[arm];
      return v && v !== 'other_arm' ? arm.toUpperCase() + ' ' + (CONSUMED_WORD[v] || hum(v)) : null;
    }).filter(Boolean).join(' · ') : node.stage === 'result' && node.raw?.fresh_on_arrival != null ? (node.raw.fresh_on_arrival ? 'fresh on arrival' : 'stale on arrival') : '';
    const head = <div className="pl-g-node-head">
      <div className="pl-g-node-title"><span className="pl-g-name">{node.title}</span><span className="pl-g-flex" />
        {node.varied && <span className="pl-g-varied-tag">VARIED</span>}
        <span className={'pl-g-kind' + (fut ? ' pl-g-kind-fut' : '')}>{node.tag}</span>
        <span className={'pl-g-lane pl-g-lane-tag-' + node.lane}>{laneT}</span></div>
      {mode === 'detailed' && <div className="pl-g-node-sub"><span className="pl-g-ident">{node.ident}</span><span className="pl-g-flex" /><StateDot state={st} /><span className={'pl-g-state pl-g-tone-' + st.tone}>{st.label}</span>{finite(node.raw?.age_s) && <span className="pl-g-age">age {ago(node.raw.age_s)}</span>}</div>}
    </div>;
    let body;
    if (mode === 'simple') {
      const sm = node.simple;
      body = <div className="pl-g-simple">
        <div className={'pl-g-value pl-g-tone-' + (st.tone === 'warn' ? 'warn' : ['unav', 'fut', 'muted'].includes(st.tone) ? 'fut' : 'live') + (changed ? ' pl-g-value-chg' : '') + (node.col === 'jev' ? ' pl-g-value-big' : '')}>{sm.v}</div>
        <div className="pl-g-subline"><StateDot state={st} /><span className={'pl-g-state pl-g-tone-' + st.tone}>{st.label}</span><span className="pl-g-subtext">{sm.sub}</span></div>
        {(sm.sx || []).map((x, i) => <div key={i} className={'pl-g-sx' + (x.w != null ? ' pl-g-sx-bar' : '')}><span className="pl-g-sx-k">{x.k}</span><span className={'pl-g-sx-v' + (x.warn ? ' pl-g-tone-warn' : x.muted ? ' pl-g-tone-fut' : '')}>{x.v}</span>{x.w != null && <div className="pl-g-bar"><i style={{width: Math.max(0, Math.min(100, x.w)) + '%'}} /></div>}</div>)}
      </div>;
    } else {
      body = <div className="pl-g-detail" data-scroll="1">
        {traced && traceNote && <div className="pl-g-banner-line">{traceNote}</div>}
        {fut && <div className="pl-g-banner-line pl-g-muted">{node.kind === 'proposed' ? 'No Lab adapter · no values are recorded' : 'Inactive · no live events'}</div>}
        {st.why && !fut && <div className="pl-g-banner-line pl-g-muted">{st.why}</div>}
        <JsonView value={detailOf(node)} expKey={node.id} exp={exp} onToggle={onToggle} max={Math.floor((box.w - 24) / 6.6)} lim={3} hot={hotLines} />
      </div>;
    }
    const snip = node.snippet;
    const foot = mode === 'detailed' ? <div className="pl-g-node-foot"><span className="pl-g-rid">{node.rid}</span><span>{node.ver}</span>
      {snip && <span className={snip.truncated ? 'pl-g-tone-warn' : ''}>{snip.bytes} B{snip.truncated ? ' · truncated' : ''}{snip.redacted ? ' · ' + snip.redacted + ' redacted' : ''}</span>}
      <span className="pl-g-flex" /><span className="pl-g-cons">{cons}</span></div> : null;
    return <div className={cls} data-node={node.id} role="button" tabIndex={0} aria-label={node.title + ' · ' + st.label}
      style={{left: box.x, top: box.y, width: box.w, height: box.h}} onClick={e => { e.stopPropagation(); onClick(node); }}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); onClick(node); } }}>{head}{body}{foot}</div>;
  }

  function Inspector({node, onClose, onDetailed, onTrace, traceActive, traceReason}) {
    const [raw, setRaw] = useState(false), [exp, setExp] = useState({});
    const payload = payloadOf(node), entries = Object.entries(payload), single = entries.length === 1;
    const exact = single ? entries[0][1] : payload;
    const own = k => Object.fromEntries(Object.entries(exp).filter(([x]) => x.startsWith(k + '|')).map(([x, v]) => [x.slice(k.length + 1), v]));
    return <aside className="pl-g-inspector" data-ui="1" aria-label={'Payload · ' + node.title}>
      <header><div className="pl-g-inspector-top"><span className="pl-g-eyebrow">PAYLOAD BEHIND THE SUMMARY</span><span className="pl-g-flex" /><button type="button" className="pl-g-x" aria-label="Close inspector" onClick={onClose}>×</button></div>
        <strong>{node.title}</strong><span className="pl-g-mono pl-g-muted">{node.ident} · {node.lane === 'S' ? 'shared' : 'arm ' + node.lane}</span>
        <div className="pl-g-inline"><StateDot state={node.state} /><span className={'pl-g-state pl-g-tone-' + node.state.tone}>{node.state.label}</span><span>{node.simple.v}</span></div>
        {(node.simple.sx || []).filter(x => ['lifecycle', 'coverage', 'freshness', 'sections', 'exclusions', 'preparation'].includes(x.k)).map(x => <div key={x.k} className={'pl-g-mono pl-g-inspector-note' + (x.warn ? ' pl-g-tone-warn' : '')}>{x.k} · {x.v}</div>)}
        <div className="pl-g-inspector-src pl-g-mono pl-g-muted">{entries.map(([k]) => k).join(' + ')}</div></header>
      <div className="pl-g-inspector-body">{raw ? <pre className="pl-g-raw" data-exact-json="1">{JSON.stringify(exact, null, 2)}</pre> :
        entries.map(([k, v]) => <div key={k}>{!single && <div className="pl-g-eyebrow">{k}</div>}<JsonView value={v === undefined ? null : v} expKey={k} exp={own(k)} onToggle={key => setExp(e => ({...e, [key]: !e[key]}))} max={58} lim={24} /></div>)}</div>
      <footer><span className="pl-g-mono pl-g-muted pl-g-ellipsis">{node.rid}{node.ver ? ' · ' + node.ver : ''}</span>
        <button type="button" className="pl-g-btn" aria-pressed={raw} onClick={() => setRaw(!raw)}>{raw ? 'Highlighted' : 'Raw JSON'}</button>
        {(node.stage === 'result' || node.stage === 'consumed_request') && <button type="button" className="pl-g-btn pl-g-btn-ice" disabled={!!traceReason} title={traceReason || 'Dim everything not in the records this request consumed'} onClick={onTrace}>{traceActive ? 'Clear trace' : 'Trace evidence'}</button>}
        <button type="button" className="pl-g-btn pl-g-btn-primary" onClick={onDetailed}>Show in Detailed</button></footer>
    </aside>;
  }

  function DiffPanel({diff, when, onClose, hidden}) {
    return <aside className="pl-g-diff" data-ui="1" aria-label="A/B difference">
      <div className="pl-g-inline"><span className="pl-g-eyebrow">A / B</span><span className="pl-g-mono pl-g-muted">{when}</span><span className="pl-g-flex" /><button type="button" className="pl-g-x" aria-label="Close A/B diff" onClick={onClose}>×</button></div>
      <p className="pl-g-diff-summary">{hidden ? 'Predictions are hidden. Input differences stay visible; returned probabilities are not shown.' : diff.summary}</p>
      <span className="pl-g-eyebrow">INPUT DIFF · PREPARED EVIDENCE {diff.requests.a || diff.requests.b ? '· ' + (diff.requests.a || '—') + ' vs ' + (diff.requests.b || '—') : ''}</span>
      {!diff.inputComparable ? <p className="pl-g-muted">{diff.inputReason} Only saved evidence inside admitted requests can be compared.</p> :
        diff.input.length ? <div className="pl-g-diff-list">{diff.input.slice(0, 40).map((d, i) => <div key={i}><span className="pl-g-muted">{d.path}</span><span>A {d.a}</span><span className="pl-g-ice">B {d.b}</span></div>)}{diff.input.length > 40 && <span className="pl-g-muted">+{diff.input.length - 40} more differences</span>}</div> :
        <p className="pl-g-muted">No differences in record counts, sources, coverage, freshness or preparation sections.</p>}
      {diff.truncated.length > 0 && <p className="pl-g-tone-warn">Snippets truncated for {diff.truncated.map(words).join(', ')}: the diff covers the bounded prefix only.</p>}
      {diff.preparation === 'not_supplied' && <p className="pl-g-muted">Preparation sections not supplied by this controller (contract revision not supplied); compared state.semantic instead.</p>}
      <span className="pl-g-eyebrow">OUTPUT DIFF · RETURNED DISTRIBUTION</span>
      {hidden ? <p className="pl-g-muted">Returned distributions hidden.</p> : diff.output.length ? <div className="pl-g-diff-grid"><span className="pl-g-muted">category</span><span>A</span><span className="pl-g-ice">B</span><span className="pl-g-muted">Δ</span>
        {diff.output.map(o => <React.Fragment key={o.label}><span>{activityText(o.label)}</span><span>{o.a == null ? '—' : o.a.toFixed(2)}</span><span className="pl-g-ice">{o.b == null ? '—' : o.b.toFixed(2)}</span><span className={o.d != null && Math.abs(o.d) >= .1 ? 'pl-g-tone-warn' : 'pl-g-muted'}>{o.d == null ? '—' : (o.d >= 0 ? '+' : '') + o.d.toFixed(2)}</span></React.Fragment>)}</div> :
        <p className="pl-g-muted">No returned distribution recorded for either arm.</p>}
      {!hidden && <p className="pl-g-muted">Provider confidence (not a probability, never compared as one): A {diff.confidence.a == null ? 'not recorded' : diff.confidence.a.toFixed(2)} · B {diff.confidence.b == null ? 'not recorded' : diff.confidence.b.toFixed(2)}. Probabilities are shown exactly as recorded.</p>}
    </aside>;
  }

  // ------------------------------------------------------------------ video dock
  const OVERLAYS = [['boxes', 'Boxes', 'boxes'], ['skeleton', 'Skeleton', 'skeleton'], ['relations', 'Relations', 'spatial'], ['labels', 'Labels', 'labels']];
  function paintedInfo(p, review, playhead, FrameAge) {
    if (!p) return 'Waiting for the first analyzed frame to decode';
    const f = p.frame || {}, t = p.arm?.timing || {};
    const observed = finite(f.session_time_s) ? 'observed ' + fmt(f.session_time_s) : 'observation not clock-mapped';
    const latency = finite(t.source_to_controller_ms) ? 'source→controller ' + t.source_to_controller_ms.toFixed(0) + ' ms' : finite(t.stages_ms?.inference) ? 'inference ' + t.stages_ms.inference.toFixed(1) + ' ms' : 'latency not recorded';
    return <>{'frame ' + (p.arm?.frame_id ?? f.id ?? '—') + ' · ' + observed + ' · ' + latency + ' · '}{FrameAge ? <FrameAge frame={f} review={review} playhead={playhead} /> : null}</>;
  }
  function AnalyzedPane({armId, arm, layers, sessionId, review, playhead, parts, live, jev}) {
    const [painted, setPainted] = useState(null);
    const {Scene, FrameAge, replayHold} = parts;
    const key = armId === 'A' ? 'A' : 'B';
    useEffect(() => { setPainted(null); }, [sessionId]);
    const C = global.HomePerceptionLabController;
    const onPaint = (f, a) => { setPainted({frame: f, arm: a}); if (live) C?.delivery?.paint?.(key, {...f, frame_id: a?.frame_id ?? f?.id}, sessionId); };
    const report = live ? (kind, code) => C?.delivery?.[kind]?.(key, code, sessionId) : undefined;
    useEffect(() => live ? () => C?.delivery?.pause?.(key) : undefined, [live, key]);
    return <div className={'pl-g-pane pl-g-pane-' + key}>
      <div className="pl-g-pane-frame">{Scene ? <Scene frame={arm?.frame} arm={arm} layers={layers} sessionId={sessionId} onPaint={onPaint} report={report}
        ageLabel={f => FrameAge ? <FrameAge frame={f} review={review} playhead={playhead} /> : ''} nextLabel={review ? 'frame at cursor' : 'newer frame'} canHold={review && replayHold ? replayHold(playhead) : undefined} /> :
        <div className="pl-g-empty">Analyzed frame view unavailable in this build</div>}
        <span className={'pl-g-vtag pl-g-vtag-' + key}>ANALYZED {key}{arm?.profile?.name ? ' · ' + arm.profile.name : ''}</span></div>
      <div className="pl-g-vinfo" data-painted={painted ? String(painted.arm?.frame_id ?? painted.frame?.id) : ''}>{arm ? paintedInfo(painted, review, playhead, FrameAge) : review ? 'No analyzed output for arm ' + key + ' at or before the cursor' : 'No analyzed output for arm ' + key + ' yet'}</div>
      {arm?.validation?.length > 0 && <div className="pl-g-vwarn">Overlay withheld: {arm.validation.join(', ')}</div>}
      {jev && <div className="pl-g-vjev"><strong>JEV {key} · {jev.v}</strong><span className="pl-g-muted">{jev.sub}</span></div>}
    </div>;
  }
  function VideoDock({layout, setLayout, view, setView, overlays, setOverlays, width, setWidth, narrow, mode, frozen, session, cameraId, siteId, caps, parts, arms, t, playing, speed, videoMedia, clockWarn, model, onPick, focusId, recording, frameAge}) {
    const review = mode === 'replay', live = mode === 'live' && !frozen;
    const {LiveCameraVideo, VideoSource} = parts;
    const layers = {boxes: overlays.boxes, labels: overlays.labels, skeleton: overlays.skeleton, spatial: overlays.relations, masks: false, tracks: false};
    const showArms = view === 'AB' ? ['A', 'B'] : view === 'A' || view === 'B' ? [view] : [];
    const support = key => showArms.map(a => Data().overlaySupport ? Data().overlaySupport(arms[a], key) : {available: false, reason: 'Overlay support unknown'});
    const resize = e => {
      if (layout === 'expanded' || narrow) return;
      e.preventDefault(); e.stopPropagation();
      const x0 = e.clientX, w0 = width, side = layout === 'side';
      const move = ev => setWidth(Math.max(260, Math.min(960, side ? w0 - (ev.clientX - x0) : w0 + (ev.clientX - x0))));
      const up = () => { global.removeEventListener('pointermove', move); global.removeEventListener('pointerup', up); };
      global.addEventListener('pointermove', move); global.addEventListener('pointerup', up);
    };
    const camera = cameraId || null;
    const jevOf = a => model?.byId?.[a.toLowerCase() + ':result']?.simple;
    const liveView = review ? (VideoSource && global.HomePerceptionLabMedia?.videoItems?.(videoMedia)?.length ?
      <VideoSource media={videoMedia} session={session} t={t} playing={playing} speed={speed || 1} review /> : <div className="pl-g-empty">No recorded video covers this cursor window. Analyzed views show the stored frame per frame id.</div>) :
      frozen ? (frozen.still ? <img className="pl-g-still" src={frozen.still} alt="Live camera held at freeze" /> : <div className="pl-g-empty">Live video paused for inspection. Recording and processing continue.</div>) :
      LiveCameraVideo && camera ? <LiveCameraVideo key={siteId + ':' + camera} siteId={siteId} cameraId={camera} available={!!caps?.live_videos?.[camera]} snapshotAvailable={!!caps?.live_previews?.[camera]} /> :
      <div className="pl-g-empty">{camera ? 'Live video unavailable in this build' : 'No camera for this session'}</div>;
    const rows = ['a:specialist_output', 'b:specialist_output', 'a:relations', 'b:relations', 'a:prepared_evidence', 'b:prepared_evidence', 'a:result', 'b:result'].map(id => model?.byId?.[id]).filter(Boolean);
    return <section className={'pl-g-dock pl-g-dock-' + layout + (narrow ? ' pl-g-dock-narrow' : '')} data-ui="1" aria-label="Video dock" style={layout === 'side' && !narrow ? {width} : layout === 'pip' ? {width: view === 'AB' ? Math.round(width * 1.6) : width} : undefined}>
      {(layout === 'side' || layout === 'pip') && !narrow && <div className={'pl-g-resize pl-g-resize-' + layout} onPointerDown={resize} title="Drag to resize" role="separator" aria-orientation="vertical" />}
      <div className="pl-g-dock-head"><span className="pl-g-eyebrow">VIDEO</span>
        <div className="pl-g-seg">{[['live', review ? 'Recorded' : 'Live camera'], ['A', 'Analyzed A'], ['B', 'Analyzed B'], ['AB', 'A + B']].map(([id, l]) => <button key={id} type="button" aria-pressed={view === id} onClick={() => setView(id)}>{l}</button>)}</div>
        <span className="pl-g-flex" />
        {[['side', 'Side', 'Dock beside the graph'], ['pip', 'PiP', 'Picture in picture'], ['expanded', 'Expand', 'Expand over the graph'], ['hidden', 'Hide', 'Hide video']].map(([id, l, title]) => <button key={id} type="button" className="pl-g-mini" aria-pressed={layout === id} title={title} onClick={() => setLayout(id)}>{l}</button>)}</div>
      {view !== 'live' && <div className="pl-g-dock-ovl"><span className="pl-g-eyebrow">OVERLAYS</span>{OVERLAYS.map(([id, label, key]) => {
        const s = support(key), ok = s.some(x => x.available), reason = s.find(x => !x.available)?.reason;
        return <button key={id} type="button" className="pl-g-chip" aria-pressed={overlays[id] && ok} disabled={!ok} title={ok ? (reason ? 'Not produced by one arm: ' + reason : 'Display only; recording is unchanged') : reason || 'Not produced'}
          onClick={() => setOverlays({...overlays, [id]: !overlays[id]})}>{label}{!ok && <small> · not produced</small>}</button>;
      })}</div>}
      {!review && recording && view === 'live' && <div className="pl-g-vwarn">No analyzed view is open, so this view measures no delivery. Choose Analyzed A, B or A + B to measure it.</div>}
      <div className="pl-g-dock-media">
        {view === 'live' && <div className="pl-g-pane"><div className="pl-g-pane-frame pl-g-live">{liveView}<span className="pl-g-vtag">{review ? 'RECORDED VIDEO' : 'LIVE CAMERA'}{!review && !frozen ? ' · NO INFERENCE OVERLAY' : ''}</span>{frozen && <span className="pl-g-frozen-tag">FROZEN {frozen.label}</span>}</div>
          <div className="pl-g-vinfo">{review ? 'Encoded-video clock · seeking never runs inference' : 'Smooth live video is not the analyzed frame; analyzed views show the exact frame each model processed.'}</div></div>}
        {showArms.length > 0 && <div className={'pl-g-panes' + (showArms.length > 1 && !(layout === 'side' && width < 520) && !narrow ? ' pl-g-panes-2' : '')}>
          {showArms.map(a => <div key={a} className="pl-g-pane-wrap">{frozen && <span className="pl-g-frozen-tag">FROZEN {frozen.label}</span>}
            <AnalyzedPane armId={a} arm={arms[a]} layers={layers} sessionId={session?.id} review={review} playhead={t} parts={parts} live={live} jev={view === 'AB' ? jevOf(a) : null} /></div>)}</div>}
        {frameAge && <div className="pl-g-vinfo pl-g-frame-age" data-frame-age="1">{frameAge.map(line => <div key={line}>{line}</div>)}</div>}
        {clockWarn && <details className="pl-g-info pl-g-vinfo"><summary><span className="pl-g-i" aria-hidden="true">i</span>Clock mapping</summary><div className="pl-g-info-body"><p>Clock mapping for this session is unverified. Video and overlays are matched by frame id only, so exact alignment is not guaranteed.</p></div></details>}
      </div>
      {(layout === 'side' || layout === 'expanded' || narrow) && rows.length > 0 && <div className="pl-g-moment"><span className="pl-g-eyebrow">THE PIPELINE AT THE SAME MOMENT</span>
        {rows.map(n => <button key={n.id} type="button" className={'pl-g-moment-row' + (focusId === n.id ? ' pl-g-moment-on' : '')} onClick={() => onPick(n.id)}>
          <span className={'pl-g-lane-tag-' + n.lane}>{n.lane}</span><span className="pl-g-muted">{n.title.replace(/ · arm [AB]$/, '')}</span><span className={'pl-g-tone-' + (n.state.tone === 'warn' ? 'warn' : 'live')}>{n.simple.v}</span></button>)}</div>}
    </section>;
  }

  // ------------------------------------------------------------------ timeline
  function trackFlags(track) {
    if (!track) return '';
    const f = [];
    if (track.downsampled) f.push('latest per bucket' + (finite(track.bucket_ms) ? ' ' + track.bucket_ms + ' ms' : ''));
    if (track.scan_truncated) f.push('scan truncated · narrow the range');
    else if (track.truncated) {
      const shown = finite(track.returned) ? track.returned : Array.isArray(track.items) ? track.items.length : null;
      f.push(shown == null ? 'truncated · returned count not recorded' : 'first ' + shown + ' of ' + (finite(track.total) ? track.total : 'total not recorded'));
    }
    return f.join(' · ');
  }
  // Rows are pure over the timeline payload; only the cursor moves per tick.
  function timelineRows(data, hide) {
    if (!data?.range) return null;
    const from = data.range.from_ms, to = Math.max(from + 1, data.range.to_ms), span = to - from, tr = data.tracks || {};
    const pc = ms => Math.max(0, Math.min(100, (ms - from) / span * 100));
    const tick = (ms, cls, title, key) => ({l: pc(ms), w: 0, cls, title, key});
    const bar = (a, b, cls, title, key) => ({l: pc(a), w: Math.max(.15, pc(b) - pc(a)), cls, title, key});
    const rows = [];
    for (const arm of ['a', 'b']) {
      const P = tr.perception?.[arm];
      rows.push({label: 'Perception ' + arm.toUpperCase(), lane: arm, flags: trackFlags(P), count: P?.total, items: (P?.points || []).filter(p => finite(p.t_ms)).map((p, i) => tick(p.t_ms, p.status === 'fresh' ? 'fresh-' + arm : 'stale', 'frame ' + p.frame_id + ' · ' + p.status + (p.objects != null ? ' · ' + p.objects + ' objects' : '') + (p.poses != null ? ' · ' + p.poses + ' poses' : ''), i))
        .concat((tr.delivery_gaps?.[arm]?.items || []).filter(g => finite(g.from_ms) && finite(g.to_ms)).map((g, i) => bar(g.from_ms, g.to_ms, 'gap', hum(g.kind) + ' gap ' + (g.gap_ms / 1000).toFixed(1) + ' s', 'g' + i)))
        .concat((tr.perception_markers?.[arm]?.items || []).filter(m => finite(m.t_ms)).map((m, i) => tick(m.t_ms, 'marker', hum(m.kind) + (m.reason ? ' · ' + m.reason : ''), 'm' + i)))});
    }
    for (const arm of ['a', 'b']) {
      const J = tr.jev?.[arm], reqs = J?.requests?.items || [], res = (J?.results?.items || []).filter(r => finite(r.t_ms));
      const items = res.map((r, i) => {
        const q = reqs.find(x => x.request_id === r.request_id);
        const start = q && finite(q.t_ms) ? q.t_ms : r.t_ms;
        return bar(start, r.t_ms, 'jev-' + arm + (['ready'].includes(r.status) ? '' : ' pl-g-tl-warnbar'), 'request → result · ' + (hide ? hum(r.status) + ' · prediction hidden' : r.activity ? activityText(r.activity) + ' ' + pnum(r.probability) : hum(r.status)) + (r.reason ? ' · ' + r.reason : ''), 'r' + i);
      }).concat(res.filter(r => finite(r.valid_until_ms) && r.valid_until_ms > r.t_ms).map((r, i) => bar(r.t_ms, r.valid_until_ms, 'valid', 'valid until ' + fmt(r.valid_until_ms / 1000), 'v' + i)))
        // Contract revision 3: held while the arm's facts are unchanged (up to 12 s), beyond the frame TTL.
        .concat(res.filter(r => finite(r.valid_through_ms) && r.valid_through_ms > Math.max(r.t_ms, finite(r.valid_until_ms) ? r.valid_until_ms : r.t_ms)).map((r, i) =>
          bar(Math.max(r.t_ms, finite(r.valid_until_ms) ? r.valid_until_ms : r.t_ms), r.valid_through_ms, 'valid', 'facts unchanged · held until ' + fmt(r.valid_through_ms / 1000), 'f' + i)))
        .concat(reqs.filter(q => finite(q.t_ms) && !res.some(r => r.request_id === q.request_id)).map((q, i) => tick(q.t_ms, 'req-' + arm, 'request ' + q.request_id + ' · no result in range', 'q' + i)));
      rows.push({label: 'JEV ' + arm.toUpperCase() + ' · request → result', lane: arm, flags: [trackFlags(J?.requests), trackFlags(J?.results)].filter(Boolean).join(' · '), count: J?.results?.total, items});
    }
    // The code-owned smoothed activity strip (contract revision 3); absent from older controllers.
    for (const arm of ['a', 'b']) {
      const S = tr.jev_smoothed?.[arm];
      if (!S) continue;
      const items = (S.items || []).filter(s => finite(s.t_ms) && finite(s.until_ms) && s.until_ms > s.t_ms).map((s, i) => {
        const shown = typeof s.shown === 'string' && s.shown !== 'abstain' && s.shown !== 'no_person';
        return bar(s.t_ms, s.until_ms, shown ? 'jev-' + arm : 'valid', 'smoothed · ' + (hide ? 'prediction hidden' : shown ? activityText(s.shown) + ' ' + pnum(s.confidence) : words(s.shown || 'not recorded')) +
          (s.source === 'code_gate' ? ' · local gate, nothing sent' : ''), 's' + i);
      });
      rows.push({label: 'JEV ' + arm.toUpperCase() + ' · smoothed', lane: arm, flags: trackFlags(S), count: S.total, items});
    }
    const list = (label, track, map, keep = () => true) => { if (track) rows.push({label, flags: trackFlags(track), count: track.total, items: (track.items || []).filter(keep).map(map)}); };
    const timed = x => finite(x?.t_ms);
    if (tr.jev_markers) {
      const J = tr.jev_markers, routine = J.routine_lifecycle, lower = J.routine_lifecycle_is_lower_bound === true;
      const counts = isObj(routine) ? ['a', 'b'].flatMap(arm => Object.entries(routine[arm] || {}).filter(([, n]) => finite(n) && n > 0)
        .map(([state, n]) => arm.toUpperCase() + ': ' + (lower ? '≥' : '') + n + ' ' + words(state) + ' · counted, not placed' + (lower ? ' (lower bound)' : ''))) : [];
      const flags = [trackFlags(J), ...counts, !rev2(data) ? 'lifecycle receipts not supplied by this controller' : ''].filter(Boolean).join(' · ');
      rows.push({label: 'JEV markers', flags, count: J.total, items: (J.items || []).filter(timed).map((m, i) => tick(m.t_ms, 'marker',
        hum(m.kind) + (m.arm ? ' · arm ' + String(m.arm).toUpperCase() : '') + (m.state ? ' · ' + words(m.state) : '') + (m.reason ? ' · ' + words(m.reason) : '') + (m.request_id ? ' · request ' + m.request_id : ''), i))});
    }
    list('Transitions', tr.transitions, (m, i) => tick(m.t_ms, 'transition', hum(m.kind) + (m.experiment_id ? ' · ' + m.experiment_id : '') + (m.generation != null ? ' · generation ' + m.generation : ''), i), timed);
    list('Recording', tr.recording, (m, i) => tick(m.t_ms, 'rec', hum(m.state || m.kind) + (m.reason ? ' · ' + m.reason : ''), i), timed);
    list('Workers', tr.workers, (m, i) => tick(m.t_ms, 'marker', hum(m.kind) + ' · ' + hum(m.state), i), timed);
    // Only session-clock labels sit on the session axis; encoded-video labels are counted, not placed.
    const sessionLabel = l => (l.time_basis || 'session') === 'session' && finite(l.at_ms);
    list('Labels · review only', tr.labels, (l, i) => bar(l.at_ms, finite(l.end_ms) ? l.end_ms : l.at_ms, 'lbl', activityText(l.label) + (l.reviewed ? ' · reviewed' : '') + ' · ' + (l.time_basis || 'session'), i), sessionLabel);
    const offAxis = (tr.labels?.items || []).filter(l => !sessionLabel(l)).length;
    if (offAxis) rows.push({label: 'Labels · other clock', flags: offAxis + ' on the encoded-video clock · not placed on the session axis', count: offAxis, items: []});
    list('Bookmarks', tr.bookmarks, (b, i) => tick(b.t_ms, 'bookmark', b.label || 'bookmark', i), timed);
    list('HA context', tr.context?.ha, (h, i) => tick(h.t_ms, 'ctx', 'HA · ' + h.known + '/' + h.entities + ' known', i), timed);
    if (Array.isArray(tr.context?.ha_unavailable) && (tr.context.ha_unavailable.length || finite(tr.context.ha_unavailable_total) && tr.context.ha_unavailable_total > 0)) {
      const ctx = tr.context, all = ctx.ha_unavailable, placed = all.map((h, i) => [h, i]).filter(([h]) => finite(h?.t_ms)), total = finite(ctx.ha_unavailable_total) ? ctx.ha_unavailable_total : all.length;
      rows.push({label: 'HA unavailable', count: total, flags: [ctx.ha_unavailable_truncated ? 'first ' + all.length + ' of ' + total : '', ctx.ha_unavailable_scan_truncated ? 'scan truncated · narrow the range' : '',
        placed.length < all.length ? (all.length - placed.length) + ' without a recorded time · not placed' : ''].filter(Boolean).join(' · '),
        items: placed.map(([h, i]) => ({...tick(h.t_ms, 'marker', 'HA unavailable' + (h.reason ? ' · ' + words(h.reason) : '') + (h.event_id != null ? ' · event ' + h.event_id + ' · click to inspect' : ''), 'u' + i),
          inspect: {key: 'timeline.tracks.context.ha_unavailable[' + i + ']', item: h}}))});
    }
    list('Caption receipts', tr.context?.captions, (c, i) => tick(c.t_ms, 'ctx', 'caption receipt', i), timed);
    list('Room', tr.context?.room, (r, i) => tick(r.t_ms, 'ctx', 'room observation', i), timed);
    const steps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600], step = steps.find(x => span / 1000 / x <= 8) || 900;
    const ticks = [];
    for (let x = Math.ceil(from / 1000 / step) * step; x * 1000 <= to; x += step) ticks.push({s: x, l: pc(x * 1000)});
    return {from, to, span, rows, ticks};
  }
  const TimelineRows = React.memo(function TimelineRows({rows, onInspect}) {
    return <>{rows.map(r => <div key={r.label} className="pl-g-tl-row">{r.items.map(it => <i key={it.key} className={'pl-g-tl-' + it.cls.split(' ')[0] + (it.cls.includes('warnbar') ? ' pl-g-tl-warnbar' : '') + (it.w ? ' pl-g-tl-bar' : ' pl-g-tl-tick') + (it.inspect ? ' pl-g-tl-link' : '')}
      title={it.title} style={{left: it.l + '%', width: it.w ? it.w + '%' : undefined}} data-inspect={it.inspect ? it.inspect.key : undefined}
      onClick={it.inspect && onInspect ? e => { e.stopPropagation(); onInspect(it.inspect); } : undefined} />)}</div>)}</>;
  });
  function ReplayTimeline({tl, status, error, t, duration, onSeek, playing, onTogglePlay, windowRange, hide, fastPlayback, onInspect}) {
    const ref = useRef(null);
    const built = useMemo(() => timelineRows(tl, hide), [tl, hide]);
    const head = <><button type="button" className="pl-g-btn pl-g-btn-primary" onClick={onTogglePlay}>{playing ? 'Pause' : 'Play'}</button><span className="pl-g-mono">{fmt(t)} / {fmt(duration)}</span>
      {fastPlayback && <span className="pl-g-tone-warn">Fast playback · graph and analyzed frames update when paused</span>}</>;
    if (!tl) return <div className="pl-g-timeline"><div className="pl-g-inline pl-g-wrap">{head}
      <span className="pl-g-muted">{status === 'failed' ? 'Replay timeline unavailable · ' + error : status === 'loading' ? 'Loading replay timeline…' : 'Replay timeline not loaded'}</span></div></div>;
    if (!built) return <div className="pl-g-timeline"><div className="pl-g-inline pl-g-wrap">{head}<span className="pl-g-muted">{hum(tl.reason) || 'No recording range'} · nothing to place on a timeline.</span></div></div>;
    const {from, to, span, rows, ticks} = built;
    const cursor = Math.max(0, Math.min(100, (t * 1000 - from) / span * 100));
    const seek = e => { const b = ref.current?.getBoundingClientRect(); if (!b || !b.width) return; onSeek(Math.max(0, Math.min(duration, (from + (e.clientX - b.left) / b.width * span) / 1000))); };
    const clock = tl.clock || {};
    return <div className="pl-g-timeline" data-ui="1" aria-label="Replay timeline">
      <div className="pl-g-inline pl-g-wrap">{head}
        <span className="pl-g-mono pl-g-muted">window {fmt(from / 1000)}–{fmt(to / 1000)}{windowRange ? ' of ' + fmt(duration) : ''}</span>
        {tl.scan_truncated && <span className="pl-g-tone-warn">Some tracks hit their scan bound · counts are lower bounds · narrow the range</span>}
        <span className="pl-g-flex" />
        {/* Clock caveats are an info note, not a warning: they affect overlay alignment, not scoring. */}
        <details className="pl-g-info"><summary aria-label="Timing notes"><span className="pl-g-i" aria-hidden="true">i</span>Timing notes ({clock.session_clock_verified === false ? 3 : 2})</summary>
          <div className="pl-g-info-body">{clock.session_clock_verified === false && <p>Video–sensor clock mapping unverified · overlays matched by frame id only.</p>}
            <p>Results appear when they became available · seeking never runs inference.</p>
            <p>Bars run from the admitted request to the recorded result; grey continues until the result valid_until.</p></div></details></div>
      <div className="pl-g-tl-grid">
        <div className="pl-g-tl-labels"><span className="pl-g-tl-label" />{rows.map(r => <span key={r.label} className={'pl-g-tl-label' + (r.lane ? ' pl-g-lane-tag-' + r.lane.toUpperCase() : '')} title={r.flags || ''}>{r.label}{finite(r.count) ? ' · ' + r.count : ''}{r.flags ? <small className="pl-g-tone-warn"> · {r.flags}</small> : null}</span>)}</div>
        <div className="pl-g-tl-tracks" ref={ref} onClick={seek}>
          <div className="pl-g-tl-ticks">{ticks.map(x => <span key={x.s} style={{left: x.l + '%'}}>{fmt(x.s).replace(/\.0$/, '')}</span>)}</div>
          <TimelineRows rows={rows} onInspect={onInspect} />
          <b className="pl-g-tl-cursor" style={{left: cursor + '%'}} /></div>
      </div>
      <p className="pl-g-muted">{tl.labels_note || 'Labels are review items only and never model inputs.'}</p>
    </div>;
  }

  // ------------------------------------------------------------------ workspace
  const SIZE = {w: 1000, h: 640};
  // Canvas contents. Memoized: replay ticks re-render only the cursor and the dock.
  let worldRenders = 0;
  // Ages tick with every read and a lifecycle receipt is not a new result: neither is a change.
  const DIGEST_SKIP = k => k === 'latest_lifecycle' || k === 'age_s' || k === 'server_time' || /_age_s$/.test(k);
  const SKIP_PATH = /(^|\.)(latest_lifecycle|age_s|[a-z_]+_age_s)(\.|\[|$)/;
  const World = React.memo(function World({model, L, detail, g, traceSet, trace, traceInfo, focusSet, focusNode, changedAt, frozen, tick, exp, hl, onNode, toggleExp}) {
    worldRenders++;
    const now = Date.now();
    const recent = id => !frozen && now - (changedAt[id] || 0) < 1600;
    const edges = useMemo(() => edgePaths(model, L), [model, L]);
    const nodeExp = id => Object.fromEntries(Object.entries(exp).filter(([k]) => k.startsWith(id + '|')).map(([k, v]) => [k.slice(id.length + 1), v]));
    // Detailed line highlights: lines whose text changed since the previous payload.
    const hotFor = node => {
      if (detail !== 'detailed' || frozen) return null;
      const lines = jsonLines(detailOf(node), {max: Math.floor(((L.P[node.id]?.w || 300) - 24) / 6.6), lim: 3, exp: nodeExp(node.id)}), texts = lines.map(lineText);
      const prev = hl[node.id] || {texts: [], at: []};
      const at = texts.map((tx, i) => prev.texts[i] !== undefined && prev.texts[i] !== tx ? now : prev.texts[i] === tx ? prev.at[i] || 0 : 0);
      hl[node.id] = {texts, at};
      return at.map((a, i) => now - a < 1100 && !SKIP_PATH.test(lines[i].path || ''));
    };
    const nodes = model ? model.nodes.filter(n => L.P[n.id]) : [];
    return <>
      {L.bands.map(b => <div key={b.arm} className={'pl-g-band pl-g-band-' + b.arm} style={{left: b.x, top: b.y, width: b.w, height: b.h}}><span>ARM {b.arm.toUpperCase()}{g?.arms?.[b.arm]?.profile?.model_version ? ' · ' + g.arms[b.arm].profile.model_version : ''}{g?.arms?.[b.arm]?.setup ? ' · ' + ['detector', 'pose', 'device'].map(k => g.arms[b.arm].setup[k]).filter(v => v != null).join(' · ') : ''} · INDEPENDENT EVIDENCE, JEV AND POLICY</span></div>)}
      {L.gbox.map(b => <div key={b.label} className="pl-g-group" style={{left: b.x, top: b.y, width: b.w, height: b.h}}><span>{b.label}</span></div>)}
      {model && HEADS.map(([c, label]) => <div key={c} className="pl-g-colhead" style={{left: L.cols[c][0], top: 52}}>{label}</div>)}
      <svg className="pl-g-edges" width={L.WW} height={L.WH} aria-hidden="true">{edges.map((e, i) => {
        const on = traceSet ? traceSet.has(e.f) && traceSet.has(e.t) && (e.lane === 'S' || e.lane === trace.toUpperCase()) : true;
        const dimmed = traceSet ? !on : focusSet ? !(focusSet.has(e.f) || focusSet.has(e.t)) : false;
        const hot = !traceSet && e.kind !== 'fut' && recent(e.t) && recent(e.f);
        return <g key={i} className={'pl-g-edge pl-g-edge-' + e.kind + ' pl-g-edge-lane-' + e.lane + (hot ? ' pl-g-edge-hot' : '') + (dimmed ? ' pl-g-edge-dim' : '') + (traceSet && on ? ' pl-g-edge-traced' : '')}>
          <path d={e.d} />{e.label && detail === 'detailed' && !dimmed && e.x1 != null && <text x={e.x1 + 8} y={e.y1 + (e.arm === 'b' ? 14 : -5)}>{(e.arm || e.lane).toUpperCase() + ' · ' + e.label}</text>}</g>;
      })}</svg>
      {nodes.map(n => {
        const dim = (traceSet && !traceSet.has(n.id)) || (focusSet && !focusSet.has(n.id));
        return <NodeCard key={n.id} node={n} box={L.P[n.id]} mode={detail} changed={recent(n.id)} hotLines={hotFor(n)} dim={!!dim} traced={!!traceSet?.has(n.id)}
          traceNote={traceInfo?.recordNote?.[n.id]} focused={focusNode === n.id} onClick={onNode} exp={nodeExp(n.id)} onToggle={toggleExp} />;
      })}
    </>;
  });
  function GraphWorkspace(props) {
    const {C, mode, graphSlot, detailArms, replayArms, session, cameraId, siteId, caps, parts, t, duration, playing, speed, onTogglePlay, onSeek, timelineSlot, videoMedia, setupTitle, windowRange, hidePredictions, fastPlayback} = props;
    const review = mode === 'replay';
    const recording = !review && !!Data().isRecording?.(session);
    const [detail, setDetail] = useState('simple');
    const [view, setViewState] = useState({x: 20, y: 20, k: .6});
    const [focus, setFocus] = useState('all');
    const [focusNode, setFocusNode] = useState(null);
    const [insp, setInsp] = useState(null);
    const [tlItem, setTlItem] = useState(null);
    const inspectTimeline = useCallback(x => { setInsp(null); setTlItem(x); }, []);
    const [trace, setTrace] = useState(null);
    const [diffOpen, setDiffOpen] = useState(false);
    const [full, setFull] = useState(false);
    const [exp, setExp] = useState({});
    const [frozen, setFrozen] = useState(null);
    const [dock, setDock] = useState('side');
    // While recording, the analyzed panes are open by default so delivery is measured.
    const [vview, setVview] = useState(() => recording ? 'AB' : 'live');
    useEffect(() => { if (recording) setVview('AB'); }, [recording, session?.id]);
    const [overlays, setOverlays] = useState({boxes: true, skeleton: true, relations: true, labels: true});
    const [dockW, setDockW] = useState(400);
    const [width, setWidth] = useState(0);
    const [, force] = useState(0);
    const rootRef = useRef(null), canvasRef = useRef(null), worldRef = useRef(null), viewRef = useRef(view), fitted = useRef(false);
    const hl = useRef({}), changedAt = useRef({}), digests = useRef({}), lastSession = useRef(null), centerRef = useRef(null);
    const narrow = width > 0 && width < 760;
    // Live graph reads run only while the workspace is on screen: not when it
    // is scrolled out of view or covered by the expanded video dock.
    const [inView, setInView] = useState(true);
    useEffect(() => {
      const el = rootRef.current, IO = global.IntersectionObserver;
      if (!el || typeof IO !== 'function') return;
      const io = new IO(entries => { const e = entries[entries.length - 1]; if (e) setInView(!!e.isIntersecting); });
      io.observe(el);
      return () => io.disconnect();
    }, []);
    const graphOnScreen = inView && dock !== 'expanded';
    useEffect(() => {
      if (review || !session?.id || !C?.graphVisible) return;
      C.graphVisible(session.id, graphOnScreen);
    }, [review, session?.id, graphOnScreen]);
    useEffect(() => () => { if (!review && session?.id) C?.graphVisible?.(session.id, true); }, [review, session?.id]);

    // Display source: frozen snapshot, else the slot data.
    const liveGraph = graphSlot?.data || null;
    const shownGraph = frozen ? frozen.graph : liveGraph;
    const model = useMemo(() => buildModel(hidePredictions ? maskPredictions(shownGraph) : shownGraph), [shownGraph, hidePredictions]);
    const layouts = useMemo(() => ({simple: layout(model, 'simple'), detailed: layout(model, 'detailed')}), [model]);
    const L = layouts[detail];
    const arms = frozen ? frozen.arms : review ? replayArms || {} : detailArms || {};

    // Changed detection: digest per node, once per model, across successive payloads.
    const now = Date.now();
    const changedMap = useMemo(() => {
      if (!model || frozen) return {...changedAt.current};
      if (lastSession.current !== shownGraph?.session_id) { digests.current = {}; changedAt.current = {}; hl.current = {}; lastSession.current = shownGraph?.session_id; }
      const at = Date.now();
      for (const n of model.nodes) {
        const d = JSON.stringify([n.raw, n.prep ?? null], (k, v) => DIGEST_SKIP(k) ? undefined : v);
        if (digests.current[n.id] !== undefined && digests.current[n.id] !== d) changedAt.current[n.id] = at;
        digests.current[n.id] = d;
      }
      return {...changedAt.current};
    }, [model]);
    const [tick, setTick] = useState(0);
    useEffect(() => { const timer = setTimeout(() => setTick(x => x + 1), 1700); return () => clearTimeout(timer); }, [model]);
    useEffect(() => {
      if (!frozen) return;
      const timer = setInterval(() => force(x => x + 1), 1000);
      return () => clearInterval(timer);
    }, [frozen]);

    const canvasSize = () => {
      const b = canvasRef.current?.getBoundingClientRect?.();
      return b && b.width > 0 && b.height > 0 ? {w: b.width, h: b.height} : SIZE;
    };
    const apply = () => { const w = worldRef.current, v = viewRef.current; if (w) w.style.transform = 'translate(' + v.x + 'px,' + v.y + 'px) scale(' + v.k + ')'; };
    const setView = v => { viewRef.current = v; apply(); setViewState(v); };
    const fitTo = (ids, lay = L) => {
      const size = canvasSize(), top = 64;
      const boxes = (ids || Object.keys(lay.P)).map(id => lay.P[id]).filter(Boolean);
      if (!boxes.length) { setView({x: 20, y: 20, k: .6}); return; }
      const x0 = Math.min(...boxes.map(b => b.x)) - 60, y0 = Math.min(...boxes.map(b => b.y)) - (ids ? 60 : 90), x1 = Math.max(...boxes.map(b => b.x + b.w)) + 60, y1 = Math.max(...boxes.map(b => b.y + b.h)) + 60;
      const k = Math.min(1.2, Math.max(.12, Math.min((size.w - 40) / (x1 - x0), (size.h - top - 20) / (y1 - y0))));
      setView({k, x: (size.w - (x1 - x0) * k) / 2 - x0 * k, y: top + (size.h - top - (y1 - y0) * k) / 2 - y0 * k});
    };
    const zoomBy = f => {
      const size = canvasSize(), v = viewRef.current, k1 = Math.min(1.6, Math.max(.12, v.k * f)), px = size.w / 2, py = size.h / 2;
      setView({k: k1, x: px - (px - v.x) * k1 / v.k, y: py - (py - v.y) * k1 / v.k});
    };
    const centerOn = (id, lay = L, k = viewRef.current.k) => {
      const p = lay.P[id]; if (!p) return;
      const size = canvasSize();
      setView({k, x: size.w / 2 - (p.x + p.w / 2) * k, y: size.h / 2 - (p.y + Math.min(p.h / 2, 200)) * k});
    };
    centerRef.current = centerOn;
    // Simple ⇄ Detailed: the focused node (or the one nearest the centre) keeps its screen position.
    const switchDetail = next => {
      if (next === detail) return;
      const from = layouts[detail].P, to = layouts[next].P, v = viewRef.current, size = canvasSize();
      let id = insp || focusNode;
      if (!id || !from[id] || !to[id]) {
        const cx = (size.w / 2 - v.x) / v.k, cy = (size.h / 2 - v.y) / v.k;
        let best = Infinity;
        for (const [k, q] of Object.entries(from)) { if (!to[k]) continue; const d = (q.x + q.w / 2 - cx) ** 2 + (q.y + q.h / 2 - cy) ** 2; if (d < best) { best = d; id = k; } }
      }
      setDetail(next);
      if (id && from[id] && to[id]) setView({k: v.k, x: v.x + (from[id].x - to[id].x) * v.k, y: v.y + (from[id].y - to[id].y) * v.k});
    };

    // First view: the sources-to-JEV span at a readable scale, top aligned.
    const initialView = () => {
      const size = canvasSize(), boxes = model.nodes.filter(n => ['src', 'p1', 'p2', 'ev', 'jev'].includes(n.col)).map(n => L.P[n.id]).filter(Boolean);
      if (!boxes.length) return fitTo(null);
      const x0 = Math.min(...boxes.map(b => b.x)) - 40, x1 = Math.max(...boxes.map(b => b.x + b.w)) + 40, y0 = Math.min(...boxes.map(b => b.y)) - 70;
      const k = Math.min(1, Math.max(.3, (size.w - 24) / (x1 - x0)));
      setView({k, x: 12 - x0 * k, y: 56 - y0 * k});
    };
    useEffect(() => { if (model && !fitted.current) { fitted.current = true; initialView(); } }, [model]);
    useEffect(() => { apply(); });
    useEffect(() => {
      const el = rootRef.current;
      if (!el || typeof ResizeObserver === 'undefined') return;
      const ro = new ResizeObserver(() => setWidth(el.getBoundingClientRect().width));
      ro.observe(el); setWidth(el.getBoundingClientRect().width);
      return () => ro.disconnect();
    }, []);
    // Pan (drag), zoom (ctrl/⌘ + wheel), scroll to pan.
    useEffect(() => {
      const c = canvasRef.current;
      if (!c) return;
      let drag = null, wt = null;
      const down = e => { if (e.button !== 0 || e.target.closest?.('[data-node],[data-ui]')) return; drag = {x: e.clientX, y: e.clientY, v: {...viewRef.current}}; c.setPointerCapture?.(e.pointerId); c.classList.add('pl-g-grabbing'); };
      const move = e => { if (!drag) return; viewRef.current = {...drag.v, x: drag.v.x + e.clientX - drag.x, y: drag.v.y + e.clientY - drag.y}; apply(); };
      const up = () => { if (!drag) return; drag = null; c.classList.remove('pl-g-grabbing'); setViewState({...viewRef.current}); };
      const wheel = e => {
        if (e.target.closest?.('[data-ui]')) return;
        const body = e.target.closest?.('[data-scroll]');
        if (body && !e.ctrlKey && !e.metaKey && body.scrollHeight > body.clientHeight) return;
        e.preventDefault();
        const v = viewRef.current;
        if (e.ctrlKey || e.metaKey) {
          const b = c.getBoundingClientRect(), px = e.clientX - b.left, py = e.clientY - b.top, k1 = Math.min(1.6, Math.max(.12, v.k * Math.exp(-e.deltaY * .0025)));
          viewRef.current = {k: k1, x: px - (px - v.x) * k1 / v.k, y: py - (py - v.y) * k1 / v.k};
        } else viewRef.current = {...v, x: v.x - e.deltaX, y: v.y - e.deltaY};
        apply(); clearTimeout(wt); wt = setTimeout(() => setViewState({...viewRef.current}), 160);
      };
      c.addEventListener('pointerdown', down); c.addEventListener('pointermove', move); c.addEventListener('pointerup', up); c.addEventListener('pointercancel', up);
      c.addEventListener('wheel', wheel, {passive: false});
      // Focusing a node can scroll this overflow:hidden canvas; undo it and pan instead.
      const scroll = () => {
        if (!c.scrollTop && !c.scrollLeft) return;
        c.scrollTop = 0; c.scrollLeft = 0;
        const id = document.activeElement?.closest?.('[data-node]')?.getAttribute('data-node');
        if (id) centerRef.current?.(id);
      };
      c.addEventListener('scroll', scroll);
      return () => { clearTimeout(wt); c.removeEventListener('scroll', scroll); c.removeEventListener('pointerdown', down); c.removeEventListener('pointermove', move); c.removeEventListener('pointerup', up); c.removeEventListener('pointercancel', up); c.removeEventListener('wheel', wheel); };
    }, []);
    // Esc closes the inspector, trace or full screen first; otherwise Home's Esc still closes the Lab.
    useEffect(() => {
      if (!insp && !trace && !full) return;
      const key = e => {
        if (e.key !== 'Escape' || e.target?.closest?.('input,textarea,select,[contenteditable]') || document.querySelector('.pl-modal-backdrop')) return;
        e.stopPropagation();
        if (insp) setInsp(null); else if (trace) setTrace(null); else setFull(false);
      };
      global.addEventListener('keydown', key, true);
      return () => global.removeEventListener('keydown', key, true);
    }, [insp, trace, full]);
    useEffect(() => {
      if (!full) return;
      const exit = () => { if (!document.fullscreenElement) setFull(false); };
      document.addEventListener('fullscreenchange', exit);
      return () => document.removeEventListener('fullscreenchange', exit);
    }, [full]);
    const toggleFull = () => {
      if (full) { if (document.fullscreenElement) document.exitFullscreen?.().catch?.(() => {}); setFull(false); return; }
      setFull(true);
      try { rootRef.current?.requestFullscreen?.()?.catch?.(() => {}); } catch (_) {}
    };
    // Replay and session changes drop freeze/trace; the new session refits.
    useEffect(() => { setFrozen(null); setTrace(null); setInsp(null); fitted.current = false; }, [session?.id, mode]);

    const freeze = () => {
      if (frozen) { setFrozen(null); return; }
      let still = null;
      try {
        const video = rootRef.current?.querySelector('.pl-g-live video');
        if (video && video.videoWidth) { const c = document.createElement('canvas'); c.width = video.videoWidth; c.height = video.videoHeight; c.getContext('2d').drawImage(video, 0, 0); still = c.toDataURL('image/jpeg', .85); }
      } catch (_) { still = null; }
      const st = liveGraph?.cursor?.session_time_s;
      setFrozen({graph: liveGraph, arms: {A: detailArms?.A || null, B: detailArms?.B || null}, at: Date.now(), still, session_time_s: st, label: finite(st) ? fmt(st) : new Date().toLocaleTimeString()});
    };

    const traceInfo = useMemo(() => trace ? traceOf(model, trace) : null, [model, trace]);
    const traceSet = traceInfo?.available ? traceInfo.ids : null;
    const focusSet = useMemo(() => model?.focus?.[focus] ? new Set(model.focus[focus]) : null, [model, focus]);
    const diff = useMemo(() => diffOpen ? diffOf(model) : null, [diffOpen, model]);
    // A timeline item (HA unavailable) opens the inspector with its exact recorded item.
    const tlNode = tlItem ? {id: 'tl:' + tlItem.key, title: 'HA unavailable' + (tlItem.item?.event_id != null ? ' · event ' + tlItem.item.event_id : ''), ident: tlItem.key, lane: 'S', linkTo: 'src:ha_snapshot',
      state: {label: 'Unavailable', tone: 'unav'}, simple: {v: words(tlItem.item?.reason) || 'reason not recorded', sub: ''}, payload: {[tlItem.key]: tlItem.item},
      rid: tlItem.item?.event_id != null ? 'event ' + tlItem.item.event_id : 'event id not supplied by this controller', ver: ''} : null;
    const inspNode = insp && model?.byId?.[insp] || (!insp ? tlNode : null);
    const traceReason = n => { const x = traceOf(model, n?.arm); return x?.available ? null : x?.reason || 'Nothing to trace'; };

    const onNode = useCallback(n => {
      setFocusNode(n.id);
      if (detail === 'simple') { setInsp(n.id); return; }
      if ((n.stage === 'result' || n.stage === 'consumed_request') && n.arm && traceOf(model, n.arm)?.available) setTrace(x => x === n.arm ? null : n.arm);
    }, [detail, model]);
    const toggleExp = useCallback(key => setExp(e => ({...e, [key]: !e[key]})), []);
    const pickMoment = id => { setFocusNode(id); if (detail === 'simple') setInsp(id); centerOn(id); };

    const varied = model?.varied || [];
    const g = shownGraph;
    const clockText = review ? 'cursor ' + fmt(t) + (g?.cursor && finite(g.cursor.at_ms) && Math.abs(g.cursor.at_ms / 1000 - t) >= .5 ? ' · graph at ' + fmt(g.cursor.at_ms / 1000) : '') + (graphSlot?.status === 'loading' ? ' · loading' : '') :
      'session ' + fmt(g?.cursor?.session_time_s) + (graphSlot?.received_at_ms && !frozen ? ' · read ' + ago((now - graphSlot.received_at_ms) / 1000) + ' ago' : '');
    const modeText = review ? 'REPLAY' : frozen ? 'FROZEN DISPLAY' : 'LIVE';
    const banner = traceInfo?.available ? {text: 'Tracing JEV ' + trace.toUpperCase() + (traceInfo.request_id ? ' · request ' + traceInfo.request_id : '') + ' · ' + plural(traceInfo.count, 'input record') + (finite(traceInfo.cutoff) ? ' · cutoff ' + fmt(traceInfo.cutoff) : '') + (traceInfo.evidence_truncated ? ' · evidence list truncated at 64' : ''), btn: 'Clear trace', act: () => setTrace(null), tone: 'ice'} :
      frozen ? {text: 'Display frozen at ' + frozen.label + ' · recording and processing continue · ' + Math.max(0, Math.round((now - frozen.at) / 1000)) + ' s behind', btn: 'Return to live', act: () => setFrozen(null), tone: 'warn'} : null;
    const slotNote = !g ? (graphSlot?.status === 'failed' ? (graphSlot.error === 'operation_unavailable' ? 'Graph projection is not available from this connection (developer demo or older service).' : 'Graph read failed · ' + graphSlot.error) :
      !session ? 'Not recording. The live camera is in the video dock; sources, arms and stages fill in from the session graph once a recording starts. Saved sessions open in Review & analyze.' :
      fastPlayback ? 'Fast playback · the graph at the cursor loads when playback pauses or runs at 1×.' : 'Loading the session graph at this cursor…') :
      graphSlot?.status === 'failed' ? 'Latest graph read failed (' + graphSlot.error + '); showing the previous read.' : null;

    const dockShown = dock !== 'hidden';
    const sideDock = dockShown && dock === 'side' && !narrow;
    const dockEl = dockShown ? <VideoDock layout={narrow && dock === 'side' ? 'side' : dock} setLayout={setDock} view={vview} setView={setVview} overlays={overlays} setOverlays={setOverlays} width={dockW} setWidth={setDockW} narrow={narrow}
      mode={mode} frozen={frozen} session={session} cameraId={cameraId} siteId={siteId} caps={caps} parts={parts} arms={arms} t={t} playing={playing} speed={speed} videoMedia={videoMedia} recording={recording}
      clockWarn={review && timelineSlot?.data?.clock?.session_clock_verified === false} model={model} onPick={pickMoment} focusId={focusNode}
      frameAge={!review && g ? pausedAgeLines(frameAgeLines(g.frame_age, rev2(g)), !graphOnScreen && recording) : null} /> : null;

    return <section ref={rootRef} className={'pl-g-root' + (full ? ' pl-g-full' : '') + (narrow ? ' pl-g-narrow' : '')} aria-label="Perception pipeline graph">
      <div className="pl-g-titlebar"><div className="pl-g-titletext"><strong>{setupTitle || 'Perception pipeline'}</strong>
        {isObj(g?.experiment?.lever) && typeof g.experiment.lever.note === 'string' && <span className="pl-g-eyebrow" title={'Recorded lever: ' + (g.experiment.lever.paths || []).join(', ')}>{'LEVER: ' + g.experiment.lever.note}</span>}
        <span className="pl-g-eyebrow">{varied.length ? 'VARIES: ' + varied.map(v => hum(v.replace('profile.', 'profile '))).join(' · ') + ' · all other recorded inputs shared' : g?.arms?.a?.setup || g?.arms?.b?.setup ? 'NO SETUP FIELD DIFFERS BETWEEN ARMS AT THIS CURSOR' : 'ARM SETUP AT THIS CURSOR NOT RECORDED'}</span></div>
        {g && <span className="pl-g-pill pl-g-mono" title={rev2(g) ? 'Graph contract revision ' + g.contract_revision : 'This controller does not report a contract revision: revision 2 fields are not supplied, which is not the same as none recorded'}>{rev2(g) ? 'CONTRACT REV ' + g.contract_revision : 'CONTRACT REVISION NOT SUPPLIED · OLDER CONTROLLER'}</span>}
        <span className="pl-g-pill pl-g-mono">{g?.inference === 'none' ? 'READ-ONLY · NO INFERENCE' : 'READ-ONLY'}</span></div>
      <div className={'pl-g-body' + (narrow ? ' pl-g-body-narrow' : '')}>
        <div ref={canvasRef} className="pl-g-canvas">
          <div ref={worldRef} className="pl-g-world" style={{width: L.WW, height: L.WH, transform: 'translate(' + view.x + 'px,' + view.y + 'px) scale(' + view.k + ')'}}>
            <World model={model} L={L} detail={detail} g={g} traceSet={traceSet} trace={trace} traceInfo={traceInfo} focusSet={focusSet} focusNode={focusNode} changedAt={changedMap} frozen={!!frozen}
              tick={frozen ? 0 : tick} exp={exp} hl={hl.current} onNode={onNode} toggleExp={toggleExp} />
          </div>
          <div className="pl-g-toolbar" data-ui="1">
            <div className="pl-g-seg pl-g-seg-strong" role="group" aria-label="Detail">{[['simple', 'Simple'], ['detailed', 'Detailed']].map(([m, l]) => <button key={m} type="button" aria-pressed={detail === m} onClick={() => switchDetail(m)}>{l}</button>)}</div>
            <span className={'pl-g-pill pl-g-mode-' + (review ? 'replay' : frozen ? 'frozen' : 'live')}><i />{modeText}<span className="pl-g-muted">{clockText}</span></span>
            {recording && typeof props.onBookmark === 'function' && <button type="button" className="pl-g-btn pl-g-btn-primary pl-g-bookmark" title="Mark this moment to label it later (B)" onClick={() => props.onBookmark()}>Bookmark · B</button>}
            {recording && session?.reasoning_enabled === false && <span className="pl-g-pill pl-g-mono pl-g-reasoning-off" role="status">JEV REASONING: OFF</span>}
            {!review && <button type="button" className={'pl-g-btn' + (frozen ? ' pl-g-btn-primary' : '')} disabled={!liveGraph && !frozen} onClick={freeze}>{frozen ? 'Return to live' : 'Freeze display'}</button>}
            <div className="pl-g-seg" role="group" aria-label="Focus"><span className="pl-g-eyebrow">FOCUS</span>{[['all', 'All'], ['shared', 'Shared inputs'], ['A', 'Arm A'], ['B', 'Arm B'], ['decision', 'Decision']].map(([id, l]) => <button key={id} type="button" aria-pressed={focus === id} onClick={() => { setFocus(id); if (model) fitTo(id === 'all' ? null : focusFit(model, id)); }}>{l}</button>)}</div>
            <div className="pl-g-seg" role="group" aria-label="Zoom"><button type="button" aria-label="Zoom out" onClick={() => zoomBy(.8)}>−</button><span className="pl-g-mono pl-g-zoom">{Math.round(view.k * 100)}%</span><button type="button" aria-label="Zoom in" onClick={() => zoomBy(1.25)}>+</button>
              <button type="button" onClick={() => fitTo(null)}>Fit</button><button type="button" onClick={() => { const s = canvasSize(), v = viewRef.current, cx = (s.w / 2 - v.x) / v.k, cy = (s.h / 2 - v.y) / v.k; setView({k: 1, x: s.w / 2 - cx, y: s.h / 2 - cy}); }}>100%</button></div>
            <button type="button" className="pl-g-btn" onClick={toggleFull}>{full ? 'Exit full screen' : 'Full screen'}</button>
            <button type="button" className="pl-g-btn" aria-pressed={diffOpen} disabled={!model} onClick={() => setDiffOpen(!diffOpen)}>{diffOpen ? 'Hide A/B diff' : 'A/B diff'}</button>
            {dock === 'hidden' && <button type="button" className="pl-g-btn" onClick={() => setDock('side')}>Show video</button>}
          </div>
          {banner && <div className={'pl-g-banner pl-g-banner-' + banner.tone} data-ui="1" role="status"><span>{banner.text}</span><button type="button" className="pl-g-btn pl-g-btn-primary" onClick={banner.act}>{banner.btn}</button></div>}
          {slotNote && <div className="pl-g-note" data-ui="1" role="status">{slotNote}</div>}
          {g?.cursor?.clamped_to_now && <div className="pl-g-note pl-g-note-low" data-ui="1">Cursor is ahead of the ledger; the graph is clamped to now, never extrapolated.</div>}
          {inspNode && <Inspector key={inspNode.id} node={inspNode} onClose={() => { setInsp(null); setTlItem(null); }}
            onDetailed={() => { const id = inspNode.linkTo || inspNode.id; setInsp(null); setTlItem(null); setFocusNode(id); setDetail('detailed'); centerOn(id, layouts.detailed, Math.max(.75, viewRef.current.k)); }}
            onTrace={() => { setTrace(trace === inspNode.arm ? null : inspNode.arm); setInsp(null); }} traceActive={trace === inspNode.arm} traceReason={traceReason(inspNode)} />}
          {diff && !inspNode && <DiffPanel hidden={!!hidePredictions} diff={diff} when={(review ? 'at cursor ' : 'at display time ') + fmt(review ? t : g?.cursor?.session_time_s)} onClose={() => setDiffOpen(false)} />}
          {!narrow && <div className="pl-g-legend" data-ui="1"><span><i className="pl-g-lg-a" />ARM A</span><span><i className="pl-g-lg-b" />ARM B</span><span><i className="pl-g-lg-s" />SHARED</span><span><i className="pl-g-lg-x" />EXCLUDED / WITHHELD</span><span><i className="pl-g-lg-f" />INACTIVE</span><span>BRIGHT = RECORD JUST CHANGED</span></div>}
          {dockShown && dock !== 'side' && !narrow && dockEl}
        </div>
        {(sideDock || (dockShown && narrow)) && dockEl}
      </div>
      {review && <ReplayTimeline tl={timelineSlot?.data} status={timelineSlot?.status} error={timelineSlot?.error} t={t} duration={duration} onSeek={onSeek} playing={playing} onTogglePlay={onTogglePlay} windowRange={windowRange} hide={!!hidePredictions} fastPlayback={fastPlayback} onInspect={inspectTimeline} />}
    </section>;
  }

  // Replay arms: the stored analyzed frame of each arm's latest live output that
  // had arrived by the cursor. Offline analysis outputs are never shown here.
  function replayArmsAt(replayData, t) {
    if (!replayData?.outputs) return {};
    if (finite(replayData.at_ms) && replayData.at_ms > Math.round(t * 1000)) return {};
    const D = Data();
    const runs = D.normalizeDetail ? D.normalizeDetail({outputs: replayData.outputs}).runs : [];
    const pick = arm => runs.filter(r => r.arm === arm && r.mode === 'live' && (r.event_kind || r.kind) !== 'reasoning.output' && (r.arm_snapshot || r.result?.arm_snapshot) &&
      finite(r.available_at_s) && r.available_at_s <= t + 1e-9)
      .sort((a, b) => a.available_at_s - b.available_at_s).pop();
    const out = {};
    for (const arm of ['A', 'B']) { const r = pick(arm); if (r) out[arm] = r.arm_snapshot || r.result?.arm_snapshot; }
    return out;
  }
  // Timeline range for a cursor: the whole session up to 300 s, else a 240 s
  // window that moves when the cursor comes within 30 s of an inner edge.
  function timelineRange(win, ms, durationMs) {
    const inside = !!win && ms >= (win.from <= 0 ? 0 : win.from + 30000) && ms <= (win.to >= durationMs ? Infinity : win.to - 30000);
    if (inside) return win;
    const from = Math.max(0, Math.round(ms - 120000));
    return {from, to: Math.min(Math.ceil(durationMs), from + 240000)};
  }

  // Host: owns the graph/timeline/replay reads for the view and renders the workspace.
  function GraphHost(props) {
    const {C, lab, mode, session, t, duration, playing, speed = 1} = props;
    const sid = session?.id;
    const review = mode === 'replay';
    const epoch = lab.projectionEpoch || 0;
    useEffect(() => {
      if (review || !sid || !C.watchGraph) return;
      return C.watchGraph(sid);
    }, [review, sid, lab.generation]);
    const bucket = Math.floor(Math.max(0, t) * 2);
    // Fast playback reads nothing; the cursor's graph is fetched on pause.
    const fast = playing && speed > 1;
    useEffect(() => {
      if (!review || !sid || !C.graphAt || fast) return;
      C.graphAt(sid, Math.round(t * 1000));
      C.replayAt?.(sid, Math.round(t * 1000));
    }, [review, sid, bucket, lab.generation, epoch, fast]);
    const winRef = useRef(null);
    const long = duration > 300;
    useEffect(() => {
      if (!review || !sid || !C.timelineWindow) return;
      if (!long) { C.timelineWindow(sid); return; }
      const own = winRef.current && winRef.current.sid === sid && winRef.current.generation === lab.generation && winRef.current.epoch === epoch ? winRef.current : null;
      const next = timelineRange(own, t * 1000, duration * 1000);
      if (next === own) return;
      winRef.current = {...next, sid, generation: lab.generation, epoch};
      C.timelineWindow(sid, next.from, next.to);
    }, [review, sid, long, Math.floor(t / 5), lab.generation, epoch]);
    let graphSlot = review ? (lab.graphReplay?.session_id === sid ? lab.graphReplay : null) : (lab.graph?.session_id === sid ? lab.graph : null);
    // Never show a later cursor's graph after a backward seek.
    if (review && graphSlot?.data && finite(graphSlot.data.cursor?.at_ms) && graphSlot.data.cursor.at_ms > Math.round(t * 1000))
      graphSlot = {...graphSlot, data: null, status: fast ? 'paused' : 'loading'};
    const timelineSlot = review && lab.timeline?.session_id === sid ? lab.timeline : null;
    const replayData = review && lab.replayOutputs?.session_id === sid ? lab.replayOutputs.data : null;
    const replayArms = useMemo(() => replayArmsAt(replayData, t), [replayData, Math.floor(t * 10)]);
    return <GraphWorkspace {...props} graphSlot={graphSlot} timelineSlot={timelineSlot} replayArms={replayArms} windowRange={long} fastPlayback={review && fast} />;
  }

  global.HomePerceptionLabGraph = Object.freeze({GraphHost, GraphWorkspace, model: Object.freeze({buildModel, stateOf, simpleOf, traceOf, diffOf, layout, edgePaths, jsonLines, detailOf, variedFields, replayArmsAt, timelineRange, timelineRows, maskPredictions, frameAgeLines, pausedAgeLines, lifecycleText, exclusionsText}), stats: () => ({worldRenders})});
})(window);

"""Optional per-person JEV viewing projection; never a decision authority."""
from copy import deepcopy
import json
from reasoning import canonical, digest, finite

SCHEMA = 'home-lab-view-person-judgments-1'
FLAGS = dict(viewing_only=True, identity_authority=False, geometry_authority=False,
             engagement_authority=False, physical_permission=False)


def attach_person_judgments(store, session, result, now):
    if session.get('state') != 'recording' or not result.get('arms') or not finite(now):
        return result
    with store.lock:
        rows = store.db.execute("SELECT payload FROM events WHERE session_id=? AND kind='reasoning.person_output' "
                                "ORDER BY id DESC LIMIT 64", (session['id'],)).fetchall()
    if not rows:
        return result
    result = deepcopy(result)
    for shown in result['arms'].values():
        people = shown.get('persons2d') if isinstance(shown, dict) else None
        semantics = people.get('semantics') if isinstance(people, dict) else None
        if not isinstance(semantics, dict):
            continue
        for row in rows:
            output = json.loads(row['payload'])
            frozen = output.get('person_context', {})
            if canonical(frozen.get('packet')) != canonical(semantics):
                continue
            if (output.get('schema') != 'home-lab-person-jev-output-1' or output.get('version') != 1
                    or output.get('status') not in ('ready', 'abstain') or output.get('fresh_on_arrival') is not True
                    or output.get('physical_permission') is not False
                    or not all(finite(output.get(k)) for k in ('observed_at', 'available_at', 'valid_until'))
                    or not semantics['available_at'] <= output['available_at'] <= now < output['valid_until'] <= semantics['valid_until']
                    or output['observed_at'] != semantics['observed_at']):
                break
            provenance, diagnostics = output.get('provenance', {}), output.get('diagnostics', {})
            packet = diagnostics.get('request')
            slots, subjects = frozen.get('slots'), output.get('subjects')
            if (not isinstance(packet, dict) or provenance.get('input_digest') != digest(packet)
                    or provenance.get('response_version') != 'jev-1.13.0'
                    or not isinstance(slots, list) or not isinstance(subjects, list)
                    or not 1 <= len(slots) == len(subjects) <= 2):
                break
            mapped = {subject.get('subject_slot'): subject for subject in subjects}
            if len(mapped) != len(subjects) or set(mapped) != {s.get('subject_slot') for s in slots}:
                break
            values = []
            invalid = False
            for slot in slots:
                subject = mapped[slot['subject_slot']]
                if any(subject.get(k) != slot.get(k) for k in ('instance_id', 'track_ref')):
                    invalid = True
                    break
                judgment = subject.get('judgment')
                available = subject.get('status') == 'ready' and isinstance(judgment, dict) and judgment.get('activity') != 'insufficient_evidence'
                values.append(dict(instance_id=slot['instance_id'], track_ref=slot['track_ref'],
                                   status='available' if available else 'unknown',
                                   reason=None if available else 'insufficient_evidence',
                                   judgment=deepcopy(judgment) if available else None))
            if invalid:
                break
            people['judgments'] = dict(schema=SCHEMA, version=1, basis=deepcopy(semantics['basis']),
                model=dict(request_id=output['request_id'], packet_sha256=provenance['input_digest'], response_version='jev-1.13.0'),
                input_model={k: semantics['model'][k] for k in ('request_id', 'body_sha256')},
                evidence={k: frozen[k] for k in ('semantics_event_id', 'semantics_payload_digest', 'visual_event_id', 'visual_payload_digest')},
                observed_at=output['observed_at'], available_at=output['available_at'], valid_until=output['valid_until'],
                subjects=values, **FLAGS)
            break
    return result

"""Fixed commissioning plus actual retained stereo anchors; no source owner.

This private assembler supplies the existing desk publisher's immutable inputs.
Neither a browser nor an operator request can replace its commissioning records,
independent association method, or fixed media-retention capability.
"""
from copy import deepcopy
import threading

from journal import canonical, digest
from lab_desk_capability import TrustedDeskCapability
from lab_live_desk_admission import INPUT_KEYS, STEREO_INPUT_KEYS, DeskAdmissionRefusal
from live_capture import _original_digest


COMMISSIONING_KEYS = INPUT_KEYS - {'anchor', 'capture_ref', 'capture_original', 'capture_record'}


def _need(condition, reason):
    if not condition:
        raise DeskAdmissionRefusal(reason)


class LabStereoDeskCapability(TrustedDeskCapability):
    def __init__(self, *, anchor_source, commissioning, policy, lab_binding_digest,
                 media_archive, clock):
        from lab_live_desk_anchor import LabLiveDeskAnchor
        from live_den_mount import _runtime_module
        _runtime_module()
        from home_den_runtime.lab_stereo_media import LabStereoMedia
        _need(isinstance(anchor_source, LabLiveDeskAnchor)
              and isinstance(media_archive, LabStereoMedia)
              and isinstance(commissioning, dict) and set(commissioning) == COMMISSIONING_KEYS
              and isinstance(policy, dict) and type(policy.get('version')) is int and policy['version'] in (2, 3, 4)
              and policy.get('schema') == 'home-lab-live-desk-admission-policy-' + str(policy['version'])
              and callable(clock)
              and all(callable(getattr(media_archive, name, None)) for name in
                      ('retain_media', 'resolve_retained', 'media_refs', 'quiesce', 'finalize')),
              'fixed_stereo_desk_capabilities_required')
        self.commissioning, self.policy = deepcopy(commissioning), deepcopy(policy)
        self.scope = deepcopy(commissioning['binding']['scope'])
        self.expected = {'scope': deepcopy(self.scope), 'desk_policy_digest': digest(policy),
                         'lab_binding_digest': lab_binding_digest}
        self.anchor_binding = {'scope': deepcopy(self.scope), 'lab_binding_digest': lab_binding_digest,
                               'stereo_policy_ref': deepcopy(policy['stereo_policy_ref'])}
        if policy['version'] == 3:
            _need(policy.get('original_family') == 'existing-api-assessment-1', 'fixed_assessment_family_required')
            self.anchor_binding.update(original_family=policy['original_family'],
                                       assessment_policy_sha256=policy['assessment_policy_sha256'])
            _need(all(media_archive.binding.get(k) == policy[k] for k in
                      ('original_family', 'assessment_policy_sha256')), 'fixed_assessment_media_binding_required')
        elif policy['version'] == 4:
            _need(policy.get('original_family') == 'existing-api-bundled-rgb-1', 'fixed_bundled_rgb_family_required')
            self.anchor_binding.update(original_family=policy['original_family'],
                                       bundle_input_policy_sha256=policy['bundle_input_policy_sha256'])
            _need(all(media_archive.binding.get(k) == policy[k] for k in
                      ('original_family', 'bundle_input_policy_sha256')), 'fixed_bundled_rgb_media_binding_required')
        else:
            _need('original_family' not in media_archive.binding, 'unexpected_assessment_media_family')
        _need(anchor_source.binding() == self.anchor_binding, 'fixed_stereo_anchor_binding_required')
        _need(media_archive.binding['scope'] == self.scope
              and media_archive.binding['lab_binding_digest'] == lab_binding_digest
              and media_archive.binding['camera_id'] == commissioning['registration']['camera_id'],
              'fixed_stereo_media_binding_required')
        self.anchor_source, self.media_archive, self.clock = anchor_source, media_archive, clock
        self.pin = digest([self.commissioning, self.policy, self.expected])
        self.originals = {}
        self.lock, self.closed, self.retainer_attached = threading.Lock(), False, False
        self.raw_ack_unconfirmed = False

    def attach_retainer(self, callback):
        _need(callable(callback) and self.lock.acquire(False), 'stereo_desk_retainer_install_busy')
        try:
            self.binding()
            _need(not self.closed and not self.retainer_attached and not self.originals,
                  'stereo_desk_retainer_already_installed_or_closed')
            self.anchor_source.install_original_retainer(callback)
            self.retainer_attached = True
        finally:
            self.lock.release()

    def binding(self):
        _need(self.pin == digest([self.commissioning, self.policy, self.expected])
              and self.anchor_source.binding() == self.anchor_binding,
              'stereo_desk_binding_changed')
        return deepcopy(self.expected)

    def select(self, descriptor):
        if not self.lock.acquire(False):
            return {'status': 'unavailable', 'reason': 'stereo_desk_busy'}
        try:
            self.binding()
            if self.closed:
                return {'status': 'unavailable', 'reason': 'stereo_desk_quiesced'}
            _need(self.retainer_attached, 'stereo_desk_factory_retainer_required')
            _need(descriptor.get('scope') == self.scope
                  and descriptor.get('lab_binding_digest') == self.expected['lab_binding_digest'],
                  'stereo_desk_capture_binding_mismatch')
            selected = self.anchor_source.select(deepcopy(descriptor), received_at=self.clock())
            _need(isinstance(selected, dict), 'closed_stereo_anchor_result_required')
            if selected.get('status') == 'unavailable' or (self.policy['version'] in (3, 4) and selected.get('status') == 'pending'):
                _need(set(selected) == {'status', 'reason'}, 'closed_stereo_anchor_result_required')
                return deepcopy(selected)
            _need(set(selected) == {'status', 'anchor', 'stereo_support', 'stereo_qualification'}
                  and selected['status'] == 'selected', 'closed_stereo_anchor_result_required')
            _need(not self.closed, 'stereo_desk_quiesced')
            anchor = selected['anchor']
            value = deepcopy(self.commissioning)
            b = value['binding']
            # A new source/person epoch requires its own independent assessment;
            # this adapter never repins the old person's assessment automatically.
            _need(anchor['scope'] == self.scope and anchor['person_ref'] == b['person_ref']
                  and anchor['method_ref'] == self.policy['association_method_ref']
                  and value['assessment']['person_ref'] == anchor['person_ref'],
                  'stereo_desk_independent_person_applicability_required')
            original_ref = {**deepcopy(b['anchor_original_ref']), 'receipt_id': anchor['id'],
                'digest': _original_digest(anchor, limit=512 * 1024),
                'observed_at': anchor['observed_at'], 'available_at': anchor['available_at'],
                'clock_mapping_id': anchor['clock']['mapping_id']}
            b.update(source_generation=anchor['source_generation'], source_position=deepcopy(anchor['source_position']),
                     parent_publication_refs=[deepcopy(descriptor['publication_ref'])],
                     anchor_original_ref=deepcopy(original_ref))
            value.update(anchor=deepcopy(anchor), capture_ref=deepcopy(descriptor['publication_ref']),
                capture_original=deepcopy(descriptor['original']), capture_record=deepcopy(descriptor['original_record']),
                stereo_support=deepcopy(selected['stereo_support']),
                stereo_qualification=deepcopy(selected['stereo_qualification']))
            _need(set(value) == STEREO_INPUT_KEYS, 'complete_stereo_desk_originals_required')
            identity = canonical(original_ref)
            if identity in self.originals:
                _need(self.originals[identity] == value, 'stereo_desk_original_identity_conflict')
            else:
                _need(len(self.originals) < 24, 'stereo_desk_session_budget_exhausted')
                self.originals[identity] = value
            return {'status': 'selected', 'anchor_ref': deepcopy(original_ref)}
        finally:
            self.lock.release()

    def resolve_inputs(self, anchor_ref):
        # Pure lookup inside Journal's admission transaction. All I/O and
        # qualification happened before selection returned this exact ref.
        self.binding()
        return deepcopy(self.originals[canonical(anchor_ref)])

    def resolve_media(self, media_ref):
        return self.media_archive.resolve_retained(deepcopy(media_ref))

    def quiesce(self):
        self.closed = True
        receipts = []
        for port in (self.anchor_source, self.media_archive):
            try:
                receipts.append(port.quiesce())
            except Exception:
                receipts.append(None)
        valid = all(isinstance(r, dict) and set(r) == {'status', 'in_flight'}
                    and r['status'] in ('quiescent', 'unconfirmed') and type(r['in_flight']) is bool
                    for r in receipts)
        if valid and receipts[0] == {'status': 'unconfirmed', 'in_flight': False}:
            self.raw_ack_unconfirmed = True
        # Private original ACK uncertainty is not an in-flight Journal append.
        # Admission can be quiescent so safety withdrawal/drain still proceed;
        # finalization separately refuses a complete durable result below.
        busy = self.lock.locked() or not valid or any(r['in_flight'] for r in receipts)
        return {'status': 'unconfirmed' if busy else 'quiescent', 'in_flight': busy}

    def finalize_media(self):
        if self.quiesce()['in_flight']:
            return {'status': 'incomplete', 'reason': 'stereo_desk_selection_inflight'}
        media = self.media_archive.finalize(expected_refs=self.media_archive.media_refs())
        if media.get('status') != 'finalized':
            return media
        anchors = self.anchor_source.finalize()
        if self.raw_ack_unconfirmed or anchors != {'status': 'finalized', 'buffers_cleared': True}:
            return {**media, 'status': 'incomplete', 'reason': 'stereo_anchor_finalization_unconfirmed',
                    'raw_original_ack_status': 'unconfirmed', 'buffers_cleared': False}
        self.originals.clear()
        return {**media, 'buffers_cleared': True}

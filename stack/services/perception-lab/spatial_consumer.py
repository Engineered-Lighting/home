"""Trusted associated-depth input to the existing room/reasoning event stream.

Association is a separate provisioned geometric operation. This consumer does
not infer that an RGB box occupies the same pixels in rectified-left depth.
RGB floor contacts use only the registration's validated floor homography.
"""
from copy import deepcopy
import json
from geometry import floor_projection, _finite
from registered_depth import RegisteredDepth, MAX_VALIDITY_S
from room_context import validate_registration
from room_evidence import publish_room
from persisted_evidence import observation_record

# Reasons an upstream association may report instead of a surface. They are
# recorded in room.sampling, never dropped and never turned into positions.
ASSOCIATION_ABSTENTIONS = frozenset({
    'no_projected_support', 'region_outside_frame', 'region_too_small', 'no_depth_frame_in_window',
    'depth_geometry_unvalidated', 'frame_intrinsics_changed', 'insufficient_valid_support',
    'insufficient_inliers', 'depth_discontinuity', 'no_person_region', 'track_order_conflict',
    'floor_contact_not_visible', 'beyond_floor_horizon', 'person_region_limit'})
CONTACT_KINDS = frozenset({'box_bottom_centre', 'ankle_midpoint'})
# A box bottom may be a furniture edge in front of the person, never the floor.
VISIBLE_CONTACT_KINDS = frozenset({'ankle_midpoint'})


class SpatialConsumer:
    def __init__(self, store, calibration, registration, room_map, *, association_revision,
                 max_age_s=.5, max_skew_s=.04):
        validate_registration(registration, room_map)
        if not isinstance(association_revision, str) or not 1 <= len(association_revision) <= 256:
            raise ValueError('provisioned association revision required')
        # Experiment configuration only: it can tighten, never extend, freshness.
        if not _finite([max_age_s, max_skew_s]) or not 0 <= max_age_s <= 1 or not 0 <= max_skew_s <= .1:
            raise ValueError('bounded depth age and RGB skew configuration required')
        for key, cal_key in (('site_id','site_id'), ('camera_id','camera_id'),
                             ('calibration_version','id'), ('mount_revision','mount_revision'),
                             ('source_frame','frame')):
            if registration[key] != calibration.get(cal_key):
                raise ValueError('depth and room calibration binding mismatch')
        self.store = store
        self.camera_id = registration['camera_id']
        self.calibration, self.registration, self.room_map = deepcopy((calibration, registration, room_map))
        self.association_revision = association_revision
        self.max_age_s, self.max_skew_s = max_age_s, max_skew_s
        self.reader = RegisteredDepth(store)

    def _floor(self, sid, source, association, now):
        homography = self.registration.get('floor_homography')
        if homography is None:
            raise ValueError('validated RGB floor homography required')
        pixel = association.get('contact_pixel')
        uncertainty = association.get('pixel_uncertainty_px')
        if (not isinstance(pixel, list) or len(pixel) != 2 or not _finite(pixel)
                or not 0 <= pixel[0] <= homography['width'] or not 0 <= pixel[1] <= homography['height']
                or association.get('contact_kind') not in CONTACT_KINDS
                or not _finite([uncertainty]) or not 0 <= uncertainty <= 64):
            raise ValueError('registered RGB floor contact pixel required')
        unknown = lambda reason: {'status': 'unknown', 'reason': reason, 'depth_m': None}
        # A hidden, truncated or box-bottom contact is not a floor position.
        if association.get('contact_visible') is not True or association['contact_kind'] not in VISIBLE_CONTACT_KINDS:
            return unknown('floor_contact_not_visible')
        expiry = min(source['valid_until'], source['observed_at']+MAX_VALIDITY_S)
        if now > expiry:
            return unknown('outside_valid_observation_interval')
        estimate = floor_projection(pixel, homography['matrix'], calibration_version=homography['id'],
            reference_error_m=homography['error_bound_m'], target_frame=self.registration['target_frame'],
            pixel_uncertainty_px=uncertainty)
        if estimate['status'] != 'estimated':
            return estimate
        return {**estimate, 'contact_kind': association['contact_kind'], 'contact_pixel': list(pixel),
            'floor_contact': True, 'mount_revision': self.registration['mount_revision'],
            'session_id': sid, 'site_id': source['site_id'], 'camera_id': source['camera_id'],
            'frame_id': source.get('frame_id'), 'capture_epoch': source.get('capture_epoch'),
            'media_ids': list(source.get('media_ids') or []), 'observed_at': source['observed_at'],
            'available_at': now, 'valid_until': expiry, 'processing_version': 'rgb-floor-contact-v1'}

    def ingest(self, sid, associations, *, generation):
        if not isinstance(associations, list) or len(associations) > 64:
            raise ValueError('bounded associated surfaces required')
        now = self.store.clock()
        session = self.store.get_session(sid)
        points, abstentions, covered = [], [], set()
        for association in associations:
            abstention, count = association.get('abstention'), association.get('count')
            if (association.get('association_revision') != self.association_revision
                    or type(association.get('source_event_id')) is not int
                    or (abstention is not None and abstention not in ASSOCIATION_ABSTENTIONS)
                    or (count is not None and (abstention is None or type(count) is not int or not 1 <= count <= 1024))
                    or (abstention is None and (not isinstance(association.get('track_id'), str)
                        or not 1 <= len(association['track_id']) <= 256))):
                raise ValueError('registered association and source event required')
            with self.store.lock:
                row = self.store.db.execute("SELECT kind,payload FROM events WHERE session_id=? AND id=?",
                    (sid, association['source_event_id'])).fetchone()
            if not row or row['kind'] != 'perception.output':
                raise ValueError('recorded specialist observation required')
            source = json.loads(row['payload'])
            if source.get('viewing_only') is True:
                raise ValueError('viewing-only specialist evidence has no operational geometry')
            evidence = observation_record(association['source_event_id'], row['kind'], source, session)
            if (evidence is None or source.get('generation') != generation
                    or not source['observed_at'] <= source['available_at'] <= now <= source['valid_until']):
                raise ValueError('current generation specialist evidence required')
            covered.add(source['arm'])
            refusal = {'arm':source['arm'], 'track_id':association.get('track_id'),
                       'source_event_id':association['source_event_id']}
            if abstention is not None:
                abstentions.append({**refusal, 'reason':abstention, **({'count':count} if count is not None else {})})
                continue
            localization = association.get('localization', 'stereo')
            if localization == 'floor_projection':
                sample = self._floor(sid, source, association, now)
            elif localization == 'stereo':
                frame = association['depth_frame']
                if (frame.get('session_id') != sid or frame.get('capture_epoch') != source.get('capture_epoch')
                        or frame.get('site_id') != source.get('site_id') or frame.get('camera_id') != source.get('camera_id')):
                    raise ValueError('associated depth belongs to another source')
                # Age, skew and expiry derive from recorded source times, never
                # a caller assertion or the specialist's processing latency.
                sample = self.reader.surface(frame, association['roi'], self.calibration,
                    roi_frame=association['roi_frame'], as_of=now, reference_observed_at=source['observed_at'],
                    valid_until=source['valid_until'], floor_matrix=self.registration['matrix'],
                    floor_axes=self.room_map['floor_axes'], visible=association.get('visible') is True,
                    mask=association.get('mask'), max_age_s=self.max_age_s, max_skew_s=self.max_skew_s)
            else:
                raise ValueError('explicit stereo or floor_projection localization required')
            if sample['status'] not in ('measured', 'estimated'):
                abstentions.append({**refusal, 'reason':sample['reason']})
                continue
            points.append({**sample, 'arm':source['arm'], 'track_id':association['track_id'],
                'generation':generation, 'evidence_roots':evidence['evidence_roots']})
        # Atomic publication makes retry after a failed write safe; an explicit
        # empty frame clears the covered arms' older positions instead of
        # extending their lifetime. Other arms keep their own expiry; an empty
        # call remains a full clearing snapshot.
        arms = sorted(covered) if associations else None
        context = publish_room(self.store, sid, points, self.registration, self.room_map,
            generation=generation, as_of=now, arms=arms, sampling_receipt={'arms':arms,'generation':generation,
                'available_at':now, 'association_revision':self.association_revision,
                'source_event_ids':[a['source_event_id'] for a in associations],
                'measured':len(points), 'abstentions':abstentions,
                'max_age_s':self.max_age_s, 'max_skew_s':self.max_skew_s})
        return context

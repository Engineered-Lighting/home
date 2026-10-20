"""Write one verified camera-session successor and its review from local files.

Offline only. The inputs are the three reviewed v1 originals plus the
verification read of one camera session (/calibration, one /frame.npz, the RGB
image and the landmark overlay), or authored test files of the same shape. It
opens no host, camera, network or model, edits no original, and writes nothing
unless the unchanged runtime binder accepts the result.

  python -I -B tools/generate-apartment-view-alignment-successor.py \
    --alignment A --review R --source-candidate C --calibration CAL --npz NPZ \
    --rgb RGB --landmark-overlay PNG --verified-epoch E --verified-at T \
    --reviewed-by NAME --landmark-overlay-matches --out-dir DIR     [--person-boxes BOXES.json] [--mount-replacement REPLACEMENT.json --mount-replacement-frames-reviewed]

MOUNT-SIGNATURE inputs (optional): --person-boxes is the verification frame's
own model output (or a list of {label, x, y, w, h} boxes in 640x360 RGB pixels);
those people are zeroed in the depth plane before check_mount. --mount-replacement
is a reviewed empty-room signature from tools/generate-mount-signature-replacement.py;
when given it is carried by the successor and is the check_mount reference.
check_mount and its limits are unchanged.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / 'stack/services/perception-lab'
for folder in (LAB, ROOT / 'tools/shared-home', ROOT / 'stack/services/home-context'):
    if str(folder) not in sys.path:
        sys.path.append(str(folder))
SUCCESSOR_NAME = 'view-alignment-successor.json'
REVIEW_NAME = 'view-alignment-successor-review.json'
PUBLIC_RECEIPT = 'home-apartment-view-alignment-successor-receipt-1'


def sha(raw): return hashlib.sha256(raw).hexdigest()
def document(value): return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def strict_json(raw, maximum):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= maximum:
        raise ValueError('verification_input_size_invalid')
    def unique(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError('verification_input_duplicate_key')
            out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=unique)


def build(*, alignment, review, source_candidate, calibration, npz, rgb, landmark_overlay,
          verified_epoch, verified_at, reviewed_by, landmark_overlay_matches,
          person_boxes=None, mount_replacement=None, mount_replacement_frames_reviewed=False):
    """Return (successor bytes, review bytes, public receipt) for exact input bytes.

    The overlay comparison is the reviewer's own attestation; this tool never
    judges images. check_mount and the calibration id are recomputed here.
    """
    import apartment_view_alignment as view
    from bundle_ingest import calibration_content_id
    from bundle_rgb_input import decode_bundle
    from spatial_validation import check_mount
    if landmark_overlay_matches is not True:
        raise ValueError('landmark_overlay_review_required')
    if not all(isinstance(raw, bytes) and raw for raw in (rgb, landmark_overlay)):
        raise ValueError('verification_image_bytes_required')
    bound = view.bind_view_alignment(alignment_original_bytes=alignment, review_original_bytes=review,
                                     source_candidate_original_bytes=source_candidate)
    a = bound.document
    camera_calibration = strict_json(calibration, 24576)
    depth, _, metadata = decode_bundle(npz)
    # One verification read: calibration and depth come from the named session.
    if not isinstance(camera_calibration, dict) or camera_calibration.get('session') != verified_epoch \
            or metadata.get('session') != verified_epoch:
        raise ValueError('verification_read_session_mismatch')
    import mount_signature_inputs as inputs
    replacement = None
    reference = a['mount']['signature']
    if mount_replacement is not None:
        if mount_replacement_frames_reviewed is not True:
            raise ValueError('mount_replacement_review_required')
        replacement = strict_json(mount_replacement, 16384)
        reference = inputs.validate_replacement(replacement, revision=a['mount']['revision'], digest=view.digest)
    boxes = []
    if person_boxes is not None:
        value = strict_json(person_boxes, 2 * 1024**2)
        boxes = inputs.person_boxes(value if isinstance(value, dict) else {'boxes': value})
    masked_pixels = 0
    if boxes:
        model = inputs.nominal_camera_model(metadata, camera_calibration, a['camera']['calibration_content_id'])
        depth, masked_pixels = inputs.mask_people(depth, model, boxes)
    mount = check_mount(reference, depth)
    verification = dict(verified_epoch=verified_epoch, calibration_sha256=sha(calibration),
        calibration_content_id=calibration_content_id(camera_calibration), npz_sha256=sha(npz),
        check_mount={key: mount.get(key) for key in ('status', 'compared_cells', 'changed_cells')},
        rgb_sha256=sha(rgb), landmark_overlay_sha256=sha(landmark_overlay), verified_at=verified_at)
    successor = document(dict(schema=view.SUCCESSOR_SCHEMA, version=1, status='verified_camera_session_successor',
        label=view.LABEL, scope=a['scope'],
        predecessor=dict(alignment_sha256=bound.alignment_sha256, review_sha256=bound.review_sha256,
            source_candidate_sha256=a['source_candidate']['sha256'], source_fit_status='refused',
            reviewed_capture_epoch=a['camera']['capture_epoch']),
        matrix_sha256=a['matrix_sha256'],
        mount=dict(revision=a['mount']['revision'], signature_sha256=a['mount']['signature_sha256']),
        camera=dict(camera_id='den', source_frame='rectified_left',
            calibration_content_id=a['camera']['calibration_content_id']),
        verification=verification, metric_error_bound_m=None, owner_or_physical_calibration=False,
        **({view.REPLACEMENT_KEY: replacement} if replacement is not None else {}), **view.NO_AUTHORITY))
    successor_review = document(dict(schema=view.SUCCESSOR_REVIEW_SCHEMA, version=1,
        status='reviewed_for_verified_camera_session', successor_sha256=sha(successor),
        predecessor_alignment_sha256=bound.alignment_sha256, verified_epoch=verified_epoch,
        verification_sha256s=[verification[key] for key in
            ('calibration_sha256', 'npz_sha256', 'rgb_sha256', 'landmark_overlay_sha256')],
        landmark_overlay_matches=True, metric_error_unknown=True, owner_or_physical_calibration=False,
        reviewed_by=reviewed_by, **({'mount_replacement_sha256': view.digest(replacement),
            'mount_replacement_frames_reviewed': True} if replacement is not None else {}), **view.NO_AUTHORITY))
    # The runtime binder is the only acceptance; a refusal names its reason.
    accepted = view.bind_view_alignment_successor(alignment_original_bytes=alignment, review_original_bytes=review,
        source_candidate_original_bytes=source_candidate, successor_original_bytes=successor,
        successor_review_original_bytes=successor_review)
    receipt = dict(schema=PUBLIC_RECEIPT, view_alignment_sha256=accepted.alignment_sha256,
        view_alignment_review_sha256=accepted.review_sha256, view_alignment_successor_sha256=accepted.successor_sha256,
        view_alignment_successor_review_sha256=accepted.successor_review_sha256, verified_epoch=accepted.verified_epoch,
        calibration_content_id=verification['calibration_content_id'], check_mount=verification['check_mount'],
        input_sha256s={key: verification[key] for key in
            ('calibration_sha256', 'npz_sha256', 'rgb_sha256', 'landmark_overlay_sha256')},
        mount_inputs=dict(person_boxes=len(boxes), masked_person_pixels=masked_pixels,
            reference='reviewed_replacement' if replacement is not None else 'reviewed_signature',
            replacement_sha256=view.digest(replacement) if replacement is not None else None),
        host_operations=0, camera_operations=0, model_operations=0)
    return successor, successor_review, receipt


def write_private(path, raw):
    """Create one owner-only file; never replace an existing document."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ('alignment', 'review', 'source-candidate', 'calibration', 'npz', 'rgb', 'landmark-overlay'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--verified-epoch', required=True)
    parser.add_argument('--verified-at', required=True, help='UTC time of the verification read, e.g. 2026-10-19T12:00:00Z')
    parser.add_argument('--reviewed-by', required=True)
    parser.add_argument('--landmark-overlay-matches', action='store_true',
        help='the reviewer compared the landmark overlay with the reviewed fit image')
    parser.add_argument('--out-dir', required=True, type=Path)
    parser.add_argument('--person-boxes', type=Path, help='model output or person boxes of the verification frame (JSON)')
    parser.add_argument('--mount-replacement', type=Path, help='reviewed empty-room replacement signature (JSON)')
    parser.add_argument('--mount-replacement-frames-reviewed', action='store_true',
        help='the reviewer looked at the replacement frames: nobody near the camera, trial lighting')
    args = parser.parse_args(argv)
    successor, review, receipt = build(
        **{key: getattr(args, key).read_bytes() for key in
           ('alignment', 'review', 'source_candidate', 'calibration', 'npz', 'rgb', 'landmark_overlay')},
        verified_epoch=args.verified_epoch, verified_at=args.verified_at, reviewed_by=args.reviewed_by,
        landmark_overlay_matches=args.landmark_overlay_matches,
        person_boxes=args.person_boxes.read_bytes() if args.person_boxes else None,
        mount_replacement=args.mount_replacement.read_bytes() if args.mount_replacement else None,
        mount_replacement_frames_reviewed=args.mount_replacement_frames_reviewed)
    targets = (args.out_dir / SUCCESSOR_NAME, args.out_dir / REVIEW_NAME)
    if any(path.exists() for path in targets):
        raise FileExistsError('successor documents already exist; choose a new directory')
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for path, raw in zip(targets, (successor, review)):
        write_private(path, raw)
    sys.stdout.write(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
    return receipt


if __name__ == '__main__':
    main()

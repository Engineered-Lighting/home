"""Pinned V-JEPA 2.1 artifacts, probe sources and host gates for the LA Lab worker.

Literals only (no imports beyond hashlib/json), so the supervisor, the worker
and the Victoria tests can all read the same values. Every value here came from
a read-only audit of EngineeredLightingServer1 on 2026-09-29: sha256sum of the
files, `git rev-parse HEAD` of ~/vjepa-probe-0 (clean tree) and a CPU-only,
no-bytecode import of the probe modules to list their exact source closure.
Re-verified the same day at probe commit 67853d2f (the occupancy-gating commit
after 791ee868 touched only probe0/home_v2/live_* and tests_ml): every SOURCES
digest, head, the NTU head, the encoder size and the venv versions unchanged.

PINS and the model/profile identities must equal
stack/services/perception-lab/vjepa_scene.py (checked by test_vjepa_worker).
"""
import hashlib
import json

PROBE_ROOT = "/home/marcelo-lima/vjepa-probe-0"
PROBE_PYTHON = PROBE_ROOT + "/.venv/bin/python"
PROBE_COMMIT = "67853d2f85a5d4a072023b90bc48043a3994bf76"
BUNDLE_LINK = "/opt/vjepa-v2/current"
BUNDLE_ROOT = "/opt/vjepa-v2/bundles/61ab0b0c0be0e741daba599af1cb8593e85fa3875fc36fbbc24711defb9f9659"
META_COMMIT = "204698b45b3712590f06245fbfba32d3be539812"
META_ROOT = BUNDLE_ROOT + "/store/source/" + META_COMMIT
ENCODER_SHA256 = "848a77c33cc9e6649ed2119c9bea1e2c569bcdab9539ff3e7c02ccc2959ddf4d"
ENCODER_BYTES = 1664223428
ENCODER_PATH = BUNDLE_ROOT + "/store/objects/" + ENCODER_SHA256
ENCODER_SELECTOR = "ema_encoder"

FITS = "runs/home-v2/expanded-optimization-20260908/fits/"
HEADS = {
    "posture": ((FITS + "posture-household-excludenone-seed0.pt", "d2b405993fe22251d4417239fcd2535e9144617dd02eb9c6df00a7ac491d934a"),
                (FITS + "posture-household-excludenone-seed1.pt", "628c1e6094c129f939f9a834bf61fb181bfed080bb41c860048c8f7a95e2e039"),
                (FITS + "posture-household-excludenone-seed2.pt", "1afec41295fe0f44c45d9423a471e8c813a68452849b8fd38b333a47983f613b")),
    "activity": ((FITS + "activity-mixed_standard-excludenone-seed0.pt", "618d80c3b71efb3862b0398f19fcadbb20c0a284f9bed8c002d36c5e912dc40d"),
                 (FITS + "activity-mixed_standard-excludenone-seed1.pt", "3478f4f4e9d58320b72c7f059cd8592c6af4dc9f3d2c60857f3bb5d15d58e8a2"),
                 (FITS + "activity-mixed_standard-excludenone-seed2.pt", "11f6bb03c3b0a48a9847ef09d55a5d76eea6ccbb6ed33c3e42234f79c881066d")),
}
HEAD_SCHEMA = "home-v2-optimized-development-readout/v1"
NTU_PATH = "/srv/data/probe-0/v3/finetune-unfreeze-0.pt"
NTU_SHA256 = "1dcfa5ec8328c1a4e006bff575784819d56b4109d5120141030881dffa6dc3af"

# Exact import closure of v3_finetune, v4_home_runtime, probe0.home_v2.models and
# the pinned Meta vision_transformer (site-packages excluded). The worker checks
# these digests before import and refuses any other module loaded from either root.
SOURCES = {
    "probe:probe0/__init__.py": "52eedb7db8e42db37f45a06fce24fc3f086ac4fd45bef067b6372d81a75e8d23",
    "probe:probe0/canonical.py": "0e49c13f5f419920e4200780afcfbfc40c71aae3ab93c746d4c9365613b4df63",
    "probe:probe0/contracts.py": "bd23db30e7515effa5edae66d56952f2756e75f190c2cec648e56dc241ef08a9",
    "probe:probe0/digest.py": "34c3d4f883cd4db47dd88a51391ba4d236e65c9b12e0af30c13408ef362ac755",
    "probe:probe0/errors.py": "e33b8498536f494ca032f004e05b5f2ab2e2ffd6506c5626d2c120e164f8d932",
    "probe:probe0/home_v2/__init__.py": "8e65d7c10c57c31dac4e5964ccbdf5308d4c96e5d8606ee62d451921117bf592",
    "probe:probe0/home_v2/contracts.py": "c6581acf70e4b0c26ac9c6430a2592343b0f2ddb4f966f72af123579c92c3bc5",
    "probe:probe0/home_v2/models.py": "a1958710e580a9872fe9eb178fe8141076fa2f2832859fdb9977067f184e8510",
    "probe:probe0/home_v2/preprocessing.py": "e61b3408d933aca09607e6e1299fbc7ebfa2ff0e65ebc77a987127ec8ad2ad83",
    "probe:probe0/provenance.py": "0bde8416fd30c8c40c3c67403d4199691f6193a592501333a537472c0f7a1fc4",
    "probe:probe0/v2/__init__.py": "8991a4afaba4bc8dedb8eab92cd6e0875c095346471e4b6ed05d412d75fb00dc",
    "probe:probe0/v2/encoder.py": "44742300d38daee1b52db6eaa8bd0032508c9163713a4436b79f3acbcaa35c17",
    "probe:spec/probe-0-source-provenance.json": "f73ac6e613acfcca19563c21b8f4bd17fd58dbfd843277830e1fba3c767536d0",
    "probe:tools/pooling_common.py": "b57974383206419e18736c2c71afdda591bb84d83f106fd67cbd7de62e34c8dc",
    "probe:tools/v3_finetune.py": "496d0d1b63c15b99a9931dd87081813c2cb2d47bbe6d5c4fc52691c626d5c8d6",
    "probe:tools/v4_home_runtime.py": "9019e1493dcffe3b8ea80cf48f7f7d12091a4ae1a29344b39ef84b2c2569632f",
    "meta:app/vjepa_2_1/models/utils/modules.py": "64be6a87bd9f18d385f4e44186db3347d1665e18a1f0511d51d3b305531562e2",
    "meta:app/vjepa_2_1/models/utils/patch_embed.py": "29e11ab97ab3ccdef107d6a7d0d7b374b58e712076cc3561f07b7e603c9b5165",
    "meta:app/vjepa_2_1/models/vision_transformer.py": "d2932eabeba684d8f558302a13cfd4be70a0170ee5112f5a794652d0a29089b9",
    "meta:src/masks/utils.py": "833f111a0fa5ffdbd3a6412e2dace2517c3c178f49c14f8bb631d9f6a070dfd0",
    "meta:src/utils/tensors.py": "782b58bd2af456e184750e5318ab773105108383f61b280fe4c7a90f46add2c8",
}
# Observed in ~/vjepa-probe-0/.venv (checked, never installed or changed).
RUNTIME = {"torch": "2.11.0+cu128", "numpy": "2.5.2", "pillow": "12.3.0"}

# probe0/home_v2/live_models.Jepa.profile, which validate_profile accepts unchanged.
PROFILE = {"frame_count": 8, "span_ns": 3_500_000_000, "resolution": 384, "sampling": "causal_endpoint_uniform/v1",
           "normalization": {"mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225]},
           "crop_producer": "tracked-union-box-square-1.5-pil-bilinear/v1", "precision": "bfloat16",
           "raw_grid": [4, 24, 24], "raw_token_count": 2304}
POSTURES = ("sitting", "standing", "walking", "lying_down", "bending", "exercising_pose")
ACTIVITIES = ("reading", "eating", "drinking", "cooking", "cleaning", "watching_tv", "on_computer", "using_phone",
              "phone_call", "exercising", "sleeping")
NTU_CLASSES = ("brushing_hair", "brushing_teeth", "check_watch", "clapping", "drinking", "eating", "fall", "fan_self",
               "hand_waving", "hopping", "jump_up", "phone_call", "phone_or_tablet", "reading", "remove_jacket",
               "remove_shoe", "sit_down", "stand_up", "typing", "wear_jacket", "wear_shoe", "wipe_face", "writing")
NTU_MAP = {"computer_work": ("typing",), "phone_use": ("phone_or_tablet", "phone_call"), "reading": ("reading", "writing")}

# The Victoria-visible identity (same literals as vjepa_scene).
PINS = {
    "encoder": {"file": "vjepa2_1_vitb_dist_vitG_384.pt", "selector": ENCODER_SELECTOR, "sha256": ENCODER_SHA256,
                "bytes": ENCODER_BYTES, "bundle": BUNDLE_ROOT.rsplit("/", 1)[1], "meta_source_commit": META_COMMIT},
    "probe_commit": PROBE_COMMIT,
    "posture": [sha for _, sha in HEADS["posture"]],
    "activity": [sha for _, sha in HEADS["activity"]],
    "ntu": NTU_SHA256,
}
MODEL_VERSION = "vjepa2.1-vitb-384-frozen+home-v2-20260908+ntu23-e13"
INPUT_PROFILE = "den-preview-640x360-8f-3.5s-union-box-square-1.5-bilinear-384-bf16/v1"
REQUEST_SCHEMA = "vjepa-clip-request-1"
RESULT_SCHEMA = "vjepa-clip-result-1"
IMAGE_SIZE = (640, 360)

# Host and GPU admission (checked by the launcher before systemd-run and by
# the worker again immediately before loading any weights).
GPU_MIN_FREE_MIB = 6144
GPU_MAX_TEMPERATURE_C = 80
HOST_MIN_AVAILABLE_BYTES = 8 * 1024**3
ALLOCATOR_LIMIT_BYTES = 3 * 1024**3
WORKER_MEMORY_MAX = "6G"
WORKER_MEMORY_BYTES = 6 * 1024**3


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


ARTIFACT_SHA256 = hashlib.sha256(canonical(PINS).encode()).hexdigest()


def profile(engine_epoch, runtime_revision):
    return {"model_version": MODEL_VERSION, "artifact_sha256": ARTIFACT_SHA256, "runtime_revision": runtime_revision,
            "engine_epoch": engine_epoch, "input_profile": INPUT_PROFILE}

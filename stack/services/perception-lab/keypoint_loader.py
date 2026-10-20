"""Incumbent loader pinned by read-only LA source inspection on 2026-09-26.

Calling load_sdk loads a model: the worker must obtain resource/workload admission
before calling it. Importing this module imports no Torch modules and does no IO.
"""
from importlib.metadata import version, distribution
from worker_artifacts import verified_artifact

CHECKPOINT_SHA256="fc266e953d2b302cdcbb9ae66f71f6b0d4649928bf02dc573961e361e4918926"
LOADER_SHA256="bf36999735a556697bab25ec4f1cb8b6e32c58178b86e80ba040c372fb9d022d"
TORCH_VERSION="2.11.0+cu128"
VISION_VERSION="0.26.0+cu128"


def construct(torch, backbone_factory, detector_factory, frozen_norm, overwrite_eps, path, device):
    # Reproduce the installed COCO_V1 factory's trained-backbone branch explicitly.
    # weights=None on the generic detector factory would choose BatchNorm instead;
    # weights='DEFAULT' may attempt network access. Neither route is equivalent.
    backbone=backbone_factory(backbone_name="resnet50",weights=None,
        norm_layer=frozen_norm,trainable_layers=3)
    model=detector_factory(backbone,2,num_keypoints=17)
    state=torch.load(str(path),map_location="cpu",weights_only=True)
    model.load_state_dict(state,strict=True)
    overwrite_eps(model,0.0)
    return model.eval().requires_grad_(False).to(device)


def load_sdk(path, manifest):
    if (manifest.get("checkpoint")!="KeypointRCNN_ResNet50_FPN_Weights.COCO_V1"
            or manifest.get("sha256")!=CHECKPOINT_SHA256 or manifest.get("precision")!="fp32"
            or manifest.get("torch_version")!=TORCH_VERSION
            or manifest.get("torchvision_version")!=VISION_VERSION
            or manifest.get("device") not in {"cpu","cuda:0"}):
        raise ValueError("exact incumbent detector manifest required")
    target=verified_artifact(path,CHECKPOINT_SHA256,".pth",maximum_bytes=512*1024**2)
    if version("torch")!=TORCH_VERSION or version("torchvision")!=VISION_VERSION:
        raise ValueError("incumbent runtime version mismatch")
    source=distribution("torchvision").locate_file("torchvision/models/detection/keypoint_rcnn.py")
    verified_artifact(source,LOADER_SHA256,".py",maximum_bytes=1024**2)
    import torch
    from torchvision.models.detection import KeypointRCNN
    from torchvision.models.detection.backbone_utils import resnet_fpn_backbone
    from torchvision.models.detection._utils import overwrite_eps
    from torchvision.ops.misc import FrozenBatchNorm2d
    if manifest["device"]=="cuda:0" and not torch.cuda.is_available():
        raise ValueError("requested incumbent GPU unavailable")
    return construct(torch,resnet_fpn_backbone,KeypointRCNN,FrozenBatchNorm2d,
        overwrite_eps,target,manifest["device"])

"""Pinned incumbent YOLOX runtime. Importing this module loads no model.

Only an admitted worker may call load_sdk. CPU is a placement experiment, not
the installed CUDA configuration. Crop scheduling/tracking belong to the worker.
"""
from importlib.metadata import version, distribution
from worker_artifacts import verified_artifact

CHECKPOINT_SHA256='7860ae79de6c89a3c1eb72ae9a2756c0ccfbe04b7791bb5880afabd97855a411'
SOURCE_SHA256={
    'tools/base.py':'0d287088838eee78cc8cb5ef722a53148f7c100bf73eed16457ce3f9f40b272a',
    'tools/object_detection/post_processings.py':'8b6bc31fd13c3af3ba64a6983efdc927448bfb17245b7502602847e0d2b2775e',
    'tools/object_detection/yolox.py':'f72b756611450ebddbb6d593ca6d7d1398a3f10b682b86e3f16db4525a072fb9',
}


def construct(ort, detector_class, path, device, *, intra_op_threads=4):
    if device not in {'cpu','cuda:0'}:
        raise ValueError('explicit supported device required')
    if type(intra_op_threads) is not int or not 1<=intra_op_threads<=4:
        raise ValueError('bounded ORT intra-op thread count required')
    expected='CPUExecutionProvider' if device=='cpu' else 'CUDAExecutionProvider'
    if expected not in ort.get_available_providers():
        raise ValueError('requested YOLOX provider unavailable')
    options=ort.SessionOptions()
    options.intra_op_num_threads=intra_op_threads
    options.inter_op_num_threads=1
    options.log_severity_level=3
    providers=[expected] if device=='cpu' else [(expected,{
        'cudnn_conv_algo_search':'HEURISTIC','cudnn_conv_use_max_workspace':'0',
        'gpu_mem_limit':2*1024**3,'arena_extend_strategy':'kSameAsRequested'})]
    session=ort.InferenceSession(str(path),sess_options=options,providers=providers)
    if session.get_providers()[0]!=expected:
        raise ValueError('YOLOX provider fallback refused')
    session.disable_fallback()
    # Preserve installed configured_tool's initialization without invoking the
    # library constructor's download/session defaults. Math stays in RTMLib.
    tool=detector_class.__new__(detector_class)
    tool.session,tool.backend,tool.device=session,'onnxruntime','cpu' if device=='cpu' else 'cuda'
    tool.onnx_model,tool.model_input_size=str(path),(640,640)
    tool.mean=tool.std=None
    tool.det_mode,tool.nms_thr,tool.score_thr='multiclass',.45,.45
    # Receipts report the session options actually applied, not an assumption.
    tool.runtime_threads={'intra_op':options.intra_op_num_threads,'inter_op':options.inter_op_num_threads}
    return tool


def load_sdk(path,manifest,*,intra_op_threads=4):
    if (manifest.get('checkpoint')!='yolox_l.onnx' or manifest.get('sha256')!=CHECKPOINT_SHA256
            or manifest.get('precision')!='fp32' or manifest.get('runtime_version')!='0.0.16'
            or manifest.get('onnxruntime_version')!='1.23.2'
            or manifest.get('device') not in {'cpu','cuda:0'}):
        raise ValueError('exact incumbent YOLOX manifest required')
    target=verified_artifact(path,CHECKPOINT_SHA256,'.onnx',maximum_bytes=512*1024**2)
    if version('rtmlib')!='0.0.16':
        raise ValueError('YOLOX runtime version mismatch')
    package=distribution('rtmlib')
    for relative,sha in SOURCE_SHA256.items():
        verified_artifact(package.locate_file('rtmlib/'+relative),sha,'.py',maximum_bytes=1024**2)
    import onnxruntime as ort
    if ort.__version__!='1.23.2':
        raise ValueError('YOLOX ONNX Runtime mismatch')
    if manifest['device']=='cuda:0':
        ort.preload_dlls()
    from rtmlib import YOLOX
    return construct(ort,YOLOX,target,manifest['device'],intra_op_threads=intra_op_threads)

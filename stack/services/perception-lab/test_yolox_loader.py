import unittest
from types import SimpleNamespace
from yolox_loader import construct,load_sdk


class YoloLoaderTests(unittest.TestCase):
    def runtime(self,available=('CPUExecutionProvider','CUDAExecutionProvider'),actual=None):
        calls=[]
        def session(path,**kwargs):
            calls.append((path,kwargs))
            requested=kwargs['providers'][0]
            provider=requested[0] if isinstance(requested,tuple) else requested
            return SimpleNamespace(get_providers=lambda:[actual or provider],
                disable_fallback=lambda:calls.append('disabled'))
        return SimpleNamespace(SessionOptions=SimpleNamespace,InferenceSession=session,
            get_available_providers=lambda:available),calls

    def test_pinned_cuda_session_without_constructor_or_download(self):
        class Detector:
            def __init__(self,*args,**kwargs): raise AssertionError('constructor must not run')
        ort,calls=self.runtime()
        tool=construct(ort,Detector,'local.onnx','cuda:0')
        self.assertEqual((tool.model_input_size,tool.det_mode,tool.score_thr,tool.nms_thr),
                         ((640,640),'multiclass',.45,.45))
        self.assertIsNone(tool.mean); self.assertIsNone(tool.std)
        options=calls[0][1]['sess_options']
        self.assertEqual((options.intra_op_num_threads,options.inter_op_num_threads,options.log_severity_level),(4,1,3))
        self.assertEqual(tool.runtime_threads,{'intra_op':4,'inter_op':1})
        self.assertEqual(calls[0][1]['providers'],[('CUDAExecutionProvider',{
            'cudnn_conv_algo_search':'HEURISTIC','cudnn_conv_use_max_workspace':'0',
            'gpu_mem_limit':2147483648,'arena_extend_strategy':'kSameAsRequested'})])
        self.assertEqual(calls[-1],'disabled')

    def test_explicit_thread_count_is_bounded_applied_and_recorded(self):
        class Detector: pass
        ort,calls=self.runtime()
        tool=construct(ort,Detector,'local.onnx','cpu',intra_op_threads=1)
        self.assertEqual(calls[0][1]['sess_options'].intra_op_num_threads,1)
        self.assertEqual(tool.runtime_threads,{'intra_op':1,'inter_op':1})
        for value in (0,5,1.0,'2',True):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'thread'):
                construct(ort,Detector,'local.onnx','cpu',intra_op_threads=value)
        self.assertEqual(len([c for c in calls if c!='disabled']),1)

    def test_unavailable_or_silent_fallback_refused(self):
        class Detector: pass
        ort,calls=self.runtime(available=('CPUExecutionProvider',))
        with self.assertRaisesRegex(ValueError,'unavailable'): construct(ort,Detector,'local.onnx','cuda:0')
        self.assertEqual(calls,[])
        ort,calls=self.runtime(actual='CPUExecutionProvider')
        with self.assertRaisesRegex(ValueError,'fallback'): construct(ort,Detector,'local.onnx','cuda:0')

    def test_cpu_is_explicit_and_unpinned_manifest_fails_before_io(self):
        class Detector: pass
        ort,calls=self.runtime()
        self.assertEqual(construct(ort,Detector,'local.onnx','cpu').device,'cpu')
        self.assertEqual(calls[0][1]['providers'],['CPUExecutionProvider'])
        with self.assertRaisesRegex(ValueError,'manifest'): load_sdk('missing.onnx',{})


if __name__=='__main__': unittest.main()

import unittest
from types import SimpleNamespace
from keypoint_loader import construct,load_sdk


class KeypointLoaderTests(unittest.TestCase):
    def test_local_construction_matches_inspected_trained_branch(self):
        calls=[]
        class Model:
            def load_state_dict(self,state,strict): calls.append(("state",state,strict))
            def eval(self): calls.append("eval"); return self
            def requires_grad_(self,value): calls.append(("grad",value)); return self
            def to(self,device): calls.append(("device",device)); return self
        model=Model(); norm=object()
        def backbone(**kwargs): calls.append(("backbone",kwargs)); return "backbone"
        def detector(backbone,classes,**kwargs):
            calls.append(("detector",backbone,classes,kwargs)); return model
        def load(path,**kwargs): calls.append(("load",path,kwargs)); return "local-state"
        def eps(target,value): self.assertIs(target,model); calls.append(("eps",value))
        self.assertIs(construct(SimpleNamespace(load=load),backbone,detector,norm,eps,"fixture.pth","cpu"),model)
        self.assertEqual(calls[0],("backbone",{"backbone_name":"resnet50","weights":None,"norm_layer":norm,"trainable_layers":3}))
        self.assertEqual(calls[1],("detector","backbone",2,{"num_keypoints":17}))
        self.assertEqual(calls[2],("load","fixture.pth",{"map_location":"cpu","weights_only":True}))
        self.assertEqual(calls[3:],[('state','local-state',True),('eps',0.0),'eval',('grad',False),('device','cpu')])

    def test_unpinned_manifest_rejected_before_file_or_sdk_access(self):
        with self.assertRaisesRegex(ValueError,"manifest"):
            load_sdk("not-a-file",{"checkpoint":"DEFAULT"})


if __name__=='__main__': unittest.main()

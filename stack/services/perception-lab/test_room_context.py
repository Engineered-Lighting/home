from copy import deepcopy
import unittest
from room_context import apartment_map,build_room_context
from reasoning import digest


class RoomContextTests(unittest.TestCase):
    def setUp(self):
        self.model={"revision":7,"meta":{"frame":"z_up_metric_floor0"},"zones":[{"id":"desk","name":"Desk area","floor_polygon":[[.5,1.5],[1.5,1.5],[1.5,2.5],[.5,2.5]]}],"targets":[{"id":"desk-surface","pos":[1,2,.75]}]}
        self.binding={"site_id":"victoria","map_id":"den","revision":"7","units":"metres","model_sha256":digest(self.model)}
        self.map=apartment_map(self.model,self.binding)
        self.registration={"site_id":"victoria","camera_id":"den","map_id":"den","map_version":"7","source_frame":"rectified_left","target_frame":"z_up_metric_floor0","calibration_version":"cal1","mount_revision":"mount1","transform_version":"tf1","status":"validated","units":"metres","validation_receipt":"fixture-only","error_bound_m":.02,"matrix":[[1,0,0,0],[0,0,1,0],[0,-1,0,0],[0,0,0,1]]}
        self.point={"site_id":"victoria","camera_id":"den","frame":"rectified_left","xyz_m":[1,-.5,2],"calibration_version":"cal1","mount_revision":"mount1","track_id":"t1","arm":"a","status":"measured","method":"stereo_surface","uncertainty_m":.03,"observed_at":100,"available_at":100,"valid_until":101}
        # RGB source pixels (640x360) -> map floor metres; fixture scale only.
        self.homography={"id":"fh1","matrix":[[.01,0,0],[0,.01,0],[0,0,1]],"source_frame":"rgb_preview_640x360","width":640,"height":360,"error_bound_m":.1,"validation_receipt":"fixture-only"}
        self.floor_point={"site_id":"victoria","camera_id":"den","frame":"z_up_metric_floor0","xyz_m":[1,2,0.0],"calibration_version":"fh1","mount_revision":"mount1","track_id":"t2","arm":"b","status":"estimated","method":"rgb_floor_contact","floor_contact":True,"uncertainty_m":.2,"observed_at":100,"available_at":100,"valid_until":101}

    def test_camera_point_uses_existing_xy_floor_without_changing_map(self):
        original=deepcopy(self.model)
        context=build_room_context([self.point],self.registration,self.map,as_of=100.5)
        self.assertEqual(context["people"][0]["pos"],[1,2,.5])
        self.assertEqual(context["floor_axes"],"xy")
        self.assertEqual(context["memberships"][0]["relation"],"inside")
        self.assertAlmostEqual(context["people"][0]["uncertainty_m"],.05)
        self.assertIsNone(context["people"][0]["identity"])
        self.assertEqual(self.model,original)

    def test_revision_camera_movement_and_cross_home_registration_are_rejected(self):
        for update in ({"site_id":"echo"},{"mount_revision":"mount2"},{"calibration_version":"cal2"}):
            with self.assertRaises(ValueError):
                build_room_context([{**self.point,**update}],self.registration,self.map,as_of=100)
        with self.assertRaises(ValueError):
            apartment_map({**self.model,"revision":8},self.binding)
        with self.assertRaises(ValueError):
            build_room_context([self.point],{**self.registration,"map_version":"8"},self.map,as_of=100)

    def test_stale_position_is_not_displayed_as_current(self):
        result=build_room_context([self.point],self.registration,self.map,as_of=102)
        self.assertEqual(result["people"],[])
        self.assertEqual(result["unavailable"][0]["reason"],"outside_valid_observation_interval")

    def test_axis_name_does_not_replace_physical_registration(self):
        reflected=deepcopy(self.registration)
        reflected["matrix"][2][1]=1
        with self.assertRaises(ValueError):build_room_context([self.point],reflected,self.map,as_of=100)
        with self.assertRaises(ValueError):build_room_context([self.point],{**self.registration,"validation_receipt":None},self.map,as_of=100)

    def test_late_geometry_does_not_leak_into_causal_map_or_zone_membership(self):
        point={**self.point,'available_at':100.75}
        early=build_room_context([point],self.registration,self.map,as_of=100.5)
        self.assertEqual(early['people'],[])
        self.assertEqual(early['memberships'],[])
        self.assertEqual(early['unavailable'][0]['reason'],'not_available_at_cutoff')
        ready=build_room_context([point],self.registration,self.map,as_of=100.75)
        self.assertEqual(ready['people'][0]['available_at'],100.75)
        self.assertEqual(ready['memberships'][0]['available_at'],100.75)
        self.assertEqual(ready['memberships'][0]['transform_version'],'tf1')
        for value in (None,99,float('nan')):
            with self.assertRaises(ValueError):
                build_room_context([{**point,'available_at':value}],self.registration,self.map,as_of=100.75)

    def test_rgb_floor_contact_is_already_in_map_frame_and_keeps_its_uncertainty(self):
        registration={**self.registration,"floor_homography":self.homography}
        context=build_room_context([self.point,self.floor_point],registration,self.map,as_of=100.5)
        floor=next(p for p in context["people"] if p["method"]=="rgb_floor_contact")
        self.assertEqual(floor["pos"],[1,2,0.0])
        self.assertEqual(floor["uncertainty_m"],.2)
        self.assertEqual((floor["calibration_version"],floor["homography_version"]),("fh1","fh1"))
        self.assertIsNone(floor["identity"])
        relation=next(m for m in context["memberships"] if m["track_id"]=="t2")
        self.assertEqual((relation["relation"],relation["method"]),("inside","rgb_floor_contact"))
        # Uncertainty wider than the distance to the edge abstains as boundary.
        wide=build_room_context([{**self.floor_point,"uncertainty_m":.6}],registration,self.map,as_of=100.5)
        self.assertEqual(wide["memberships"][0]["relation"],"boundary")

    def test_rgb_floor_contact_requires_its_own_validated_homography_and_frame(self):
        registration={**self.registration,"floor_homography":self.homography}
        for point,reg in (({**self.floor_point},self.registration),
                          ({**self.floor_point,"frame":"rectified_left"},registration),
                          ({**self.floor_point,"calibration_version":"cal1"},registration),
                          ({**self.floor_point,"status":"measured"},registration),
                          ({**self.floor_point,"floor_contact":None},registration),
                          ({**self.point,"calibration_version":"fh1"},registration)):
            with self.subTest(point=point),self.assertRaises(ValueError):
                build_room_context([point],reg,self.map,as_of=100.5)
        for change in ({"validation_receipt":None},{"matrix":[[1,0],[0,1]]},{"width":0},{"error_bound_m":-1}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                build_room_context([self.point],{**self.registration,"floor_homography":{**self.homography,**change}},self.map,as_of=100.5)

    def _anchored(self,**fields):
        model={**self.model,"targets":[{"id":"anchor-monitor","name":"monitor","pos":[1,2,1.1],**fields}]}
        binding={**self.binding,"model_sha256":digest(model)}
        return apartment_map(model,binding)["targets"][0]

    def test_named_anchor_fields_survive_normalization(self):
        # Plain targets keep their original three keys.
        self.assertEqual(self.map["targets"],[{"id":"desk-surface","label":"desk-surface","pos":[1,2,.75]}])
        point=self._anchored(category="monitor",shape="point",radius_m=.35)
        self.assertEqual(point,{"id":"anchor-monitor","label":"monitor","pos":[1,2,1.1],
                                "category":"monitor","shape":"point","radius_m":.35})
        box=self._anchored(category="bookshelf",shape="box",size_m=[.9,.3,1.8])
        self.assertEqual(box["size_m"],[.9,.3,1.8])
        # Home's surface targets carry a two-element size.
        self.assertEqual(self._anchored(shape="surface",size_m=[1.8,.9])["size_m"],[1.8,.9])

    def test_invalid_named_anchor_fields_refuse_the_map(self):
        for fields in ({"radius_m":0},{"radius_m":float("nan")},{"radius_m":"1"},{"size_m":[1,2,3,4]},
                       {"size_m":[1,-1,1]},{"category":""},{"shape":7}):
            with self.subTest(fields=fields),self.assertRaises(ValueError):
                self._anchored(**fields)


if __name__=="__main__":unittest.main()

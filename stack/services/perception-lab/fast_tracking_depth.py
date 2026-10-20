"""One exact camera pair, compressed in RAM and projected on the LA consumer."""
import base64
import hashlib
import json
import math
import zlib

def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()

def encode_paired_depth_frame(value):
    raw=value['depth'].astype('<u2',copy=False).tobytes(order='C')
    if sha(raw)!=value['identity']['depth_sha256']:raise ValueError('tracking_depth_integrity')
    compressed=zlib.compress(raw,1)
    return dict(encoding='zlib-u16le',shape=[400,640],raw_sha256=sha(raw),encoded_sha256=sha(compressed),
                data_base64=base64.b64encode(compressed).decode())

def decode_paired_depth_frame(frame):
    import numpy as np
    from bundle_ingest import calibration_content_id
    identity=frame['identity'];payload=frame.get('paired_depth')
    if (frame.get('schema')!='home-fast-camera-2' or sha(encoded(identity))!=frame.get('frame_key')
            or not isinstance(payload,dict) or set(payload)!={'encoding','shape','raw_sha256','encoded_sha256','data_base64'}
            or payload['encoding']!='zlib-u16le' or payload['shape']!=[400,640]
            or not isinstance(payload['data_base64'],str) or len(payload['data_base64'])>700000):
        raise ValueError('tracking_depth_contract')
    try:
        compressed=base64.b64decode(payload['data_base64'],validate=True)
        if not 0<len(compressed)<=512256 or sha(compressed)!=payload['encoded_sha256']:raise ValueError()
        decoder=zlib.decompressobj();raw=decoder.decompress(compressed,512001)
        if (len(raw)!=512000 or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
                or sha(raw)!=payload['raw_sha256'] or sha(raw)!=identity.get('depth_sha256')):raise ValueError()
    except (ValueError,zlib.error):raise ValueError('tracking_depth_integrity') from None
    metadata=frame.get('projection_metadata');calibration=frame.get('calibration')
    if (not isinstance(metadata,dict) or len(encoded(metadata))>32768
            or sha(encoded(metadata))!=identity.get('projection_metadata_sha256')
            or not isinstance(calibration,dict) or len(encoded(calibration))>24576
            or calibration.get('session')!=identity['epoch']
            or calibration_content_id(calibration)!=identity['calibration_content_id']):
        raise ValueError('tracking_depth_camera_binding')
    frames=metadata.get('frames',{})
    for name,width,height,sequence,timestamp in [('rgb',640,360,'sequence','rgb_device_timestamp_s'),('depth',640,400,'depth_sequence','depth_device_timestamp_s')]:
        m=frames.get(name,{})
        if ((m.get('width'),m.get('height'))!=(width,height) or m.get('sequence')!=identity[sequence]
                or m.get('device_timestamp_seconds')!=identity[timestamp] or m.get('transformation_valid') is not True):
            raise ValueError('tracking_depth_camera_binding')
    depth=np.frombuffer(raw,dtype='<u2').reshape(400,640);depth.flags.writeable=False
    return dict(depth=depth,metadata=metadata,calibration=calibration,identity=identity)

class DepthProjector:
    def __init__(self):self.model=None;self.model_key=None
    def project(self,value,poses):
        if not isinstance(poses,list) or len(poses)>2:raise ValueError('tracking_person_limit')
        points=[];seen_instances=set()
        for pose in poses:
            if not isinstance(pose,dict) or pose.get('layout')!='coco17' or pose.get('instance_id') in seen_instances:
                raise ValueError('tracking_pose_identity')
            seen_instances.add(pose['instance_id']);seen=set();joints=pose.get('joints')
            if not isinstance(joints,list) or len(joints)!=17:raise ValueError('tracking_joint_layout')
            for joint in joints:
                index=joint.get('joint_id');pixel=joint.get('pixel')
                if type(index) is not int or not 0<=index<17 or index in seen:raise ValueError('tracking_joint_identity')
                seen.add(index)
                if joint.get('status')=='supported':
                    if (not isinstance(pixel,list) or len(pixel)!=2 or any(type(x) not in (int,float) or not math.isfinite(x) for x in pixel)
                            or not 0<=pixel[0]<640 or not 0<=pixel[1]<360):raise ValueError('tracking_joint_pixel')
                    points.append(pixel)
        from depth_association import camera_model,eeprom_rgb_from_rectified_left
        from head3d import DepthLifter
        model_key=sha(encoded([value['identity']['calibration_content_id'],
            [{k:value['metadata']['frames'][name].get(k) for k in ('intrinsics','source_distortion_coefficients','width','height')} for name in ('rgb','depth')]]))
        if self.model_key!=model_key:
            metadata=value['metadata'];calibration=value['calibration']
            reference=dict(id=value['identity']['calibration_content_id'],mount_revision='diagnostic_nominal_eeprom',
                reference_intrinsics=dict(rectified_left=metadata['frames']['depth']['intrinsics'],rgb=metadata['frames']['rgb']['intrinsics']),
                rgb_from_rectified_left=eeprom_rgb_from_rectified_left(calibration['eeprom']))
            self.model=camera_model(metadata,reference,measuring_reprojection=True);self.model_key=model_key
        lifter=None
        if points:
            roi=(min(p[0] for p in points)-4,min(p[1] for p in points)-4,max(p[0] for p in points)+4,max(p[1] for p in points)+4)
            lifter=DepthLifter(value['depth'],self.model,rgb_roi=roi)
        instances=[]
        for pose in poses:
            joints=[]
            for joint in pose['joints']:
                if joint['status']!='supported':continue
                sample=lifter.lift(*joint['pixel'],radius_px=4,min_support=3)
                if sample['status']=='measured':joints.append(dict(id=joint['joint_id'],camera_xyz_m=sample['xyz'],
                    depth_support=sample['support'],depth_spread_m=sample.get('spread_m'),pixel=joint['pixel']))
            instances.append(dict(instance_id=pose['instance_id'],joints=joints))
        return instances

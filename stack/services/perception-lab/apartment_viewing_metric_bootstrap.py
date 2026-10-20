"""Protected explicit opt-in for approximate Apartment metric viewing.

Absent configuration leaves JPEG viewing unchanged. This reads three bounded
owner-private alignment originals, plus an explicitly configured desk preview;
it starts no camera, clock publisher,
session, model or thread and does not alter the source fit's refused status.
A separate v2 policy adds one verified camera-session successor; the v1 file
and parser are untouched, so an older release simply ignores the v2 file.
"""
from copy import deepcopy
import hashlib,json,os,re,stat,time
from pathlib import Path,PurePosixPath
from host_home import JETSON_HOME

PRIVATE_PATH=JETSON_HOME + '/perception-lab/private'
PRIVATE=Path(PRIVATE_PATH)
POLICY_PATH=PRIVATE_PATH+'/apartment-viewing-metric.json'
SUCCESSOR_POLICY_PATH=PRIVATE_PATH+'/apartment-viewing-metric-v2.json'
DESK_PREVIEW_POLICY_PATH=PRIVATE_PATH+'/apartment-desk-preview.json'
SCHEMA='home-apartment-viewing-metric-source-1'
SCHEMA_V2='home-apartment-viewing-metric-source-2'
ROLES={'alignment','review','source_candidate'}
SUCCESSOR_ROLES={'successor','successor_review'}
HEX=re.compile('[0-9a-f]{64}')
def need(ok,reason):
    if not ok:raise ValueError(reason)
def policy(value):
    need(isinstance(value,dict) and set(value)=={'schema','version','enabled','files','pack','mount_revision'}
        and value['schema']==SCHEMA and type(value['version']) is int and value['version']==1
        and value['enabled'] is True,'exact metric viewing opt-in required')
    files=value['files'];need(isinstance(files,dict) and set(files)==ROLES,'three pinned private originals required')
    for entry in files.values():
        need(isinstance(entry,dict) and set(entry)=={'path','sha256'} and isinstance(entry['path'],str)
            and len(entry['path'])<=512 and isinstance(entry['sha256'],str) and HEX.fullmatch(entry['sha256']) is not None,
            'bounded pinned private original required')
        path=PurePosixPath(entry['path']);root=PurePosixPath(PRIVATE_PATH)
        need(path.is_absolute() and '..' not in path.parts and path.is_relative_to(root) and path!=root,
            'fixed owner-private original path required')
    pack=value['pack'];need(isinstance(pack,dict) and set(pack)=={'content_revision','registration_version','frame_sha256','collision_sha256'}
        and isinstance(pack['content_revision'],str) and 0<len(pack['content_revision'])<=256
        and type(pack['registration_version']) is int and pack['registration_version']>=0
        and all(isinstance(pack[k],str) and HEX.fullmatch(pack[k]) for k in ('frame_sha256','collision_sha256')),
        'exact active pack binding required')
    need(isinstance(value['mount_revision'],str) and 0<len(value['mount_revision'])<=256,'explicit private mount revision required')
    return deepcopy(value)

def _pinned_private(entry):
    need(isinstance(entry,dict) and set(entry)=={'path','sha256'} and isinstance(entry['path'],str)
        and len(entry['path'])<=512 and isinstance(entry['sha256'],str) and HEX.fullmatch(entry['sha256']) is not None,
        'bounded pinned private original required')
    path=PurePosixPath(entry['path']);root=PurePosixPath(PRIVATE_PATH)
    need(path.is_absolute() and '..' not in path.parts and path.is_relative_to(root) and path!=root,
        'fixed owner-private original path required')

def policy_v2(value):
    """Verified-session opt-in: the three reviewed originals plus a successor and its review."""
    need(isinstance(value,dict) and set(value)=={'schema','version','enabled','files','pack','mount_revision','timing_policy'}
        and value['schema']==SCHEMA_V2 and type(value['version']) is int and value['version']==2
        and value['enabled'] is True,'exact verified-session metric viewing opt-in required')
    # 'development' turns preview-path deadline refusals into named warnings
    # (owner decision, 2026-10-19); strict is the default and the 1726 rule.
    need(value['timing_policy'] in ('strict','development'),'explicit metric timing policy required')
    files=value['files'];need(isinstance(files,dict) and set(files)==ROLES|SUCCESSOR_ROLES,'five pinned private originals required')
    for role in sorted(SUCCESSOR_ROLES):_pinned_private(files[role])
    # The reviewed originals, pack and mount still pass the unchanged v1 parser.
    policy({**{k:v for k,v in value.items() if k!='timing_policy'},'schema':SCHEMA,'version':1,
        'files':{role:files[role] for role in ROLES}})
    return deepcopy(value)

def rollback_consistency(v1_value,v2_value):
    """Preflight only: removing the v2 file must restore exactly the reviewed v1 opt-in.

    The loader never calls this, so the running service does not depend on v1.
    """
    v1=policy(v1_value);v2=policy_v2(v2_value)
    mismatches=[role+'_pin_changed' for role in sorted(ROLES) if v1['files'][role]!=v2['files'][role]]
    mismatches+=[key+'_changed' for key in ('pack','mount_revision') if v1[key]!=v2[key]]
    return {'status':'rollback_inconsistent' if mismatches else 'consistent','mismatches':mismatches}

def read_successor_policy():
    """Strictly parse the fixed v2 policy; the operator preflight reads it too."""
    def unique(items):
        out={}
        for key,value in items:
            need(key not in out,'duplicate metric viewing policy key');out[key]=value
        return out
    return policy_v2(json.loads(_read_private(SUCCESSOR_POLICY_PATH),object_pairs_hook=unique))

def _read_private(path):
    need(os.name=='posix','private metric bootstrap requires the commissioned owner host')
    path=PurePosixPath(path);root=PurePosixPath(PRIVATE_PATH)
    need(path.is_absolute() and '..' not in path.parts and path.is_relative_to(root) and path!=root,
        'fixed owner-private original path required')
    # Walk using directory descriptors: a path/symlink swap between stat and
    # open must not redirect this private loader to another file.
    directory=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    file_descriptor=None
    try:
        for part in path.parts[1:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            os.close(directory);directory=child
            info=os.fstat(directory)
            need(stat.S_ISDIR(info.st_mode) and not info.st_mode&0o022,'writable private parent refused')
        file_descriptor=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory)
        info=os.fstat(file_descriptor)
        need(stat.S_ISREG(info.st_mode) and info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==0o600
            and 0<info.st_size<=131072,'owner-only bounded regular metric original required')
        chunks=[];remaining=131073
        while remaining:
            chunk=os.read(file_descriptor,remaining)
            if not chunk:break
            chunks.append(chunk);remaining-=len(chunk)
        raw=b''.join(chunks)
        final=os.fstat(file_descriptor)
        need((info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)==
             (final.st_dev,final.st_ino,final.st_size,final.st_mtime_ns),'private original changed during read')
    finally:
        if file_descriptor is not None:os.close(file_descriptor)
        os.close(directory)
    need(0<len(raw)<=131072,'bounded private metric original required')
    return raw

def load_optional_factory(*,client):
    """A missing fixed policy means legacy JPEG; an invalid one never falls back.

    No existing managed/auth/camera configuration schema is expanded. The old
    release ignores this separate file on rollback. Error text exposes no
    private paths or source coordinates through capabilities. A present v2
    policy governs instead of v1 and, like v1, never falls back when invalid.
    """
    try:os.lstat(SUCCESSOR_POLICY_PATH)
    except FileNotFoundError:pass
    except Exception:
        return None,{'present':True,'provisioned':False,'reason':'metric_viewing_policy_unavailable'}
    else:
        try:result=load_factory(read_successor_policy(),client=client)
        except Exception:
            return None,{'present':True,'provisioned':False,'reason':'metric_viewing_policy_invalid'}
        return result,{'present':True,'provisioned':True,'reason':None}
    try:os.lstat(POLICY_PATH)
    except FileNotFoundError:
        return None,{'present':False,'provisioned':False,'reason':'metric_viewing_not_configured'}
    except Exception:
        return None,{'present':True,'provisioned':False,'reason':'metric_viewing_policy_unavailable'}
    try:
        raw=_read_private(POLICY_PATH)
        value=json.loads(raw);result=load_factory(value,client=client)
    except Exception:
        return None,{'present':True,'provisioned':False,'reason':'metric_viewing_policy_invalid'}
    return result,{'present':True,'provisioned':True,'reason':None}

def load_optional_desk_preview(*,pack):
    """One explicit owner-configured preview; no operational desk authority.

    The saved candidate comes from the existing desk-target solver. Missing or
    invalid opt-in affects only this new sibling, preserving ordinary viewing.
    """
    try:os.lstat(DESK_PREVIEW_POLICY_PATH)
    except FileNotFoundError:
        return None,{'present':False,'provisioned':False,'reason':'desk_preview_not_configured'}
    except Exception:
        return None,{'present':True,'provisioned':False,'reason':'desk_preview_policy_unavailable'}
    try:
        def unique(items):
            out={}
            for key,value in items:
                need(key not in out,'duplicate desk preview policy key');out[key]=value
            return out
        value=json.loads(_read_private(DESK_PREVIEW_POLICY_PATH),object_pairs_hook=unique)
        need(isinstance(value,dict) and set(value)=={'schema','version','enabled','candidate'}
            and value['schema']=='home-apartment-desk-preview-source-1'
            and type(value['version']) is int and value['version']==1 and value['enabled'] is True,
            'explicit desk preview policy required')
        entry=value['candidate']
        need(isinstance(entry,dict) and set(entry)=={'path','sha256'}
            and isinstance(entry['path'],str) and len(entry['path'])<=512
            and isinstance(entry['sha256'],str) and HEX.fullmatch(entry['sha256']) is not None,
            'pinned desk preview candidate required')
        raw=_read_private(entry['path'])
        from apartment_desk_preview import bind_desk_preview
        result=bind_desk_preview(raw,entry['sha256'],pack)
    except Exception:
        return None,{'present':True,'provisioned':False,'reason':'desk_preview_policy_invalid'}
    return result,{'present':True,'provisioned':True,'reason':None,
        'virtual_preview_only':True,'physical_permission':False,'operational_geometry':False}


def load_alignment(value):
    """Read and bind the pinned private originals a v1 or v2 policy names.

    The operator preflight pairs this with camera_session_compatibility. It
    opens only the fixed owner-private files and contacts no camera.
    """
    successor=isinstance(value,dict) and value.get('schema')==SCHEMA_V2
    configured=policy_v2(value) if successor else policy(value)
    originals={}
    for role,entry in configured['files'].items():
        raw=_read_private(entry['path']);need(hashlib.sha256(raw).hexdigest()==entry['sha256'],'private metric original digest changed')
        originals[role]=raw
    from apartment_view_alignment import bind_view_alignment,bind_view_alignment_successor
    reviewed=dict(alignment_original_bytes=originals['alignment'],review_original_bytes=originals['review'],
        source_candidate_original_bytes=originals['source_candidate'])
    bound=(bind_view_alignment_successor(**reviewed,successor_original_bytes=originals['successor'],
        successor_review_original_bytes=originals['successor_review']) if successor else bind_view_alignment(**reviewed))
    doc=bound.document
    need(doc['scope']=={'site_id':'victoria','map_id':'victoria-den','room_id':'den'}
        and doc['pack']==configured['pack'] and doc['mount']['revision']==configured['mount_revision'],
        'private view alignment pack or mount binding changed')
    return configured,bound

def load_factory(value,*,client):
    configured,bound=load_alignment(value)
    from apartment_view_alignment import ApproximatePoseReceiptTracker
    # Only the immutable original byte aliases (three, or five with a verified
    # successor) survive this loader. The source owner charges them with its
    # one lease, registered PNGs and packet.
    def projection(owner):
        scope={'siteId':'victoria','contentRevision':configured['pack']['content_revision'],
            'frameDigest':configured['pack']['frame_sha256'],'registrationVersion':configured['pack']['registration_version'],
            'cameraId':'den','sessionId':owner.sid,'generation':owner.media_generation}
        return ApproximatePoseReceiptTracker(alignment=bound,scope=scope,
            active_collision_sha256=configured['pack']['collision_sha256'],current_mount_revision=configured['mount_revision'],
            monotonic=time.monotonic,max_hold_s=2.)
    from apartment_viewing_metric_source import MetricInputFactory
    desk_preview,desk_status=load_optional_desk_preview(pack=configured['pack'])
    result=MetricInputFactory(client,projection_factory=projection,desk_preview=desk_preview,
        timing_policy=configured.get('timing_policy','strict'))
    result.capability={'input':'same_npz_png','metric_depth':'diagnostic_nominal_eeprom','room_alignment':'approximate_visual_alignment',
        'view_alignment_sha256':bound.alignment_sha256,'view_alignment_review_sha256':bound.review_sha256,
        'physical_permission':False,'operational_geometry':False,'maximum_npz_reads_per_capture':1,'capture_hz_max':1}
    if configured['schema']==SCHEMA_V2:
        result.capability.update(view_alignment_successor_sha256=bound.successor_sha256,
            view_alignment_successor_review_sha256=bound.successor_review_sha256,verified_epoch=bound.verified_epoch,
            timing_policy=configured['timing_policy'])
    if desk_status['present']:result.capability['virtual_target_preview']=desk_status
    return result

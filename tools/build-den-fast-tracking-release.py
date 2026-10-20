"""Seal the reviewed fast-lane source closure. Never installs or starts it."""
import argparse,ast,hashlib,io,json
from pathlib import Path
import subprocess,tarfile

ROOT=Path(__file__).resolve().parents[1]
SERVICE=ROOT/'stack/services/perception-lab'
REVIEWED=frozenset('ambient_context,apartment_desk_preview,apartment_view_alignment,apartment_viewing_metric,apartment_viewing_metric_source,apartment_viewing_source,assessment_existing_api,bundle_ingest,bundle_rgb_input,chrony_preview_client,config,depth_association,fast_tracking_camera,fast_tracking_camera_entry,fast_tracking_runtime,geometry,head3d,host_home,jev_evidence,jev_semantics,jev_wire,keypoint_loader,keypoint_reference,keypoint_worker,la_detector_app,ownership,persisted_evidence,pose_composition,presentation,preview_client,preview_ingest,qwen_scene,reasoning,recorder,registered_image,remote_crops,remote_http,remote_receiver,remote_specialist,remote_transport,room_context,room_mapper,room_mesh,room_words,rtmpose_worker,rtmw3d_worker,runtime,session_budget,spatial_validation,staged_specialist,store,ultralytics_worker,view_person_association,vjepa_scene,worker_artifacts,yolox_loader,yolox_worker'.split(','))
ENTRYPOINTS={'fast_tracking_camera_entry':'main','la_detector_app':'create_fast_tracking_app'}
def sha(value):return hashlib.sha256(value).hexdigest()
def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sources(root=SERVICE):
    files={};pending=list(ENTRYPOINTS)
    while pending:
        name=pending.pop()
        if name in files:continue
        if name not in REVIEWED:raise ValueError('unreviewed local import: '+name)
        p=root/(name+'.py')
        if p.is_symlink() or not p.is_file() or p.stat().st_size>1024**2:raise ValueError('invalid source: '+name)
        raw=p.read_bytes();tree=ast.parse(raw)
        if name in ENTRYPOINTS and not any(isinstance(n,ast.FunctionDef) and n.name==ENTRYPOINTS[name] for n in tree.body):
            raise ValueError('required explicit factory absent')
        files[name]=raw
        for n in ast.walk(tree):
            imports=[a.name.split('.')[0] for a in n.names] if isinstance(n,ast.Import) else [n.module.split('.')[0]] if isinstance(n,ast.ImportFrom) and n.module else []
            for value in imports:
                if (root/(value+'.py')).exists() or (root/value).is_dir():pending.append(value)
    return {'service/'+name+'.py':raw for name,raw in sorted(files.items())}
def build(output,source_ref):
    files=sources();manifest={'schema':'home-fast-tracking-release-1','source_ref':source_ref,'entrypoints':ENTRYPOINTS,
        'camera_baseline_sha256':'d052fc217b2d412cb877cfb49608e7dcef4a52526932b8d036a51380accb6132',
        'files':{name:{'sha256':sha(raw),'bytes':len(raw),'mode':0o644} for name,raw in files.items()}}
    identifier=sha(encoded(manifest))[:16];manifest['release_id']=identifier
    raw_manifest=encoded(manifest)+b'\n';files['manifest.json']=raw_manifest
    output.mkdir(parents=True,exist_ok=False);archive=output/('den-fast-tracking-'+identifier+'.tar')
    with tarfile.open(archive,'w',format=tarfile.USTAR_FORMAT) as tar:
        for name,raw in sorted(files.items()):
            info=tarfile.TarInfo(name);info.size=len(raw);info.mode=0o644;info.mtime=0;tar.addfile(info,io.BytesIO(raw))
    (output/'manifest.json').write_bytes(raw_manifest)
    return {'release_id':identifier,'archive':str(archive),'archive_sha256':sha(archive.read_bytes()),'archive_bytes':archive.stat().st_size,'source_ref':source_ref,'modules':len(files)-1,'host_mutated':False}
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip():raise ValueError('seal committed clean source only')
    print(json.dumps(build(args.output,revision)))

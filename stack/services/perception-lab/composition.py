"""Reviewed Python composition of existing Lab workers; no dynamic imports/URLs."""
from analysis import SequentialAnalysis
from analysis_inputs import RegisteredAnalysisInputs
from analysis_jobs import AnalysisJobs
from artifacts import ArtifactPreparation
from bundle_ingest import BundleIngestor
from live_reasoning import LiveReasoning
from preview_client import LocalPreviewClient, LocalBundleClient
from preview_ingest import PreviewIngestor
from qwen_adapter import QwenAdapter, RegisteredAnalysisMedia
from recorder import StreamCopySupervisor
from service_runtime import ServiceRuntime
from specialist_registry import RegisteredSpecialists


def compose_runtime(store, *, sources, camera_credential=None, receiver_clock=None,
                    resolve_profiles=None, specialist_executor=None, local_workers=None, crop_loaders=None, jev_provider=None,
                    qwen_profile=None, qwen_transport=None, policy=None, artifact_preparation=None, ha_sources=None, ha_clients=None,
                    recording_only=False, spatial_sources=None, auxiliary_capture=True):
    """Build against the app-owned store, with injected reviewed admission clients.

    `sources` are exact StreamSource instances. Construction contacts no camera,
    starts no workers and performs no model/provider requests. Nothing here
    authorizes the sources or supplies a global admission implementation.
    """
    if type(recording_only) is not bool:
        raise ValueError("explicit recording-only boolean required")
    if type(auxiliary_capture) is not bool or (not auxiliary_capture and (
            not recording_only or camera_credential is not None or receiver_clock is not None)):
        raise ValueError("encoded-only capture requires recording-only mode without auxiliary inputs")
    if recording_only:
        if any(value is not None for value in (resolve_profiles,specialist_executor,local_workers,crop_loaders,
                jev_provider,qwen_profile,qwen_transport,policy,artifact_preparation,spatial_sources)):
            raise ValueError("recording-only commissioning cannot bind models or reasoning")
        def resolve_profiles(_session,_experiment):
            return {"state":"blocked","reason":"recording_only_commissioning"}
        def specialist_executor(_request):
            raise ValueError("inference disabled for recording-only commissioning")
    elif not callable(resolve_profiles):
        raise ValueError("provisioned profile resolver required")
    sources=list(sources)
    if store.settings.site_id!="victoria":
        raise ValueError("OAK local composition is Victoria-specific")
    cameras={camera["id"] for camera in store.settings.cameras}
    if len(sources)!=1:
        raise ValueError("one OAK owner/source per local controller required")
    if any(s.camera_id not in cameras or not s.enabled or not s.admitted or not s.codec_validation_id for s in sources):
        raise ValueError("commissioned registered encoded sources required")
    if (qwen_profile is None)!=(qwen_transport is None):
        raise ValueError("Qwen profile and admission transport must be provisioned together")
    if artifact_preparation is not None and (
            not isinstance(artifact_preparation,ArtifactPreparation)
            or artifact_preparation.registry.store is not store):
        raise ValueError("artifact preparation must use the controller-owned registry")
    if local_workers is not None:
        if specialist_executor is not None:
            raise ValueError("choose local registered workers or a provisioned executor")
        specialist_executor=RegisteredSpecialists(store,local_workers,crop_loaders=crop_loaders)
    elif crop_loaders is not None:
        raise ValueError("crop loaders require local worker bindings")
    if not callable(specialist_executor):
        raise ValueError("provisioned specialist executor required")
    recorders={s.camera_id:StreamCopySupervisor(store,s) for s in sources}
    previews={s.camera_id:PreviewIngestor(store,site_id=store.settings.site_id,camera_id=s.camera_id,
        clock_validation_id=receiver_clock["validation_id"],clock_uncertainty_s=receiver_clock["uncertainty_seconds"]) for s in sources} if auxiliary_capture else {}
    bundles={s.camera_id:BundleIngestor(store,site_id=store.settings.site_id,camera_id=s.camera_id) for s in sources} if auxiliary_capture else {}
    analysis=None
    if qwen_profile is not None:
        executor=QwenAdapter(qwen_profile,RegisteredAnalysisMedia(store),qwen_transport)
        analysis=AnalysisJobs(store,SequentialAnalysis(store,qwen_profile,executor),RegisteredAnalysisInputs(store))
    reasoner=(lambda ledger,sid:LiveReasoning(ledger,sid,jev_provider,policy=policy)) if jev_provider is not None else None
    preparation=(AnalysisJobs(store,artifact_preparation,lambda _job:(None,None),kind="preparation")
                 if artifact_preparation is not None else None)
    return ServiceRuntime(store,recorders,resolve_profiles,specialist_executor,analysis_jobs=analysis,
        recording_only=recording_only,
        preparation_jobs=preparation,ha_sources=ha_sources,ha_clients=ha_clients,
        spatial_sources=spatial_sources,
        reasoning_factory=reasoner,preview_sources=previews,bundle_sources=bundles,
        preview_clients={s.camera_id:LocalPreviewClient(camera_credential) for s in sources} if auxiliary_capture else {},
        bundle_clients={s.camera_id:LocalBundleClient(camera_credential) for s in sources} if auxiliary_capture else {})

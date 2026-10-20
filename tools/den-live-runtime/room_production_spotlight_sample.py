"""Translate one exact accepted Home spotlight SAMPLE, without command authority.

The fixture's fixed production translator owns brightness/CCT support. This
module proves only which original current Home SAMPLE it receives. It neither
creates a manual grant nor treats a desired endpoint as the sampled output.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json

from journal import Journal,canonical,finite
from room_committed_source import CommittedRoomSource,RoomCommitUnavailable
from room_production_canonical_provider import load_production_spotlight_translator


def need(ok,reason):
    if not ok:raise RoomCommitUnavailable(reason)


@dataclass(frozen=True)
class CommittedSpotlightTranslation:
    """Translation evidence only; not a grant or an accepted command capability."""
    fixture_id:str
    request_id:str
    room_commit_ref:dict
    publication_bytes:bytes
    publication_sha256:str
    sample_pointer:str
    sample_sha256:str
    observed_at:float
    checked_at:float
    valid_until:float
    translation:dict


class TrustedCommittedSpotlightSample:
    def __init__(self,source):
        need(type(source) is CommittedRoomSource and type(source.journal) is Journal,
             'actual_committed_room_source_required')
        self.source=source

    def _original(self,fixture_id):
        source=self.source
        value=source.current()
        need(type(fixture_id) is str and fixture_id in source.binding.fixture_ids,'foreign_room_fixture')
        output=value['decision']['room_output']
        fixtures=output['fixtures']
        selected=[(i,f) for i,f in enumerate(fixtures) if f['fixture_id']==fixture_id]
        need(len(selected)==1,'one_original_room_fixture_required')
        index,fixture=selected[0]
        selected=[(i,d) for i,d in enumerate(fixture['domains']) if d['domain']=='spotlight']
        need(len(selected)==1,'one_original_spotlight_domain_required')
        domain_index,domain=selected[0]
        now,until=value['checked_at'],domain['valid_until']
        need(domain['status'] in ('modeled','off') and type(domain['sample']) is dict,
             'committed_spotlight_sample_unavailable')
        need(finite(until) and finite(now) and value['sample_at']<=now<until,
             'committed_spotlight_sample_expired')
        need(domain['status']!='off' or domain['sample'].get('brightness_fraction')==0,
             'committed_spotlight_off_sample_changed')
        rows=source.journal.db.execute('SELECT body FROM live_worker_current WHERE payload_type=?',
            (source.journal.dynamic.worker.decision_type,)).fetchall()
        need(len(rows)==1,'one_retained_current_room_publication_required')
        stored=rows[0]['body']
        need(type(stored) in (str,bytes),'retained_original_publication_bytes_required')
        raw=stored.encode('utf-8') if type(stored) is str else stored
        # Do not reconstruct a smaller JSON document or select a desired value
        # when the fixture-side original-publication bound refuses this body.
        need(0<len(raw)<=65536,'bounded_original_publication_bytes_required')
        need(raw==canonical(value['publication']).encode('utf-8')
             and canonical(json.loads(raw))==canonical(value['publication']),
             'retained_room_publication_changed')
        pointer=f'/payload/room_output/fixtures/{index}/domains/{domain_index}/sample'
        return value,raw,pointer,deepcopy(domain['sample']),until

    def translate(self,fixture_id,*,request_id):
        """Read/recheck the current source and invoke only brightness/CCT mode.

        The independent manual-authority owner must still authorize any supported
        result through its ordinary ManualProducer, Plan409 and current state.
        A returned result must never be accepted as caller-supplied commit proof.
        """
        with self.source.journal.lock:
            value,raw,pointer,expected,until=self._original(fixture_id)
            translator=load_production_spotlight_translator()
            original_sha=hashlib.sha256(raw).hexdigest()
            sample,reference=translator.extract_sample(raw,publication_sha256=original_sha,sample_pointer=pointer)
            need(sample==expected,'original_committed_sample_pointer_changed')
            translated=translator.translate(sample,source_reference=reference,request_id=request_id,
                                            kind='brightness_cct')
            later,later_raw,later_pointer,later_sample,later_until=self._original(fixture_id)
            need(later['room_commit_ref']==value['room_commit_ref'] and later_raw==raw
                 and later_pointer==pointer and later_sample==expected and later_until==until,
                 'committed_spotlight_source_changed_during_translation')
            return CommittedSpotlightTranslation(fixture_id,request_id,deepcopy(value['room_commit_ref']),raw,
                original_sha,pointer,reference['sample_sha256'],value['sample_at'],later['checked_at'],
                until,deepcopy(translated))

    def require_current(self,translation):
        """Recompute exact evidence; a typed result alone is never current proof."""
        need(type(translation) is CommittedSpotlightTranslation,
             'exact_committed_spotlight_translation_required')
        fresh=self.translate(translation.fixture_id,request_id=translation.request_id)
        need(all(getattr(translation,key)==getattr(fresh,key) for key in
                 CommittedSpotlightTranslation.__dataclass_fields__ if key!='checked_at'),
             'committed_spotlight_translation_changed')
        need(finite(translation.checked_at) and translation.observed_at<=translation.checked_at
             <=fresh.checked_at<translation.valid_until,'committed_spotlight_translation_clock_changed')
        return fresh

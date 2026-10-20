"""Two production-specific original-byte passes, each requiring actual Home birth.

This reader does not launch/recompute a native process or admit a command. A
producer DATA grammar pass alone never creates a source-owner qualification.
"""
from copy import deepcopy
import hashlib
import json

import room_fake_plant_replay as raw_archive
from room_production_canonical_client import production_protocol_validator
from room_production_canonical_provider import FAMILY,PROTOCOL,verified_production_provider_export
from room_production_canonical_recording import RECORD_SCHEMA,RECEIPT_SCHEMA,validate_header

need=raw_archive._require


def _candidate_transcript(index,member,header,qualification):
    """Independent second pass over exact confirmed per-member original streams."""
    validate_header(header,qualification)
    _,manifest,_=verified_production_provider_export()
    validator=production_protocol_validator(manifest,header,index)
    need(member['attempts'] and len(member['attempts'])<=100000 and not member['stderr'],
         'production_missing_input_or_stderr')
    need(len(member['results'])==len(member['attempts']),'production_missing_write_result')
    output=bytes(member['stdout']);lines=output.splitlines(keepends=True)
    need(output.endswith(b'\n') and len(lines)==len(member['attempts']),'production_response_count_or_framing')
    responses=[];offset=0
    for attempt,line in zip(member['attempts'],lines):
        result=member['results'].get(attempt['request_id'])
        need(result is not None and type(result['reported_bytes']) is int and result['reported_bytes']==len(attempt['raw'])
             and result['flush_completed'] is True and result['error'] is None,'production_unconfirmed_pipe_write')
        offset+=len(line)
        completed=next(n for end,n in member['stdout_segments'] if end>=offset)
        need(completed>attempt['ordinal'],'production_response_precedes_attempt')
        validator.request(attempt['raw'])
        for start in range(0,len(line),4096):responses.extend(validator.stdout(line[start:start+4096]))
    validator.finish();validate_header(header,qualification)
    return dict(fixture_index=index,fixture=deepcopy(header['fixtures'][index]),
        stdin_bytes=b''.join(a['raw'] for a in member['attempts']),stdout_bytes=output,
        stderr_bytes=bytes(member['stderr']),responses=responses)


def _verify(path,*,expected_header,recording_receipt,qualification,max_records=8192,max_bytes=33554432):
    """Raw archive pass: birth, write/response joins, EOF/exit and DATA grammar."""
    validate_header(expected_header,qualification)
    raw_archive._int(max_records,1,8192);raw_archive._int(max_bytes,1,33554432)
    raw=raw_archive._read_archive(path,max_bytes)
    need(raw and raw.endswith(b'\n'),'production_truncated_archive')
    lines=raw.splitlines(keepends=True);need(len(lines)<=max_records,'production_record_budget')
    _,manifest,_=verified_production_provider_export()
    validators=[production_protocol_validator(manifest,expected_header,i) for i in range(4)]
    members=[raw_archive._member() for _ in range(4)];joins=[None]*4
    previous='0'*64;header=final=None;birth_seen=False
    for sequence,line in enumerate(lines,1):
        need(len(line)<=49152,'production_archive_row_budget');row=raw_archive._json(line[:-1])
        raw_archive._keys(row,('schema','version','sequence','kind','previous_sha256','payload','record_sha256'),'production_row_fields')
        need(row['schema']==RECORD_SCHEMA and type(row['version']) is int and row['version']==1
             and row['sequence']==str(sequence) and raw_archive._canonical(row)+b'\n'==line,
             'production_archive_abi_or_sequence')
        need(row['previous_sha256']==previous and row['record_sha256']==hashlib.sha256(raw_archive._canonical(
            {k:v for k,v in row.items() if k!='record_sha256'})).hexdigest(),'production_archive_chain')
        previous=row['record_sha256'];need(final is None,'production_record_after_final')
        if sequence==1:
            need(row['kind']=='header' and row['payload']==expected_header,'production_header_first')
            validate_header(row['payload'],qualification);header=row['payload']
        elif sequence==2:
            need(row['kind']=='source_birth' and row['payload']==qualification.public(),'production_original_source_birth_required')
            birth_seen=True
        elif row['kind']=='io':
            need(birth_seen,'production_birth_before_io_required');p=row['payload']
            raw_archive._io(p,header,members);e=p['event'];i=p['fixture_index'];kind=e['kind'];v=validators[i]
            if kind=='stdin_attempt':
                prior=joins[i]
                need(prior is None or prior['written'] and prior['responded'],'production_prior_exchange_join_incomplete')
                b=raw_archive._original(p['original'],16384);v.request(b)
                joins[i]=dict(id=e['request_id'],size=len(b),written=False,responded=False)
            elif kind=='stdin_write_result':
                current=joins[i];need(current is not None and current['id']==e['request_id'],'production_write_join_scope')
                need(type(e['reported_bytes']) is int and e['reported_bytes']==current['size']
                     and e['flush_completed'] is True and e['error'] is None,'production_write_ack_unconfirmed')
                current['written']=True
            elif kind=='stdout_bytes':
                completed=v.stdout(raw_archive._original(p['original'],4096))
                if completed:
                    current=joins[i]
                    need(current is not None and not current['responded'] and len(completed)==1
                         and completed[0]['request_id']==current['id'],'production_response_join_scope')
                    current['responded']=True
            elif kind=='stdout_eof':v.finish()
        elif row['kind']=='final':final=row['payload']
        else:raise raw_archive.ArchiveInvalid('production_archive_kind')
    need(final is not None and birth_seen,'production_missing_final_or_birth')
    need(all(j is not None and j['written'] and j['responded'] for j in joins),'production_terminal_join_incomplete')
    raw_archive._footer(final,header,members)
    need(type(recording_receipt) is dict and recording_receipt.get('schema')==RECEIPT_SCHEMA
         and recording_receipt.get('selection')==FAMILY and recording_receipt.get('protocol')==PROTOCOL
         and recording_receipt.get('source_birth_sha256')==qualification.public()['birth_sha256']
         and recording_receipt.get('physical_truth') is False,'production_original_recording_receipt_required')
    view=deepcopy(recording_receipt)
    for key in ('selection','protocol','source_birth_sha256','physical_truth'):view.pop(key)
    view['schema']='home-room-fake-plant-recording-receipt-1'
    raw_archive._receipt(view,path,final,previous,len(lines))
    validate_header(header,qualification)
    return dict(header=header,members=members,archive_sha256=hashlib.sha256(raw).hexdigest(),archive_bytes=len(raw),record_count=len(lines))


def verify_room_production_canonical_archive(path,*,expected_header,recording_receipt,qualification,
                                            max_records=8192,max_bytes=33554432):
    result=dict(status='incomplete',selection=FAMILY,protocol=PROTOCOL,native_recomputed=False,
                home_room_replayed=False,physical_permission=False,physical_truth=False,home_qualified=False)
    try:
        first=_verify(path,expected_header=expected_header,recording_receipt=recording_receipt,
            qualification=qualification,max_records=max_records,max_bytes=max_bytes)
        transcripts=[_candidate_transcript(i,m,first['header'],qualification) for i,m in enumerate(first['members'])]
        result.update({k:v for k,v in first.items() if k!='members'},status='production_archive_verified',
            reason='two_original_passes_with_registered_home_source_birth',transcripts=transcripts)
    except (OSError,ValueError,TypeError,KeyError,OverflowError,RecursionError,StopIteration) as error:
        result['reason']=str(error)[:160]
    return result

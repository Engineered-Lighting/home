"""Optional private HA binding for the existing managed capture runtime."""
from pathlib import Path, PurePosixPath
import http.client
import os
import re
import ssl
import threading
import time

from ha_ingest import HAIngestor, derived_rules

REQUIRED={'schema_version','site_id','camera_id','source_id','origin','credential_file','entities'}
OPTIONAL={'camera_derived_entities','ca_file','expected_hostname','api','derived','entity_list','presence'}
# Optional second, independent HA source: owner home/away from another HA
# (LA). Its own origin, pinned host, trust and secret file; one person entity.
PRESENCE_REQUIRED={'source_id','origin','credential_file','entity'}
PRESENCE_OPTIONAL={'ca_file','expected_hostname'}
# The only JEV fact the presence entity may feed, and the only entity that may feed it.
PRESENCE_FACT='owner_home'
ENTITY=re.compile(r'[a-z_]+\.[a-z0-9_]{1,100}')
SNAPSHOT_KEYS=('snapshot_id','observed_at','states','acquisition_duration_s')


def _connection_factory(config):
    if 'ca_file' not in config:
        return http.client.HTTPSConnection
    # Private boundary CA for this reader only; JEV egress keeps system trust.
    context=ssl.create_default_context(cafile=config['ca_file'])
    def factory(host,port,timeout,context=context):
        return http.client.HTTPSConnection(host,port,timeout=timeout,context=context)
    return factory


def _credential_reader(path):
    def credential():
        p=Path(path)
        if p.is_symlink() or p.stat().st_uid!=os.geteuid() or p.stat().st_mode&0o077:
            raise ValueError('Private HA credential required')
        with p.open(encoding='utf-8') as f:token=f.read(4097).strip()
        if not 1<=len(token)<=4096:raise ValueError('Invalid HA credential')
        return token
    return credential


def _absolute_paths(config):
    if any(not isinstance(config.get(k,'/'),str) or not PurePosixPath(config.get(k,'/')).is_absolute()
           for k in ('credential_file','ca_file')):
        raise ValueError('Absolute HA credential/trust paths required')


def _pin(config, client):
    pinned=config.get('expected_hostname',client.host)
    if not isinstance(pinned,str) or pinned.lower()!=client.host:
        raise ValueError('Pinned HA origin hostname required')


def _derived_inputs(spec):
    """Input entity ids a derived rule names (validated by derived_rules)."""
    if not isinstance(spec,dict):
        return set()
    return {v for rule in spec.values() if isinstance(rule,dict)
            for k,v in rule.items() if k in ('session_entity','last_active_entity')
            and isinstance(v,str) and ENTITY.fullmatch(v)}


class HASourceMember:
    """One independent HA source of this session and what its ledger may hold."""
    def __init__(self, *, source_id, role, client, api, entities=None, wanted=None,
                 referenced=(), camera_derived=(), derived=None):
        self.source_id,self.role,self.client,self.api=source_id,role,client,api
        self.host=client.host
        # entities: fixed ingestor allowlist; None while it awaits the listing.
        self.entities,self.wanted=entities,wanted
        self.referenced,self.camera_derived,self.derived=frozenset(referenced),tuple(camera_derived),derived


def _presence(value, primary, known, identifier, clock):
    from ha_client import HAStateClient,PRESENCE_PROJECTION
    if not isinstance(value,dict) or set(value)-PRESENCE_OPTIONAL!=PRESENCE_REQUIRED:
        raise ValueError('Registered presence HA source required')
    identifier(value['source_id'])
    if value['source_id']==primary['source_id']:
        raise ValueError('Presence HA source needs its own source_id')
    _absolute_paths(value)
    if value['credential_file']==primary['credential_file']:
        raise ValueError('Presence HA source needs its own credential file')
    entity=value['entity']
    if not isinstance(entity,str) or not ENTITY.fullmatch(entity) or not entity.startswith('person.'):
        raise ValueError('Presence HA source needs one exact person entity')
    if entity in known:
        raise ValueError('Presence entity must not also be read from the primary HA source')
    client=HAStateClient(value['origin'],_credential_reader(value['credential_file']),[entity],clock=clock,
                         connection_factory=_connection_factory(value),api='perception_lab_source',
                         projection=PRESENCE_PROJECTION)
    _pin(value,client)
    return HASourceMember(source_id=value['source_id'],role='presence',client=client,
                          api='perception_lab_source',entities=(entity,))


def ha_source(site_id, camera_id, path, *, ha_bindings=None, clock=time.time):
    """Validate before workers start; reads no credential and contacts no host."""
    from live_detector_app import private_json
    from ha_client import HAStateClient,ListedHAStateClient
    from store import identifier
    config=private_json(path)
    # 'entity_list': true reads the scoped view's own allowlist (e.g. its Den
    # area) at session start; 'entities' then narrows it (required): the Lab
    # reads only the listed ids it names, never the whole listing.
    listed=isinstance(config,dict) and config.get('entity_list') is True
    if listed and 'entities' not in config:
        raise ValueError('Listed HA source requires an exact entities list')
    if (not isinstance(config,dict) or set(config)-OPTIONAL!=REQUIRED
            or config.get('entity_list',True) is not True
            or config['schema_version']!=1 or config['site_id']!=site_id
            or config['camera_id']!=camera_id):
        raise ValueError('Registered local HA source required')
    identifier(config['source_id'])
    if not isinstance(config['entities'],list):
        raise ValueError('bounded exact entity allowlist required')
    _absolute_paths(config)
    factory=_connection_factory(config)
    credential=_credential_reader(config['credential_file'])
    # 'api': HA core /api/states (default) or the scoped perception_lab_source view,
    # whose credential_file holds that view's secret instead of an HA user token.
    if listed:
        client=ListedHAStateClient(config['origin'],credential,wanted=config['entities'],clock=clock,
                                   connection_factory=factory,api=config.get('api','core'))
        known=client.wanted
    else:
        client=HAStateClient(config['origin'],credential,config['entities'],clock=clock,
                             connection_factory=factory,api=config.get('api','core'))
        known=client.entities
    _pin(config,client)
    derived=config.get('camera_derived_entities',[])
    if (not isinstance(derived,list) or any(not isinstance(e,str) or not ENTITY.fullmatch(e) for e in derived)
            or any(e not in known for e in derived)):
        raise ValueError('camera-derived entities must belong to the entity allowlist')
    # Locally derived facts (e.g. computer_active) read only allowlisted entities.
    if 'derived' in config and not isinstance(config['derived'],dict):
        raise ValueError('registered derived HA facts required')
    rules=derived_rules(config.get('derived'),known)
    presence=None
    if 'presence' in config:
        presence=_presence(config['presence'],config,set(known)|set(derived),identifier,clock)
    from jev_evidence import DERIVED_HA
    # JEV bindings may name only read entities or declared derived facts, each
    # derived id bound to its own fact; occupancy must keep its camera origin.
    # owner_home binds only the presence entity, and the presence entity nothing else.
    bound=set()
    for entity,fact in (ha_bindings or {}).items():
        if presence is not None and entity==presence.entities[0]:
            if fact!=PRESENCE_FACT:
                raise ValueError('The presence entity binds only owner_home')
            continue
        if fact==PRESENCE_FACT:
            raise ValueError('owner_home binds only the presence HA source entity')
        if entity in rules:
            if DERIVED_HA.get(entity)!=fact:
                raise ValueError('Derived HA binding must name its own fact')
            continue
        if entity not in known:
            raise ValueError('JEV HA binding outside the HA source allowlist')
        if fact=='person_present' and entity not in derived:
            raise ValueError('Camera-derived provenance required for person_present')
        bound.add(entity)
    if not listed and presence is None:
        return config,client
    api=config.get('api','core')
    primary=HASourceMember(source_id=config['source_id'],role='primary',client=client,api=api,
        entities=None if listed else client.entities,wanted=known if listed else None,
        referenced=set(derived)|_derived_inputs(config.get('derived'))|bound,
        camera_derived=derived,derived=config.get('derived'))
    return config,HASourceSetClient([primary]+([presence] if presence else []))


class HASourceSetClient:
    """Polls every HA source of the session independently: the primary inline,
    any other on a short-lived thread, so a slow or failing source never blocks
    or fails another. Each result is that source's own snapshot (with its own
    acquisition time) or a content-free failure reason."""

    def __init__(self, members):
        self.members=tuple(members)
        self.host=self.members[0].host

    def __call__(self):
        results={}
        def run(member):
            try:
                results[member.source_id]=member.client()[0]
            except ValueError as error:
                reason=str(error)
                results[member.source_id]={'failure':reason if re.fullmatch('ha_[a-z_]{1,40}',reason) else 'ha_snapshot_unavailable'}
            except Exception:
                results[member.source_id]={'failure':'ha_snapshot_unavailable'}
        threads=[threading.Thread(target=run,args=(m,),name='lab-ha-source',daemon=True) for m in self.members[1:]]
        for thread in threads:thread.start()
        run(self.members[0])
        for thread in threads:thread.join(5)
        # A source still running past its own 2 s deadline counts as failed.
        return {'sources':{m.source_id:dict(results.get(m.source_id) or {'failure':'ha_snapshot_unavailable'})
                           for m in self.members}},None


class HASourceSet(HAIngestor):
    """Ledger side of HASourceSetClient: one HAIngestor per source, so every
    'ha.observation' keeps its own source_id, snapshot key, acquisition time and
    ordering. A listed source's ingestor is built from the listing it read; its
    revision is recorded once per session as an 'ha.source' status event."""

    def __init__(self, store, camera_id, members):
        if camera_id not in {c['id'] for c in store.settings.cameras}:
            raise ValueError('registered local source required')
        self.store,self.site_id,self.camera_id=store,store.settings.site_id,camera_id
        self.members={m.source_id:m for m in members}
        primary=members[0]
        self.source_id,self.ttl=primary.source_id,3
        self.camera_derived_entities=frozenset(primary.camera_derived)
        self.ingestors,self.revisions,self.recorded={},{},set()
        for member in members:
            if member.entities is not None:
                self.ingestors[member.source_id]=self._ingestor(member,member.entities)
        self.entities=frozenset().union(*(i.entities for i in self.ingestors.values()))
        self.derived=self.ingestors[primary.source_id].derived if primary.source_id in self.ingestors else {}

    def _ingestor(self, member, entities):
        return HAIngestor(self.store,site_id=self.site_id,camera_id=self.camera_id,source_id=member.source_id,
                          entities=sorted(entities),camera_derived_entities=member.camera_derived,derived=member.derived)

    def _resolved(self, member, entity_list):
        if member.entities is not None:
            if entity_list is not None:
                raise ValueError('unexpected entity listing')
            return self.ingestors[member.source_id],None
        if (not isinstance(entity_list,dict) or set(entity_list)!={'revision','listed','read','unlisted'}
                or not isinstance(entity_list['revision'],str) or not re.fullmatch('[0-9a-f]{64}',entity_list['revision'])
                or not isinstance(entity_list['read'],list) or not set(entity_list['read'])<=set(entity_list['listed'])):
            raise ValueError('listed HA source needs its entity listing')
        revision=entity_list['revision']
        if self.revisions.get(member.source_id)!=revision:
            # Read entities plus the ones this Lab binds or derives from; those
            # outside the listing are never requested and stay unknown.
            entities=set(entity_list['read'])|member.referenced|set(member.wanted or ())
            self.ingestors[member.source_id]=self._ingestor(member,entities)
            self.revisions[member.source_id]=revision
            self.entities=frozenset().union(*(i.entities for i in self.ingestors.values()))
        return self.ingestors[member.source_id],revision

    def _record_once(self, sid, kind, payload, match):
        memo=(sid,kind,*(payload[k] for k in match))
        if memo in self.recorded:
            return
        with self.store.transaction() as db:
            session=self.store.get_session(sid)
            if session['state']!='recording' or session['camera_id']!=self.camera_id:
                return
            where=' AND '.join(f"json_extract(payload,'$.{k}') IS ?" for k in match)
            if not db.execute(f"SELECT 1 FROM events WHERE session_id=? AND kind=? AND {where}",
                              (sid,kind,*(payload[k] for k in match))).fetchone():
                self.store.check_capacity(65536)
                self.store._event(db,sid,kind,{**payload,'available_at':self.store.clock()})
        self.recorded.add(memo)

    def _status(self, sid, member, revision, entity_list):
        payload={'source_id':member.source_id,'role':member.role,'api':member.api,'origin_host':member.host,
                 'entities':'listed' if member.entities is None else 'exact','revision':revision}
        if entity_list is not None:
            payload['entity_list']={k:entity_list[k] for k in ('listed','read','unlisted')}
        if member.role=='presence':
            from ha_client import PRESENCE_PROJECTION
            payload.update(fact=PRESENCE_FACT,projection=PRESENCE_PROJECTION)
        self._record_once(sid,'ha.source',payload,('source_id','revision'))

    def ingest(self, sid, *, sources):
        from ha_client import LIST_REFUSALS
        if not isinstance(sources,dict) or not sources or set(sources)-set(self.members):
            raise ValueError('snapshot outside the registered HA sources')
        stored={}
        for source_id,member in self.members.items():
            snapshot=sources.get(source_id)
            if not isinstance(snapshot,dict):
                continue
            try:
                if 'failure' in snapshot:
                    # A listing refused by policy (over cap, invalid, empty) is
                    # recorded once, content-free; transient failures are not.
                    if snapshot['failure'] in LIST_REFUSALS:
                        self._record_once(sid,'ha.unavailable',{'reason':snapshot['failure'],'source_id':source_id},
                                          ('reason','source_id'))
                    continue
                entity_list=snapshot.get('entity_list')
                ingestor,revision=self._resolved(member,entity_list)
                self._status(sid,member,revision,entity_list)
                stored[source_id]=ingestor.ingest(sid,**{k:snapshot[k] for k in SNAPSHOT_KEYS if k in snapshot})
            except Exception:
                continue
        if not stored:
            raise ValueError('ha_snapshot_unavailable')
        return stored


def ha_sources(store, source, path, *, ha_bindings=None):
    config,client=ha_source(store.settings.site_id,source.camera_id,path,ha_bindings=ha_bindings,clock=store.clock)
    if isinstance(client,HASourceSetClient):
        ingestor=HASourceSet(store,source.camera_id,client.members)
    else:
        ingestor=HAIngestor(store,site_id=config['site_id'],camera_id=source.camera_id,
            source_id=config['source_id'],entities=config['entities'],camera_derived_entities=config.get('camera_derived_entities',()),
            derived=config.get('derived'))
    return {'ha_sources':{source.camera_id:ingestor},'ha_clients':{source.camera_id:client}}


class UnavailableHA(HAIngestor):
    """This session's HA file failed validation after startup. Nothing is read or
    sent; one content-free reason is recorded and the pump reports unavailable."""
    def __init__(self, store, camera_id, reason='ha_source_invalid'):
        self.store,self.site_id,self.camera_id,self.reason=store,store.settings.site_id,camera_id,reason
        self.source_id,self.entities,self.camera_derived_entities,self.ttl=None,frozenset(),frozenset(),None
        self.derived={}

    def __call__(self):
        return {'reason':self.reason},None

    def ingest(self, sid, *, reason):
        with self.store.transaction() as db:
            session=self.store.get_session(sid)
            if (session['state']=='recording' and session['camera_id']==self.camera_id
                    and not db.execute("SELECT 1 FROM events WHERE session_id=? AND kind='ha.unavailable'",(sid,)).fetchone()):
                self.store.check_capacity(65536)
                self.store._event(db,sid,'ha.unavailable',{'reason':reason,'available_at':self.store.clock()})
        raise ValueError(reason)


def unavailable_ha(store, source):
    value=UnavailableHA(store,source.camera_id)
    return {'ha_sources':{source.camera_id:value},'ha_clients':{source.camera_id:value}}

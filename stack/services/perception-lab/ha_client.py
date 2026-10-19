"""Provisioned, bounded read-only HA state polling. Construction does no I/O."""
import http.client
import json
import re
import time
import uuid
from urllib.parse import urlsplit

# Read path per HA API. 'core' is HA's own /api/states (HA user token);
# 'perception_lab_source' is the Lab-owned, read-only, entity-allowlisted HA
# view guarded by its own secret, so no HA user token lives on the Lab host.
STATE_PATHS = {'core':'/api/states/',
               'perception_lab_source':'/api/perception_lab_source/v1/states/'}
# The component's allowlist listing (ids + revision digest only).
LIST_PATH = '/api/perception_lab_source/v1/entities'
ENTITY_RE = re.compile(r'[a-z_]+\.[a-z0-9_]{1,100}')
# Client read cap per source. The component may list up to 16, but every
# entity is a separate sequential HTTPS GET inside one 2 s snapshot deadline.
# The GETs of one snapshot share one keep-alive connection: measured from the
# Jetson, a new TLS connection costs ~0.25 s per GET and a reused one ~0.05 s,
# so 7 entities took ~1.9 s with a connection each, available only ~1.1 s
# before the snapshot's 3 s validity (from acquisition start) ran out. 8 stays
# the reviewed cap. A listed source must name at most 8 wanted ids and reads
# only those that are listed, so a larger listing is never cut.
MAX_ENTITIES = 8
MAX_LISTED = 16
# Presence projection served by the component's presence mode. Anything else
# (a raw zone name from a misconfigured view) is refused, never recorded.
PRESENCE_PROJECTION = 'home_away_v1'
PRESENCE_STATES = ('home','away','unknown')
# Content-free, permanent list-resolution refusals (kept for the session).
# With a required 'wanted' list, the listed client raises only the invalid one
# in practice (over_cap stays as a guard); the ledger side keeps all three.
LIST_REFUSALS =('ha_entity_list_over_cap','ha_entity_list_invalid','ha_entity_list_empty')


def _exact_entities(entities, low, high):
    entities = tuple(entities)
    if (not low <= len(entities) <= high or len(set(entities)) != len(entities)
            or any(not isinstance(e,str) or not ENTITY_RE.fullmatch(e) for e in entities)):
        return None
    return entities


class HAStateClient:
    def __init__(self, origin, credential, entities, *, clock=time.time,
                 monotonic=time.monotonic, connection_factory=http.client.HTTPSConnection,
                 api='core', projection=None):
        self._bind(origin, credential, api, clock, monotonic, connection_factory)
        entities = _exact_entities(entities, 1, MAX_ENTITIES)
        if entities is None:
            raise ValueError('one to eight registered entities required')
        if projection is not None and (projection != PRESENCE_PROJECTION or api != 'perception_lab_source'
                                       or len(entities) != 1 or not entities[0].startswith('person.')):
            raise ValueError('presence projection needs one person entity on the scoped view')
        self.entities, self.projection = entities, projection

    def _bind(self, origin, credential, api, clock, monotonic, connection_factory):
        parsed = urlsplit(origin)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in {'','/'} or parsed.query or parsed.fragment):
            raise ValueError('provisioned HTTPS origin required')
        if not isinstance(api,str) or api not in STATE_PATHS:
            raise ValueError('supported HA read api required')
        self.api, self.state_path = api, STATE_PATHS[api]
        self.host, self.port = parsed.hostname, parsed.port or 443
        self.credential = credential
        self.clock, self.monotonic, self.connection_factory = clock, monotonic, connection_factory

    def _token(self):
        token = self.credential()
        if not isinstance(token,str) or not token or any(c in token for c in '\r\n'):
            raise ValueError()
        return token

    def _get(self, path, token, deadline, connection=None):
        """One bounded GET: None on 404, parsed JSON on 200, ValueError otherwise.

        With `connection` (one snapshot's keep-alive connection, owned and
        closed by the caller) the GET reuses it; otherwise it opens and closes
        its own. Either way the whole exchange stays within the deadline."""
        remaining = deadline-self.monotonic()
        if remaining <= 0: raise TimeoutError()
        own = connection is None
        if own:
            connection = self.connection_factory(self.host,self.port,timeout=remaining)
        else:
            # A (re)connect uses the connection's timeout: keep it in the deadline.
            connection.timeout = remaining
            if connection.sock is not None: connection.sock.settimeout(remaining)
        try:
            connection.request('GET',path,headers={
                'Authorization':'Bearer '+token,'Accept':'application/json',
                'Accept-Encoding':'identity','Cache-Control':'no-cache'})
            response = connection.getresponse()
            if response.status == 404:
                # The unread 404 body must not reach the next GET: a reused
                # connection is dropped (the next GET reconnects).
                if not own: connection.close()
                return None
            if (response.status != 200 or response.getheader('Content-Type','').split(';')[0] != 'application/json'
                    or response.getheader('Content-Encoding','identity') != 'identity'):
                raise ValueError()
            data = bytearray()
            while True:
                remaining = deadline-self.monotonic()
                if remaining <= 0: raise TimeoutError()
                if connection.sock is not None: connection.sock.settimeout(remaining)
                chunk = response.read1(min(8192,65537-len(data)))
                if not chunk: break
                data.extend(chunk)
                if len(data)>65536: raise ValueError()
            # Python 3.10's read1() never marks a fully read Content-Length
            # response closed, so a reused connection would refuse the next
            # GET (ResponseNotReady). Closing the response releases it.
            response.close()
            return json.loads(data)
        finally:
            if own: connection.close()

    def _state(self, entity, value):
        if (not isinstance(value,dict) or value.get('entity_id') != entity
                or not isinstance(value.get('state'),str) or not 1<=len(value['state'])<=128):
            raise ValueError()
        if self.projection is not None and (value.get('projection') != self.projection
                                            or value['state'] not in PRESENCE_STATES):
            raise ValueError()
        # Only the state is kept; any returned attributes are ignored.
        return value['state']

    def __call__(self):
        started = self.clock()
        deadline = self.monotonic() + 2
        states = {}
        connection = None
        try:
            token = self._token()
            # One keep-alive connection per snapshot (constructing it does no
            # I/O; the first GET connects), closed below: never kept between
            # snapshots, so each snapshot still starts from a fresh connection.
            connection = self.connection_factory(self.host,self.port,timeout=max(deadline-self.monotonic(),0.001))
            for entity in self.entities:
                value = self._get(self.state_path+entity, token, deadline, connection)
                states[entity] = 'unavailable' if value is None else self._state(entity, value)
            # Earliest acquisition bound; these sequential reads are not atomic.
            duration = self.clock()-started
            if not 0 <= duration <= 2 or self.monotonic()>deadline:
                raise ValueError()
            return {'snapshot_id':uuid.uuid4().hex,'observed_at':started,'states':states,
                    'acquisition_duration_s':duration}, None
        except Exception:
            raise ValueError('ha_snapshot_unavailable') from None
        finally:
            if connection is not None:
                connection.close()


class ListedHAStateClient(HAStateClient):
    """Reads the scoped view's own allowlist (e.g. its Den-area set) once, at the
    first poll of the session, then snapshots the listed entities the Lab wants.
    ``wanted`` (required, 1..MAX_ENTITIES exact ids) narrows the listing: the
    Lab never reads a listed entity it did not name, so a view that grows (a new
    device in the Den) cannot widen what the Lab reads.

    The listing is fixed for this client's life (one recording session); the
    component's revision digest travels with every snapshot so the ledger
    records which allowlist revision was read. Listed-but-unwanted entities are
    never requested; wanted-but-unlisted ones stay unknown.
    """

    def __init__(self, origin, credential, *, wanted, clock=time.time,
                 monotonic=time.monotonic, connection_factory=http.client.HTTPSConnection,
                 api='perception_lab_source'):
        if api != 'perception_lab_source':
            raise ValueError('entity listing needs the scoped perception_lab_source view')
        self._bind(origin, credential, api, clock, monotonic, connection_factory)
        try:
            wanted = None if wanted is None or isinstance(wanted,str) else _exact_entities(wanted, 1, MAX_ENTITIES)
        except TypeError:
            wanted = None
        if wanted is None:
            raise ValueError('one to eight registered entities required')
        self.wanted, self.projection = wanted, None
        self.entities, self.entity_list, self.refusal = (), None, None

    def resolve(self):
        """GET the listing within its own 2 s deadline; fixed once it succeeds."""
        if self.entity_list is not None:
            return self.entity_list
        if self.refusal is not None:
            raise ValueError(self.refusal)
        try:
            value = self._get(LIST_PATH, self._token(), self.monotonic()+2)
        except Exception:
            raise ValueError('ha_entity_list_unavailable') from None
        listed = value.get('entities') if isinstance(value,dict) else None
        revision = value.get('revision') if isinstance(value,dict) else None
        if (not isinstance(value,dict) or set(value) != {'entities','revision'}
                or not isinstance(listed,list) or _exact_entities(listed,0,MAX_LISTED) is None
                or any(e.split('.',1)[0] == 'derived' for e in listed)
                or not isinstance(revision,str) or not re.fullmatch('[0-9a-f]{64}',revision)):
            self.refusal = 'ha_entity_list_invalid'
        else:
            read = sorted(set(listed) & set(self.wanted))
            if len(read) > MAX_ENTITIES:    # defence in depth: wanted is already capped
                self.refusal = 'ha_entity_list_over_cap'
        if self.refusal is not None:
            raise ValueError(self.refusal)
        self.entities = tuple(read)
        self.entity_list = {'revision':revision,'listed':sorted(listed),'read':list(read),
                            'unlisted':sorted(set(self.wanted)-set(listed))}
        return self.entity_list

    def __call__(self):
        entity_list = self.resolve()
        if not self.entities:
            # Every wanted entity is outside the listing: nothing is requested
            # and each stays unknown; the acquisition is this instant.
            now = self.clock()
            snapshot = {'snapshot_id':uuid.uuid4().hex,'observed_at':now,'states':{},'acquisition_duration_s':0}
        else:
            snapshot,_ = super().__call__()
        return {**snapshot,'entity_list':entity_list}, None

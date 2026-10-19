import json
import threading
import unittest
from ha_client import HAStateClient
import test_ha_ingest as fixtures


class HAClientTests(unittest.TestCase):
    def client(self, status=200, extra=None, content_type='application/json', **kw):
        calls=[]
        class Response:
            def __init__(self):
                self.status=status
                self.data=json.dumps(extra or {'entity_id':'light.desk','state':'on',
                    'attributes':{'private':'not retained'},'last_changed':'old'}).encode()
            def getheader(self,name,default=None): return content_type if name=='Content-Type' else default
            def read1(self,n):
                data,self.data=self.data[:n],self.data[n:]; return data
            def close(self): self.closed=True
        class Connection:
            sock=None
            def request(self,*args,**kw): calls.append(args)
            def getresponse(self): return Response()
            def close(self): calls.append('closed')
        client=HAStateClient('https://ha.example',lambda:'test-only',['light.desk'],
                            clock=lambda:100,connection_factory=lambda *a,**k:Connection(),**kw)
        return client,calls

    def test_exact_get_only_strips_attributes_and_uses_acquisition_time(self):
        client,calls=self.client()
        self.assertEqual(calls,[])
        value,_=client()
        self.assertEqual(value['states'],{'light.desk':'on'})
        self.assertEqual(value['observed_at'],100)
        self.assertEqual(calls,[('GET','/api/states/light.desk'),'closed'])
        self.assertNotIn('private',json.dumps(value))

    def test_missing_entity_unknown_but_redirect_auth_and_mismatch_fail(self):
        client,_=self.client(status=404)
        self.assertEqual(client()[0]['states'],{'light.desk':'unavailable'})
        for status in (301,401,500):
            client,calls=self.client(status=status)
            with self.assertRaisesRegex(ValueError,'^ha_snapshot_unavailable$'): client()
            self.assertEqual(calls[-1],'closed')
        client,_=self.client(extra={'entity_id':'person.owner','state':'home'})
        with self.assertRaises(ValueError): client()

    def test_scoped_source_reads_its_own_path_and_keeps_the_observation_shape(self):
        scoped={'entity_id':'light.desk','state':'on','last_changed':'2026-09-28T10:00:00+00:00',
                'last_updated':'2026-09-28T10:00:00+00:00','attributes':{'brightness':128}}
        core,_=self.client(extra=scoped)
        client,calls=self.client(extra=scoped,api='perception_lab_source')
        self.assertEqual((core.api,client.api),('core','perception_lab_source'))
        value,_=client()
        self.assertEqual(calls,[('GET','/api/perception_lab_source/v1/states/light.desk'),'closed'])
        self.assertEqual(set(value),set(core()[0]))
        self.assertEqual(value['states'],{'light.desk':'on'})
        self.assertNotIn('brightness',json.dumps(value))

    def test_scoped_source_keeps_existing_failure_rules(self):
        client,_=self.client(status=404,api='perception_lab_source')
        self.assertEqual(client()[0]['states'],{'light.desk':'unavailable'})
        for kw in ({'status':401},{'status':405},{'status':302},{'content_type':'text/html'},
                   {'extra':{'entity_id':'lock.front_door','state':'locked'}},
                   {'extra':{'entity_id':'light.desk','state':''}}):
            with self.subTest(**{k:str(v) for k,v in kw.items()}):
                client,calls=self.client(api='perception_lab_source',**kw)
                with self.assertRaisesRegex(ValueError,'^ha_snapshot_unavailable$'): client()
                self.assertEqual(calls[-1],'closed')

    def test_unknown_api_rejected_at_construction(self):
        for api in ('Core','states','/api/states/',None,['core'],''):
            with self.subTest(api=api),self.assertRaisesRegex(ValueError,'supported HA read api'):
                self.client(api=api)


REVISION='ab'*32


class Routed:
    """Connection factory answering per path; records every GET."""
    def __init__(self, routes):
        self.routes,self.calls=routes,[]

    def __call__(self, *a, **k):
        routed=self
        class Response:
            def __init__(self, status, body):
                self.status=status;self.data=json.dumps(body).encode()
            def getheader(self,name,default=None): return 'application/json' if name=='Content-Type' else default
            def read1(self,n):
                data,self.data=self.data[:n],self.data[n:]; return data
            def close(self): self.closed=True
        class Connection:
            sock=None
            def request(self,method,path,**kw): self.path=path;routed.calls.append(path)
            def getresponse(self):
                route=routed.routes.get(self.path,(404,{'error':'not_found'}))
                if isinstance(route,Exception): raise route
                return Response(*route)
            def close(self): pass
        return Connection()


def state(entity, value, **extra):
    return (200,{'entity_id':entity,'state':value,**extra})


class ListedClientTests(unittest.TestCase):
    def client(self, listed, wanted=('sensor.a',), extra_routes=None, **kw):
        from ha_client import ListedHAStateClient,LIST_PATH,STATE_PATHS
        base=STATE_PATHS['perception_lab_source']
        routes={LIST_PATH:(200,{'entities':listed,'revision':REVISION})}
        routes.update({base+e:state(e,'on') for e in listed})
        routes.update(extra_routes or {})
        factory=Routed(routes)
        client=ListedHAStateClient('https://ha.example',lambda:'test-only',wanted=wanted,clock=lambda:100,
                                   connection_factory=factory,**kw)
        return client,factory

    def test_lists_once_per_session_then_reads_only_listed_entities(self):
        client,net=self.client(['sensor.b','binary_sensor.a','sun.sun'],wanted=['sun.sun','sensor.b','binary_sensor.a'])
        self.assertEqual(net.calls,[])
        value,_=client()
        self.assertEqual(net.calls,['/api/perception_lab_source/v1/entities',
            '/api/perception_lab_source/v1/states/binary_sensor.a','/api/perception_lab_source/v1/states/sensor.b',
            '/api/perception_lab_source/v1/states/sun.sun'])
        self.assertEqual(value['states'],{'binary_sensor.a':'on','sensor.b':'on','sun.sun':'on'})
        self.assertEqual(value['entity_list'],{'revision':REVISION,'listed':['binary_sensor.a','sensor.b','sun.sun'],
                                               'read':['binary_sensor.a','sensor.b','sun.sun'],'unlisted':[]})
        self.assertEqual(value['observed_at'],100)
        client()
        self.assertEqual(net.calls.count('/api/perception_lab_source/v1/entities'),1)

    def test_wanted_narrows_the_listing_and_unlisted_wanted_are_never_requested(self):
        client,net=self.client(['sensor.b','binary_sensor.a','media_player.tv'],wanted=['sensor.b','sensor.not_in_den'])
        value,_=client()
        self.assertEqual(value['states'],{'sensor.b':'on'})
        self.assertEqual(value['entity_list']['read'],['sensor.b'])
        self.assertEqual(value['entity_list']['unlisted'],['sensor.not_in_den'])
        self.assertNotIn('/api/perception_lab_source/v1/states/media_player.tv',net.calls)
        self.assertNotIn('/api/perception_lab_source/v1/states/sensor.not_in_den',net.calls)
        client,net=self.client(['sensor.b'],wanted=['sensor.elsewhere'])
        value,_=client()
        self.assertEqual((value['states'],value['acquisition_duration_s'],value['entity_list']['read']),({},0,[]))
        self.assertEqual(net.calls,['/api/perception_lab_source/v1/entities'])

    def test_a_large_or_empty_listing_reads_only_wanted_and_an_invalid_one_is_refused(self):
        from ha_client import LIST_PATH,STATE_PATHS
        sixteen=[f'sensor.s{i}' for i in range(16)]
        client,net=self.client(sixteen,wanted=sixteen[:8])
        value,_=client()
        self.assertEqual((value['entity_list']['read'],len(value['entity_list']['listed'])),(sorted(sixteen[:8]),16))
        self.assertEqual(sorted(net.calls[1:]),sorted(STATE_PATHS['perception_lab_source']+e for e in sixteen[:8]))
        client,net=self.client([],wanted=['sensor.a'])  # an empty listing: nothing requested, all unknown
        value,_=client()
        self.assertEqual((value['states'],value['entity_list']['read'],value['entity_list']['unlisted']),
                         ({},[],['sensor.a']))
        self.assertEqual(net.calls,[LIST_PATH])
        bad=[{'entities':['sensor.a']},{'entities':['sensor.a'],'revision':'x'},
             {'entities':['sensor.a'],'revision':REVISION,'names':{'sensor.a':'Den'}},
             {'entities':'sensor.a','revision':REVISION},{'entities':['sensor.*'],'revision':REVISION},
             {'entities':['sensor.a','sensor.a'],'revision':REVISION},{'entities':['derived.computer_active'],'revision':REVISION},
             {'entities':[f'sensor.s{i}' for i in range(17)],'revision':REVISION},['sensor.a']]
        for body in bad:
            with self.subTest(body=body):
                client,_=self.client([],extra_routes={LIST_PATH:(200,body)})
                with self.assertRaisesRegex(ValueError,'^ha_entity_list_invalid$'): client()
        client,net=self.client([],extra_routes={LIST_PATH:(404,{'error':'not_found'})})  # a v0.1.0 view
        for _ in range(2):
            with self.assertRaisesRegex(ValueError,'^ha_entity_list_invalid$'): client()
        self.assertEqual(net.calls,[LIST_PATH])       # refused for the session, never re-listed

    def test_transient_listing_failure_retries_and_snapshot_failures_keep_their_rules(self):
        from ha_client import LIST_PATH,STATE_PATHS
        client,net=self.client(['sensor.a'],extra_routes={LIST_PATH:OSError('down')})
        with self.assertRaisesRegex(ValueError,'^ha_entity_list_unavailable$'): client()
        net.routes[LIST_PATH]=(200,{'entities':['sensor.a'],'revision':REVISION})
        self.assertEqual(client()[0]['states'],{'sensor.a':'on'})
        net.routes[STATE_PATHS['perception_lab_source']+'sensor.a']=(401,{'error':'unauthorized'})
        with self.assertRaisesRegex(ValueError,'^ha_snapshot_unavailable$'): client()

    def test_listing_needs_the_scoped_view_and_exact_wanted_ids(self):
        from ha_client import ListedHAStateClient
        with self.assertRaisesRegex(ValueError,'scoped'):
            ListedHAStateClient('https://ha.example',lambda:'x',wanted=['sensor.a'],api='core')
        # 'wanted' is required: the Lab never reads a whole listing.
        for wanted in (None,[],['sensor.*'],['sensor.a']*2,[f'sensor.s{i}' for i in range(9)],'sensor.a',5,[None]):
            with self.subTest(wanted=wanted),self.assertRaisesRegex(ValueError,'one to eight'):
                ListedHAStateClient('https://ha.example',lambda:'x',wanted=wanted)
        with self.assertRaises(TypeError):
            ListedHAStateClient('https://ha.example',lambda:'x')
        with self.assertRaisesRegex(ValueError,'HTTPS origin'):
            ListedHAStateClient('http://ha.example',lambda:'x',wanted=['sensor.a'])


class PresenceClientTests(unittest.TestCase):
    PERSON='person.engineeredlighting'

    def client(self, route):
        from ha_client import STATE_PATHS
        factory=Routed({STATE_PATHS['perception_lab_source']+self.PERSON:route})
        return HAStateClient('https://la-ha.example',lambda:'x',[self.PERSON],clock=lambda:100,connection_factory=factory,
                             api='perception_lab_source',projection='home_away_v1')

    def test_only_the_projected_home_away_value_is_kept(self):
        for value in ('home','away','unknown'):
            with self.subTest(value=value):
                snapshot,_=self.client(state(self.PERSON,value,projection='home_away_v1'))()
                self.assertEqual(snapshot['states'],{self.PERSON:value})
        self.assertEqual(self.client((404,{'error':'not_found'}))()[0]['states'],{self.PERSON:'unavailable'})

    def test_a_raw_zone_or_unprojected_body_is_refused_not_recorded(self):
        for route in (state(self.PERSON,'Work',projection='home_away_v1'),
                      state(self.PERSON,'not_home',projection='home_away_v1'),
                      state(self.PERSON,'home'),state(self.PERSON,'home',projection='zones_v1'),
                      state('person.other','home',projection='home_away_v1')):
            with self.subTest(route=route),self.assertRaisesRegex(ValueError,'^ha_snapshot_unavailable$'):
                self.client(route)()

    def test_projection_needs_one_person_on_the_scoped_view(self):
        for entities,api in ((['person.a'],'core'),(['device_tracker.phone'],'perception_lab_source'),
                             (['person.a','person.b'],'perception_lab_source')):
            with self.subTest(entities=entities,api=api),self.assertRaisesRegex(ValueError,'presence projection'):
                HAStateClient('https://la-ha.example',lambda:'x',entities,api=api,projection='home_away_v1')
        with self.assertRaisesRegex(ValueError,'presence projection'):
            HAStateClient('https://la-ha.example',lambda:'x',['person.a'],api='perception_lab_source',projection='zones')


class KeepAliveTests(unittest.TestCase):
    """The GETs of one snapshot share one connection. Measured from the Jetson,
    a new TLS connection costs ~0.25 s per GET and a reused one ~0.05 s: with
    a connection per GET a 7-entity snapshot took ~1.9 s of its 3 s validity."""
    ENTITIES=['light.a','light.b','sensor.c']

    def test_one_connection_per_snapshot_and_a_404_drops_it_before_the_next_get(self):
        from ha_client import STATE_PATHS
        events=[]
        class Response:
            def __init__(self,path):
                entity=path.rsplit('/',1)[1]
                self.status=404 if entity=='light.b' else 200
                self.data=json.dumps({'entity_id':entity,'state':'on'}).encode()
            def getheader(self,name,default=None): return 'application/json' if name=='Content-Type' else default
            def read1(self,n):
                data,self.data=self.data[:n],self.data[n:]; return data
            def close(self): self.closed=True
        class Connection:
            sock=None
            def request(self,method,path,**kw): self.path=path;events.append(path.rsplit('/',1)[1])
            def getresponse(self): return Response(self.path)
            def close(self): events.append('close')
        def factory(*a,**k):
            events.append('open');return Connection()
        client=HAStateClient('https://ha.example',lambda:'test-only',self.ENTITIES,clock=lambda:100,connection_factory=factory)
        self.assertEqual(events,[])
        value,_=client()
        self.assertEqual(value['states'],{'light.a':'on','light.b':'unavailable','sensor.c':'on'})
        self.assertEqual(events,['open','light.a','light.b','close','sensor.c','close'])
        events.clear();client()
        self.assertEqual(events[0],'open')            # never kept between snapshots

    def test_real_http_client_reuses_one_tcp_connection(self):
        import http.client
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        peers=[]
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            close_after=False
            def setup(self):
                super().setup();peers.append(self.client_address)
            def do_GET(self):
                entity=self.path.rsplit('/',1)[1]
                missing=entity=='light.b'
                body=json.dumps({'message':'Entity not found.'} if missing else {'entity_id':entity,'state':'on'}).encode()
                self.send_response(404 if missing else 200)
                self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)))
                if Handler.close_after:self.send_header('Connection','close')
                self.end_headers();self.wfile.write(body)
            def log_message(self,*a):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            origin='https://127.0.0.1:%d'%server.server_address[1]  # plain HTTP underneath: the factory decides
            client=HAStateClient(origin,lambda:'test-only',['light.a','sensor.c'],connection_factory=http.client.HTTPConnection)
            self.assertEqual(client()[0]['states'],{'light.a':'on','sensor.c':'on'})
            self.assertEqual(len(peers),1)
            client=HAStateClient(origin,lambda:'test-only',self.ENTITIES,connection_factory=http.client.HTTPConnection)
            self.assertEqual(client()[0]['states'],{'light.a':'on','light.b':'unavailable','sensor.c':'on'})
            self.assertEqual(len(peers),3)               # one more, plus one reconnect after the 404
            # A server that will not keep the connection alive still answers every GET.
            Handler.close_after=True
            self.assertEqual(client()[0]['states'],{'light.a':'on','light.b':'unavailable','sensor.c':'on'})
            self.assertEqual(len(peers),6)
        finally:
            server.shutdown();server.server_close()


class HAPumpTests(unittest.TestCase):
    setUp=fixtures.HAIngestTests.setUp
    tearDown=fixtures.HAIngestTests.tearDown
    source=fixtures.HAIngestTests.source

    def test_recording_starts_polling_and_stop_discards_inflight_result(self):
        from service_runtime import ServiceRuntime
        entered=threading.Event(); release=threading.Event()
        def fetch():
            entered.set(); release.wait(3)
            return {'snapshot_id':'late','observed_at':100,'states':{}},None
        class Recorder:
            def start(self,session): return {'state':'recording'}
            def stop(self,session): return {'state':'stopped'}
        runtime=ServiceRuntime(self.store,{'den':Recorder()},lambda *_:{'state':'blocked'},lambda *_:None,
            ha_sources={'den':self.source()},ha_clients={'den':fetch})
        self.assertFalse(entered.is_set())
        runtime.start_session(self.session)
        try:
            self.assertTrue(entered.wait(2))
            runtime.stop_session(self.session)
            release.set()
            self.assertTrue(runtime.close(timeout=2)['drained'])
            self.assertFalse(any(e['kind']=='ha.observation' for e in self.store.list_events(self.session['id'])))
        finally:
            release.set();runtime.close(timeout=3)


if __name__=='__main__': unittest.main()

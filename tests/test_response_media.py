import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from test_workbench import client as client, environment, submit, finish
from test_resources import PNG
from api_post.response_media import ResponseMedia


@pytest.fixture
def media_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length','0')))
            if self.path=='/binary':
                payload=PNG; mime='image/png'
            elif self.path=='/base64':
                payload=json.dumps({'data':[{'b64_json':base64.b64encode(PNG).decode()}]}).encode();mime='application/json'
            else:
                value={'data':[{'type':'image','url':'https://media.invalid/download?token=fixture-secret'}], 'video_url':'https://media.invalid/a.mp4', 'audio':{'url':'https://media.invalid/a.mp3'}}
                payload=json.dumps(value).encode();mime='application/json'
                if self.path=='/sse':
                    payload=b'data: '+payload+b'\n\ndata: [DONE]\n\n';mime='text/event-stream'
            self.send_response(400 if self.path=='/error' else 200)
            self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(payload)))
            self.end_headers();self.wfile.write(payload)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield 'http://127.0.0.1:'+str(server.server_port)
    server.shutdown();server.server_close();thread.join(2)


def test_response_urls_auto_saved_dedup_sources_and_signed_link_protection(client,media_server):
    env=environment(client,media_server)
    first=finish(client,submit(client,env,path='/json')['id'])
    assert first['state']=='completed' and len(first['response_resources'])==3
    assert 'fixture-secret' not in json.dumps(first)
    items=client.get('/api/resources').json()['items']
    assert {r['kind'] for r in items}=={'image','video','audio'}
    assert all(r['source_runs']==[first['id']] for r in items)
    assert all(not r['addresses'][0]['trusted'] for r in items)
    assert 'fixture-secret' not in json.dumps(items)
    image=next(r for r in items if r['kind']=='image')
    manager=client.app.state.resources
    assert manager.private_url(image,image['addresses'][0]).endswith('token=fixture-secret')
    second=finish(client,submit(client,env,path='/sse',stream=True)['id'])
    assert second['state']=='completed' and len(second['response_resources'])==3
    assert {r['id'] for r in second['response_resources']}=={r['id'] for r in first['response_resources']}
    assert client.get('/api/resources').json()['total']==3
    assert client.get('/api/resource-jobs').json()==[]
    assert all(second['id'] in r['source_runs'] for r in client.get('/api/resources').json()['items'])
    error=finish(client,submit(client,env,path='/error')['id'])
    assert not error.get('response_resources')


def test_response_base64_binary_dedup_and_capture_failure_keeps_request_result(client,media_server,monkeypatch):
    env=environment(client,media_server)
    first=finish(client,submit(client,env,path='/base64')['id'])
    assert len(first['response_resources'])==1
    asset=client.get('/api/resources/'+first['response_resources'][0]['id']).json()
    assert asset['file']['mime']=='image/png' and asset['file']['source']=='response'
    assert client.get('/api/resources/'+asset['id']+'/file').content==PNG
    binary=finish(client,submit(client,env,path='/binary')['id'])
    assert binary['response_resources'][0]['id']==asset['id']
    def failure(*args,**kwargs):raise OSError('not exposed')
    monkeypatch.setattr(client.app.state.resources,'capture_response',failure)
    result=finish(client,submit(client,env,path='/json')['id'])
    assert result['state']=='completed' and 'resource_capture_warning' in result
    assert 'not exposed' not in json.dumps(result)


def test_discovery_limits_and_no_arbitrary_urls():
    collector=ResponseMedia()
    collector.text(json.dumps({'help':'https://example.invalid/docs','script':'file:///a.png','image_url':'javascript:alert(1)'}))
    assert not collector.items
    collector.text(json.dumps({'image_urls':['https://example.invalid/'+str(n)+'.png' for n in range(100)]}))
    assert len(collector.items)==32 and collector.limited
    collector.text(json.dumps({'b64_json':'notbase64'}))
    assert len(collector.items)==32

def test_history_backfill_realistic_answer_links_and_idempotence(client,media_server):
    from pathlib import Path
    from api_post.storage import now
    env=environment(client,media_server)
    run=finish(client,submit(client,env,path='/empty')['id'])
    # Simulate a request saved before automatic capture was implemented.
    store=client.app.state.store
    path=store.path('runs',run['id'])/'response.body.txt'
    path.write_text(json.dumps({'code':0,'batch_data':[{'answer':['http://bj.oss.storage.internal.invalid/example-media/fixture-history.mp4']}]}),encoding='utf-8')
    record=store.run(run['id']);record.pop('response_resources',None);store.update_run(record)
    before=client.get('/api/resources').json()['total']
    result=client.post('/api/resources/collect-history').json()
    assert result['failed']==0 and result['updated']==1 and len(result['resource_ids'])==1
    resource=client.get('/api/resources/'+result['resource_ids'][0]).json()
    assert resource['kind']=='video' and resource['addresses'][0]['network_scope']=='internal'
    assert resource['addresses'][0]['trusted'] is False
    assert run['id'] in resource['source_runs']
    saved=client.get('/api/runs/'+run['id']).json()
    assert saved['response_resources'][0]['id']==resource['id']
    assert saved['request']==run['request'] and saved['state']==run['state']
    assert client.post('/api/resources/collect-history').status_code==200
    assert client.get('/api/resources').json()['total']==before+1
    assert client.get('/api/resource-jobs').json()==[]
    assert path.read_text(encoding='utf-8').find('fixture-history.mp4')!=-1


def test_history_sse_local_only_and_masked_urls_not_registered(client,media_server):
    from api_post.response_media import link_scope
    env=environment(client,media_server)
    run=finish(client,submit(client,env,path='/sse',stream=True)['id'])
    store=client.app.state.store
    row=store.run(run['id']);row.pop('response_resources',None);store.update_run(row)
    result=client.post('/api/resources/collect-history').json()
    # Signed URLs were redacted in the historical event; the recoverable two still link correctly.
    refs=client.get('/api/runs/'+run['id']).json()['response_resources']
    assert len(refs)==2 and result['failed']==0
    assert link_scope('http://bj.oss.storage.internal.invalid/a.mp4')=='internal'
    assert link_scope('http://bj.oss.storage.internal.invalid.evil.invalid/a.mp4')=='public'
    collector=ResponseMedia();collector.text('{"image_url":"https://example.invalid/a.png?token=%5BREDACTED%5D"}')
    assert not collector.items
    assert client.post('/api/resources/collect-history',headers={'Authorization':''}).status_code==401

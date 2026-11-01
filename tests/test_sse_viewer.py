import json
from test_workbench import client as client, upstream as upstream, environment, submit, finish


def test_complete_events_cursor_keeps_large_event_and_all_pages(client,upstream):
    run=finish(client,submit(client,environment(client,upstream),path='/sse',stream=True)['id'])
    store=client.app.state.store
    path=store.path('runs',run['id'])/'stream.events.ndjson'
    events=[{'sequence':n,'event':'message','data':json.dumps({'index':n}),'elapsed_ms':n} for n in range(1,602)]
    path.write_text(''.join(json.dumps(e)+'\n' for e in events),encoding='utf-8')
    offset=0;seen=[]
    while True:
        result=client.get('/api/runs/'+run['id']+'/events',params={'offset':offset,'full':'true'}).json()
        seen.extend(e['sequence'] for e in result['items'])
        assert result['offset']>offset
        offset=result['offset']
        if not result['has_more']:break
    assert seen==list(range(1,602))
    large={'sequence':1,'event':'message','data':'start'+('x'*300000)+'end','elapsed_ms':0}
    path.write_text(json.dumps(large)+'\n',encoding='utf-8')
    normal=client.get('/api/runs/'+run['id']+'/events').json()
    assert normal['items'][0]['event']=='preview_omitted'
    full=client.get('/api/runs/'+run['id']+'/events?full=true').json()
    assert full['items'][0]['data']==large['data'] and not full['has_more']
    assert client.get('/api/runs/'+run['id']+'/events?full=true',headers={'Authorization':''}).status_code==401

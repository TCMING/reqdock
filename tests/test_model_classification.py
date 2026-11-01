import json
from test_workbench import client as client
from api_post.model_classification import models, family


def create(client,name,group,body,path='/echo'):
    response=client.post('/api/cases',json={'group':group,'request':{'name':name,'path':path,'body':json.dumps(body)}})
    assert response.status_code==200
    return response.json()


def test_reclassify_models_request_truth_scope_pending_and_backup(client):
    actual=create(client,'qwen title is wrong','ibrain接口测试/Prompt工程/对话-OpenAI模式/Qwen',{'model':'deepseek-v4.1-flash'})
    pending=create(client,'seedream','第三方服务/字节',{'model':'ep-123'})
    prompt=create(client,'prompt-only','ibrain接口测试/Prompt工程/生视频/任意模型',{'prompt_id':123})
    asset=create(client,'asset','ibrain接口测试/Prompt工程/火山素材资产',{},'/asset')
    common=create(client,'models','ibrain接口测试/Prompt工程/OpenAI-多模态',{},'/v1/models')
    revision=client.get('/api/case-taxonomy').json()['revision']
    response=client.post('/api/cases/classify-models',params={'revision':revision})
    assert response.status_code==200
    assert response.json()['families']=={'DeepSeek':1,'待确认模型':2,'通用接口':1}
    def business(item):return client.get('/api/cases/'+item['id']).json()['modules'][0]['groups']
    assert business(actual)==['DeepSeek']
    assert business(pending)==['待确认模型'] and business(prompt)==['待确认模型']
    assert business(asset)==['火山素材资产'] and business(common)==['通用接口']
    detail=client.get('/api/cases/'+actual['id']).json()
    assert detail['request']==actual['request'] and detail['revision']==actual['revision']
    assert detail['modules'][0]['id']=='prompt'
    groups=client.get('/api/cases',params={'module':'prompt'}).json()['group_labels'].values()
    assert '对话-OpenAI模式' not in groups and '生视频' not in groups
    assert list((client.app.state.store.root/'case-classification-backups').glob('*.json'))
    current=client.get('/api/case-taxonomy').json()['revision']
    assert client.post('/api/cases/classify-models',params={'revision':current}).json()['changed']==0
    assert client.post('/api/cases/classify-models',params={'revision':revision}).status_code==400
    assert client.post('/api/cases/classify-models',params={'revision':current},headers={'Authorization':''}).status_code==401


def test_model_fields_only_no_guessing_from_messages_or_provider_ids():
    assert models('{"messages":[{"content":"gemini","model":"gemini-2.5-pro"}]}')==([],False)
    assert models('{"parameters":{"model_name":"Qwen3"}}')==(['Qwen3'],False)
    assert family('ep-20260709104646-v57l6') is None
    assert family('{{model}}') is None
    for value, expected in [('DeepSeek-V3.2','DeepSeek'),('gemini-3.1-pro-preview-sz','Gemini'),('anthropic.claude-opus-4-5','Claude'),('doubao-seedream-5-0','Seedream'),('MiniMax-Hailuo-2.3','Hailuo'),('wan3.0-video','万相（Wan）'),('Qwen3-Reranker-8B','Qwen')]:
        assert family(value)==expected

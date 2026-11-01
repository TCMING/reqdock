from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from test_workbench import client as client
from test_case_modules import case
from api_post.storage import Store


def catalog(client):
    return client.get('/api/case-taxonomy').json()


def command(client, action, kind, **extra):
    return client.put('/api/case-taxonomy', json={
        'revision': catalog(client)['revision'], 'action': action, 'kind': kind, **extra})


def assign(client, item, memberships, **extra):
    return client.put('/api/cases/'+item['id']+'/classification', json={
        'revision': catalog(client)['revision'], 'case_revision': item['revision'],
        'memberships': memberships, **extra})


def test_crud_assignment_reimport_restart_and_request_preservation(client):
    item = case(client, 'keep request', 'ibrain接口测试/base/视频/veo')
    original = deepcopy(client.app.state.store.case(item['id']))
    result = command(client, 'create', 'module', name='媒体应用').json()
    module = next(m for m in result['modules'] if m['name'] == '媒体应用')['id']
    result = command(client, 'create', 'business', module_id=module, name='生成/图片').json()
    business = next(b for b in result['businesses'] if b['name'] == '生成/图片')['id']
    assert any(b['name'] == '生成' for b in result['businesses'])
    assert assign(client, item, [{'module_id': module, 'business_id': business}]).status_code == 200
    assert client.get('/api/cases', params={'module': module, 'group': '生成'}).json()['total'] == 1
    assert client.get('/api/cases', params={'module': 'base'}).json()['total'] == 0
    assert command(client, 'rename', 'module', id=module, name='多媒体').status_code == 200
    parent = next(b for b in catalog(client)['businesses'] if b['module_id'] == module and b['name'] == '生成')['id']
    assert command(client, 'rename', 'business', id=parent, name='媒体生成').status_code == 200
    detail = client.get('/api/cases/'+item['id']).json()
    assert detail['modules'][0]['name'] == '多媒体'
    assert detail['modules'][0]['groups'] == ['媒体生成/图片']
    assert detail['request'] == original['request']
    # Neither index reconstruction nor reimporting source metadata undoes local classification.
    store = client.app.state.store
    imported = deepcopy(original)
    imported['source']['digest'] = 'changed-source'
    store.save_imported_case(imported)
    assert client.get('/api/cases/'+item['id']).json()['classification'] == detail['classification']
    reopened = Store(store.root)
    try:
        assert reopened.classified_case(item['id'])['classification'] == detail['classification']
        assert reopened.list_cases(module=module)['total'] == 1
    finally:
        reopened.db.close()
    assert command(client, 'delete', 'business', id=business).status_code == 400
    assert command(client, 'delete', 'module', id=module).status_code == 400
    current = store.case(item['id'])
    assert assign(client, current, [{'module_id': 'base', 'business_id': None}]).status_code == 200
    assert command(client, 'delete', 'business', id=business).status_code == 200
    assert command(client, 'delete', 'business', id=parent).status_code == 200
    assert command(client, 'delete', 'module', id=module).status_code == 200
    assert module not in [m['id'] for m in catalog(client)['modules']]
    assert client.get('/api/cases/'+item['id']).json()['request'] == original['request']


def test_multi_membership_validation_conflicts_and_auth(client):
    item = case(client, 'multi', 'base/chat')
    assert client.get('/api/cases', params={'group': 'base/chat'}).json()['total'] == 1
    assert client.get('/api/cases', params={'module': 'base', 'group': 'base/chat'}).json()['total'] == 1
    state = catalog(client)
    foreign = next(b for b in state['businesses'] if b['module_id'] == 'base')['id']
    assert assign(client, item, [{'module_id': 'gpt', 'business_id': foreign}]).status_code == 400
    assert assign(client, item, [{'module_id': 'missing', 'business_id': None}]).status_code == 400
    assert assign(client, item, []).status_code == 422
    assert assign(client, item, [{'module_id': 'base', 'business_id': None}], case_revision=99).status_code == 400
    assert assign(client, item, [{'module_id': 'base', 'business_id': None}], revision=99).status_code == 400
    memberships = [{'module_id': 'base', 'business_id': foreign}, {'module_id': 'gpt', 'business_id': None}]
    assert assign(client, item, memberships).status_code == 200
    assert client.get('/api/cases', params={'module': 'base'}).json()['total'] == 1
    assert client.get('/api/cases', params={'module': 'gpt'}).json()['total'] == 1
    assert command(client, 'create', 'module', name='base').status_code == 400
    assert command(client, 'create', 'module', name='../bad').status_code == 400
    assert command(client, 'create', 'business', module_id='base', name='bad//path').status_code == 400
    revision = catalog(client)['revision']
    response = command(client, 'create', 'module', name='new')
    assert response.status_code == 200
    assert client.put('/api/case-taxonomy', json={'revision': revision, 'action': 'delete', 'kind': 'module', 'id': 'gpt'}).status_code == 400
    # Request edits do not overwrite locally assigned membership.
    request = deepcopy(item['request']); request['name'] = 'edited request'
    assert client.put('/api/cases/'+item['id'], json={'revision': item['revision'], 'request': request}).status_code == 200
    assert client.get('/api/cases/'+item['id']).json()['classification'] == memberships
    headers = {'Authorization': ''}
    assert client.get('/api/case-taxonomy', headers=headers).status_code == 401
    assert client.put('/api/cases/'+item['id']+'/classification', headers=headers, json={}).status_code == 401


def test_atomic_conflicting_writers_and_failure(client, monkeypatch):
    store = client.app.state.store
    rev = catalog(client)['revision']
    def write(name):
        try:
            store.taxonomy_update({'revision': rev, 'action': 'create', 'kind': 'module', 'name': name})
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, ['A', 'B'])) == [False, True]
    before = catalog(client)
    def fail(*args, **kwargs):
        raise OSError('simulated disk failure')
    monkeypatch.setattr('api_post.storage.atomic', fail)
    try:
        store.taxonomy_update({'revision': before['revision'], 'action': 'create', 'kind': 'module', 'name': 'unsaved'})
    except OSError:
        pass
    assert catalog(client) == before

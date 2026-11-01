import json
import pytest
from fastapi.testclient import TestClient
from api_post.app import create_app
from test_workbench import MemoryVault


def local_app(root):
    root.mkdir(parents=True)
    (root/'access.json').write_text(json.dumps({'schema_version':1,'mode':'local'}),encoding='utf-8')
    return create_app(root,vault=MemoryVault())


def test_local_access_no_cookie_token_pair_or_csrf_required(tmp_path):
    root=tmp_path/'local';app=local_app(root)
    with TestClient(app,base_url='http://127.0.0.1:18765',client=('127.0.0.1',51000)) as c:
        assert not c.cookies
        assert c.get('/api/environments').status_code==200
        assert c.get('/api/sql/connections',headers={'Authorization':'invalid'}).status_code==200
        session=c.get('/api/auth/session')
        assert session.json()['access_mode']=='local' and session.json()['csrf']==''
        assert 'set-cookie' not in session.headers
        environment=c.post('/api/environments',json={'name':'anonymous local','base_url':'http://127.0.0.1:8080','network_scope':'local'})
        assert environment.status_code==200,environment.text
        assert c.post('/api/sql/environments',json={'name':'local SQL'},headers={'Origin':'http://127.0.0.1:18765','X-CSRF-Token':'not-needed'}).status_code==200
        assert c.post('/api/auth/code').status_code==410
        assert c.post('/api/auth/pair',json={'code':'no-code-required-local'}).status_code==410
        assert not c.cookies
    # Access mode is a persistent workspace setting, not a browser session.
    restarted=create_app(root,vault=MemoryVault())
    with TestClient(restarted,base_url='http://127.0.0.1:18765',client=('127.0.0.1',51000)) as c:
        assert c.get('/api/environments').json()[0]['name']=='anonymous local'
        assert c.get('/api/auth/session').json()['access_mode']=='local'


@pytest.mark.parametrize('headers',[
    {'Host':'evil.example'},
    {'Origin':'https://evil.example'},
    {'Origin':'null'},
    {'Sec-Fetch-Site':'cross-site'},
    {'Sec-Fetch-Site':'same-site'},
])
def test_local_access_rejects_untrusted_hosts_and_browser_origins(tmp_path,headers):
    app=local_app(tmp_path/'local')
    with TestClient(app,base_url='http://127.0.0.1:18765',client=('127.0.0.1',51000)) as c:
        assert c.get('/api/environments',headers=headers).status_code==403
        assert c.post('/api/sql/environments',headers=headers,json={'name':'blocked'}).status_code==403
        assert c.get('/api/sql/environments').status_code==200
        assert 'blocked' not in c.get('/api/sql/environments').json()


def test_local_access_requires_loopback_even_with_legacy_token(tmp_path):
    app=local_app(tmp_path/'local')
    with TestClient(app,base_url='http://127.0.0.1:18765',client=('198.51.100.25',51000),headers={'Authorization':'Bearer '+app.state.auth.master}) as c:
        assert c.get('/api/environments').status_code==403
        assert c.post('/api/service/stop').status_code==403


def test_local_browser_writes_keep_cross_site_defenses(tmp_path):
    app=local_app(tmp_path/'local')
    with TestClient(app,base_url='http://127.0.0.1:18765',client=('127.0.0.1',51000)) as c:
        assert c.post('/api/sql/environments',json={'name':'blocked'},headers={'Sec-Fetch-Site':'same-origin'}).status_code==403
        assert c.post('/api/sql/environments',content='name=blocked',headers={'Content-Type':'application/x-www-form-urlencoded'}).status_code==403
        assert c.post('/api/sql/environments',json={'name':'allowed'},headers={'Origin':'http://127.0.0.1:18765','Sec-Fetch-Site':'same-origin'}).status_code==200
        assert c.get('/api/sql/environments').json().count('allowed')==1


def test_invalid_local_access_configuration_is_not_silently_permissive(tmp_path):
    root=tmp_path/'invalid';root.mkdir();(root/'access.json').write_text('{"mode":"unknown"}')
    with pytest.raises(ValueError):create_app(root,vault=MemoryVault())

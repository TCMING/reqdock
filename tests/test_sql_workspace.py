import json
import time
import threading
from pathlib import Path
import pytest
from test_workbench import client as client
from mysql_fixture import MySQLFixture
from api_post.sql_policy import parse_statement,selected_statement

@pytest.fixture
def mysql():
    value=MySQLFixture();yield value;value.close()


def profile(client,mysql,write=False):
    r=client.post('/api/sql/connections',json={'name':'isolated SQL fixture','environment':'测试环境','host':'127.0.0.1','port':mysql.port,'username':'fixture','password':'fixture-password','database':'lab','network_scope':'local','tls':False,'allow_write':write,'trusted':True})
    assert r.status_code==200,r.text
    return r.json()


def start(client,p,sql,**extra):
    r=client.post('/api/sql/jobs',json={'connection_id':p['id'],'database':'lab','sql':sql,**extra});assert r.status_code==200,r.text;return r.json()


def wait(client,j):
    for _ in range(200):
        r=client.get('/api/sql/jobs/'+j['id']).json()
        if r['state']!='running':return r
        time.sleep(.03)
    raise AssertionError('SQL task not finished')


def test_sql_policy_selection_and_rejection():
    text="-- 说明\nSELECT ';' AS value;\nSELECT 2;"
    assert selected_statement(text,len(text)-2,len(text)-2)=='SELECT 2'
    assert parse_statement("SELECT 'a;b' AS a")[1]=='query'
    for sql in ['DROP TABLE t','TRUNCATE t','INSERT INTO t VALUES(1)','DELETE FROM t','UPDATE t SET a=1','SELECT 1; DELETE FROM t WHERE id=1','SELECT SLEEP(100)','SELECT GET_LOCK(\'a\',100)','SELECT @a:=1','SELECT 1 INTO OUTFILE \'a\'','/*!50000 DELETE FROM t */','CALL x()','EXPLAIN ANALYZE SELECT 1','UPDATE t JOIN u ON t.id=u.id SET t.a=1 WHERE t.id=1']:
        with pytest.raises(ValueError):parse_statement(sql)


def test_sql_wire_read_explain_and_api_isolation(client,mysql):
    before=client.get('/api/cases').json()['total'];api_runs=client.get('/api/runs').json()['total']
    p=profile(client,mysql)
    assert 'password' not in p
    assert client.get('/api/sql/connections/'+p['id']+'/databases').json()['items']==['lab']
    assert client.get('/api/sql/connections/'+p['id']+'/tables?database=lab').json()['items'][0]['name']=='records'
    cache=client.get('/api/sql/connections/'+p['id']+'/catalog-cache').json()
    assert cache['databases']==['lab'] and cache['tables']['lab'][0]['name']=='records'
    j=wait(client,start(client,p,'SELECT id,name,password FROM records WHERE id<3'))
    assert j['state']=='completed',j
    assert len(j['rows'])==2 and j['rows'][0][2]=='[REDACTED]'
    plan=wait(client,start(client,p,'SELECT * FROM records WHERE id=1',explain=True))
    assert plan['state']=='completed' and 'select_type' in plan['columns']
    assert client.post('/api/sql/jobs',json={'connection_id':p['id'],'database':'lab','sql':'UPDATE records SET name=\'x\' WHERE id=1'}).status_code==400
    assert client.get('/api/cases').json()['total']==before and client.get('/api/runs').json()['total']==api_runs
    doc=client.post('/api/sql/documents',json={'connection_id':p['id'],'database':'lab','title':'common fixture','sql':"SELECT 'never-plain-SQL'"}).json()
    raw=(client.app.state.store.root/'sql'/'queries'/doc['id']/'query.json').read_text()
    assert 'never-plain-SQL' not in raw
    assert client.get('/api/sql/documents/'+doc['id']).json()['sql']=="SELECT 'never-plain-SQL'"
    assert client.get('/api/sql/connections',headers={'Authorization':''}).status_code==401
    assert client.post('/api/sql/jobs',json={},headers={'Authorization':''}).status_code==401
    assert client.get('/api/sql/jobs/'+j['id'],headers={'Authorization':''}).status_code==401


def test_write_preview_confirm_cancel_expire_and_pool_recycle(client,mysql):
    p=profile(client,mysql,True);manager=client.app.state.sql_workspace;manager.IDLE=.15
    original=wait(client,start(client,p,"UPDATE records SET name='confirmed-100%' WHERE id=1"))
    assert original['state']=='awaiting_confirmation',(original,mysql.statements)
    assert original['row_count']==1
    assert not any(s.upper().startswith('UPDATE ') for s in mysql.statements)
    assert client.post('/api/sql/jobs/'+original['id']+'/confirm',json={'token':'00000000-0000-0000-0000-000000000000','confirmed':True}).status_code==400
    response=client.post('/api/sql/jobs/'+original['id']+'/confirm',json={'token':original['confirmation_token'],'confirmed':True});assert response.status_code==200,response.text
    result=wait(client,response.json());assert result['state']=='completed',result
    assert result['affected_rows']==1
    assert client.post('/api/sql/jobs/'+original['id']+'/confirm',json={'token':original['confirmation_token'],'confirmed':True}).status_code==400
    result=wait(client,start(client,p,'SELECT name FROM records WHERE id=1'));assert result['rows'][0][0]=='confirmed-100%'
    j=wait(client,start(client,p,'DELETE FROM records WHERE id=2'));assert j['state']=='awaiting_confirmation'
    client.post('/api/sql/jobs/'+j['id']+'/cancel')
    j=wait(client,start(client,p,'DELETE FROM records WHERE id=3'));manager.expire_preview(manager.jobs[j['id']]);assert client.get('/api/sql/jobs/'+j['id']).json()['state']=='expired'
    big=wait(client,start(client,p,"DELETE FROM records WHERE id>0"));assert big['state']=='failed'
    time.sleep(.4)
    assert manager.pool_status()==[]
    assert not manager.workers
    assert not any(s.upper().startswith('DELETE ') for s in mysql.statements)


def test_pool_per_database_three_and_result_cap(client,mysql):
    p=profile(client,mysql,True);m=client.app.state.sql_workspace
    a=[m.lease(p,'db-a') for _ in range(3)]
    with pytest.raises(ValueError):m.lease(p,'db-a')
    b=[m.lease(p,'db-b') for _ in range(3)]
    assert len(m.pool_status())==2 # No global connection cap.
    for e in a+b:m.connection(e)
    assert mysql.open_count==6
    for e in a+b:m.drop(e)
    j=wait(client,start(client,p,'SELECT id,name FROM records'))
    assert j['state']=='completed',j
    assert j['truncated'] and j['row_count']==500
    assert m.pool_status()==[]
    timed=wait(client,start(client,p,"SELECT 'fixture_slow'",timeout=1))
    assert timed['state']=='timed_out',timed


def test_tls_does_not_silently_downgrade_and_max_timeout(client,mysql):
    p=profile(client,mysql)
    data={k:v for k,v in p.items() if k!='id'}
    data['tls']=True
    assert client.put('/api/sql/connections/'+p['id'],json=data).status_code==200
    value=wait(client,start(client,p,'SELECT 1'))
    assert value['state']=='failed' and 'TLS' in value['error']
    assert mysql.auth_count==0
    assert client.post('/api/sql/jobs',json={'connection_id':p['id'],'database':'lab','sql':'SELECT 1','timeout':301}).status_code==422
    assert selected_statement('SELECT 1;',9,9)=='SELECT 1'


def test_sql_environment_create_empty_persist_and_api_isolation(client):
    before=client.get('/api/environments').json()
    result=client.post('/api/sql/environments',json={'name':'  国内预发环境  '})
    assert result.status_code==200 and result.json()=={'name':'国内预发环境','created':True}
    assert '国内预发环境' in client.get('/api/sql/environments').json()
    assert client.app.state.sql_workspace.connections()==[]
    assert client.app.state.sql_workspace.pool_status()==[]
    assert client.post('/api/sql/environments',json={'name':'国内预发环境'}).json()['created'] is False
    assert client.post('/api/sql/environments',json={'name':'   '}).status_code==422
    assert client.post('/api/sql/environments',json={'name':'bad\nname'}).status_code==422
    assert client.post('/api/sql/environments',json={'name':'x'*81}).status_code==422
    assert client.get('/api/environments').json()==before
    assert client.get('/api/sql/environments',headers={'Authorization':''}).status_code==401
    assert client.post('/api/sql/environments',json={'name':'未授权'},headers={'Authorization':''}).status_code==401
    stored=json.loads((client.app.state.store.root/'sql/environments.json').read_text(encoding='utf-8'))
    assert stored['names']==['国内预发环境']


def test_table_structure_parameter_binding_cache_auth_and_api_isolation(client,mysql):
    p=profile(client,mysql);url='/api/sql/connections/'+p['id']+'/table-structure'
    before=client.get('/api/cases').json()['total']
    r=client.get(url,params={'database':'lab','table':'records'})
    assert r.status_code==200,r.text
    value=r.json()
    assert value['engine']=='InnoDB' and value['estimated_rows']=='1104'
    assert value['columns'][0]['key']=='PRI'
    assert value['columns'][1]['nullable'] is True
    assert value['columns'][2]['default']=='[REDACTED]'
    assert value['cached'] is False
    count=len(mysql.statements)
    assert client.get(url,params={'database':'lab','table':'records'}).json()['cached'] is True
    assert len(mysql.statements)==count
    # Visiting more than 20 tables must preserve all previously viewed structures.
    for i in range(21):
        r=client.get(url,params={'database':'lab','table':'cached_table_'+str(i)})
        assert r.status_code==200,r.text
    directory=client.app.state.store.root/'sql'/'catalog'/p['id']/'structures'
    assert len(list(directory.glob('*.json')))==22
    count=len(mysql.statements)
    assert client.get(url,params={'database':'lab','table':'records'}).json()['cached'] is True
    assert client.get(url,params={'database':'lab','table':'cached_table_0'}).json()['cached'] is True
    assert len(mysql.statements)==count
    assert client.get(url,params={'database':'lab','table':'records','refresh':'true'}).json()['cached'] is False
    assert len(mysql.statements)>count
    # A crafted identifier stays a bound value, never an executable SQL suffix.
    evil="records' OR 1=1 --"
    r=client.get(url,params={'database':'lab','table':evil})
    assert r.status_code==200
    assert any("records\\' OR 1=1 --" in s for s in mysql.statements)
    assert client.get(url,params={'database':'lab','table':'records'},headers={'Authorization':''}).status_code==401
    files=list((client.app.state.store.root/'sql'/'catalog'/p['id']/'structures').glob('*.json'))
    assert files and all('Display name' not in f.read_text() for f in files)
    assert client.get('/api/cases').json()['total']==before


@pytest.mark.parametrize('version,errors,expected',[
    ('5.6.51',{'max_execution_time':1193,'max_statement_time':1193},None),
    ('5.5.5-10.6.0-MariaDB',{},'max_statement_time'),
    ('proxy-version',{'max_execution_time':1193},'max_statement_time'),
])
def test_timeout_variable_compatibility_and_metadata(client,mysql,version,errors,expected):
    mysql.version=version;mysql.timeout_errors=errors;p=profile(client,mysql)
    j=wait(client,start(client,p,'SELECT 1'))
    assert j['state']=='completed',j
    assert bool('服务端终止时间' in ' '.join(j['messages']))==(expected is None)
    url='/api/sql/connections/'+p['id']
    assert client.get(url+'/tables',params={'database':'lab'}).status_code==200
    assert client.get(url+'/table-structure',params={'database':'lab','table':'records'}).status_code==200
    sets=[s for s in mysql.statements if s.upper().startswith(('SET SESSION MAX_EXECUTION_TIME=', 'SET SESSION MAX_STATEMENT_TIME='))]
    if expected is None:
        assert len(sets)==2 # Unsupported variables are probed once per physical connection.
        timed=wait(client,start(client,p,"SELECT 'fixture_slow'",timeout=1))
        assert timed['state']=='timed_out',timed
        assert client.app.state.sql_workspace.pool_status()==[]
    else:
        assert any('max_statement_time=300' in s for s in sets)


def test_timeout_permission_error_not_silenced_and_metadata_details(client,mysql):
    mysql.timeout_errors={'max_execution_time':1227};p=profile(client,mysql)
    url='/api/sql/connections/'+p['id']
    r=client.get(url+'/tables',params={'database':'lab'})
    assert r.status_code==400 and '1227' in r.json()['detail']
    assert 'fixture permission denied' in r.json()['detail']
    assert not any('max_statement_time' in s for s in mysql.statements)
    assert client.app.state.sql_workspace.pool_status()==[]



def enable_create(client,p):
    data={k:v for k,v in p.items() if k!='id'};data['allow_create']=True
    response=client.put('/api/sql/connections/'+p['id'],json=data)
    assert response.status_code==200,response.text
    return response.json()


def test_create_table_policy_permission_confirm_and_cancel(client,mysql):
    statement="CREATE TABLE IF NOT EXISTS created_fixture(id BIGINT PRIMARY KEY, name VARCHAR(128) DEFAULT '' COMMENT '名称') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='示例'"
    assert parse_statement(statement)[1]=='create'
    for bad in ['CREATE DATABASE x','CREATE VIEW x AS SELECT 1','CREATE TABLE x AS SELECT 1','CREATE TABLE x LIKE records','CREATE TEMPORARY TABLE x(id INT)','CREATE OR REPLACE TABLE x(id INT)',"CREATE TABLE x(id INT) ENGINE=FEDERATED CONNECTION='mysql://x'", "CREATE TABLE x(id INT) DATA DIRECTORY='/tmp'",'CREATE TABLE x(id INT); DROP TABLE records']:
        with pytest.raises(ValueError):parse_statement(bad)
    p=profile(client,mysql)
    assert client.post('/api/sql/jobs',json={'connection_id':p['id'],'database':'lab','sql':statement}).status_code==400
    p=enable_create(client,p)
    assert client.post('/api/sql/jobs',json={'connection_id':p['id'],'database':'lab','sql':'CREATE TABLE otherdb.x(id INT)'}).status_code==400
    assert client.post('/api/sql/jobs',json={'connection_id':p['id'],'database':'lab','sql':'CREATE TABLE x(id INT, FOREIGN KEY(id) REFERENCES otherdb.t(id))'}).status_code==400
    pending=wait(client,start(client,p,statement))
    assert pending['state']=='awaiting_confirmation' and pending['mode']=='create'
    assert mysql.total==0 # Confirmation preview must not connect or send DDL.
    client.post('/api/sql/jobs/'+pending['id']+'/cancel')
    assert mysql.total==0
    pending=wait(client,start(client,p,statement))
    response=client.post('/api/sql/jobs/'+pending['id']+'/confirm',json={'token':pending['confirmation_token'],'confirmed':True})
    assert response.status_code==200,response.text
    result=wait(client,response.json())
    assert result['state']=='completed',result
    creates=[s for s in mysql.statements if s.startswith('CREATE TABLE')]
    assert len(creates)==1 and '`lab`.' in creates[0]
    assert client.post('/api/sql/jobs/'+pending['id']+'/confirm',json={'token':pending['confirmation_token'],'confirmed':True}).status_code==400
    assert client.post('/api/sql/jobs',json={'connection_id':p['id'],'database':'lab','sql':"UPDATE records SET name='x' WHERE id=1"}).status_code==400
    pending=wait(client,start(client,p,'CREATE TABLE expired_fixture(id INT)'))
    client.app.state.sql_workspace.expire_preview(client.app.state.sql_workspace.jobs[pending['id']])
    assert client.get('/api/sql/jobs/'+pending['id']).json()['state']=='expired'
    assert len([s for s in mysql.statements if s.startswith('CREATE TABLE')])==1


@pytest.mark.parametrize('disconnect,error,state',[(True,None,'unknown'),(False,1142,'failed')])
def test_create_table_disconnect_is_unknown_without_retry(client,mysql,disconnect,error,state):
    p=enable_create(client,profile(client,mysql));mysql.disconnect_create=disconnect;mysql.create_error=error
    pending=wait(client,start(client,p,'CREATE TABLE outcome_fixture(id INT)'))
    result=client.post('/api/sql/jobs/'+pending['id']+'/confirm',json={'token':pending['confirmation_token'],'confirmed':True})
    assert result.status_code==200,result.text
    final=wait(client,result.json());assert final['state']==state,final
    assert len([s for s in mysql.statements if s.startswith('CREATE TABLE')])==1
    assert client.app.state.sql_workspace.pool_status()==[]


def test_export_saved_results_csv_json_auth_and_no_requery(client,mysql):
    import csv,io
    p=profile(client,mysql)
    j=wait(client,start(client,p,"SELECT '=2+3' AS formula, '你好,世界' AS label, NULL AS missing, -7 AS number"))
    assert j['state']=='completed',j
    before=len(mysql.statements);url='/api/sql/jobs/'+j['id']+'/export'
    response=client.get(url,params={'format':'csv'})
    assert response.status_code==200 and response.content.startswith(b'\xef\xbb\xbf')
    values=list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
    assert values[1]==["'=2+3",'你好,世界','',"'-7"]
    assert 'attachment;' in response.headers['content-disposition']
    assert response.headers['cache-control']=='no-store'
    data=client.get(url,params={'format':'json'}).json()
    assert data['columns']==['formula','label','missing','number']
    assert data['rows'][0]==['=2+3','你好,世界',None,'-7']
    assert len(mysql.statements)==before
    assert client.get(url,headers={'Authorization':''}).status_code==401
    assert client.get(url,params={'format':'xlsx'}).status_code==422
    limited=wait(client,start(client,p,'SELECT id,password FROM records'))
    exported=client.get('/api/sql/jobs/'+limited['id']+'/export',params={'format':'json'}).json()
    assert exported['truncated'] is True and len(exported['rows'])==500
    assert exported['rows'][0][1]=='[REDACTED]'

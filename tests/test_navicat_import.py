import json,sqlite3
from pathlib import Path
from api_post.navicat_import import collect,apply,stable,api_snapshot
from api_post.private_data import transform
import base64


def fixture(root):
    folder=root/'server';folder.mkdir(parents=True)
    db=sqlite3.connect(folder/'id_cache.db')
    db.execute('CREATE TABLE IdentifierCache(SchemaName TEXT,ObjectName TEXT,ObjectType TEXT)')
    db.executemany('INSERT INTO IdentifierCache VALUES (?,?,?)',[('lab','lab','Schema'),('lab','records','Table')]);db.commit();db.close()
    (folder/'lab').mkdir();(folder/'lab'/'常用.sql').write_text("SELECT 'private-import-fixture' AS example;",encoding='utf-8')
    records=[{'name':'server','Host':'127.0.0.1','Port':3306,'UserName':'fixture','UseSSL':0,'UseSSH':0,'UseHTTP':0,'QuerySavePath':str(folder)}]
    return records


def test_navicat_metadata_only_import_encrypted_idempotent_and_api_unchanged(tmp_path):
    source=tmp_path/'navicat';records=fixture(source)
    workspace=tmp_path/'workspace';(workspace/'cases').mkdir(parents=True)
    (workspace/'cases'/'example.json').write_text('{"preserve":"API-data"}')
    before=api_snapshot(workspace)
    plan=collect(source,records,{'server':'test'})
    assert plan['report']['connections']==1 and plan['report']['queries']==1
    report=apply(workspace,plan)
    assert report['api_unchanged'] and before==api_snapshot(workspace)
    assert report['database_connections_opened']==0 and report['statements_executed']==0
    assert report['passwords_imported']==0
    profile=json.loads((workspace/'sql/connections.json').read_text(encoding='utf-8'))[0]
    assert profile['password_pending'] and not profile['allow_write'] and profile['environment']=='测试环境'
    assert 'password' not in profile
    doc=plan['documents'][0];path=workspace/'sql/queries'/doc['id']/'query.json'
    encrypted=json.loads(path.read_text(encoding='utf-8'));assert 'private-import-fixture' not in path.read_text(encoding='utf-8')
    restored=json.loads(transform(base64.b64decode(encrypted['dpapi']),decrypt=True))
    assert restored['sql']==doc['sql']
    again=apply(workspace,plan)
    assert again['created_connections']==0 and again['created_queries']==0 and again['unchanged_queries']==1
    # Preserve a user's edits if an import with the same stable source ID is repeated.
    from api_post.navicat_import import encrypted as protect
    restored['sql']='SELECT 999';path.write_text(json.dumps(protect(restored)))
    report=apply(workspace,plan)
    assert report['preserved_existing_queries']==1
    assert json.loads(transform(base64.b64decode(json.loads(path.read_text(encoding='utf-8'))['dpapi']),decrypt=True))['sql']=='SELECT 999'
    assert before==api_snapshot(workspace)


def test_navicat_does_not_read_untrusted_query_root_or_tunnel(tmp_path):
    source=tmp_path/'navicat';records=fixture(source)
    outside=tmp_path/'outside';outside.mkdir();(outside/'secret.sql').write_text('SELECT 42')
    records[0]['QuerySavePath']=str(outside)
    result=collect(source,records,{'server':'product'})
    assert result['documents']==[] and result['report']['warnings']
    records[0]['UseSSH']=1
    result=collect(source,records,{})
    assert result['profiles']==[] and result['report']['warnings']

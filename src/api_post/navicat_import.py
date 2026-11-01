"""Read local Navicat MySQL metadata only; import to SQL files, never open a database connection."""
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4,uuid5,NAMESPACE_URL
from .storage import atomic
from .private_data import transform
from .sql_workspace import ConnectionInput
import base64

SYSTEM={'information_schema','performance_schema','sys','mysql'}
ENV={'test':'测试环境','product':'线上环境','intl_product':'海外线上环境','intl_test':'海外测试环境'}
FIELDS=('Host','Port','UserName','UseSSL','UseSSLAuthen','UseSSH','UseHTTP','QuerySavePath')


def stable(value):return str(uuid5(NAMESPACE_URL,'api-lab/navicat/mysql/'+value))
def digest(value):return hashlib.sha256(value).hexdigest()
def read_json(path,default):return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default


def read_registry():
    import winreg
    base=winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\PremiumSoft\Navicat\Servers')
    result=[]
    for i in range(winreg.QueryInfoKey(base)[0]):
        name=winreg.EnumKey(base,i)
        with winreg.OpenKey(base,name) as key:
            item={'name':name}
            # Never read Pwd*, SSH passwords, registration or license values.
            for field in FIELDS:
                try:item[field]=winreg.QueryValueEx(key,field)[0]
                except FileNotFoundError:pass
            result.append(item)
    base.Close();return result


def groups_from(path):
    groups={}
    for group in read_json(path,{}).get('vgroups',[]):
        if group.get('vgroup_type')!='CONNECTION':continue
        for item in group.get('items',[]):
            if item.get('server_type')=='MYSQL' and item.get('type')=='CONNECTION':
                groups[item['name']]=group['vgroup_name']
    return groups


def cache_from(folder):
    path=folder/'id_cache.db'
    if not path.is_file():return {},None
    if not path.resolve().is_relative_to(folder.resolve()):return {},'目录缓存链接越界，未读取'
    if path.stat().st_size>64*1024*1024:return {},'目录缓存超过64MiB，未读取'
    try:
        try:db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)
        except sqlite3.OperationalError:
            # No WAL means the base file is a complete snapshot; immutable avoids creating a new SHM.
            wal=Path(str(path)+'-wal')
            if wal.exists() and wal.stat().st_size:raise
            db=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True,timeout=2)
        with db:
            db.execute('PRAGMA trusted_schema=OFF');db.execute('PRAGMA query_only=ON')
            rows=db.execute("SELECT SchemaName,ObjectName,ObjectType FROM IdentifierCache WHERE ObjectType IN ('Schema','Table','View') LIMIT 20001").fetchall()
        db.close()
        if len(rows)>20000:return {},'缓存元数据超过20000项，未读取'
        databases=set();tables={}
        for schema,name,kind in rows:
            if not schema or len(schema)>64:continue
            databases.add(schema)
            if kind in {'Table','View'} and name and len(name)<=64:
                tables.setdefault(schema,[]).append({'name':name,'kind':'VIEW' if kind=='View' else 'BASE TABLE'})
        return {'databases':sorted(databases),'tables':tables},None
    except sqlite3.Error:return {},'本地目录缓存不可读，未连接服务器补查'


def decode_sql(path):
    if path.stat().st_size>128*1024:raise ValueError('SQL文件超过128KiB')
    raw=path.read_bytes()
    if raw.startswith((b'\xff\xfe',b'\xfe\xff')):value=raw.decode('utf-16')
    else:
        try:value=raw.decode('utf-8-sig')
        except UnicodeDecodeError:value=raw.decode('gb18030')
    if '\x00' in value:raise ValueError('SQL文件包含NUL，不作为文本导入')
    return value


def collect(base,records,groups):
    base=Path(base).resolve();profiles=[];documents=[];catalog={};warnings=[];source_paths=set();roots={}
    report={'source_connections':len(records),'connections':0,'queries':0,'databases':0,'tables':0,'groups':{},'warnings':warnings,'orphan_aliases':[]}
    for row in records:
        name=row['name'];group=groups.get(name,'未分组')
        if row.get('UseSSH') or row.get('UseHTTP') or row.get('UseSSLAuthen'):
            warnings.append({'connection':name,'reason':'此连接使用SSH/HTTP/客户端证书，第一版不能直接恢复'});continue
        try:
            host=str(row.get('Host',''));scope='local' if host in {'localhost','127.0.0.1','::1'} else 'internal'
            value=ConnectionInput(name=name,environment=ENV.get(group,group),host=host,port=int(row.get('Port',3306)),username=row.get('UserName',''),password='',database='',network_scope=scope,tls=bool(row.get('UseSSL')),allow_write=False,trusted=True).model_dump(exclude={'password','revision'})
        except Exception:
            warnings.append({'connection':name,'reason':'连接元数据格式不支持'});continue
        value.update(id=stable('connection/'+name),revision=1,password_pending=True)
        folder=Path(row.get('QuerySavePath') or base/name).resolve()
        if not folder.is_relative_to(base):
            warnings.append({'connection':name,'reason':'查询目录不在已授权Navicat Servers目录，未读取'});cache={}
        else:
            cache,warning=cache_from(folder)
            if warning:warnings.append({'connection':name,'reason':warning})
            roots[folder]=value;source_paths.add(folder)
        business=[d for d in cache.get('databases',[]) if d.lower() not in SYSTEM]
        files=sorted(folder.rglob('*.sql')) if folder.is_relative_to(base) and folder.is_dir() else []
        db_counts=Counter()
        for file in files:
            file=file.resolve()
            if not file.is_relative_to(folder) or len(file.relative_to(folder).parts)>8:continue
            parts=file.relative_to(folder).parts
            db=parts[0] if len(parts)>1 else business[0] if len(business)==1 else None
            if not db or len(db)>64:
                warnings.append({'connection':name,'file':file.name,'reason':'无法唯一确定所属库，未猜测'});continue
            try:sql=decode_sql(file)
            except (ValueError,UnicodeError,OSError):
                warnings.append({'connection':name,'file':file.name,'reason':'SQL文本超限或编码不可读'});continue
            key='query/'+name+'/'+file.relative_to(folder).as_posix()
            documents.append({'id':stable(key),'revision':1,'connection_id':value['id'],'database':db,'title':file.stem[:120],'sql':sql,'updated_at':datetime.fromtimestamp(file.stat().st_mtime,timezone.utc).isoformat()})
            db_counts[db]+=1
        known=set(cache.get('databases',[]))|set(db_counts)
        candidates=sorted(d for d in known if d.lower() not in SYSTEM)
        value['database']=db_counts.most_common(1)[0][0] if db_counts else candidates[0] if candidates else ''
        cache.update(databases=sorted(known),revision=1,source='navicat_local_cache')
        catalog[value['id']]=cache;profiles.append(value)
        report['groups'][value['environment']]=report['groups'].get(value['environment'],0)+1
    # Recover a renamed query folder only when schema + international group identify one connection.
    for file in base.rglob('*.sql'):
        file=file.resolve()
        if not file.is_relative_to(base):continue
        if any(file.is_relative_to(p) for p in source_paths):continue
        parts=file.relative_to(base).parts
        if len(parts)<3:continue
        alias,db=parts[:2]
        candidates=[p for p in profiles if db in catalog[p['id']].get('databases',[]) and
                    ('intl' in alias.lower())==p['environment'].startswith('海外')]
        if len(candidates)!=1:
            warnings.append({'file':file.name,'folder':alias,'reason':'孤立SQL目录无唯一连接，未导入'});continue
        try:sql=decode_sql(file)
        except (ValueError,UnicodeError,OSError):warnings.append({'file':file.name,'reason':'孤立SQL文本不可读'});continue
        p=candidates[0]
        documents.append({'id':stable('orphan-query/'+file.relative_to(base).as_posix()),'revision':1,'connection_id':p['id'],'database':db,'title':file.stem[:120],'sql':sql,'updated_at':datetime.fromtimestamp(file.stat().st_mtime,timezone.utc).isoformat()})
        report['orphan_aliases'].append({'folder':alias,'connection':p['name'],'file':file.name})
    report.update(connections=len(profiles),queries=len(documents),databases=sum(len(c.get('databases',[])) for c in catalog.values()),tables=sum(len(t) for c in catalog.values() for t in c.get('tables',{}).values()))
    return {'profiles':profiles,'documents':documents,'catalog':catalog,'report':report}


def snapshot(root,exclude_imports=False):
    result={}
    if root.exists():
        for p in root.rglob('*'):
            if p.is_file() and not (exclude_imports and p.is_relative_to(root/'imports')):
                result[p.relative_to(root).as_posix()]=digest(p.read_bytes())
    return result


def api_snapshot(workspace):
    result={}
    for section in ('cases','environments','environment_groups','resources','runs','domains','imports'):
        folder=workspace/section
        if not folder.exists():continue
        for p in folder.rglob('*'):
            if p.is_file() and not p.is_relative_to(workspace/'resources'/'objects'):
                result[p.relative_to(workspace).as_posix()]=digest(p.read_bytes())
    for filename in ('case-taxonomy.json',):
        p=workspace/filename
        if p.exists():result[filename]=digest(p.read_bytes())
    return result


def encrypted(value):
    return {'schema_version':1,'dpapi':base64.b64encode(transform(json.dumps(value,ensure_ascii=False).encode())).decode()}


def apply(workspace,plan):
    workspace=Path(workspace).resolve();root=workspace/'sql'
    if root.exists() and root.resolve()!=root:raise ValueError('SQL目录不允许链接跳转')
    before_api=api_snapshot(workspace);before=snapshot(root,True)
    connections=read_json(root/'connections.json',[]);documents=read_json(root/'documents.json',[])
    if len(connections)+sum(not any(p['id']==c['id'] for c in connections) for p in plan['profiles'])>100:raise ValueError('导入会超过100个连接配置')
    if len(documents)+sum(not any(p['id']==d['id'] for d in documents) for p in plan['documents'])>1000:raise ValueError('导入会超过1000个查询页')
    transaction=root/'imports'/str(uuid4());staged=transaction/'staged';operations=[]
    report=dict(plan['report']);report.update(created_connections=0,created_queries=0,unchanged_queries=0,preserved_existing_queries=0,passwords_imported=0)
    for p in plan['profiles']:
        if not any(v['id']==p['id'] for v in connections):connections.append(p);report['created_connections']+=1
        target=next(v for v in connections if v['id']==p['id'])
        cache=read_json(root/'catalog'/p['id']/'current.json',{})
        if cache.get('revision')!=target['revision']:cache={}
        source=plan['catalog'][p['id']]
        known=set(cache.get('databases',[]))|set(source.get('databases',[]))
        tables=dict(source.get('tables',{}));tables.update(cache.get('tables',{}))
        merged={**cache,'databases':sorted(known),'tables':tables,'revision':target['revision'],'source':'navicat_local_cache'}
        operations.append(('catalog/'+p['id']+'/current.json',merged))
    for d in plan['documents']:
        if any(v['id']==d['id'] for v in documents):
            path=root/'queries'/d['id']/'query.json'
            existing=read_json(path,{})
            try:old=json.loads(transform(base64.b64decode(existing['dpapi']),decrypt=True))
            except Exception:report['preserved_existing_queries']+=1;continue
            report['unchanged_queries' if old.get('sql')==d['sql'] else 'preserved_existing_queries']+=1
            continue
        documents.append({k:v for k,v in d.items() if k!='sql'});operations.append(('queries/'+d['id']+'/query.json',encrypted(d)));report['created_queries']+=1
    operations.extend([('connections.json',connections),('documents.json',documents)])
    for relative,value in operations:
        target=root/relative
        if not target.resolve().is_relative_to(root):raise ValueError('SQL目标路径越界')
        if not (staged/relative).resolve().is_relative_to(root):raise ValueError('SQL导入暂存目录越界')
        if not (transaction/'before'/relative).resolve().is_relative_to(root):raise ValueError('SQL导入备份目录越界')
        atomic(staged/relative,value)
        if target.exists():
            backup=transaction/'before'/relative;backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,backup)
    if snapshot(root,True)!=before:raise ValueError('SQL空间正在修改，请重试；尚未发布导入')
    atomic(transaction/'manifest.json',{'state':'prepared','files':[name for name,_ in operations]})
    for relative,_ in operations:
        atomic(root/relative,read_json(staged/relative,None))
    after_api=api_snapshot(workspace)
    report.update(api_unchanged=before_api==after_api,api_files_checked=len(before_api),database_connections_opened=0,statements_executed=0,backup=str(transaction))
    atomic(transaction/'report.json',report)
    atomic(transaction/'manifest.json',{'state':'complete','files':[name for name,_ in operations]})
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,default=Path(__file__).resolve().parents[2]/'workspace')
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    docs=Path.home()/'Documents/Navicat'
    plan=collect(docs/'MySQL/Servers',read_registry(),groups_from(docs/'Premium/profiles/vgroup.json'))
    if args.apply:
        from .__main__ import call_local
        pools=call_local(args.workspace.resolve(),18765,'/api/sql/pools')
        if any(p.get('busy') for p in pools):raise SystemExit('SQL空间有运行或待确认任务，未导入')
        report=apply(args.workspace,plan)
    else:report=plan['report']
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

"""Independent local SQL workspace. Per-database pools, ephemeral workers, encrypted evidence."""
import asyncio
import base64
import hashlib
import csv
import io
import ipaddress
import json
import shutil
import socket
import ssl
import threading
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4
import pymysql
from pymysql.cursors import SSCursor
from pymysql.constants import CLIENT
from sqlglot import exp
from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import Field, field_validator
from typing import Literal
from .models import StrictModel, ExecutionContext
from . import executions
from .storage import atomic, now
from .private_data import transform
from .security import sensitive, redact, safe_text
from .network import check_ip
from .sql_policy import parse_statement, selected_statement, quote


class SQLEnvironmentInput(StrictModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        value=value.strip()
        if not value or any(ord(c)<32 or ord(c)==127 for c in value):
            raise ValueError("环境名称不能为空或含控制字符")
        return value


class ConnectionInput(StrictModel):
    revision: int | None = Field(default=None, ge=1)
    name: str = Field(min_length=1, max_length=80)
    environment: str = Field(default="测试环境", min_length=1, max_length=80)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=3306, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=80)
    password: str | None = Field(default=None, max_length=4096)
    database: str = Field(default="", max_length=64)
    network_scope: Literal["local", "internal", "public"] = "internal"
    tls: bool = True
    allow_write: bool = False
    allow_create: bool = False
    trusted: Literal[True]

    @field_validator("host")
    @classmethod
    def host_name(cls, v):
        if v.endswith('.') or '%' in v or not all(c.isascii() and (c.isalnum() or c in '-.:') for c in v):
            raise ValueError("仅支持明确的ASCII域名或IP，不支持URL、代理或主机混淆")
        return v.lower()


class SQLText(StrictModel):
    execution_context: ExecutionContext = Field(default_factory=ExecutionContext)
    connection_id: UUID
    database: str = Field(min_length=1, max_length=64)
    sql: str = Field(min_length=1, max_length=131072)
    selection_start: int = Field(default=0, ge=0)
    selection_end: int = Field(default=0, ge=0)
    explain: bool = False
    timeout: int = Field(default=300, ge=1, le=300)


class QueryDocument(StrictModel):
    id: UUID | None = None
    revision: int | None = Field(default=None, ge=1)
    connection_id: UUID
    database: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=120)
    sql: str = Field(default="", max_length=131072)


class Confirm(StrictModel):
    token: UUID
    confirmed: Literal[True]


class BoundedConnection(pymysql.connections.Connection):
    def _request_authentication(self):
        if self.ssl and not self.server_capabilities & CLIENT.SSL:
            raise pymysql.OperationalError(2026, "服务器不支持TLS，已拒绝降级连接")
        return super()._request_authentication()

    def _read_bytes(self, num_bytes):
        if num_bytes > 4 * 1024 * 1024:
            self._force_close()
            raise pymysql.OperationalError(2013, "单个数据库响应包超出4MiB限制")
        return super()._read_bytes(num_bytes)


class SQLWorkspace:
    IDLE = 60
    PREVIEW_ROWS = 1000
    def __init__(self, store, vault, port):
        self.store, self.vault, self.port = store, vault, port
        self.root = store.root / 'sql'
        self.guard = threading.RLock()
        self.pools = {}
        self.jobs = {}
        self.workers = set()
        self.closed = False
        with store.lock:
            store.db.execute('CREATE TABLE IF NOT EXISTS sql_runs (id TEXT PRIMARY KEY, created TEXT, summary TEXT)')
            executions.recover_sql(store.db)
            store.db.commit()

    def read(self, path, default=None):
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default

    def encrypted(self, path, value):
        atomic(path, {'schema_version': 1, 'dpapi': base64.b64encode(transform(json.dumps(value,ensure_ascii=False).encode())).decode()})

    def decrypted(self, path):
        if not path.exists(): raise KeyError('SQL记录不存在')
        return json.loads(transform(base64.b64decode(self.read(path)['dpapi']), decrypt=True))

    def catalog_cache(self,id):
        p=self.profile(id)
        value=self.read(self.root/'catalog'/str(UUID(str(id)))/'current.json',{})
        return value if value.get('revision')==p['revision'] else {}

    def environments(self):
        with self.guard:
            saved=self.read(self.root/'environments.json',{'names':[]})
            names=[p['environment'] for p in self.connections()]+saved['names']+['测试环境','线上环境','海外测试环境','海外线上环境']
            return list(dict.fromkeys(names))

    def create_environment(self,name):
        with self.guard:
            existing=self.environments()
            same=next((v for v in existing if v.casefold()==name.casefold()),None)
            if same:return {'name':same,'created':False}
            if len(existing)>=100:raise ValueError('SQL环境最多100个')
            saved=self.read(self.root/'environments.json',{'names':[]})
            atomic(self.root/'environments.json',{'schema_version':1,'names':[*saved['names'],name]})
            return {'name':name,'created':True}

    def connections(self):
        return self.read(self.root/'connections.json', [])

    def profile(self, id):
        for p in self.connections():
            if p['id'] == str(id): return p
        raise KeyError('SQL连接不存在')

    def save_connection(self, data, id=None):
        with self.guard:
            items=self.connections(); old=self.profile(id) if id else None
            if old and data.revision != old['revision']: raise ValueError('连接配置已变化，请刷新')
            if old and any(e['busy'] for key, pool in self.pools.items() if key[0]==str(id) for e in pool):
                raise ValueError('此连接有执行或待确认任务，请先结束')
            if not old and len(items)>=100: raise ValueError('最多保存100个连接配置')
            value=data.model_dump(exclude={'password','revision'}); value.update(id=str(id or uuid4()),revision=old['revision']+1 if old else 1)
            if value['network_scope']=='public' and not value['tls']: raise ValueError('公网数据库必须启用TLS证书校验')
            if value['network_scope']=='local' and value['host'] not in {'localhost','127.0.0.1','::1'}:raise ValueError('本机范围只允许回环主机')
            if not old and data.password is None: raise ValueError('请输入密码（无密码可明确留空）')
            if data.password is not None:self.vault.set('sql-password:'+value['id'],data.password)
            for key in list(self.pools):
                if key[0]==value['id']:
                    for e in list(self.pools[key]):self.drop(e)
            atomic(self.root/'connections.json',[value if p['id']==value['id'] else p for p in items] if old else [*items,value])
            return value

    def archive_connection(self, id, revision):
        with self.guard:
            p=self.profile(id)
            if p['revision']!=revision:raise ValueError('配置已变化，请刷新')
            if any(e['busy'] for key,pool in self.pools.items() if key[0]==str(id) for e in pool):raise ValueError('连接有执行或待确认任务')
            for key in list(self.pools):
                if key[0]==str(id):
                    for e in list(self.pools[key]):self.drop(e)
            atomic(self.root/'connections.json',[p for p in self.connections() if p['id']!=str(id)])
            self.vault.delete('sql-password:'+str(id))
            return {'ok':True}

    def documents(self, connection_id, database):
        with self.guard:
            items=self.read(self.root/'documents.json',[])
            return [p for p in items if p['connection_id']==str(connection_id) and p['database']==database]

    def document(self,id):return self.decrypted(self.root/'queries'/str(UUID(str(id)))/'query.json')

    def save_document(self,data):
        with self.guard:
            self.profile(data.connection_id)
            items=self.read(self.root/'documents.json',[])
            old=self.document(data.id) if data.id else None
            if old and old['revision']!=data.revision:raise ValueError('查询页已变化，请刷新')
            if not old and len(items)>=1000:raise ValueError('最多保存1000个查询页')
            value=data.model_dump(mode='json',exclude={'revision'});value.update(id=str(data.id or uuid4()),revision=old['revision']+1 if old else 1,updated_at=now())
            self.encrypted(self.root/'queries'/value['id']/'query.json',value)
            summary={k:v for k,v in value.items() if k!='sql'}
            atomic(self.root/'documents.json',[summary,*[p for p in items if p['id']!=value['id']]])
            return value

    def persist(self,job):
        if self.closed:return
        value={k:v for k,v in job.items() if not k.startswith('_')}
        self.encrypted(self.root/'runs'/job['id']/'record.json',value)
        summary={k:value.get(k) for k in ('id','created_at','connection_id','connection_name','environment','database','state','mode','elapsed_ms','row_count','execution_context')}
        with self.store.lock:
            self.store.db.execute('INSERT INTO sql_runs VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET summary=excluded.summary',(value['id'],value['created_at'],json.dumps(summary,ensure_ascii=False)))
            executions.index(self.store.db, 'sql', value)
            self.store.db.commit()

    def record(self,id):
        with self.guard:
            if str(id) in self.jobs:return {k:v for k,v in self.jobs[str(id)].items() if not k.startswith('_')}
        value=self.decrypted(self.root/'runs'/str(UUID(str(id)))/'record.json')
        if value['state'] in {'running','awaiting_confirmation'}:
            if value['state']=='running' and value.get('mode')=='create':value.update(state='unknown',error='客户端已退出，建表结果未知；请人工核对表目录，勿直接重试')
            else:value.update(state='interrupted',error='客户端已退出，未自动重放SQL；如提交阶段中断需人工核对')
        return value

    def history(self,limit=50):
        with self.store.lock:
            rows=self.store.db.execute('SELECT summary FROM sql_runs ORDER BY created DESC LIMIT ?',(limit,)).fetchall()
        result=[]
        for row in rows:
            value=json.loads(row[0])
            if value['id'] not in self.jobs and value['state'] in {'running','awaiting_confirmation'}:value['state']='unknown' if value['state']=='running' and value.get('mode')=='create' else 'interrupted'
            result.append(value)
        return result

    def drop(self,e):
        with self.guard:
            for name in ('timer','deadline'):
                if e.get(name):e[name].cancel();e[name]=None
            pending_socket=e.pop('connecting_socket',None)
            if pending_socket:
                try:pending_socket.shutdown(socket.SHUT_RDWR)
                except OSError:pass
                pending_socket.close()
            conn=e.get('conn')
            if conn:
                try:
                    sock=conn._sock
                    if sock:
                        try:sock.shutdown(socket.SHUT_RDWR)
                        except OSError:pass
                    conn._force_close()
                except Exception:pass
            e['conn']=None
            pool=self.pools.get(e['key'],[])
            if e in pool:pool.remove(e)
            if not pool:self.pools.pop(e['key'],None)

    def lease(self,p,database):
        quote(database)
        with self.guard:
            if self.closed:raise ValueError('SQL空间正在关闭')
            key=(p['id'],database);pool=self.pools.setdefault(key,[])
            e=next((v for v in pool if not v['busy']),None)
            if e:
                if e.get('timer'):e['timer'].cancel();e['timer']=None
                e['busy']=True;return e
            if len(pool)>=3:raise ValueError('此库的3条连接均在使用，请等待任务结束或取消待确认任务')
            e={'key':key,'busy':True,'conn':None,'timer':None,'deadline':None,'profile':p}
            pool.append(e);return e

    def release(self,e):
        with self.guard:
            if e.get('deadline'):e['deadline'].cancel();e['deadline']=None
            if e not in self.pools.get(e['key'],[]):return
            if not e.get('conn') or not e['conn'].open:self.drop(e);return
            e['busy']=False
            e['timer']=threading.Timer(self.IDLE,lambda:self.expire_idle(e));e['timer'].daemon=True;e['timer'].start()

    def expire_idle(self,e):
        with self.guard:
            if not e['busy']:self.drop(e)

    def connection(self,e):
        if e.get('conn') and e['conn'].open:return e['conn']
        p=e['profile'];password=self.vault.get('sql-password:'+p['id'])
        if password is None:raise ValueError('SQL密码缺失，请重新保存连接')
        records=socket.getaddrinfo(p['host'],p['port'],type=socket.SOCK_STREAM)
        addresses=list(dict.fromkeys(r[4][0] for r in records))
        for ip in addresses:check_ip(ip,p['network_scope'],p['port'],self.port)
        if not addresses:raise ValueError('数据库域名没有解析结果')
        context=ssl.create_default_context() if p['tls'] else None
        conn=BoundedConnection(host=p['host'],port=p['port'],user=p['username'],password=password.encode('utf-8'),database=e['key'][1] or None,charset='utf8mb4',autocommit=False,connect_timeout=10,read_timeout=10,write_timeout=10,local_infile=False,cursorclass=SSCursor,ssl=context,ssl_disabled=not p['tls'],defer_connect=True,client_flag=CLIENT.FOUND_ROWS,program_name='ReqDock SQL')
        last=None
        for ip in addresses:
            try:
                sock=socket.create_connection((ip,p['port']),timeout=10)
                with self.guard:
                    if self.closed or e not in self.pools.get(e['key'],[]):
                        sock.close();raise ValueError('连接任务已取消')
                    e['connecting_socket']=sock;e['conn']=conn
                try:conn.connect(sock=sock)
                except Exception:sock.close();raise
                break
            except Exception as exc:last=exc
        else:raise last
        with self.guard:
            e.pop('connecting_socket',None)
            if self.closed or e not in self.pools.get(e['key'],[]):
                conn._force_close();raise ValueError('连接任务已取消')
            e['conn']=conn
            e.pop('timeout_variable',None)
        with conn.cursor() as c:
            c.execute('SELECT VERSION()');version=c.fetchall()[0][0]
            e['mariadb']='mariadb' in str(version).lower()
            c.execute("SET SESSION sql_mode='STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION'")
            c.execute('SET SESSION sql_select_limit=501')
            c.execute('SET SESSION innodb_lock_wait_timeout=10')
        conn.rollback()
        return conn

    def spawn(self,fn):
        def task():
            try:fn()
            finally:
                with self.guard:self.workers.discard(threading.current_thread())
        thread=threading.Thread(target=task,daemon=True,name='api-lab-sql-task')
        with self.guard:self.workers.add(thread)
        thread.start()

    def limits(self,e,timeout):
        # Jobs already have a total deadline. Metadata calls need one as well;
        # a socket timeout alone does not bound a slowly streaming response.
        if not e.get('deadline'):
            def deadline():
                with self.guard:
                    e['limit_expired']=True;self.drop(e)
            e['limit_expired']=False
            e['deadline']=threading.Timer(timeout,deadline);e['deadline'].daemon=True;e['deadline'].start()
        conn=self.connection(e)
        conn._read_timeout=timeout
        variables=[e['timeout_variable']] if 'timeout_variable' in e else (['max_statement_time','max_execution_time'] if e['mariadb'] else ['max_execution_time','max_statement_time'])
        with conn.cursor() as c:
            for variable in variables:
                if variable is None:break
                try:
                    c.execute('SET SESSION '+variable+'=%s',(timeout if variable=='max_statement_time' else timeout*1000,))
                    e['timeout_variable']=variable
                    break
                except pymysql.OperationalError as exc:
                    if not exc.args or exc.args[0]!=1193:raise
            else:e['timeout_variable']=None
        return conn

    def metadata_error(self,label,exc,e):
        if e.get('limit_expired'):return ValueError(label+'：读取超时，连接已关闭')
        secret=self.vault.get('sql-password:'+e['profile']['id']) or ''
        detail=redact(str(exc),[secret])[:512]
        code=exc.args[0] if isinstance(exc,pymysql.Error) and exc.args else None
        return ValueError(label+('（MySQL '+str(code)+'）' if code else '')+'：'+detail)

    def materialize(self,c,password):
        columns=[d[0] for d in c.description or []];rows=[];size=0;truncated=False
        while True:
            row=c.fetchone()
            if row is None:break
            if len(rows)>=500:truncated=True;break
            values=[]
            for key,value in zip(columns,row):
                if sensitive(key):value='[REDACTED]'
                elif isinstance(value,bytes):value='0x'+value[:4096].hex()+('…' if len(value)>4096 else '')
                elif value is not None and not isinstance(value,(str,int,float,bool)):value=str(value)
                if isinstance(value,str):value=safe_text(value,[password]);value=value[:65536]+('…' if len(value)>65536 else '')
                values.append(value)
            size+=len(json.dumps(values,ensure_ascii=False).encode())
            if size>2*1024*1024:truncated=True;break
            rows.append(values)
        return columns,rows,truncated

    def start(self,data):
        text=selected_statement(data.sql,data.selection_start,data.selection_end);tree,kind=parse_statement(text)
        if data.explain:
            target=tree.this if isinstance(tree,exp.Describe) else tree
            if not isinstance(target,(exp.Select,exp.SetOperation,exp.Update,exp.Delete)):raise ValueError('此语句不支持EXPLAIN')
            text='EXPLAIN '+target.sql(dialect='mysql');kind='query'
        else:text=tree.sql(dialect='mysql')
        if isinstance(tree,exp.Describe) and isinstance(tree.this,(exp.Select,exp.SetOperation)) and not data.explain:text='EXPLAIN '+tree.this.sql(dialect='mysql')
        with self.guard:
            p=self.profile(data.connection_id)
            if kind=='write' and not p['allow_write']:raise ValueError('此连接为只读；如需更新删除，请在连接设置显式开启')
            if kind=='create':
                if not p.get('allow_create'):raise ValueError('此连接未启用建表；请在连接设置开启允许CREATE TABLE')
                for target in tree.find_all(exp.Table):
                    if target.catalog or target.args.get('alias') or target.args.get('joins') or (target.db and target.db!=data.database):
                        raise ValueError('建表及引用表必须属于当前选中的库')
                    quote(target.name)
                    target.set('db',exp.to_identifier(data.database,quoted=True))
                text=tree.sql(dialect='mysql')

            if shutil.disk_usage(self.store.root).free<1024**3:raise ValueError('可用磁盘不足1GiB')
            with self.store.lock:
                count=self.store.db.execute('SELECT COUNT(*) FROM sql_runs').fetchone()[0]
            if count>=10000:raise ValueError('SQL执行记录已达10000条，请先整理本地记录')
            e=self.lease(p,data.database)
            job={'id':str(uuid4()),'created_at':now(),'connection_id':p['id'],'connection_name':p['name'],'environment':p['environment'],'host':p['host'],'port':p['port'],'database':data.database,'sql':text,'state':'running','mode':'explain' if data.explain else kind,'row_count':0,'columns':[],'rows':[],'messages':[],'_entry':e,'_tree':tree,'_start':time.monotonic(),'_timeout':data.timeout}
            try:
                job['execution_context']={**redact(data.execution_context.model_dump(), [self.vault.get('sql-password:'+p['id']) or '']), 'source':data.execution_context.source}
                self.persist(job)
            except Exception:self.release(e);raise
            self.jobs[job['id']]=job
            self.spawn(lambda:self.execute(job,kind))
            return self.record(job['id'])

    def execute(self,j,kind,confirmed=False):
        e=j['_entry'];conn=None;awaiting=False
        def deadline():
            with self.guard:
                j['_timed_out']=True;self.drop(e)
        e['deadline']=threading.Timer(j['_timeout'],deadline);e['deadline'].daemon=True;e['deadline'].start()
        try:
            if kind=='create' and not confirmed:
                self.await_confirmation(j,messages=['尚未发送建表语句；确认后执行CREATE TABLE。DDL会隐式提交，不能用ROLLBACK撤销。'])
                awaiting=True
                return
            conn=self.limits(e,j['_timeout'])
            if j.get('_cancelled') or j.get('_timed_out'):raise ValueError('任务已停止')
            password=self.vault.get('sql-password:'+j['connection_id']) or ''
            timeout_note=[] if e.get('timeout_variable') else ['数据库未提供兼容的服务端语句超时变量；本机仍按截止时间关闭连接，服务端终止时间取决于数据库。']
            if kind=='create' and confirmed:
                if not self.profile(j['connection_id']).get('allow_create'):raise ValueError('连接未启用建表')
                conn.rollback()
                if j.get('_cancelled') or j.get('_timed_out'):raise ValueError('建表已停止，未发送DDL')
                j['_ddl_sent']=True
                with conn.cursor() as c:c.execute(j['sql'])
                j['_ddl_sent']=False
                j.update(state='completed',messages=timeout_note+['建表语句执行完成；DDL已隐式提交，请刷新表目录。'])
                try:
                    with self.guard:
                        cache=self.catalog_cache(j['connection_id'])
                        cache.get('tables',{}).pop(j['database'],None)
                        if cache:atomic(self.root/'catalog'/j['connection_id']/'current.json',cache)
                except Exception:j['messages'].append('本地目录缓存未更新，请手动刷新表目录。')
            elif confirmed:
                with conn.cursor() as c:c.execute(j['_guarded_sql'],j['_params']);affected=c.rowcount
                if affected!=j['row_count']:raise ValueError('实际匹配行数与预览不一致，已回滚')
                if j.get('_cancelled') or j.get('_timed_out'):raise ValueError('写入已停止，未提交')
                j['_committing']=True;conn.commit();j['_committing']=False
                j.update(state='completed',affected_rows=affected,messages=timeout_note+[f'已提交，直接匹配 {affected} 行（不含触发器和外键级联）'])
            elif kind=='write':
                self.preview(j,conn)
                awaiting=True
                return
            else:
                with conn.cursor() as c:
                    c.execute('START TRANSACTION READ ONLY')
                    c.execute(j['sql']);columns,rows,truncated=self.materialize(c,password)
                    if truncated:
                        c._result.unbuffered_active=False
                        self.drop(e)
                if not truncated:conn.rollback()
                if j.get('_cancelled') or j.get('_timed_out'):raise ValueError('查询已停止')
                j.update(state='completed',columns=columns,rows=rows,row_count=len(rows),truncated=truncated,messages=timeout_note+['查询完成'+('；结果已截断（最多500行/2MiB/单元格64KiB）' if truncated else '')])
        except Exception as exc:
            try:
                if conn and conn.open:conn.rollback()
            except Exception:pass
            # Socket read timeout can win the race with the deadline timer.
            if time.monotonic()-j['_start']>=j['_timeout']:j['_timed_out']=True
            code=exc.args[0] if isinstance(exc,pymysql.Error) and exc.args else None
            message=redact(str(exc),[self.vault.get('sql-password:'+j['connection_id']) or ''])[:512]
            if j.get('_ddl_sent') and (not code or code>=2000 or j.get('_timed_out') or j.get('_cancelled')):j.update(state='unknown',error='建表语句发送后连接中断或超时，建表结果未知；请核对表目录，勿直接重试')
            elif j.get('_committing'):j.update(state='unknown',error='提交时连接中断，提交结果未知；请人工核对，勿直接重试')
            else:j.update(state='timed_out' if j.get('_timed_out') else 'cancelled' if j.get('_cancelled') else 'failed',error=(f'MySQL({code})：' if code else '')+message)
            self.drop(e)
        finally:
            # A confirm request may already have started the next worker.
            # The preview worker must not cancel that worker's timer or remove its job.
            if not awaiting and e.get('deadline'):e['deadline'].cancel();e['deadline']=None
            if not awaiting and j['state']!='awaiting_confirmation':
                j['elapsed_ms']=round((time.monotonic()-j['_start'])*1000);j['finished_at']=now()
                try:self.persist(j)
                finally:
                    self.release(e)
                    with self.guard:self.jobs.pop(j['id'],None)

    def preview(self,j,conn):
        e=j['_entry'];tree=j['_tree'];table=tree.this;database=table.db or j['database'];name=table.name
        if database!=j['database']:raise ValueError('写入库必须与当前选中的库一致')
        with conn.cursor() as c:
            c.execute('SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s',(database,name));rows=c.fetchall();row=rows[0] if rows else None
            if not row or str(row[0]).lower()!='innodb':raise ValueError('第一版写入只支持InnoDB表')
            c.execute("SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND CONSTRAINT_NAME='PRIMARY' ORDER BY ORDINAL_POSITION",(database,name));keys=[r[0] for r in c.fetchall()]
            if not keys:raise ValueError('更新删除预览要求表具有主键')
            if isinstance(tree,exp.Update) and any(a.this.name in keys for a in tree.expressions):raise ValueError('第一版不支持更新主键')
            conn.rollback();c.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED');c.execute('START TRANSACTION')
            table_sql=quote(database)+'.'+quote(name)
            key_sql=','.join(quote(k) for k in keys)
            predicate=tree.args['where'].this.sql(dialect='mysql')
            c.execute(f'SELECT {key_sql} FROM {table_sql} WHERE {predicate} ORDER BY {key_sql} LIMIT 1001 FOR UPDATE')
            rows=c.fetchall()
            if len(rows)>self.PREVIEW_ROWS:raise ValueError('匹配超过1000行，请缩小WHERE范围后再执行')
            params=[v for row in rows for v in row]
            key_sql=key_sql.replace('%','%%')
            table_sql=table_sql.replace('%','%%')
            guard='(' + key_sql + ') IN ('+','.join('('+','.join('%s' for _ in keys)+')' for _ in rows)+')' if rows else '0=1'
            # The user predicate is only evaluated during preview. The write targets the exact locked primary keys.
            if isinstance(tree,exp.Update):
                assignments=', '.join(x.sql(dialect='mysql') for x in tree.expressions).replace('%','%%')
                statement=f'UPDATE {table_sql} SET {assignments} WHERE {guard}'
            else:statement=f'DELETE FROM {table_sql} WHERE {guard}'
            if j.get('_cancelled') or j.get('_timed_out'):raise ValueError('预览已停止')
            self.await_confirmation(j,row_count=len(rows),messages=['尚未执行写SQL；已锁定直接目标行，确认窗口60秒，过期自动断开。不含触发器/外键级联影响。'],_guarded_sql=statement,_params=params)

    def await_confirmation(self,j,**values):
        e=j['_entry']
        with self.guard:
            if j.get('_cancelled') or j.get('_timed_out'):raise ValueError('任务已停止，未发送写SQL')
            if e.get('deadline'):e['deadline'].cancel();e['deadline']=None
            j.update(state='awaiting_confirmation',confirmation_token=str(uuid4()),expires_at=time.time()+60,**values)
            e['timer']=threading.Timer(60,lambda:self.expire_preview(j));e['timer'].daemon=True;e['timer'].start()
            self.persist(j)

    def expire_preview(self,j):
        with self.guard:
            if j['state']=='awaiting_confirmation':
                j.update(state='expired',error='确认已过期，连接关闭；未执行写SQL或建表')
                self.drop(j['_entry']);self.persist(j);self.jobs.pop(j['id'],None)

    def confirm(self,id,token):
        with self.guard:
            j=self.jobs.get(str(id))
            if not j or j['state']!='awaiting_confirmation' or j['confirmation_token']!=str(token) or time.time()>=j['expires_at']:raise ValueError('确认无效或过期，请重新预览')
            e=j['_entry'];e['timer'].cancel();e['timer']=None
            j.update(state='running');j['_start']=time.monotonic();self.persist(j)
            self.spawn(lambda:self.execute(j,'create' if j['mode']=='create' else 'write',True))
            return self.record(j['id'])

    def cancel(self,id):
        with self.guard:
            j=self.jobs.get(str(id))
            if not j:return self.record(id)
            if j['state']=='awaiting_confirmation':
                j.update(state='cancelled',error='已取消确认，未执行写SQL');self.drop(j['_entry']);self.persist(j);self.jobs.pop(j['id'],None)
            else:j['_cancelled']=True;self.drop(j['_entry'])
            return self.record(id)

    def catalog(self,connection_id,database=None):
        p=self.profile(connection_id);e=self.lease(p,database or p.get('database') or '')
        try:
            conn=self.limits(e,30)
            with conn.cursor() as c:
                c.execute('START TRANSACTION READ ONLY')
                if database:
                    c.execute('SELECT TABLE_NAME,TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME LIMIT 1001',(database,));rows=c.fetchall()
                    value=[{'name':r[0],'kind':r[1]} for r in rows[:1000]]
                else:
                    c.execute('SHOW DATABASES');rows=c.fetchmany(1001);value=[r[0] for r in rows[:1000]]
            conn.rollback()
            with self.guard:
                cache=self.catalog_cache(connection_id)
                if database:
                    tables={k:v for k,v in cache.get('tables',{}).items() if k!=database}
                    tables[database]=value
                    cache['tables']=dict(list(tables.items())[-10:])
                else:cache['databases']=value
                cache.update(revision=p['revision'],updated_at=now())
                atomic(self.root/'catalog'/p['id']/'current.json',cache)
            return {'items':value,'truncated':len(rows)>1000}
        except Exception as exc:
            self.drop(e);raise self.metadata_error('无法读取库表',exc,e) from None
        finally:self.release(e)

    def table_structure(self,connection_id,database,table,refresh=False):
        p=self.profile(connection_id)
        quote(database);quote(table)
        digest=hashlib.sha256(json.dumps([database,table]).encode()).hexdigest()
        path=self.root/'catalog'/p['id']/'structures'/(digest+'.json')
        with self.guard:
            if path.exists() and not refresh:
                saved=self.decrypted(path)
                if saved.get('revision')==p['revision']:return {**saved,'cached':True}
        e=self.lease(p,database)
        try:
            conn=self.limits(e,30)
            with conn.cursor() as c:
                c.execute('START TRANSACTION READ ONLY')
                c.execute('SELECT TABLE_TYPE,ENGINE,TABLE_COLLATION,TABLE_ROWS,LEFT(TABLE_COMMENT,4096) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s LIMIT 1',(database,table))
                info_rows=c.fetchmany(2)
                info=info_rows[0] if info_rows else None
                if not info:raise ValueError('表不存在或当前账号无权查看')
                c.execute('SELECT COLUMN_NAME,LEFT(COLUMN_TYPE,4096),IS_NULLABLE,LEFT(COLUMN_DEFAULT,4096),COLUMN_KEY,LEFT(EXTRA,4096),LEFT(COLUMN_COMMENT,4096) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION LIMIT 1001',(database,table))
                rows=c.fetchmany(1001)
            conn.rollback()
            secret=self.vault.get('sql-password:'+p['id']) or ''
            def text(value):return None if value is None else redact(str(value),[secret])[:4096]
            fields=[];size=0
            for row in rows[:1000]:
                field=dict(name=text(row[0]),type=text(row[1]),nullable=row[2]=='YES',default='[REDACTED]' if row[3] is not None and sensitive(row[0]) else text(row[3]),key=text(row[4]),extra=text(row[5]),comment=text(row[6]))
                size+=len(json.dumps(field,ensure_ascii=False).encode('utf-8'))
                if size>2*1024*1024:break
                fields.append(field)
            value=dict(revision=p['revision'],database=database,table=table,kind=text(info[0]),engine=text(info[1]),collation=text(info[2]),estimated_rows=text(info[3]),comment=text(info[4]),columns=fields,truncated=len(rows)>len(fields),updated_at=now())
            with self.guard:
                if self.profile(connection_id)['revision']!=p['revision']:raise ValueError('连接配置已变化，请重新读取表结构')
                self.encrypted(path,value)
            return {**value,'cached':False}
        except Exception as exc:
            self.drop(e)
            raise self.metadata_error('无法读取表结构',exc,e) from None
        finally:self.release(e)

    def export_result(self,id,format):
        record=self.record(id)
        if record['state']!='completed' or not record.get('columns'):raise ValueError('仅支持导出已完成且含结果列的查询记录')
        columns=record['columns'];rows=record.get('rows',[])
        if format=='csv':
            def cell(value):
                if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):return "'"+value
                return '' if value is None else value
            output=io.StringIO(newline='');writer=csv.writer(output,quoting=csv.QUOTE_ALL)
            writer.writerow([cell(c) for c in columns]);writer.writerows([[cell(v) for v in row] for row in rows])
            data=output.getvalue().encode('utf-8-sig');media='text/csv'
        else:
            data=json.dumps({'columns':columns,'rows':rows,'row_count':len(rows),'truncated':bool(record.get('truncated'))},ensure_ascii=False,indent=2).encode('utf-8');media='application/json'
        return Response(data,media_type=media,headers={'Content-Disposition':'attachment; filename="query-'+str(UUID(str(id)))+'.'+format+'"'})

    def pool_status(self):
        with self.guard:return [{'connection_id':key[0],'database':key[1],'open':len(pool),'busy':sum(e['busy'] for e in pool),'limit':3,'idle_seconds':60} for key,pool in self.pools.items()]

    def close(self):
        with self.guard:
            self.closed=True
            for j in list(self.jobs.values()):j['_cancelled']=True
            for pool in list(self.pools.values()):
                for e in list(pool):self.drop(e)
            workers=list(self.workers)
        end=time.monotonic()+5
        for worker in workers:worker.join(max(0,end-time.monotonic()))


def router(manager):
    api=APIRouter(prefix='/api/sql')
    @api.get('/environments')
    async def environments():return manager.environments()
    @api.post('/environments')
    async def create_environment(data:SQLEnvironmentInput):return manager.create_environment(data.name)
    @api.get('/connections')
    async def connections():return manager.connections()
    @api.post('/connections')
    async def create(data:ConnectionInput):return manager.save_connection(data)
    @api.put('/connections/{id}')
    async def update(id:UUID,data:ConnectionInput):return manager.save_connection(data,str(id))
    @api.delete('/connections/{id}')
    async def delete(id:UUID,revision:int=Query(...,ge=1)):return manager.archive_connection(str(id),revision)
    async def catalog_call(id, database=None, table=None, refresh=False):
        loop=asyncio.get_running_loop();future=loop.create_future()
        def deliver(value=None,error=None):
            if not future.done():
                if error:future.set_exception(error)
                else:future.set_result(value)
        def work():
            try:
                value=manager.table_structure(id,database,table,refresh) if table is not None else manager.catalog(id,database)
                loop.call_soon_threadsafe(deliver,value)
            except Exception as exc:loop.call_soon_threadsafe(deliver,None,exc)
        manager.spawn(work)
        return await future
    @api.get('/connections/{id}/catalog-cache')
    async def cache(id:UUID):return manager.catalog_cache(str(id))
    @api.get('/connections/{id}/databases')
    async def databases(id:UUID):
        return await catalog_call(str(id))
    @api.get('/connections/{id}/tables')
    async def tables(id:UUID,database:str=Query(...,min_length=1,max_length=64)):
        return await catalog_call(str(id),database)
    @api.get('/connections/{id}/table-structure')
    async def table_structure(id:UUID,database:str=Query(...,min_length=1,max_length=64),table:str=Query(...,min_length=1,max_length=64),refresh:bool=False):
        return await catalog_call(str(id),database,table,refresh)
    @api.get('/documents')
    async def documents(connection_id:UUID,database:str=Query(...,min_length=1,max_length=64)):return manager.documents(str(connection_id),database)
    @api.post('/documents')
    async def save_query(data:QueryDocument):return manager.save_document(data)
    @api.get('/documents/{id}')
    async def query(id:UUID):return manager.document(str(id))
    @api.get('/jobs')
    async def history(limit:int=Query(50,ge=1,le=100)):return manager.history(limit)
    @api.post('/jobs')
    async def execute(data:SQLText):return manager.start(data)
    @api.get('/jobs/{id}/export')
    async def export(id:UUID,format:Literal['csv','json']=Query('csv')):
        return manager.export_result(str(id),format)
    @api.get('/jobs/{id}')
    async def result(id:UUID):return manager.record(str(id))
    @api.post('/jobs/{id}/confirm')
    async def confirm(id:UUID,data:Confirm):return manager.confirm(str(id),data.token)
    @api.post('/jobs/{id}/cancel')
    async def cancel(id:UUID):return manager.cancel(str(id))
    @api.get('/pools')
    async def pools():return manager.pool_status()
    return api

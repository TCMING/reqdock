"""Local MySQL wire-protocol fixture backed by SQLite; never a production connector."""
import os
import socketserver
import sqlite3
import struct
import tempfile
import threading
import re


def length(value):
    if value<251:return bytes([value])
    if value<65536:return b'\xfc'+struct.pack('<H',value)
    return b'\xfd'+value.to_bytes(3,'little')


def field(value):
    data=str(value).encode();return length(len(data))+data


class Wire(socketserver.BaseRequestHandler):
    def send(self,data,seq):self.request.sendall(len(data).to_bytes(3,'little')+bytes([seq%256])+data)
    def read(self,n):
        data=b''
        while len(data)<n:
            piece=self.request.recv(n-len(data))
            if not piece:raise EOFError
            data+=piece
        return data
    def packet(self):
        h=self.read(4);return self.read(int.from_bytes(h[:3],'little'))
    def result(self,columns,rows):
        seq=1;self.send(length(len(columns)),seq);seq+=1
        for name in columns:
            data=b''.join(field(v) for v in ['def','lab','records','records',name,name])+b'\x0c'+struct.pack('<HIBHH',45,65535,253,0,0)+b'\x00'
            self.send(data,seq);seq+=1
        self.send(b'\xfe\x00\x00\x02\x00',seq);seq+=1
        for row in rows:
            self.send(b''.join(b'\xfb' if v is None else field(v) for v in row),seq);seq+=1
        self.send(b'\xfe\x00\x00\x02\x00',seq)
    def ok(self,count=0,seq=1):self.send(b'\x00'+length(max(count,0))+b'\x00\x02\x00\x00\x00',seq)
    def handle(self):
        salt=b'12345678abcdefghijkL';caps=0x0001|0x0004|0x0200|0x8000|0x80000|0x100000|0x8|0x20000
        greeting=b'\x0a8.0.36-api-lab-fixture\x00'+struct.pack('<I',123)+salt[:8]+b'\x00'+struct.pack('<H',caps&65535)+b'\x2d\x02\x00'+struct.pack('<H',caps>>16)+b'\x15'+b'\x00'*10+salt[8:]+b'\x00mysql_native_password\x00'
        db=sqlite3.connect(self.server.database,timeout=2)
        with self.server.guard:self.server.open_count+=1;self.server.total+=1
        try:
            self.send(greeting,0);self.packet();self.server.auth_count+=1;self.ok(seq=2)
            while True:
                packet=self.packet()
                if packet[0]==1:break
                if packet[0] in (2,14):self.ok();continue
                if packet[0]!=3:raise ValueError('Unexpected fixture command')
                sql=packet[1:].decode();upper=sql.strip().upper()
                with self.server.guard:self.server.statements.append(sql)
                try:
                    if upper.startswith(('SET SESSION MAX_EXECUTION_TIME=', 'SET SESSION MAX_STATEMENT_TIME=')):
                        variable=upper.split('=',1)[0].split()[-1].lower()
                        code=self.server.timeout_errors.get(variable)
                        if code:self.send(b'\xff'+struct.pack('<H',code)+b'#HY000'+('Unknown system variable '+variable if code==1193 else 'fixture permission denied').encode(),1)
                        else:self.ok()
                    elif upper.startswith('SET '):self.ok()
                    elif upper.startswith('START TRANSACTION'):
                        if db.in_transaction:db.rollback()
                        db.execute('BEGIN');self.ok()
                    elif upper=='ROLLBACK':db.rollback();self.ok()
                    elif upper=='COMMIT':db.commit();self.ok()
                    elif upper.startswith('SELECT VERSION()'):self.result(['VERSION()'],[(self.server.version,)])
                    elif upper.startswith('SHOW DATABASES'):self.result(['Database'],[('lab',)])
                    elif 'INFORMATION_SCHEMA.TABLES' in upper:
                        if 'TABLE_COLLATION' in upper:self.result(['TABLE_TYPE','ENGINE','TABLE_COLLATION','TABLE_ROWS','TABLE_COMMENT'],[('BASE TABLE','InnoDB','utf8mb4_general_ci',1104,'Fixture table')])
                        elif 'SELECT ENGINE' in upper:self.result(['ENGINE'],[('InnoDB',)])
                        else:self.result(['TABLE_NAME','TABLE_TYPE'],[('records','BASE TABLE')])
                    elif 'INFORMATION_SCHEMA.COLUMNS' in upper:self.result(['COLUMN_NAME','COLUMN_TYPE','IS_NULLABLE','COLUMN_DEFAULT','COLUMN_KEY','EXTRA','COLUMN_COMMENT'],[('id','int','NO',None,'PRI','auto_increment','Identifier'),('name','varchar(255)','YES','anonymous','','','Display name'),('password','varchar(255)','YES','fixture-sensitive-default','','','Secret')])
                    elif 'INFORMATION_SCHEMA.KEY_COLUMN_USAGE' in upper:self.result(['COLUMN_NAME'],[('id',)])
                    elif upper.startswith('CREATE TABLE '):
                        if self.server.disconnect_create:
                            self.request.shutdown(2);break
                        if self.server.create_error:self.send(b'\xff'+struct.pack('<H',self.server.create_error)+b'#HY000fixture create denied',1)
                        else:self.ok() # DDL lifecycle is observed via captured SQL, not SQLite grammar.
                    elif upper.startswith('EXPLAIN '):self.result(['id','select_type','table','type','rows'],[(1,'SIMPLE','records','ALL',3)])
                    else:
                        sql=re.sub(r'(?:`lab`|\blab)\.', '',sql,flags=re.I)
                        sql=re.sub(r'\s+FOR UPDATE\s*$', '',sql,flags=re.I)
                        if 'fixture_slow' in sql:
                            import time;time.sleep(2)
                        cursor=db.execute(sql)
                        if cursor.description:self.result([c[0] for c in cursor.description],cursor.fetchall())
                        else:self.ok(cursor.rowcount)
                except Exception:self.send(b'\xff'+struct.pack('<H',1105)+b'#HY000fixture SQL error',1)
        except (EOFError,OSError,ValueError):pass
        finally:
            db.rollback();db.close()
            with self.server.guard:self.server.open_count-=1


class MySQLFixture(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=True
    def __init__(self,port=0):
        self.temp=tempfile.TemporaryDirectory(prefix='api-lab-sql-')
        self.database=os.path.join(self.temp.name,'fixture.sqlite3')
        self.version='8.0.36';self.timeout_errors={};self.disconnect_create=False;self.create_error=None
        self.guard=threading.Lock();self.statements=[];self.open_count=0;self.total=0;self.auth_count=0
        db=sqlite3.connect(self.database)
        db.execute('CREATE TABLE records (id INTEGER PRIMARY KEY, name TEXT, password TEXT)')
        db.executemany('INSERT INTO records VALUES (?,?,?)',[(i,'name-'+str(i),'fixture-sensitive-value') for i in range(1,1105)])
        db.commit();db.close()
        super().__init__(('127.0.0.1',port),Wire)
        self.thread=threading.Thread(target=self.serve_forever,daemon=True);self.thread.start()
    @property
    def port(self):return self.server_address[1]
    def close(self):
        self.shutdown();self.server_close();self.thread.join(2)
        try:self.temp.cleanup()
        except PermissionError:pass

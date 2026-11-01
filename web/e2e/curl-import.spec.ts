import {test,expect} from '@playwright/test';
import {parseCurl} from '../src/curlImport';
import {execFileSync} from 'node:child_process';
import path from 'node:path';

test('cURL解析保留正文、引号、请求头和流式配置',()=>{
 const r=parseCurl(`curl 'https://example.test/v1/chat?x=1&y=2' \\\n -X POST -H 'Content-Type: application/json' \\\n -H 'Authorization: Bearer fixture-only' --data-raw '{"messages":[{"content":"hello"}],"stream":true}'`);
 expect(r.method).toBe('POST');expect(r.url).toBe('https://example.test/v1/chat?x=1&y=2');expect(r.headers.Authorization).toBe('Bearer fixture-only');expect(r.bodyType).toBe('json');expect(r.stream).toBe(true);
 expect(parseCurl(`curl https://example.test -d 'first\nsecond'`).body).toBe('first\nsecond');
 expect(parseCurl(`curl https://example.test --data-raw 'literal \\\nnewline'`).body).toBe('literal \\\nnewline');
 expect(parseCurl('curl https://example.test --data-raw "{\\"text\\":\\"quoted\\"}"').body).toBe('{"text":"quoted"}');
 const windows=parseCurl('curl.exe "http://localhost:8080/echo" ^\r\n -XPOST -H "X-Test: yes" -d "ok"');
 expect(windows.method).toBe('POST');expect(windows.headers['X-Test']).toBe('yes');expect(windows.body).toBe('ok');
});
test('cURL解析表单、查询、Basic和JSON快捷参数',()=>{
 const r=parseCurl(`curl --get 'https://example.test/q' --data-urlencode 'text=hello world' --data-urlencode '=a!b'`);
 expect(r.method).toBe('GET');expect(r.url).toBe('https://example.test/q?text=hello%20world&a%21b');expect(r.bodyType).toBe('none');
 expect(parseCurl('curl https://example.test -u user:pass').headers.Authorization).toBe('Basic dXNlcjpwYXNz');
 const json=parseCurl(`curl https://example.test --json '{"a":1}'`);expect(json.headers['Content-Type']).toBe('application/json');expect(json.headers.Accept).toBe('application/json');
 expect(parseCurl(`curl https://example.test -d a=1 -d b=2`).body).toBe('a=1&b=2');
 expect(parseCurl('curl -I https://example.test').method).toBe('HEAD');
 expect(parseCurl('curl -L --compressed https://example.test').warnings).toHaveLength(2);
});
test('cURL拒绝不完整解析、动态命令和不支持的网络选项',()=>{
 for(const input of [
  'curl https://example.test -F file=@a.txt','curl https://example.test --data-binary @file',
  'curl https://example.test --insecure','curl https://example.test --proxy http://proxy.test',
  'curl https://example.test | bash', 'curl https://example.test; echo hello',
  'curl "https://example.test/$TOKEN"', 'curl https://example.test -d $(whoami)',
  'curl https://example.test -H "X-Test: a" -H "x-test: b"',
  'curl https://example.test https://other.test', "curl 'https://example.test",
  'curl https://user:pass@example.test', 'curl file:///tmp/a',
 ])expect(()=>parseCurl(input)).toThrow();
 expect(parseCurl(`curl https://example.test --data-raw '$literal'`).body).toBe('$literal');
});
test('路径输入框粘贴cURL填充草稿，手动发送才创建本地记录',async({page})=>{
 const root=path.resolve('..');await page.goto('/');
 const pair=execFileSync(path.join(root,'.venv/Scripts/python.exe'),['-m','api_post','pair','--port','18767','--workspace',process.env.API_LAB_E2E_WORKSPACE!],{cwd:root,encoding:'utf8',stdio:['ignore','pipe','ignore']}).trim();
 await page.getByLabel('本机配对码').fill(pair);await page.getByRole('button',{name:'连接工作区'}).click();
 const before=await (await page.request.get('/api/runs?limit=200')).json();
 async function paste(text:string){await page.getByLabel('请求路径',{exact:true}).evaluate((el,text)=>{const dt=new DataTransfer();dt.setData('text',text);el.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));},text);}
 const curl=`curl 'http://127.0.0.1:18768/echo' -H 'Content-Type: application/json' -H 'X-Test: imported' --data-raw '{"message":"curl paste"}'`;
 await paste(curl);await expect(page.getByLabel('请求方法')).toHaveValue('POST');await expect(page.getByLabel('请求路径')).toHaveValue('http://127.0.0.1:18768/echo');await expect(page.getByLabel('请求正文',{exact:true})).toHaveValue('{"message":"curl paste"}');
 expect((await (await page.request.get('/api/runs?limit=200')).json()).total).toBe(before.total);
 await page.locator('.request-panel').getByRole('button',{name:'请求头',exact:true}).click();await expect(page.getByLabel('请求头 JSON')).toHaveValue(/X-Test/);
 await page.getByRole('button',{name:'发送请求'}).click();await expect(page.locator('.status-pill').last()).toContainText('已完成');await expect(page.locator('.response-content')).toContainText('curl paste');
 await paste('curl https://unknown.invalid/v1/models');await expect(page.getByRole('button',{name:'发送请求'})).toBeDisabled();await expect(page.getByRole('alert')).toContainText('没有匹配');
 await paste('curl https://example.test --insecure');await expect(page.getByLabel('请求路径')).toHaveValue('https://unknown.invalid/v1/models');await expect(page.getByRole('alert')).toContainText('尚不支持');
});

import {test,expect} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
const root=path.resolve('..');
async function pair(page:any){await page.goto('/');const code=execFileSync(path.join(root,'.venv/Scripts/python.exe'),['-m','api_post','pair','--port','18767','--workspace',process.env.API_LAB_E2E_WORKSPACE!],{cwd:root,encoding:'utf8',stdio:['ignore','pipe','ignore']}).trim();await page.getByLabel('本机配对码').fill(code);await page.getByRole('button',{name:'连接工作区'}).click();await expect(page.getByRole('heading',{name:'请求工作台'})).toBeVisible();}

test('慢SQL状态读取不重叠，完成后停止轮询并保留结果',async({page})=>{
 await pair(page);const id=randomUUID(),job=randomUUID();let active=0,max=0,reads=0;
 const profile={id,revision:1,name:'memory polling fixture',environment:'测试环境',host:'127.0.0.1',port:18769,username:'fixture',database:'lab',tls:false,network_scope:'local',allow_write:false};
 await page.route('**/api/sql/connections',r=>r.fulfill({json:[profile]}));
 await page.route('**/api/sql/connections/*/catalog-cache',r=>r.fulfill({json:{revision:1,databases:['lab'],tables:{lab:[{name:'records',kind:'BASE TABLE'}]}}}));
 const result={id:job,state:'running',connection_id:id,connection_name:profile.name,database:'lab',environment:'测试环境',sql:'SELECT 42',row_count:1,columns:['answer'],rows:[[42]],messages:[],created_at:new Date().toISOString(),mode:'query'};
 await page.route('**/api/sql/jobs',r=>r.request().method()==='POST'?r.fulfill({json:result}):r.continue());
 await page.route('**/api/sql/jobs/'+job,async r=>{active++;max=Math.max(max,active);reads++;const complete=reads>=2;await new Promise(resolve=>setTimeout(resolve,1000));await r.fulfill({json:{...result,state:complete?'completed':'running'}});active--;});
 await page.getByLabel('工作空间',{exact:true}).selectOption('sql');const sql=page.locator('.sql-shell'),tree=page.getByLabel('数据库目录');await tree.getByRole('button',{name:'展开环境 测试环境',exact:true}).click();await tree.getByRole('button',{name:'展开连接 '+profile.name,exact:true}).click();await tree.getByRole('button',{name:'新建查询 '+profile.name+' lab',exact:true}).click();
 await sql.getByLabel('SQL编辑区').fill('SELECT 42');await sql.getByRole('button',{name:'▷ 运行选中 / 当前语句',exact:true}).click();await expect(sql.locator('.sql-results').first()).toContainText('已完成',{timeout:12000});await expect(sql.locator('.sql-grid-wrap')).toContainText('42');expect(max).toBe(1);const finished=reads;await page.waitForTimeout(900);expect(reads).toBe(finished);
});

test('关闭音频预览显式停止播放并清除媒体源',async({page})=>{
 await pair(page);const name='memory audio '+randomUUID();const wave=Buffer.alloc(44+3200);wave.write('RIFF',0);wave.writeUInt32LE(wave.length-8,4);wave.write('WAVEfmt ',8);wave.writeUInt32LE(16,16);wave.writeUInt16LE(1,20);wave.writeUInt16LE(1,22);wave.writeUInt32LE(8000,24);wave.writeUInt32LE(16000,28);wave.writeUInt16LE(2,32);wave.writeUInt16LE(16,34);wave.write('data',36);wave.writeUInt32LE(3200,40);
 await page.evaluate(async ({name,data})=>{const session=await(await fetch('/api/auth/session')).json();const headers={'Content-Type':'application/json','X-CSRF-Token':session.csrf};const r=await fetch('/api/resources',{method:'POST',headers,body:JSON.stringify({name,kind:'audio',tags:[],addresses:[]})});if(!r.ok)throw Error('fixture resource failed');const resource=await r.json(),bytes=Uint8Array.from(atob(data),c=>c.charCodeAt(0));const uploaded=await fetch('/api/resources/'+resource.id+'/file?revision=1&filename=fixture.wav',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-CSRF-Token':session.csrf},body:bytes});if(!uploaded.ok)throw Error('fixture upload failed');}, {name,data:wave.toString('base64')});
 await page.evaluate(()=>{const w=window as any;w.__mediaLog=[];const originalPause=HTMLMediaElement.prototype.pause,originalLoad=HTMLMediaElement.prototype.load;HTMLMediaElement.prototype.pause=function(){w.__mediaLog.push({action:'pause',source:!!this.getAttribute('src')});return originalPause.call(this);};HTMLMediaElement.prototype.load=function(){w.__mediaLog.push({action:'load',source:!!this.getAttribute('src')});return originalLoad.call(this);};});
 await page.getByRole('button',{name:'资源库',exact:false}).first().click();await page.getByLabel('搜索资源').fill(name);const row=page.locator('.resource-row').filter({hasText:name});await expect(row).toBeVisible();await row.getByRole('button',{name:'预览',exact:true}).click();const dialog=page.getByRole('dialog',{name:'资源预览'});await expect(dialog.locator('audio')).toBeVisible();await dialog.locator('audio').evaluate(node=>{(window as any).__previewAudio=node;});await dialog.getByRole('button',{name:'关闭',exact:false}).click();await expect(dialog).toHaveCount(0);
 expect(await page.evaluate(()=>{const w=window as any;return w.__previewAudio.paused&&!w.__previewAudio.getAttribute('src')&&w.__mediaLog.some((x:any)=>x.action==='load'&&!x.source);})).toBe(true);
});

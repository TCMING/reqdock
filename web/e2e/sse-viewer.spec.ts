import {test,expect} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {writeFileSync} from 'node:fs';
import path from 'node:path';
import {chooseDemoEnvironment,prepareSse} from './helpers';
test('SSE按data行展示，滚轮可读取超过500条并返回开头',async({page})=>{
 const root=path.resolve('..');await page.goto('/');
 const workspace=path.resolve(process.env.API_LAB_E2E_WORKSPACE!);expect(workspace.startsWith(path.join(root,'workspace','qa','e2e-'))).toBeTruthy();
 const code=execFileSync(path.join(root,'.venv/Scripts/python.exe'),['-m','api_post','pair','--port','18767','--workspace',workspace],{cwd:root,encoding:'utf8',stdio:['ignore','pipe','ignore']}).trim();
 await page.getByLabel('本机配对码').fill(code);await page.getByRole('button',{name:'连接工作区'}).click();await chooseDemoEnvironment(page);
 await prepareSse(page);await page.getByLabel('请求名称').fill('SSE 滚轮验证');
 await page.getByRole('button',{name:'发送请求'}).click();await expect(page.locator('.status-pill').last()).toContainText('已完成');await expect(page.locator('.sse-scroll')).toContainText('data: [DONE]');
 const runs=await (await page.request.get('/api/runs?limit=200')).json(),run=runs.items.find((r:any)=>r.name==='SSE 滚轮验证');expect(run).toBeTruthy();
 const events=Array.from({length:600},(_,i)=>({sequence:i+1,event:'message',data:JSON.stringify({choices:[{delta:{reasoning_content:'chunk-'+String(i+1).padStart(4,'0')}}]}),elapsed_ms:i}));
 events.push({sequence:601,event:'message',data:'[DONE]',elapsed_ms:601});
 writeFileSync(path.join(workspace,'runs',run.id,'stream.events.ndjson'),events.map(e=>JSON.stringify(e)).join('\n')+'\n');
 await page.locator('.recent').filter({hasText:'SSE 滚轮验证'}).click();
 const viewer=page.getByLabel('SSE响应内容',{exact:true});await expect(viewer).toContainText('chunk-0001');await viewer.hover();
 for(let n=1;n<=12;n++){await page.mouse.wheel(0,6000);await expect(viewer).toContainText(n===12?'data: [DONE]':'chunk-'+String(n*50+1).padStart(4,'0'));}
 await expect(viewer.locator('[data-stream-page]')).toHaveCount(4);
 for(let n=8;n>=0;n--){await page.mouse.wheel(0,-10000);await expect(viewer.locator('[data-stream-page]').first()).toHaveAttribute('data-stream-page',String(n));}
 await expect(viewer).toContainText('chunk-0001');await expect(page.getByLabel('跟随最新')).not.toBeChecked();
 await page.getByLabel('自动换行').uncheck();await expect(viewer).not.toHaveClass(/wrapped/);
 await page.screenshot({path:path.join(root,'workspace/qa/sse-viewer.png'),fullPage:true});
});

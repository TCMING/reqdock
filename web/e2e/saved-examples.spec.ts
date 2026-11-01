import {test,expect} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
import {chooseDemoEnvironment,prepareSse} from './helpers';

test('实际JSON和SSE结果保存到用例，重新打开仍可查看',async({page})=>{
 const root=path.resolve('..');await page.goto('/');
 const code=execFileSync(path.join(root,'.venv/Scripts/python.exe'),['-m','api_post','pair','--port','18767','--workspace',process.env.API_LAB_E2E_WORKSPACE!],{cwd:root,encoding:'utf8',stdio:['ignore','pipe','ignore']}).trim();
 await page.getByLabel('本机配对码').fill(code);await page.getByRole('button',{name:'连接工作区'}).click();
 const session=await (await page.request.get('/api/auth/session')).json(),headers={Origin:'http://127.0.0.1:18767','X-CSRF-Token':session.csrf};
 const created=await page.request.post('/api/cases',{headers,data:{request:{name:'saved response fixture',path:'/echo',body:'{"message":"saved-result-marker"}'}}});expect(created.ok()).toBeTruthy();const c=await created.json();
 async function load(){await page.getByRole('button',{name:'用例库',exact:false}).first().click();await page.getByLabel('搜索用例',{exact:true}).fill(c.name);await page.locator('tbody tr').filter({hasText:c.name}).getByRole('button',{name:'载入',exact:false}).click();}
 await load();await chooseDemoEnvironment(page);await page.getByRole('button',{name:'发送请求'}).click();await expect(page.locator('.status-pill').last()).toContainText('已完成');
 const panel=page.locator('.response-panel');await panel.getByRole('button',{name:'结果示例',exact:true}).click();await panel.getByRole('button',{name:'保存本次结果',exact:true}).click();await expect(panel.getByRole('status')).toContainText('已保存到当前用例');
 await expect(panel.getByLabel('已保存结果内容')).toContainText('saved-result-marker');
 await panel.getByText('响应头',{exact:true}).last().click();await expect(panel.getByLabel('已保存结果内容')).toContainText('Content-Type');
 const before=(await (await page.request.get('/api/runs?limit=1')).json()).total;
 await page.reload();await load();await panel.getByRole('button',{name:'结果示例',exact:true}).click();await expect(panel.getByLabel('已保存结果内容')).toContainText('saved-result-marker');await expect(panel.getByRole('button',{name:'保存本次结果',exact:true})).toBeDisabled();
 expect((await (await page.request.get('/api/runs?limit=1')).json()).total).toBe(before);
 await chooseDemoEnvironment(page);await prepareSse(page);await page.getByRole('button',{name:'发送请求'}).click();await expect(page.locator('.status-pill').last()).toContainText('已完成');
 await panel.getByRole('button',{name:'结果示例',exact:true}).click();await panel.getByRole('button',{name:'保存本次结果',exact:true}).click();await expect(panel.getByRole('status')).toContainText('已保存到当前用例');
 await expect(panel.getByLabel('已保存结果内容')).toContainText('data: [DONE]');
 await page.reload();await load();await panel.getByRole('button',{name:'结果示例',exact:true}).click();await expect(panel.getByLabel('已保存结果内容')).toContainText('data: [DONE]');
 const list=await (await page.request.get('/api/cases/'+c.id+'/examples')).json();expect(list).toHaveLength(2);
 await panel.getByLabel('已保存的结果').selectOption(list[1].id);await expect(panel.getByLabel('已保存结果内容')).toContainText('saved-result-marker');
});

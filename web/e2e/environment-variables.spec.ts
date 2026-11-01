import {chooseEnvironment} from './helpers';
import {test,expect} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
const root=path.resolve('..');
test('环境变量：导入变量可见、增删编辑、保密、切换环境实际生效',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/');
 const code=execFileSync(path.join(root,'.venv/Scripts/python.exe'),['-m','api_post','pair','--port','18767','--workspace',process.env.API_LAB_E2E_WORKSPACE!],{cwd:root,encoding:'utf8',stdio:['ignore','pipe','ignore']}).trim();
 await page.getByLabel('本机配对码').fill(code);await page.getByRole('button',{name:'连接工作区'}).click();
 await page.getByRole('button',{name:'环境管理',exact:false}).first().click();
 await page.getByLabel('搜索环境').fill('本地环境');
 const imported=page.locator('.env-card').filter({hasText:'本地环境'});
 await expect(imported.locator('.environment-variables-view')).toContainText('ibrain-common');
 await imported.getByRole('button',{name:'编辑环境',exact:true}).click();
 const editor=page.getByRole('dialog',{name:'编辑环境组',exact:true});
 await expect(editor.locator('.variable-row')).toHaveCount(4);
 await editor.getByRole('button',{name:'关闭',exact:false}).click();
 const base='环境变量验证 '+Date.now();
 for(const label of ['A','B']){
  await page.getByRole('button',{name:'＋ 新建环境',exact:true}).click();
  await page.getByLabel('环境名称').fill(base+' '+label);
  await page.getByLabel('基础地址').fill('http://127.0.0.1:18768');
  for(const [key,value,secret] of [['label',label,false],['private','fixture-only-reqdock-variable-for-tests',true]] as const){
   await page.getByRole('button',{name:'添加变量'}).click();
   const row=page.locator('.variable-row').last();
   await row.getByLabel('变量名',{exact:true}).fill(key);
   await row.getByLabel('变量值',{exact:true}).fill(value);
   if(secret)await row.getByLabel('保密变量').check();
  }
  await page.getByRole('button',{name:'保存环境',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
 }
 await page.getByLabel('搜索环境').fill(base+' A');
 const card=page.locator('.env-card').filter({hasText:base+' A'});
 await expect(card.locator('.environment-variables-view')).toContainText('已保密保存');
 await card.getByRole('button',{name:'编辑环境',exact:true}).click();
 const rows=page.locator('.variable-row');
 await expect(rows.nth(1).getByLabel('变量值',{exact:true})).toHaveValue('');
 await rows.first().getByLabel('变量值',{exact:true}).fill('A edited');
 await page.getByRole('button',{name:'添加变量'}).click();
 await rows.last().getByLabel('变量名',{exact:true}).fill('temporary');
 await rows.last().getByRole('button',{name:'删除变量',exact:true}).click();
 await expect(rows).toHaveCount(3);
 await page.getByRole('button',{name:'保存环境',exact:true}).click();
 await expect(page.getByRole('dialog')).toHaveCount(0);
 await expect(card.locator('.environment-variables-view')).toBeVisible();
 await expect(card.locator('.environment-variables-view')).toContainText('A edited');
 await page.screenshot({path:path.join(root,'workspace/qa/environment-variables.png'),fullPage:true});
 await page.getByRole('button',{name:'请求工作台',exact:false}).first().click();
 await page.getByLabel('请求路径').fill('/echo');
 await page.getByLabel('请求正文').fill('{"label":"{{label}}","private":"{{private}}"}');
 for(const label of ['A','B']){
  await chooseEnvironment(page,base+' '+label);
  await page.getByRole('button',{name:'发送请求'}).click();
  await expect(page.locator('.status-pill').last()).toContainText('已完成');
  await expect(page.locator('.response-content')).toContainText(label==='A'?'A edited':'"B"');
  await expect(page.locator('.response-content')).not.toContainText('fixture-only-reqdock-variable-for-tests');
 }
 await page.getByRole('button',{name:'变量 2 · 查看/编辑'}).click();
 await expect(page.getByRole('dialog',{name:'编辑环境组',exact:true})).toBeVisible();
 await expect(page.locator('.variable-row')).toHaveCount(3);
 expect(errors).toEqual([]);
});

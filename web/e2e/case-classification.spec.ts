import {test,expect} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import path from 'node:path';

test('模块和业务增删改，用例调整归属后请求保留',async({page})=>{
 const root=path.resolve('..');await page.goto('/');
 const code=execFileSync(path.join(root,'.venv/Scripts/python.exe'),['-m','api_post','pair','--port','18767','--workspace',process.env.API_LAB_E2E_WORKSPACE!],{cwd:root,encoding:'utf8',stdio:['ignore','pipe','ignore']}).trim();
 await page.getByLabel('本机配对码').fill(code);await page.getByRole('button',{name:'连接工作区'}).click();
 const session=await (await page.request.get('/api/auth/session')).json();
 const result=await page.request.post('/api/cases',{headers:{Origin:'http://127.0.0.1:18767','X-CSRF-Token':session.csrf},data:{group:'base/旧业务',request:{name:'classification fixture',path:'/echo',body:'{"message":"preserve me"}'}}});
 expect(result.ok()).toBeTruthy();const original=await result.json();
 await page.getByRole('button',{name:'用例库',exact:false}).first().click();
 await page.getByRole('combobox',{name:'用例模块',exact:true}).click();await page.getByRole('option',{name:/^base（/}).click();
 await page.getByRole('button',{name:'管理模块与业务',exact:true}).click();
 const dialog=page.getByRole('dialog');await expect(dialog.getByRole('button',{name:'选择模块 base',exact:true})).toHaveAttribute('aria-pressed','true');
 await dialog.getByRole('button',{name:'＋ 新增模块',exact:true}).click();
 await dialog.getByLabel('新增模块名称').fill('媒体测试');await dialog.getByRole('button',{name:'新增模块',exact:true}).click();
 await expect(dialog.getByRole('button',{name:'选择模块 媒体测试',exact:true})).toHaveAttribute('aria-pressed','true');
 await dialog.getByRole('button',{name:'＋ 新增业务',exact:true}).click();
 await dialog.getByLabel('新增业务名称').fill('生成');await dialog.getByRole('button',{name:'新增业务',exact:true}).click();
 await expect(dialog.getByRole('button',{name:'编辑业务 生成',exact:true})).toBeVisible();
 await dialog.getByRole('button',{name:'编辑模块',exact:true}).click();
 await dialog.getByLabel('模块名称',{exact:true}).fill('媒体应用');await dialog.getByRole('button',{name:'保存模块',exact:true}).click();
 await dialog.getByRole('button',{name:'编辑业务 生成',exact:true}).click();
 await dialog.getByLabel('业务名称',{exact:true}).fill('图片生成');await dialog.getByRole('button',{name:'保存业务',exact:true}).click();
 await expect(dialog.getByRole('button',{name:'编辑业务 图片生成',exact:true})).toBeVisible();
 await dialog.getByRole('button',{name:'关闭 ×',exact:true}).click();
 await page.getByLabel('搜索用例',{exact:true}).fill('classification fixture');
 await expect(page.locator('.case-library tbody tr')).toHaveCount(1);
 await page.getByRole('button',{name:'调整归属',exact:true}).click();
 await dialog.getByRole('combobox',{name:'归属模块 1',exact:true}).click();await dialog.getByRole('option',{name:'媒体应用',exact:true}).click();
 await dialog.getByRole('combobox',{name:'归属业务 1',exact:true}).click();await dialog.getByRole('option',{name:'图片生成',exact:true}).click();
 await dialog.getByRole('button',{name:'保存归属',exact:true}).click();
 await expect(page.getByRole('dialog')).toHaveCount(0);await expect(page.locator('.case-library tbody tr')).toContainText('classification fixture');
 await page.reload();await page.getByRole('button',{name:'用例库',exact:false}).first().click();await page.getByLabel('搜索用例',{exact:true}).fill('classification fixture');
 await expect(page.locator('.case-library tbody tr')).toContainText('classification fixture');
 const saved=await (await page.request.get('/api/cases/'+original.id)).json();expect(saved.request).toEqual(original.request);expect(saved.modules[0]).toMatchObject({name:'媒体应用',groups:['图片生成']});
 await page.getByRole('button',{name:'管理模块与业务',exact:true}).click();
 await dialog.getByRole('button',{name:'选择模块 媒体应用',exact:true}).click();
 await expect(dialog.getByRole('button',{name:'删除业务 图片生成',exact:true})).toBeDisabled();
 await dialog.getByRole('button',{name:'＋ 新增业务',exact:true}).click();
 await dialog.getByLabel('新增业务名称').fill('待删除');await dialog.getByRole('button',{name:'新增业务',exact:true}).click();
 await expect(dialog.getByRole('button',{name:'编辑业务 待删除',exact:true})).toBeVisible();
 await dialog.getByRole('button',{name:'删除业务 待删除',exact:true}).click();await expect(dialog.getByRole('button',{name:'编辑业务 待删除',exact:true})).toHaveCount(0);
 await dialog.getByRole('button',{name:'＋ 新增模块',exact:true}).click();
 await dialog.getByLabel('新增模块名称').fill('空模块');await dialog.getByRole('button',{name:'新增模块',exact:true}).click();
 await expect(dialog.getByRole('button',{name:'选择模块 空模块',exact:true})).toHaveAttribute('aria-pressed','true');await dialog.getByRole('button',{name:'删除模块',exact:true}).click();
 await expect(dialog.getByRole('button',{name:'选择模块 空模块',exact:true})).toHaveCount(0);
 await dialog.getByRole('button',{name:'选择模块 媒体应用',exact:true}).click();
 await page.screenshot({path:path.join(root,'workspace/qa/case-classification.png'),fullPage:true});
});

test('调整归属时直接新增业务并自动选中',async({page})=>{
 const root=path.resolve('..');await page.goto('/');
 const code=execFileSync(path.join(root,'.venv/Scripts/python.exe'),['-m','api_post','pair','--port','18767','--workspace',process.env.API_LAB_E2E_WORKSPACE!],{cwd:root,encoding:'utf8',stdio:['ignore','pipe','ignore']}).trim();
 await page.getByLabel('本机配对码').fill(code);await page.getByRole('button',{name:'连接工作区'}).click();
 const session=await (await page.request.get('/api/auth/session')).json();
 const result=await page.request.post('/api/cases',{headers:{Origin:'http://127.0.0.1:18767','X-CSRF-Token':session.csrf},data:{group:'内联模块/原业务',request:{name:'inline business fixture',path:'/echo',body:'{}'}}});expect(result.ok()).toBeTruthy();const item=await result.json();
 await page.getByRole('button',{name:'用例库',exact:false}).first().click();await page.getByLabel('搜索用例',{exact:true}).fill(item.name);
 await page.locator('tbody tr').filter({hasText:item.name}).getByRole('button',{name:'调整归属'}).click();
 const dialog=page.getByRole('dialog');await dialog.getByRole('button',{name:'＋ 新增业务',exact:true}).click();
 await dialog.getByLabel('新增业务名称').fill('新模型系列');await dialog.getByRole('button',{name:'新增业务',exact:true}).click();
 await expect(dialog.getByRole('combobox',{name:'归属业务 1',exact:true})).toContainText('新模型系列');
 await dialog.getByRole('button',{name:'保存归属'}).click();await expect(dialog).toHaveCount(0);
 const saved=await (await page.request.get('/api/cases/'+item.id)).json();expect(saved.modules[0]).toMatchObject({name:'内联模块',groups:['新模型系列']});expect(saved.request).toEqual(item.request);
});

test('多归属弹框字段对齐，底部保存始终可见',async({page})=>{
 const root=path.resolve('..');await page.goto('/');
 const code=execFileSync(path.join(root,'.venv/Scripts/python.exe'),['-m','api_post','pair','--port','18767','--workspace',process.env.API_LAB_E2E_WORKSPACE!],{cwd:root,encoding:'utf8',stdio:['ignore','pipe','ignore']}).trim();
 await page.getByLabel('本机配对码').fill(code);await page.getByRole('button',{name:'连接工作区'}).click();
 const session=await (await page.request.get('/api/auth/session')).json(),headers={Origin:'http://127.0.0.1:18767','X-CSRF-Token':session.csrf};
 const created=await page.request.post('/api/cases',{headers,data:{group:'布局模块/已有业务',request:{name:'assignment layout fixture',path:'/echo',body:'{}'}}});expect(created.ok()).toBeTruthy();const item=await created.json();
 const catalog=await (await page.request.get('/api/case-taxonomy')).json();
 const classified=await page.request.put('/api/cases/'+item.id+'/classification',{headers,data:{revision:catalog.revision,case_revision:item.revision,memberships:[item.classification[0],{module_id:item.classification[0].module_id,business_id:null}]}});expect(classified.ok()).toBeTruthy();
 await page.getByRole('button',{name:'用例库',exact:false}).first().click();await page.getByLabel('搜索用例',{exact:true}).fill(item.name);await page.locator('tbody tr').filter({hasText:item.name}).getByRole('button',{name:'调整归属'}).click();
 const dialog=page.getByRole('dialog');await expect(dialog.locator('.assignment-card')).toHaveCount(2);
 for(const width of [1440,900]){
  await page.setViewportSize({width,height:640});
  for(const n of [1,2]){const module=await dialog.getByRole('combobox',{name:'归属模块 '+n,exact:true}).boundingBox(),business=await dialog.getByRole('combobox',{name:'归属业务 '+n,exact:true}).boundingBox();expect(module!.y).toBeCloseTo(business!.y,0);}
  const footer=await dialog.locator('.assignment-footer').boundingBox();expect(footer!.y).toBeGreaterThan(0);expect(footer!.y+footer!.height).toBeLessThanOrEqual(640);
 }
 await dialog.getByRole('region',{name:'归属 2',exact:true}).getByRole('button',{name:'＋ 新增业务'}).click();await expect(dialog.getByLabel('新增业务名称')).toBeVisible();
 await expect(dialog.getByRole('button',{name:'保存归属'})).toBeDisabled();await dialog.locator('.taxonomy-editor').getByRole('button',{name:'取消',exact:true}).click();await expect(dialog.getByRole('button',{name:'保存归属'})).toBeEnabled();
 await page.screenshot({path:path.join(root,'workspace/qa/assignment-dialog.png'),fullPage:true});
});

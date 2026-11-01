import {test,expect} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
test('模块对应应用，base独立、GPT目录合并，模型分组支持下级',async({page})=>{
 const root=path.resolve('..');await page.goto('/');
 const code=execFileSync(path.join(root,'.venv/Scripts/python.exe'),['-m','api_post','pair','--port','18767','--workspace',process.env.API_LAB_E2E_WORKSPACE!],{cwd:root,encoding:'utf8',stdio:['ignore','pipe','ignore']}).trim();
 await page.getByLabel('本机配对码').fill(code);await page.getByRole('button',{name:'连接工作区'}).click();
 const session=await (await page.request.get('/api/auth/session')).json();
 for(const [name,group] of [['base veo','ibrain接口测试/base/视频生成/veo'],['base seedance','ibrain接口测试/base/视频生成/seedance'],['gpt old','Ibrain-GPT-Backend模块/模型A'],['gpt engineering','ibrain接口测试/GPT工程/模型B'],['prompt veo','ibrain接口测试/Prompt工程/视频生成/veo']]){
  const r=await page.request.post('/api/cases',{headers:{Origin:'http://127.0.0.1:18767','X-CSRF-Token':session.csrf},data:{group,request:{name,path:'/echo',body:'{}'}}});expect(r.ok()).toBeTruthy();
 }
 await page.getByRole('button',{name:'用例库',exact:false}).first().click();
 const module=page.getByRole('combobox',{name:'用例模块',exact:true}),group=page.getByRole('combobox',{name:'用例业务',exact:true});
 const positions=await Promise.all([module,group,page.getByLabel('搜索用例',{exact:true})].map(e=>e.boundingBox()));
 expect(positions[0]!.x).toBeLessThan(positions[1]!.x);expect(positions[1]!.x).toBeLessThan(positions[2]!.x);
 // Popovers must remain readable and clickable at desktop and narrow widths.
 for(const width of [1440,1032,900,650]){
  await page.setViewportSize({width,height:1000});
  for(const field of [module,group]){
   await field.click();
   const popup=page.locator('.select-popover'),bounds=await popup.boundingBox(),trigger=await field.boundingBox();
   expect(bounds!.x).toBeCloseTo(trigger!.x,0);
   expect(bounds!.width).toBeCloseTo(trigger!.width,0);
   expect(bounds!.x).toBeGreaterThanOrEqual(0);expect(bounds!.x+bounds!.width).toBeLessThanOrEqual(width);
   // Geometry alone misses ancestor overflow clipping; test the actual hit target.
   expect(await popup.evaluate(el=>{const r=el.getBoundingClientRect();return el.contains(document.elementFromPoint(r.x+12,r.y+24));})).toBeTruthy();
   await popup.locator('input').press('Escape');
  }
 }
 await page.setViewportSize({width:1440,height:1000});
 await module.click();await page.screenshot({path:path.join(root,'workspace/qa/module-dropdown-fixed.png'),fullPage:true});await page.getByLabel('搜索用例模块',{exact:true}).press('Escape');
 async function chooseModule(value:RegExp){await module.click();await page.getByRole('option',{name:value}).click();}
 await chooseModule(/^base（2）$/);
 await expect(page.locator('.case-library tbody tr')).toHaveCount(2);
 await group.click();await page.getByRole('option',{name:'视频生成',exact:true}).click();
 await expect(page.locator('.case-library tbody tr')).toHaveCount(2);
 await group.click();await page.getByRole('option',{name:'视频生成/veo',exact:true}).click();
 await expect(page.locator('.case-library tbody tr')).toHaveCount(1);
 await expect(page.locator('.case-library tbody')).toContainText('base veo');
 await chooseModule(/^GPT模块（2）$/);
 await expect(group).toContainText('全部业务');
 await expect(page.locator('.case-library tbody tr')).toHaveCount(2);
 await expect(page.locator('.case-library tbody')).toContainText('gpt old');
 await expect(page.locator('.case-library tbody')).toContainText('gpt engineering');
 await chooseModule(/^base（2）$/);
 await group.click();
 await expect(page.getByRole('option',{name:'视频生成',exact:true})).toBeVisible();
 await page.screenshot({path:path.join(root,'workspace/qa/case-modules.png'),fullPage:true});
 await page.getByRole('textbox',{name:'搜索用例业务',exact:true}).press('Escape');
 const cli=JSON.parse(execFileSync(path.join(root,'.venv/Scripts/python.exe'),['-m','api_post.skill_cli','--workspace',process.env.API_LAB_E2E_WORKSPACE!,'--port','18767','cases','--module','base','--group','视频生成'],{cwd:root,encoding:'utf8',stdio:['ignore','pipe','ignore']}));
 expect(cli.total).toBe(2);
});

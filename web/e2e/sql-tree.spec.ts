import {test,expect} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
const root=path.resolve('..');
test('大目录三级折叠、分批显示、缓存复用，不触发连库或丢API草稿',async({page})=>{
 await page.goto('/');const code=execFileSync(path.join(root,'.venv/Scripts/python.exe'),['-m','api_post','pair','--port','18767','--workspace',process.env.API_LAB_E2E_WORKSPACE!],{cwd:root,encoding:'utf8',stdio:['ignore','pipe','ignore']}).trim();await page.getByLabel('本机配对码').fill(code);await page.getByRole('button',{name:'连接工作区'}).click();
 await page.getByLabel('请求路径',{exact:true}).fill('/preserve-collapse-draft');
 const profiles=Array.from({length:31},(_,i)=>({id:randomUUID(),revision:1,name:'tree-profile-'+i,environment:i<16?'线上环境':'海外线上环境',host:'127.0.0.1',port:18769,username:'fixture',database:'lab',tls:false,network_scope:'local',allow_write:false,password_pending:true}));
 let caches=0,liveCalls=0;
 await page.route('**/api/sql/connections',route=>route.fulfill({json:profiles}));
 await page.route('**/api/sql/connections/*/catalog-cache',route=>{caches++;return route.fulfill({json:{revision:1,databases:['lab','information_schema'],tables:{lab:Array.from({length:1000},(_,i)=>({name:'table_'+String(i).padStart(4,'0'),kind:'BASE TABLE'})),information_schema:Array.from({length:1000},(_,i)=>({name:'sys_'+i,kind:'BASE TABLE'}))}}});});
 await page.route(/\/api\/sql\/connections\/[^/]+\/(?:databases|tables)(?:\?|$)/,route=>{liveCalls++;return route.fulfill({status:400,json:{detail:'Unexpected live connection'}});});
 await page.getByLabel('工作空间',{exact:true}).selectOption('sql');const tree=page.getByLabel('数据库目录',{exact:true});await expect(tree.locator('.sql-connection')).toHaveCount(0);await expect(tree.locator('.sql-database')).toHaveCount(0);expect(caches).toBe(0);expect(liveCalls).toBe(0);
 await expect(tree.getByRole('button',{name:'展开环境 线上环境',exact:true})).toHaveAttribute('data-tone','production');await expect(tree.getByRole('button',{name:'展开环境 海外线上环境',exact:true})).toHaveAttribute('data-production','true');
 await page.screenshot({path:path.join(root,'workspace/qa/sql-environments-default-collapsed.png'),fullPage:true});
 await tree.getByRole('button',{name:'展开环境 线上环境',exact:true}).click();await tree.getByRole('button',{name:'展开环境 海外线上环境',exact:true}).click();await expect(tree.locator('.sql-connection')).toHaveCount(31);
 await page.locator('.sql-shell').getByRole('button',{name:'＋ 新建环境',exact:true}).click();await page.getByLabel('SQL环境名称',{exact:true}).fill('国内预发环境');await page.getByRole('dialog',{name:'新建SQL环境'}).getByRole('button',{name:'保存环境',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0);await expect(tree.getByRole('button',{name:'展开环境 国内预发环境',exact:true})).toBeVisible();await page.locator('.sql-shell').getByRole('button',{name:'＋ 新建连接',exact:true}).click();await expect(page.getByRole('dialog').getByLabel('连接环境')).toHaveValue('国内预发环境');await page.getByRole('dialog').getByRole('button',{name:'关闭 ×',exact:true}).click();

 await tree.getByRole('button',{name:'展开连接 tree-profile-0',exact:true}).click();await expect(tree.locator('.sql-database')).toHaveCount(2);await expect(tree.locator('.sql-table')).toHaveCount(0);expect(caches).toBe(1);
 await tree.getByRole('button',{name:'新建查询 tree-profile-0 lab',exact:true}).click();await expect(page.getByLabel('SQL编辑区')).toHaveValue(/在此编写SQL/);await expect(page.getByRole('tab',{name:'新建查询',exact:true})).toBeVisible();await expect(tree.locator('.sql-table')).toHaveCount(50);expect(caches).toBe(1);
 await expect(tree.locator('.sql-database-heading button').filter({hasText:'＋'})).toHaveCount(0);
 await page.getByLabel('SQL编辑区').fill('SELECT 7; -- database draft');await page.getByLabel('查询页名称').fill('库草稿');await tree.getByRole('button',{name:'新建查询 tree-profile-0 lab',exact:true}).dblclick();await expect(page.getByRole('tab')).toHaveCount(1);await expect(page.getByLabel('SQL编辑区')).toHaveValue('SELECT 7; -- database draft');

 const tableRows=tree.locator('.sql-table-row'),firstBox=await tableRows.nth(0).boundingBox(),secondBox=await tableRows.nth(1).boundingBox();
 expect(firstBox).not.toBeNull();expect(secondBox).not.toBeNull();expect(secondBox!.y).toBeGreaterThanOrEqual(firstBox!.y+firstBox!.height);
 const structureBox=await tableRows.nth(0).getByRole('button',{name:/查看表结构/}).boundingBox();expect(structureBox!.x+structureBox!.width).toBeLessThanOrEqual(firstBox!.x+firstBox!.width+1);
 await page.screenshot({path:path.join(root,'workspace/qa/sql-table-list-layout.png'),fullPage:true});
 await tree.getByRole('button',{name:'再显示50项',exact:false}).click();await expect(tree.locator('.sql-table')).toHaveCount(100);
 await tree.getByRole('button',{name:'折叠数据库 tree-profile-0 lab',exact:true}).click();await expect(tree.locator('.sql-table')).toHaveCount(0);await tree.getByRole('button',{name:'展开数据库 tree-profile-0 lab',exact:true}).click();await expect(tree.locator('.sql-table')).toHaveCount(100);expect(caches).toBe(1);
 await tree.getByLabel('筛选表 tree-profile-0 lab',{exact:true}).fill('0999');await expect(tree.locator('.sql-table')).toHaveCount(1);await expect(tree.locator('.sql-table')).toContainText('table_0999');
 await tree.getByRole('button',{name:'折叠连接 tree-profile-0',exact:true}).click();await expect(tree.locator('.sql-database')).toHaveCount(0);await tree.getByRole('button',{name:'展开连接 tree-profile-0',exact:true}).click();await expect(tree.locator('.sql-table')).toHaveCount(1);expect(caches).toBe(1);
 await tree.getByRole('button',{name:'折叠环境 线上环境',exact:true}).click();await expect(tree.locator('.sql-connection')).toHaveCount(15);await tree.getByRole('button',{name:'展开环境 线上环境',exact:true}).click();await expect(tree.locator('.sql-connection')).toHaveCount(31);
 await tree.locator('.sql-table').click();await expect(page.getByLabel('SQL编辑区')).toHaveValue(/table_0999/);await page.getByLabel('SQL编辑区').fill('SELECT 1; -- typing does not reload tree');expect(caches).toBe(1);expect(liveCalls).toBe(0);
 const beforeReuse=await page.getByRole('tab').count();await tree.locator('.sql-table').dblclick();await expect(page.getByRole('tab')).toHaveCount(beforeReuse);await expect(page.getByLabel('SQL编辑区')).toHaveValue('SELECT 1; -- typing does not reload tree');
 await tree.getByRole('button',{name:'新建查询 tree-profile-0 lab',exact:true}).click();await expect(page.getByLabel('SQL编辑区')).toHaveValue('SELECT 7; -- database draft');await expect(page.getByRole('tab')).toHaveCount(beforeReuse);
 await page.getByRole('button',{name:'全部收起',exact:true}).click();await expect(tree.locator('.sql-connection')).toHaveCount(0);await expect(tree.locator('.sql-table')).toHaveCount(0);await tree.getByRole('button',{name:'展开环境 线上环境',exact:true}).click();await expect(tree.locator('.sql-connection')).toHaveCount(16);await expect(tree.locator('.sql-database')).toHaveCount(0);
 // The same database name in another connection must get a separate page.
 await tree.getByRole('button',{name:'展开连接 tree-profile-1',exact:true}).click();await tree.getByRole('button',{name:'新建查询 tree-profile-1 lab',exact:true}).click();await expect(page.getByRole('tab')).toHaveCount(beforeReuse+1);await expect(page.getByLabel('SQL编辑区')).toHaveValue(/在此编写SQL/);await tree.getByRole('button',{name:'新建查询 tree-profile-1 lab',exact:true}).dblclick();await expect(page.getByRole('tab')).toHaveCount(beforeReuse+1);
 await page.screenshot({path:path.join(root,'workspace/qa/sql-tree-collapse.png'),fullPage:true});
 await page.getByLabel('工作空间',{exact:true}).filter({visible:true}).selectOption('api');await expect(page.getByLabel('请求路径',{exact:true})).toHaveValue('/preserve-collapse-draft');expect(liveCalls).toBe(0);
});

import {chooseDemoEnvironment} from './helpers';
import {test,expect} from '@playwright/test';
test('本机免配对：无凭证浏览器可直接使用API及SQL，刷新不配对，跨站仍拒绝',async({page,context})=>{
 test.skip(process.env.API_LAB_E2E_ACCESS_MODE!=='local','This test needs an isolated local-access server');let pairingRequests=0;page.on('request',r=>{if(/\/api\/auth\/(?:code|pair)$/.test(new URL(r.url()).pathname))pairingRequests++;});
 await page.goto('/');await expect(page.getByRole('heading',{name:'请求工作台',exact:true})).toBeVisible();await expect(page.getByLabel('本机配对码')).toHaveCount(0);expect((await context.cookies()).filter(c=>c.name==='api_post_session')).toHaveLength(0);
 const session=await page.request.get('/api/auth/session');expect(session.status()).toBe(200);expect((await session.json()).access_mode).toBe('local');
 expect((await page.request.get('/api/environments',{headers:{Authorization:'invalid'}})).status()).toBe(200);
 await chooseDemoEnvironment(page);await page.getByLabel('请求路径',{exact:true}).fill('/echo');await page.getByLabel('请求正文',{exact:true}).fill('{"message":"anonymous local browser"}');await page.getByRole('button',{name:'发送请求',exact:false}).click();await expect(page.locator('.response-panel')).toContainText('200');await expect(page.locator('.response-panel')).toContainText('anonymous local browser');
 await page.getByLabel('工作空间',{exact:true}).selectOption('sql');await expect(page.getByRole('heading',{name:'SQL 工作台',exact:true})).toBeVisible();expect((await page.request.get('/api/sql/connections')).status()).toBe(200);
 const saved=await page.request.post('/api/sql/environments',{data:{name:'免配对环境'}});expect(saved.status()).toBe(200);expect((await page.request.get('/api/sql/environments')).status()).toBe(200);
 expect((await page.request.get('/api/environments',{headers:{Origin:'https://evil.example'}})).status()).toBe(403);expect((await page.request.post('/api/sql/environments',{headers:{Origin:'https://evil.example'},data:{name:'blocked'}})).status()).toBe(403);
 await context.clearCookies();await page.reload();await expect(page.getByRole('heading',{name:'请求工作台',exact:true})).toBeVisible();await expect(page.getByLabel('本机配对码')).toHaveCount(0);expect(pairingRequests).toBe(0);expect((await context.cookies()).filter(c=>c.name==='api_post_session')).toHaveLength(0);
});

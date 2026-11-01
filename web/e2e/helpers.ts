import type {Page} from '@playwright/test';
export async function chooseEnvironment(page:Page,label:string){
 await page.getByRole('combobox',{name:'执行环境',exact:true}).click();
 await page.getByRole('textbox',{name:'搜索执行环境',exact:true}).fill(label);
 await page.getByRole('option',{name:label,exact:true}).click();
}
export async function chooseDemoEnvironment(page:Page){
 await page.getByRole('combobox',{name:'执行环境',exact:true}).click();
 await page.getByRole('textbox',{name:'搜索执行环境',exact:true}).fill('本机演示');
 await page.getByRole('option').filter({hasText:'本机演示'}).first().click();
}

/** Prepare a local SSE fixture through normal request fields, without product templates. */
export async function prepareSse(page:Page){
 await page.getByLabel('请求名称').fill('流式连通性验证');
 await page.getByLabel('请求路径',{exact:true}).fill('/sse');
 await page.getByLabel('请求方法').selectOption('POST');
 await page.getByLabel('请求正文',{exact:true}).fill('{"message":"流式测试"}');
 await page.locator('.request-panel').getByRole('button',{name:'检查',exact:true}).click();
 await page.getByLabel('检查 OpenAI SSE 结束标记').check();
 await page.locator('.request-panel').getByRole('button',{name:'正文',exact:true}).click();
}

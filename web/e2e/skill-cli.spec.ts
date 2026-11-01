import {test,expect} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
test('Skill统一入口执行并查询同一套本地记录',async()=>{
 const root=path.resolve('..'),python=path.join(root,'.venv/Scripts/python.exe');
 const call=(args:string[])=>JSON.parse(execFileSync(python,['-m','api_post.skill_cli','--workspace',process.env.API_LAB_E2E_WORKSPACE!,'--port','18767',...args],{cwd:root,encoding:'utf8',stdio:['ignore','pipe','ignore']}));
 expect(call(['status']).ready).toBe(true);
 const cases=call(['cases','--search','gemini']);
 const result=call(['run','--case',cases.items[0].id,'--environment','本地环境','--platform','ibrain-common','--wait','10']);
 expect(result.state).toBe('completed');
 const saved=call(['runs','--id',result.id]);
 expect(saved.id).toBe(result.id);expect(saved.response_preview).toContain('local fixture');
});

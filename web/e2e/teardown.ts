import {execFileSync} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
export default async function teardown(){
 const root=path.resolve('..'),workspace=process.env.API_LAB_E2E_WORKSPACE;
 if(!workspace)return;
 try{execFileSync(path.join(root,'.venv/Scripts/python.exe'),['-m','api_post','stop','--port','18767','--workspace',workspace],{cwd:root,stdio:'ignore'});}catch{}
 for(let i=0;i<100;i++){if(fs.existsSync(path.join(workspace,'closed.json')))return;await new Promise(r=>setTimeout(r,100));}
}

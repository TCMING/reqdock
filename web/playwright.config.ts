import {defineConfig} from '@playwright/test';
import path from 'node:path';
const root=path.resolve('..');
const workspace=process.env.API_LAB_E2E_WORKSPACE||path.join(root,'workspace','qa','e2e-'+Date.now()+'-'+process.pid);
process.env.API_LAB_E2E_WORKSPACE=workspace;
export default defineConfig({testDir:'./e2e',workers:1,timeout:45000,globalTeardown:'./e2e/teardown.ts',webServer:{command:'"'+path.join(root,'.venv/Scripts/python.exe')+'" "'+path.join(root,'scripts/e2e_server.py')+'" --workspace "'+workspace+'"',url:'http://127.0.0.1:18767/api/health',timeout:60000,reuseExistingServer:false},use:{baseURL:'http://127.0.0.1:18767',channel:'msedge',viewport:{width:1440,height:1000},headless:true},reporter:[['list']]});

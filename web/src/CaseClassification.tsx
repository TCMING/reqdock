import React,{useEffect,useState} from 'react';
import SearchSelect from './SearchSelect';
export type Membership={module_id:string;business_id:string|null};
type Catalog={revision:number;modules:{id:string;name:string;count:number}[];businesses:{id:string;module_id:string;name:string;count:number}[]};
type CaseRef={id:string;revision:number;name:string;classification?:Membership[]};
type Editor={kind:'module'|'business';action:'create'|'rename';id?:string;name:string;moduleId?:string;row?:number};
type Props={api:<T=any>(path:string,options?:RequestInit)=>Promise<T>;onClose:()=>void;onChanged:()=>void;caseItem?:CaseRef;initialModule?:string};
export default function CaseClassification({api,onClose,onChanged,caseItem,initialModule=''}:Props){
 const [catalog,setCatalog]=useState<Catalog|null>(null),[module,setModule]=useState(initialModule),[business,setBusiness]=useState('');
 const [memberships,setMemberships]=useState<Membership[]>([]),[caseRevision,setCaseRevision]=useState(caseItem?.revision||1);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState(''),[editor,setEditor]=useState<Editor|null>(null),[moduleSearch,setModuleSearch]=useState(''),[businessSearch,setBusinessSearch]=useState('');
 async function refresh(){setBusy(true);setError('');try{
  const c=await api<Catalog>('/case-taxonomy');setCatalog(c);setEditor(null);
  const first=caseItem?await api<CaseRef>('/cases/'+caseItem.id):null;
  if(first){setMemberships(first.classification?.length?first.classification:[{module_id:c.modules[0]?.id||'',business_id:null}]);setCaseRevision(first.revision);}
  setModule(old=>c.modules.some(m=>m.id===old)?old:c.modules[0]?.id||'');
 }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 useEffect(()=>{void refresh();},[]);
 function selectModule(id:string){setModule(id);setBusiness('');setBusinessSearch('');setEditor(null);setError('');setMessage('');}
 async function command(action:string,kind:string,id?:string,name?:string,moduleId?:string){if(!catalog)return;setBusy(true);setError('');try{
  const next=await api<Catalog>('/case-taxonomy',{method:'PUT',body:JSON.stringify({revision:catalog.revision,action,kind,id,name:name||'',module_id:moduleId||module||null})});
  setCatalog(next);
  if(kind==='module'){if(action==='delete')selectModule(next.modules[0]?.id||'');else if(action==='create'){const added=next.modules.find(m=>!catalog.modules.some(x=>x.id===m.id));selectModule(added?.id||'');setModuleSearch('');}}
  else if(action==='delete')setBusiness('');else if(action==='create'){const added=next.businesses.find(b=>!catalog.businesses.some(x=>x.id===b.id));setBusiness(added?.id||'');setBusinessSearch('');if(editor?.row!==undefined&&added)updateRow(editor.row,{business_id:added.id});}
  setEditor(null);setMessage(action==='delete'?'已删除':action==='create'?'已新增':'已保存');await onChanged();
 }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 function updateRow(index:number,value:Partial<Membership>){if(value.module_id&&editor?.row===index)setEditor(null);setMemberships(old=>old.map((m,i)=>i===index?{...m,...value}:m));}
 async function save(){if(!catalog||!caseItem)return;setBusy(true);setError('');try{
  await api('/cases/'+caseItem.id+'/classification',{method:'PUT',body:JSON.stringify({revision:catalog.revision,case_revision:caseRevision,memberships})});await onChanged();onClose();
 }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 const moduleOptions=catalog?.modules.map(m=>({value:m.id,label:m.name}))||[];
 const selected=catalog?.modules.find(m=>m.id===module),owned=catalog?.businesses.filter(b=>b.module_id===module)||[];
 const moduleBlocked=!!selected&&(selected.count>0||owned.length>0);
 const editForm=editor&&<form className="taxonomy-editor" onSubmit={e=>{e.preventDefault();void command(editor.action,editor.kind,editor.id,editor.name,editor.moduleId);}}>
  <div><strong>{editor.action==='create'?'新增':'编辑'}{editor.kind==='module'?'模块':'业务'}</strong>{editor.kind==='business'&&<small>所属模块：{catalog?.modules.find(m=>m.id===(editor.moduleId||module))?.name}</small>}</div>
  <label className="sr-only" htmlFor="taxonomy-name">{editor.action==='create'?'新增':''}{editor.kind==='module'?'模块':'业务'}名称</label>
  <input id="taxonomy-name" autoFocus required maxLength={editor.kind==='module'?80:200} value={editor.name} placeholder={editor.kind==='module'?'例如：Prompt工程':'例如：Gemini，可使用 / 表示层级'} onChange={e=>setEditor({...editor,name:e.target.value})}/>
  <button className="primary" disabled={busy||!editor.name.trim()} type="submit">{editor.action==='create'?'新增':'保存'}{editor.kind==='module'?'模块':'业务'}</button><button type="button" className="secondary" onClick={()=>setEditor(null)}>取消</button>
 </form>;
 return <div className="modal-backdrop"><section className={'modal taxonomy-modal'+(!caseItem?' taxonomy-admin':' taxonomy-assignment')} role="dialog" aria-modal="true" aria-labelledby="taxonomy-title">
  <div className="modal-title"><div><h2 id="taxonomy-title">{caseItem?'调整用例归属':'管理模块与业务'}</h2>{!caseItem&&<p>选择模块，管理其下的业务</p>}</div><div className="taxonomy-heading-actions"><button className="text-button" disabled={busy} onClick={refresh}>刷新分类</button><button className="text-button" disabled={busy} onClick={onClose}>关闭 ×</button></div></div>
  {error&&<div className="notice error" role="alert">{error}</div>}{message&&!error&&<div className="taxonomy-success" role="status">✓ {message}</div>}
  {!catalog&&<p className="muted">正在读取分类…</p>}
  {catalog&&<fieldset disabled={busy} className="taxonomy-fields">{caseItem?<>
   <div className="assignment-summary"><div><small>当前用例</small><strong>{caseItem.name}</strong></div><span>{memberships.length} 项归属</span></div>
   <p className="assignment-description">选择所属模块与业务。可添加多个归属，请求内容和请求记录保持不变。</p>
   <div className="assignment-scroll">{memberships.map((m,i)=><section className="assignment-card" key={i} aria-label={'归属 '+(i+1)}>
    <div className="assignment-card-heading"><strong><span>{String(i+1).padStart(2,'0')}</span>归属 {i+1}</strong><button className="text-button danger-text" aria-label={'移除归属 '+(i+1)} disabled={busy||memberships.length===1} onClick={()=>{setMemberships(ms=>ms.filter((_,n)=>n!==i));setEditor(null);}}>移除</button></div>
    <div className="assignment-field-grid"><div><div className="assignment-field-heading"><span>模块（应用）</span></div><SearchSelect label={'归属模块 '+(i+1)} value={m.module_id} options={moduleOptions} onChange={id=>updateRow(i,{module_id:id,business_id:null})}/></div>
    <div><div className="assignment-field-heading"><span>业务</span><button className="text-button" disabled={!m.module_id} onClick={()=>setEditor({kind:'business',action:'create',name:'',moduleId:m.module_id,row:i})}>＋ 新增业务</button></div><SearchSelect label={'归属业务 '+(i+1)} value={m.business_id||''} options={[{value:'',label:'未分类'},...catalog.businesses.filter(b=>b.module_id===m.module_id).map(b=>({value:b.id,label:b.name}))]} onChange={id=>updateRow(i,{business_id:id||null})}/></div></div>
    {editor?.row===i&&editForm}
   </section>)}</div>
   <div className="assignment-footer"><button className="secondary" disabled={busy||memberships.length>=32||!catalog.modules.length} onClick={()=>setMemberships(ms=>[...ms,{module_id:ms.at(-1)?.module_id||initialModule||catalog.modules[0].id,business_id:null}])}>＋ 添加归属</button><div><button className="secondary" disabled={busy} onClick={onClose}>取消</button><button className="primary" disabled={busy||!!editor||memberships.some(m=>!m.module_id)} onClick={save}>{busy?'保存中…':'保存归属'}</button></div></div>
  </>:<>
   <div className="taxonomy-admin-grid"><aside className="taxonomy-module-pane"><div className="taxonomy-pane-heading"><strong>模块 <span>{catalog.modules.length}</span></strong><button className="text-button" onClick={()=>setEditor({kind:'module',action:'create',name:''})}>＋ 新增模块</button></div><input aria-label="搜索模块" placeholder="搜索模块" value={moduleSearch} onChange={e=>setModuleSearch(e.target.value)}/><div className="taxonomy-module-list" aria-label="模块列表">{catalog.modules.filter(m=>m.name.toLowerCase().includes(moduleSearch.toLowerCase())).map(m=><button key={m.id} aria-label={'选择模块 '+m.name} aria-pressed={m.id===module} className={'taxonomy-module-item'+(m.id===module?' selected':'')} onClick={()=>selectModule(m.id)}><span>{m.name}</span><small>{m.count}</small></button>)}{!catalog.modules.length&&<p className="taxonomy-empty">先新增一个模块</p>}</div></aside>
   <div className="taxonomy-business-pane"><div className="taxonomy-pane-heading"><div><strong>{selected?.name||'请选择模块'}</strong><small>{owned.length} 个业务 · {selected?.count||0} 个用例</small></div><div className="taxonomy-heading-actions">{selected&&<><button className="text-button" onClick={()=>setEditor({kind:'module',action:'rename',id:selected.id,name:selected.name})}>编辑模块</button><button className="text-button danger-text" disabled={moduleBlocked} title={moduleBlocked?'请先迁移用例并清空业务':'删除空模块'} onClick={()=>command('delete','module',module)}>删除模块</button></>}<button className="primary" disabled={!module} onClick={()=>setEditor({kind:'business',action:'create',name:'',moduleId:module})}>＋ 新增业务</button></div></div>
   <input aria-label="搜索业务" placeholder="搜索当前模块的业务" value={businessSearch} onChange={e=>setBusinessSearch(e.target.value)}/>
   <div className="taxonomy-business-list" aria-label="业务列表">{owned.filter(b=>b.name.toLowerCase().includes(businessSearch.toLowerCase())).sort((a,b)=>a.name.localeCompare(b.name)).map(b=>{const blocked=b.count>0||owned.some(x=>x.name.startsWith(b.name+'/'));return <div key={b.id} className={'taxonomy-business-item'+(b.id===business?' selected':'')}><button className="taxonomy-business-name" onClick={()=>setBusiness(b.id)}><strong>{b.name}</strong><small>{b.count} 个用例</small></button><button className="text-button" aria-label={'编辑业务 '+b.name} onClick={()=>{setBusiness(b.id);setEditor({kind:'business',action:'rename',id:b.id,name:b.name,moduleId:module});}}>编辑</button><button className="text-button danger-text" aria-label={'删除业务 '+b.name} disabled={blocked} title={blocked?'仍有用例或下级业务，请先调整归属':'删除空业务'} onClick={()=>command('delete','business',b.id)}>删除</button></div>;})}
   {!owned.length&&<div className="taxonomy-empty"><strong>此模块还没有业务</strong><p>点击“新增业务”，添加模型系列或业务分类。</p></div>}{owned.length>0&&!owned.some(b=>b.name.toLowerCase().includes(businessSearch.toLowerCase()))&&<p className="taxonomy-empty">没有匹配的业务</p>}</div></div></div>
   {editForm}<p className="taxonomy-admin-note">有用例或下级业务的分类需先调整归属，再删除。业务名称可用 / 表示层级。</p>
  </>}</fieldset>}
 </section></div>;
}

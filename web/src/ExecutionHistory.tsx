import {useEffect,useRef,useState} from 'react';
type Item={kind:'api'|'sql';id:string;created_at:string;name:string;environment:string;state:string;duration_ms:number|null;status_code:number|null;source:string;agent_name:string;task_id:string;mode:string};
type Filters={kind:string;source:string;agent_name:string;task_id:string;environment:string;state:string;search:string};
const blank:Filters={kind:'',source:'',agent_name:'',task_id:'',environment:'',state:'',search:''};
const sources:Record<string,string>={ui:'界面',cli:'CLI',skill:'Skill',api:'本地 API',unknown:'来源未知'};
const states:Record<string,string>={queued:'排队中',running:'执行中',awaiting_confirmation:'等待确认',completed:'已完成',failed:'失败',timed_out:'超时',cancelled:'已取消',interrupted:'已中断',expired:'确认过期',unknown:'结果未知'};
const stamp=(s:string)=>new Date(s).toLocaleString('zh-CN',{hour12:false});
const text=(v:unknown)=>typeof v==='string'?v:JSON.stringify(v??null,null,2);
export default function ExecutionHistory({active,onSpaceChange}:{active:boolean;onSpaceChange:(s:string)=>void}){
 const [draft,setDraft]=useState({...blank}),[filters,setFilters]=useState({...blank}),[page,setPage]=useState(1),[refresh,setRefresh]=useState(0);
 const [data,setData]=useState<{items:Item[];total:number}>({items:[],total:0}),[loading,setLoading]=useState(false),[error,setError]=useState('');
 const [selected,setSelected]=useState<Item|null>(null),[detail,setDetail]=useState<Record<string,any>|null>(null),[detailError,setDetailError]=useState('');
 const detailRequest=useRef<AbortController|null>(null);
 useEffect(()=>{
  if(!active)return;const controller=new AbortController();setLoading(true);setError('');
  const params=new URLSearchParams({page:String(page),limit:'20'});Object.entries(filters).forEach(([k,v])=>{if(v)params.set(k,v);});
  fetch('/api/executions?'+params,{signal:controller.signal}).then(async r=>{const v=await r.json();if(!r.ok)throw Error(v.detail||'读取执行记录失败');return v;}).then(v=>{setData(v);if(page>1&&!v.items.length)setPage(p=>p-1);}).catch(e=>{if(e.name!=='AbortError')setError(e.message);}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
  return()=>controller.abort();
 },[active,filters,page,refresh]);
 useEffect(()=>()=>detailRequest.current?.abort(),[]);
 useEffect(()=>{if(!active){detailRequest.current?.abort();setSelected(null);setDetail(null);}},[active]);
 function close(){detailRequest.current?.abort();setSelected(null);setDetail(null);setDetailError('');}
 async function open(item:Item){detailRequest.current?.abort();const c=new AbortController();detailRequest.current=c;setSelected(item);setDetail(null);setDetailError('');try{const r=await fetch(item.kind==='api'?'/api/runs/'+item.id:'/api/sql/jobs/'+item.id,{signal:c.signal});const value=await r.json();if(!r.ok)throw Error(value.detail||'读取详情失败');if(!c.signal.aborted)setDetail(value);}catch(e){if((e as Error).name!=='AbortError')setDetailError((e as Error).message);}}
 function field(key:keyof Filters,value:string){setDraft(d=>({...d,[key]:value}));}
 return <div className="app-shell"><aside className="sidebar"><div className="brand"><span className="brand-symbol">a.</span><div><strong>ReqDock</strong><small>本地执行追踪</small></div></div><label className="space-switch"><span>工作空间</span><select aria-label="工作空间" value="records" onChange={e=>onSpaceChange(e.target.value)}><option value="api">API 空间</option><option value="sql">SQL 空间</option><option value="records">统一执行记录</option></select></label><p className="field-note">只记录经过 ReqDock 的操作。按需读取，不增加后台监控进程。</p></aside>
 <main className="main"><header className="topbar"><span>工作空间 / 统一执行记录</span><span className="local-label">LOCAL ONLY</span></header><div className="page-content"><div className="page-title"><div><h1>统一执行记录</h1><p className="muted">在同一处追踪 API 和 SQL，按来源、Agent 与任务查看。原工作空间与草稿保持不变。</p></div><button className="secondary" disabled={loading} onClick={()=>setRefresh(n=>n+1)}>刷新记录</button></div>
 <section className="panel"><form className="execution-filters" onSubmit={e=>{e.preventDefault();setPage(1);setFilters({...draft});setRefresh(n=>n+1);}}>
 <label>类型<select aria-label="类型" value={draft.kind} onChange={e=>field('kind',e.target.value)}><option value="">全部类型</option><option value="api">API</option><option value="sql">SQL</option></select></label>
 <label>调用来源<select aria-label="调用来源" value={draft.source} onChange={e=>field('source',e.target.value)}><option value="">全部来源</option>{Object.entries(sources).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
 <label>Agent<input maxLength={80} value={draft.agent_name} onChange={e=>field('agent_name',e.target.value)} placeholder="精确匹配"/></label>
 <label>任务标识<input maxLength={128} value={draft.task_id} onChange={e=>field('task_id',e.target.value)} placeholder="精确匹配"/></label>
 <label>环境<input maxLength={200} value={draft.environment} onChange={e=>field('environment',e.target.value)} placeholder="精确匹配"/></label>
 <label>执行状态<select aria-label="执行状态" value={draft.state} onChange={e=>field('state',e.target.value)}><option value="">全部状态</option>{Object.entries(states).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
 <label>名称或记录 ID<input maxLength={120} value={draft.search} onChange={e=>field('search',e.target.value)}/></label>
 <div className="execution-filter-actions"><button className="primary" disabled={loading}>查询</button><button type="button" className="secondary" onClick={()=>{setDraft({...blank});setFilters({...blank});setPage(1);}}>重置</button></div></form>
 {error&&<div role="alert" className="notice error">{error}</div>}{loading&&<p className="field-note" role="status">正在读取记录…</p>}
 <div className="execution-table-wrap"><table className="execution-table"><thead><tr><th>时间 / 类型</th><th>名称 / 环境</th><th>调用来源</th><th>Agent</th><th>任务标识</th><th>状态 / HTTP</th><th>耗时</th><th>操作</th></tr></thead><tbody>{data.items.map(item=><tr key={item.kind+item.id}><td>{stamp(item.created_at)}<small>{item.kind.toUpperCase()} · {item.mode}</small></td><td>{item.name}<small>{item.environment}</small></td><td>{sources[item.source]||sources.unknown}</td><td>{item.agent_name||'—'}</td><td><code>{item.task_id||'—'}</code></td><td>{states[item.state]||item.state}{item.status_code!=null&&<small>HTTP {item.status_code}</small>}</td><td>{item.duration_ms==null?'—':Math.round(item.duration_ms)+' ms'}</td><td><button className="text-button" aria-label={'查看执行记录 '+item.id} onClick={()=>open(item)}>查看详情 →</button></td></tr>)}</tbody></table></div>
 {!loading&&!error&&!data.items.length&&<p className="execution-empty">没有符合条件的执行记录。</p>}
 <div className="execution-pagination"><span>第 {page} 页 · 共 {data.total} 条 · 每页 20 条</span><div><button className="secondary" disabled={loading||page===1} onClick={()=>setPage(p=>p-1)}>上一页</button><button className="secondary" disabled={loading||page*20>=data.total} onClick={()=>setPage(p=>p+1)}>下一页</button></div></div></section>
 <p className="field-note">来源和 Agent / 任务标识由调用方声明，仅用于追踪，不代表经过身份认证。旧记录显示来源未知。HTTP 200 或已完成不等于业务成功；完整流事件、结果导出仍在原工作空间查看。</p></div></main>
 {selected&&<div className="modal-backdrop"><section className="modal execution-detail" role="dialog" aria-modal="true" aria-labelledby="execution-detail-title"><div className="modal-title"><div><h2 id="execution-detail-title">{selected.kind.toUpperCase()} 执行详情</h2><p className="field-note">{selected.id}</p></div><button onClick={close}>关闭 ×</button></div><dl className="execution-detail-meta"><dt>调用来源</dt><dd>{sources[selected.source]||sources.unknown}</dd><dt>Agent</dt><dd>{selected.agent_name||'—'}</dd><dt>任务标识</dt><dd>{selected.task_id||'—'}</dd><dt>执行环境</dt><dd>{selected.environment}</dd></dl>{detailError&&<p className="notice error" role="alert">{detailError}</p>}{!detail&&!detailError&&<p role="status">正在读取详情…</p>}{detail&&<>{detail.error&&<p className="notice error">{String(detail.error)}</p>}{selected.kind==='api'?<><h3>请求快照</h3><pre>{detail.method} {detail.url}{'\n'}{text(detail.request?.configured_headers||detail.request?.headers)}{'\n'}{text(detail.request?.body).slice(0,131072)}</pre><h3>响应头</h3><pre>{text(detail.response_headers)}</pre><h3>响应正文预览</h3><pre>{text(detail.response_preview||'暂无普通正文；流式内容请在 API 请求记录中查看。').slice(0,131072)}</pre></>:<><h3>SQL</h3><pre>{text(detail.sql)}</pre><h3>执行消息</h3><pre>{text(detail.messages)}</pre><h3>结果预览</h3><pre>{text({columns:detail.columns,rows:detail.rows}).slice(0,131072)}</pre></>}<p className="field-note">只读查看，不重新执行。大段正文和结果最多预览 128K 字符；完整内容及导出在原工作空间查看。</p></>}</section></div>}
 </div>;
}

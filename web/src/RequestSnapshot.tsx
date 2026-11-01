import React,{useState} from 'react';
type SnapshotRun={name:string;method:string;url:string;request?:{body:string;headers:Record<string,string>;header_items?:{name:string;value:string}[];headers_source?:string;expected_status:number|null;contains:string;stream:boolean}};
export default function RequestSnapshot({run,onReplay,onBack}:{run:SnapshotRun;onReplay:()=>void;onBack:()=>void}){
 const [tab,setTab]=useState('body');
 return <section className="panel request-panel" aria-label="历史请求快照">
  <div className="panel-heading"><span className="section-number">01</span><strong>请求快照</strong><span className="panel-meta">READ ONLY</span></div>
  <div className="request-name"><input aria-label="快照请求名称" readOnly value={run.name}/></div>
  <div className="url-bar"><span className="snapshot-method" aria-label="快照请求方法">{run.method}</span><input aria-label="快照请求地址" readOnly value={run.url}/></div>
  <p className="field-note">本次执行的脱敏记录，与右侧响应对应。编辑复测会创建新的执行记录。</p>
  <div className="tabs">{[['body','正文'],['headers','请求头'],['checks','检查']].map(([key,label])=><button key={key} className={tab===key?'tab active':'tab'} onClick={()=>setTab(key)}>{label}</button>)}</div>
  {tab==='body'&&<textarea className="code-editor" aria-label="快照请求正文" readOnly value={run.request?.body||''}/>}
  {tab==='headers'&&<><p className="field-note">{run.request?.headers_source==='final_pre_send'?'发送前构建的最终请求头，敏感值已脱敏。':'仅保存了配置请求头。'}</p><textarea className="code-editor" aria-label="快照请求头" readOnly value={JSON.stringify(run.request?.header_items||run.request?.headers||{},null,2)}/></>}
  {tab==='checks'&&<div className="checks-form"><p>预期 HTTP 状态：{run.request?.expected_status??'未配置'}</p><p>响应包含文本：{run.request?.contains||'未配置'}</p><p>SSE 结束标记检查：{run.request?.stream?'开启':'关闭'}</p></div>}
  <div className="send-row"><button onClick={onBack}>返回草稿</button><button className="primary" disabled={!run.request} onClick={onReplay}>编辑并复测</button></div>
 </section>;
}

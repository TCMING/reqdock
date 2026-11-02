export type ContextLabels = {agent_name:string;task_id:string};
export const emptyContext:ContextLabels = {agent_name:'',task_id:''};
export default function ExecutionContextFields({value,onChange}:{value:ContextLabels;onChange:(v:ContextLabels)=>void}){
 return <details className="execution-context"><summary>执行归属（可选）</summary><div className="form-grid"><label>Agent 名称<input maxLength={80} value={value.agent_name} placeholder="例如 Codex；手动操作可留空" onChange={e=>onChange({...value,agent_name:e.target.value})}/></label><label>任务标识<input maxLength={128} value={value.task_id} placeholder="同一任务的 API / SQL 使用相同标识" onChange={e=>onChange({...value,task_id:e.target.value})}/></label></div><p className="field-note">仅供执行追踪，不作为身份或权限依据；请勿填写密钥和敏感数据。界面操作自动标记为 UI。</p></details>;
}

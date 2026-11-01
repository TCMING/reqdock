import React,{useEffect,useMemo} from 'react';
import SearchSelect from './SearchSelect';
type Connection={grouping?:{key:string;label:string;variable:string;kind:'server'|'fixed'|'local'};id:string;name:string;base_url:string;variables?:Record<string,string>;secret_variables?:string[];network_scope?:string;credential_ref?:string|null;read_timeout?:number;total_timeout?:number;source?:{key:string;group_label?:string}};
type Member={connection:Connection;variable:string};
type Group={key:string;label:string;kind:'server'|'fixed'|'local';members:Member[]};
const displayName=(name:string)=>name.replace(/^AI服务平台\s*\/\s*/,'');
function groupsFor(connections:Connection[]):Group[]{
 const groups=new Map<string,Group>();
 for(const connection of connections){
  const source=connection.source?.key||'',server=source.match(/^(.*\/server\/[^/]+)\/([^/]+)$/),fixed=source.match(/^(.*)\/fixed\//);
  const name=displayName(connection.name);let key,label,variable,kind:Group['kind'];
  if(connection.grouping){({key,label,variable,kind}=connection.grouping);}
  else if(server){key=server[1];variable=server[2];kind=key.startsWith('local/')?'local':'server';const suffix=' / '+variable;label=connection.source?.group_label||(name.endsWith(suffix)?name.slice(0,-suffix.length):name);}
  else if(fixed){key=fixed[1]+'/fixed';label='固定地址';variable=connection.base_url;kind='fixed';}
  else{key='local:'+connection.id;label=name;variable='基础地址';kind='local';}
  if(!groups.has(key))groups.set(key,{key,label,kind,members:[]});
  groups.get(key)!.members.push({connection,variable});
 }
 return [...groups.values()];
}
function equivalent(members:Member[]){return members.length>0&&members.every(m=>JSON.stringify([m.connection.base_url,m.connection.network_scope,m.connection.credential_ref,m.connection.read_timeout,m.connection.total_timeout,m.connection.variables,m.connection.secret_variables])===JSON.stringify([members[0].connection.base_url,members[0].connection.network_scope,members[0].connection.credential_ref,members[0].connection.read_timeout,members[0].connection.total_timeout,members[0].connection.variables,members[0].connection.secret_variables]));}
export function resolve(group:Group|undefined,path:string,hint?:string):{id:string;issue:string}{
 if(!group)return {id:'',issue:'请先选择环境'};
 const variable=path.match(/^\s*\{\{([^{}:]+)\}\}/)?.[1]||(!/^https?:/i.test(path)?hint:'');
 let matches:Member[]=[];
 if(variable){matches=group.members.filter(m=>m.variable===variable);if(!matches.length)matches=group.members.filter(m=>m.connection.variables?.[variable]===m.connection.base_url);if(!matches.length&&variable==='base_url'&&group.members.length===1)matches=group.members;if(!matches.length)return {id:'',issue:'该环境未配置 {{'+variable+'}}，请调整请求地址或环境配置'};}
 else if(/^https?:/i.test(path)){
  try{const target=new URL(path);matches=group.members.filter(m=>{try{const base=new URL(m.connection.base_url),prefix=base.pathname.replace(/\/$/,'');return target.origin===base.origin&&(!prefix||target.pathname===prefix||target.pathname.startsWith(prefix+'/'));}catch{return false;}});
   if(matches.length>1){const longest=Math.max(...matches.map(m=>new URL(m.connection.base_url).pathname.replace(/\/$/,'').length));matches=matches.filter(m=>new URL(m.connection.base_url).pathname.replace(/\/$/,'').length===longest);}
  }catch{return {id:'',issue:'请求地址格式不正确'};}
  if(!matches.length)return {id:'',issue:'所选环境没有与请求URL一致的地址，请调整环境或请求地址'};
 }else matches=group.members;
 if(matches.length===1||equivalent(matches))return {id:matches[0].connection.id,issue:''};
 return {id:'',issue:'无法唯一确定请求地址，请使用 {{地址变量}} 或填写该环境的完整URL'};
}
export default function EnvironmentSelector({connections,value,onChange,groupValue,onGroupChange,requestPath,serviceHint,onIssue}:{connections:Connection[];value:string;onChange:(id:string)=>void;groupValue:string;onGroupChange:(key:string)=>void;requestPath:string;serviceHint?:string|null;onIssue:(message:string)=>void}){
 const groups=useMemo(()=>groupsFor(connections),[connections]);
 const selectedGroup=groups.find(g=>g.members.some(m=>m.connection.id===value));
 const group=groups.find(g=>g.key===groupValue)||(!groupValue?selectedGroup:undefined);
 const match=resolve(group,requestPath,serviceHint||undefined);
 useEffect(()=>{if(!groupValue&&selectedGroup){onGroupChange(selectedGroup.key);return;}if(groupValue&&!groups.some(g=>g.key===groupValue)){onGroupChange('');onChange('');return;}if(value!==match.id)onChange(match.id);onIssue(match.issue);},[groupValue,groups,value,requestPath,serviceHint,match.id,match.issue]);
 function chooseGroup(key:string){onGroupChange(key);const next=resolve(groups.find(g=>g.key===key),requestPath,serviceHint||undefined);onChange(next.id);onIssue(next.issue);}
 return <div className="environment-selector header-environment"><span className="header-environment-label">环境</span><SearchSelect label="执行环境" value={group?.key||''} options={[{value:'',label:'请选择环境'},...groups.map(g=>({value:g.key,label:g.label}))]} onChange={chooseGroup}/></div>;
}

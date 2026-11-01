import React,{useEffect,useState} from 'react';
import type {Resource} from './ResourceLibrary';
type Props={resource:Resource;api:<T=any>(path:string,options?:RequestInit)=>Promise<T>;onClose:()=>void;onChanged:()=>void};
export default function MediaPreview({resource,api,onClose,onChanged}:Props){
 const [value,setValue]=useState(resource),[addressId,setAddressId]=useState(resource.addresses[0]?.id||''),[confirmed,setConfirmed]=useState(!!resource.addresses[0]?.trusted),[busy,setBusy]=useState(false),[job,setJob]=useState<any>(null),[error,setError]=useState(''),[mediaError,setMediaError]=useState(false);
 const selected=value.addresses.find(a=>a.id===addressId),active=job&&['queued','running'].includes(job.state);
 useEffect(()=>{if(!active)return;let stopped=false;let timer:number;
  async function tick(){try{const result=await api('/resource-jobs/'+job.id);if(stopped)return;setJob(result);
   if(result.state==='completed'){const current=await api<Resource>('/resources/'+value.id);if(!stopped){setValue(current);setMediaError(false);onChanged();}}
   else if(['queued','running'].includes(result.state))timer=window.setTimeout(tick,750);
   else setError(result.error||'下载未完成，请重试或导入本地文件');
  }catch(e){if(!stopped)setError((e as Error).message);}}
  void tick();return()=>{stopped=true;clearTimeout(timer);};
 },[job?.id]);
 async function download(){if(!selected||!confirmed)return;setBusy(true);setError('');try{
  let current=value;
  if(!selected.trusted){current=await api<Resource>('/resources/'+value.id,{method:'PUT',body:JSON.stringify({name:value.name,kind:value.kind,tags:value.tags,revision:value.revision,addresses:value.addresses.map(a=>({id:a.id,purpose:a.purpose,url:null,network_scope:a.network_scope,credential_ref:a.credential_ref,trusted:a.id===addressId?true:a.trusted}))})});setValue(current);}
  const link=current.addresses.find(a=>a.id===addressId);
  setJob(await api('/resources/'+current.id+'/download',{method:'POST',body:JSON.stringify({revision:current.revision,address_id:link?.id})}));onChanged();
 }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 const local=!!value.file&&!value.file_missing,src='/api/resources/'+value.id+'/file?revision='+value.revision;
 return <div className="modal-backdrop"><section className="modal media-preview" role="dialog" aria-label="资源预览"><div className="modal-title"><h2>{value.name} · v{value.revision}</h2><button className="text-button" onClick={onClose}>关闭 ×</button></div>
 {error&&<div className="notice error" role="alert">{error}</div>}
 {local?<><div className="media-stage">{value.kind==='image'?<img alt={value.name} src={src} onError={()=>setMediaError(true)}/>:value.kind==='video'?<video key={src} controls playsInline preload="metadata" src={src} onError={()=>setMediaError(true)}/>:<audio key={src} controls preload="metadata" src={src} onError={()=>setMediaError(true)}/>}</div>
 {mediaError&&<div className="notice error">无法预览，请检查文件是否完整或浏览器是否支持该媒体编码，可另存后用本机播放器打开。</div>}
 <p className="field-note">{value.file?.filename} · {value.file?.mime} · 本地文件预览</p><a href={src+'&download=true'} download>另存本地文件</a></>:<>
 <p className="field-note">{value.file_missing?'本地文件已丢失。':'此资源还没有本地文件。'}下载保存后即可预览图片、播放视频和音频。</p>
 {selected?<><label>预览来源<select aria-label="预览来源" disabled={busy||active} value={addressId} onChange={e=>{setAddressId(e.target.value);setConfirmed(!!value.addresses.find(a=>a.id===e.target.value)?.trusted);setError('');}}>{value.addresses.map((a,i)=><option key={a.id} value={a.id}>URL {i+1}</option>)}</select></label><code className="preview-source-url">{selected.url}</code>
 <label className="checkbox preview-confirm"><input type="checkbox" disabled={busy||active||selected.trusted} checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>允许从此来源下载文件用于预览</label>
 <button className="primary" disabled={!confirmed||busy||active} onClick={download}>{active?'正在下载…':busy?'准备中…':'下载并预览'}</button>
 {active&&<p className="field-note">已下载 {((job.received_bytes||0)/1024/1024).toFixed(2)} MiB。关闭预览后可在资源库查看下载进度。</p>}</>:<p className="field-note">请先编辑资源添加链接，或在资源列表导入本地文件。</p>}
 </>}
 </section></div>;
}

import React,{useEffect,useLayoutEffect,useRef,useState} from 'react';
type Event={event:string;data:string};
type Page={index:number;offset:number;next:number;start:number;rows:string[];bytes:number;more:boolean};
type Props={runId:string;running:boolean;api:<T=any>(path:string,options?:RequestInit)=>Promise<T>};
function lines(items:Event[]){return items.flatMap(ev=>{let data=ev.data;try{data=JSON.stringify(JSON.parse(data));}catch{}return [...(ev.event&&ev.event!=='message'?['event: '+ev.event.replace(/[\r\n]/g,' ')]:[]),...data.split(/\r?\n/).map(line=>'data: '+line),''];});}
export default function StreamViewer({runId,running,api}:Props){
 const [pages,setPages]=useState<Page[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState(''),[wrap,setWrap]=useState(true),[following,setFollowing]=useState(running),[catchup,setCatchup]=useState(false);
 const wasRunning=useRef(running);
 const viewport=useRef<HTMLDivElement>(null),cache=useRef<Page[]>([]),points=useRef<{offset:number;start:number}[]>([{offset:0,start:1}]),locked=useRef(false),generation=useRef(0),follow=useRef(running);
 const pending=useRef<{direction:string;top:number;removed:number;index:number;bottom:boolean}|null>(null);
 async function fetchPage(index:number,direction:string){if(locked.current||!points.current[index])return;locked.current=true;setBusy(true);setError('');const version=generation.current;
  try{const point=points.current[index],result=await api<{items:Event[];offset:number;has_more:boolean}>('/runs/'+runId+'/events?full=true&offset='+point.offset);if(version!==generation.current)return;
   if(!result.items.length){if(!running)setCatchup(false);return;}
   if(!running&&!result.has_more)setCatchup(false);
   const rows=lines(result.items),page:Page={index,offset:point.offset,next:result.offset,start:point.start,rows,bytes:rows.reduce((n,s)=>n+s.length,0),more:result.has_more};
   points.current[index+1]={offset:result.offset,start:point.start+rows.length};
   const node=viewport.current,top=node?.scrollTop||0,bottom=!!node&&node.scrollHeight-node.scrollTop-node.clientHeight<90;
   let next=direction==='previous'?[page,...cache.current]:direction==='reset'?[page]:[...cache.current,page];let removed=0;
   while(next.length>1&&(next.length>4||next.reduce((n,p)=>n+p.bytes,0)>2*1024*1024)){
    if(direction==='previous')next.pop();else{const first=next.shift()!;removed+=node?.querySelector<HTMLElement>('[data-stream-page="'+first.index+'"]')?.offsetHeight||0;}
   }
   pending.current={direction,top,removed,index,bottom};cache.current=next;setPages(next);
  }catch(e){if(version===generation.current)setError((e as Error).message);}finally{if(version===generation.current){locked.current=false;setBusy(false);}}
 }
 function next(){const last=cache.current.at(-1);if(!last){void fetchPage(0,'reset');return;}if(last.index+1<points.current.length-1||last.more||running||catchup)void fetchPage(last.index+1,'next');}
 function previous(){follow.current=false;setFollowing(false);const first=cache.current[0];if(first?.index>0)void fetchPage(first.index-1,'previous');}
 useEffect(()=>{generation.current++;locked.current=false;cache.current=[];points.current=[{offset:0,start:1}];setPages([]);follow.current=running;setFollowing(running);wasRunning.current=running;setCatchup(false);void fetchPage(0,'reset');return()=>{generation.current++;};},[runId]);
 useLayoutEffect(()=>{const change=pending.current,node=viewport.current;if(!change||!node)return;pending.current=null;
  if(change.direction==='reset')node.scrollTop=0;
  else if(change.direction==='previous')node.scrollTop=change.top+(node.querySelector<HTMLElement>('[data-stream-page="'+change.index+'"]')?.offsetHeight||0);
  else if(follow.current&&change.bottom)node.scrollTop=node.scrollHeight;
  else node.scrollTop=Math.max(0,change.top-change.removed);
 },[pages]);
 const last=pages.at(-1),first=pages[0];
 useEffect(()=>{if(wasRunning.current&&!running)setCatchup(true);wasRunning.current=running;},[running]);
 useEffect(()=>{if(!following||(!running&&!last?.more&&!catchup))return;const timer=window.setTimeout(next,600);return()=>clearTimeout(timer);},[runId,running,following,last?.index,busy,catchup]);
 function scroll(){const node=viewport.current;if(!node||locked.current)return;if(node.scrollTop<60&&cache.current[0]?.index>0)previous();else if(node.scrollHeight-node.scrollTop-node.clientHeight<100)next();}
 return <div className="sse-viewer"><div className="sse-toolbar"><strong>SSE 实时响应</strong><label><input type="checkbox" checked={wrap} onChange={e=>setWrap(e.target.checked)}/>自动换行</label><label><input type="checkbox" checked={following} onChange={e=>{follow.current=e.target.checked;setFollowing(e.target.checked);}}/>跟随最新</label><span>UTF-8</span></div>
 <p className="sse-note">按已保存事件展示 data 内容；滚轮到边缘自动加载前后记录。</p>
 {error&&<div className="notice error" role="alert">{error}<button className="text-button" onClick={next}>重试</button></div>}
 <div className={'sse-scroll'+(wrap?' wrapped':'')} ref={viewport} aria-label="SSE响应内容" tabIndex={0} onScroll={scroll} onWheel={e=>{if(e.deltaY<0){follow.current=false;setFollowing(false);if((viewport.current?.scrollTop||0)<60)previous();}else if(e.deltaY>0&&viewport.current&&viewport.current.scrollHeight-viewport.current.scrollTop-viewport.current.clientHeight<100)next();}}>
 {!pages.length&&<p className="muted">{busy?'正在读取…':running?'等待流式响应…':'暂无流式事件'}</p>}
 {pages.map(p=><div key={p.index} data-stream-page={p.index}>{p.rows.map((line,i)=><div className="sse-line" key={i}><span className="sse-number" aria-hidden="true">{p.start+i}</span><code>{line||'\u00a0'}</code></div>)}</div>)}
 </div><div className="sse-footer"><button disabled={busy||!first||first.index===0} onClick={previous}>读取前段</button><span>{busy?'读取中…':first&&last?'第 '+first.start+'–'+(last.start+last.rows.length-1)+' 行'+(!last.more&&!running?' · 已到末尾':''):''}</span><button disabled={busy||!!last&&!last.more&&!running&&!catchup&&last.index+1>=points.current.length-1} onClick={next}>读取后段</button></div></div>;
}

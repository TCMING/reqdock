/** Parse request data only. Never execute cURL or evaluate shell expressions. */
export type CurlRequest={method:string;url:string;headers:Record<string,string>;body:string;bodyType:string;stream:boolean;warnings:string[]};
function tokenize(input:string):string[]{
 if(input.length>1024*1024)throw new Error('cURL 超过 1 MiB，无法导入');
 const source=input.trim();
 const result:string[]=[];let word='',quote='',started=false;
 for(let i=0;i<source.length;i++){
  const c=source[i];
  if(quote==="'"){if(c==="'")quote='';else word+=c;continue;}
  if(['\\','^','`'].includes(c)&&(source[i+1]==='\n'||source[i+1]==='\r'&&source[i+2]==='\n')){i+=source[i+1]==='\r'?2:1;continue;}
  if(c==='\\'&&quote!=="'"){
   const next=source[i+1];if(next===undefined)throw new Error('cURL 转义字符不完整');
   if(!quote||['"','\\','$','`'].includes(next)){word+=next;i++;started=true;continue;}
   word+=c;continue;
  }
  if(c==='$'||c==='`')throw new Error('请把 Shell 变量或命令替换改成实际值；不会执行脚本');
  if(quote==='"'){if(c==='"')quote='';else word+=c;continue;}
  if(c==='"'||c==="'"){quote=c;started=true;continue;}
  if(/[;&|<>]/.test(c))throw new Error('仅支持一条 cURL 请求，请移除 Shell 管道、重定向或组合命令');
  if(/\s/.test(c)){if(started){result.push(word);word='';started=false;}}else{word+=c;started=true;}
 }
 if(quote)throw new Error('cURL 引号未闭合，请复制完整命令');
 if(started)result.push(word);
 if(result.length>4096)throw new Error('cURL 参数过多');
 return result;
}
export function parseCurl(input:string):CurlRequest{
 const tokens=tokenize(input);if(!/^curl(?:\.exe)?$/i.test(tokens.shift()||''))throw new Error('请粘贴以 curl 或 curl.exe 开头的请求');
 let url='',method='',head=false,get=false,stream=false,json=false;
 const headers:Record<string,string>=Object.create(null),data:string[]=[],warnings:string[]=[];
 function header(name:string,value:string){
  if(!/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(name)||/[\r\n\0]/.test(value))throw new Error('cURL 请求头格式不正确');
  if(Object.keys(headers).some(k=>k.toLowerCase()===name.toLowerCase()))throw new Error('当前请求编辑器不能完整保留重复请求头，请合并重复项后导入');
  headers[name]=value;
 }
 function setURL(value:string){if(url)throw new Error('一次只能导入一个请求地址');url=value;}
 for(let i=0;i<tokens.length;i++){
  let flag=tokens[i],inline:string|undefined;
  if(flag.startsWith('--')&&flag.includes('=')){const pos=flag.indexOf('=');inline=flag.slice(pos+1);flag=flag.slice(0,pos);}
  else if(/^-[XHdubAe]/.test(flag)&&flag.length>2){inline=flag.slice(2);flag=flag.slice(0,2);}
  function value(){const v=inline??tokens[++i];if(v===undefined)throw new Error('cURL 参数缺少值');return v;}
  if(flag==='--url')setURL(value());
  else if(flag==='-X'||flag==='--request')method=value().toUpperCase();
  else if(flag==='-H'||flag==='--header'){
   const v=value(),colon=v.indexOf(':');
   if(v.startsWith('@')||colon<1)throw new Error('请使用显式的 名称: 值 请求头，不支持文件引用');
   const name=v.slice(0,colon).trim(),val=v.slice(colon+1).trim();
   if(!val)throw new Error('空值或删除默认头的 cURL 语义暂不支持，请在请求头中手动核对');
   header(name,val);
  }else if(['-d','--data','--data-ascii','--data-raw','--data-binary','--json'].includes(flag)){
   const v=value();if(flag!=='--data-raw'&&v.startsWith('@'))throw new Error('暂不支持 @文件，请先把文件内容转换为正文或使用资源库');
   if(data.length&&json!==(flag==='--json'))throw new Error('暂不支持混用 --json 与其他正文参数，请合并正文');
   data.push(v);if(flag==='--json')json=true;
  }else if(flag==='--data-urlencode'){
   if(json)throw new Error('暂不支持混用 --json 与其他正文参数，请合并正文');
   const v=value();if(v.includes('@')&&!v.includes('='))throw new Error('暂不支持文件引用');
   const pos=v.indexOf('=');const encode=(s:string)=>encodeURIComponent(s).replace(/[!'()*]/g,c=>'%'+c.charCodeAt(0).toString(16).toUpperCase());data.push(pos<0?encode(v):pos===0?encode(v.slice(1)):v.slice(0,pos)+'='+encode(v.slice(pos+1)));
  }else if(flag==='-b'||flag==='--cookie'){const v=value();if(!v.includes('='))throw new Error('不支持读取 Cookie 文件');header('Cookie',v);}
  else if(flag==='-u'||flag==='--user'){
   const v=value();if(!v.includes(':'))throw new Error('Basic 认证请使用 用户名:密码，无法导入交互输入');
   header('Authorization','Basic '+btoa(Array.from(new TextEncoder().encode(v),c=>String.fromCharCode(c)).join('')));
  }else if(flag==='-A'||flag==='--user-agent')header('User-Agent',value());
  else if(flag==='-e'||flag==='--referer')header('Referer',value());
  else if(flag==='-I'||flag==='--head')head=true;
  else if(flag==='-G'||flag==='--get')get=true;
  else if(flag==='-N'||flag==='--no-buffer')stream=true;
  else if(flag==='-L'||flag==='--location')warnings.push('原命令要求跟随重定向；本工具仍使用现有的禁止自动重定向策略');
  else if(flag==='--compressed')warnings.push('压缩协商由本工具HTTP客户端处理，不保证与cURL完全一致');
  else if(/^-sS?$|^-S$|^-v$|^-i$|^--(?:silent|show-error|verbose|include|fail|fail-with-body)$/.test(flag)){}
  else if(flag==='--'){for(const rest of tokens.slice(i+1))setURL(rest);break;}
  else if(flag.startsWith('-'))throw new Error('cURL 包含尚不支持的选项，请移除或手动配置后重试');
  else setURL(flag);
 }
 if(/[\x00-\x20\x7f]/.test(url))throw new Error('URL 包含空白或控制字符，请先进行URL编码');
 let parsed:URL;try{parsed=new URL(url);}catch{throw new Error('cURL 缺少完整的 HTTP/HTTPS 地址');}
 if(!['http:','https:'].includes(parsed.protocol)||parsed.username||parsed.password||parsed.hash)throw new Error('地址仅支持 HTTP/HTTPS，不能包含用户凭证或片段');
 let body=data.join(json?'':'&');
 if((head||method==='HEAD')&&data.length)throw new Error('HEAD 与请求正文的组合暂不支持，请核对命令');
 if(get&&json)throw new Error('暂不支持 --get 与 --json 组合，请手动核对查询和请求头');
 if(get&&data.length){url+=(url.includes('?')?'&':'?')+body;body='';}
 method=method||(head?'HEAD':get?'GET':data.length?'POST':'GET');
 if(!['GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS'].includes(method))throw new Error('暂不支持该 HTTP 方法');
 if(data.length&&!get&&!Object.keys(headers).some(k=>k.toLowerCase()==='content-type'))headers['Content-Type']=json?'application/json':'application/x-www-form-urlencoded';
 if(json&&!Object.keys(headers).some(k=>k.toLowerCase()==='accept'))headers.Accept='application/json';
 let bodyType=data.length&&!get?'text':'none';
 const contentType=Object.entries(headers).find(([k])=>k.toLowerCase()==='content-type')?.[1]||'';
 if(body&&/\bjson\b/i.test(contentType)){try{const value=JSON.parse(body);bodyType='json';stream ||= value?.stream===true;}catch{warnings.push('正文声明为JSON但解析失败，已按原始文本保留');}}
 if(Object.keys(headers).length>64)throw new Error('请求头数量超过限制');
 return {url,method,headers,body,bodyType,stream,warnings:[...new Set(warnings)]};
}

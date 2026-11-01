import {chromium} from '../web/node_modules/playwright/index.mjs';
import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const svg=await readFile(path.join(root,'assets/api-lab.svg'),'utf8');
const browser=await chromium.launch({channel:'msedge',headless:true});
try {
 const chunks=[],sizes=[256,128,64,48,32,24,16];
 for(const size of sizes){
  const page=await browser.newPage({viewport:{width:size,height:size},deviceScaleFactor:1});
  await page.setContent('<style>html,body{margin:0;background:transparent}svg{display:block;width:100vw;height:100vh}</style>'+svg);
  const png=await page.screenshot({omitBackground:true});
  // DIB frames also work with the .NET Framework Icon used by the native client.
  const rgba=await page.evaluate(async encoded=>{const img=new Image();img.src='data:image/png;base64,'+encoded;await img.decode();const canvas=document.createElement('canvas');canvas.width=img.width;canvas.height=img.height;const ctx=canvas.getContext('2d');ctx.drawImage(img,0,0);return Array.from(ctx.getImageData(0,0,img.width,img.height).data);},png.toString('base64'));
  const maskRow=Math.ceil(size/32)*4,bitmap=Buffer.alloc(40+size*size*4+maskRow*size);
  bitmap.writeUInt32LE(40,0);bitmap.writeInt32LE(size,4);bitmap.writeInt32LE(size*2,8);bitmap.writeUInt16LE(1,12);bitmap.writeUInt16LE(32,14);bitmap.writeUInt32LE(size*size*4+maskRow*size,20);
  for(let y=0;y<size;y++)for(let x=0;x<size;x++){
   const source=(y*size+x)*4,dest=40+((size-1-y)*size+x)*4;
   bitmap[dest]=rgba[source+2];bitmap[dest+1]=rgba[source+1];bitmap[dest+2]=rgba[source];bitmap[dest+3]=rgba[source+3];
   if(!rgba[source+3])bitmap[40+size*size*4+(size-1-y)*maskRow+(x>>3)]|=0x80>>(x&7);
  }
  chunks.push(bitmap);
  if(size===256)await writeFile(path.join(root,'assets/api-lab.png'),png);
  await page.close();
 }
 const header=Buffer.alloc(6+16*sizes.length);header.writeUInt16LE(1,2);header.writeUInt16LE(sizes.length,4);
 let offset=header.length;
 sizes.forEach((size,i)=>{const entry=6+i*16;header[entry]=size===256?0:size;header[entry+1]=header[entry];header.writeUInt16LE(1,entry+4);header.writeUInt16LE(32,entry+6);header.writeUInt32LE(chunks[i].length,entry+8);header.writeUInt32LE(offset,entry+12);offset+=chunks[i].length;});
 await writeFile(path.join(root,'assets/api-lab.ico'),Buffer.concat([header,...chunks]));
 console.log('ReqDock icon generated (16–256 px).');
} finally {await browser.close();}

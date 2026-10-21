import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {cpSync,mkdtempSync,mkdirSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname,resolve} from 'node:path';
import {expect,it} from 'vitest';

it('regenerates hosted and standalone media artifacts without accumulating changes',()=>{
 const source=resolve(process.cwd(),'..'),root=mkdtempSync(resolve(tmpdir(),'sterling-export-audit-'));
 const paths=['frontend/public/tv/index.html','sterling-iptv/sterling-iptv.html','sterling-radio/sterling-radio.html'];
 try {
  // Regeneration must not mutate a parallel developer's working files or a
  // document currently being served by the browser regression harness.
  for(const path of [...paths,'frontend/public/radio/index.html','frontend/public/media','frontend/public/tv/appearance.css','frontend/public/radio/appearance.css','frontend/tv-mobile','scripts/embed_tv_mobile.py','scripts/export_sterling_tv.py','scripts/export_sterling_radio.py']){
   mkdirSync(dirname(resolve(root,path)),{recursive:true});cpSync(resolve(source,path),resolve(root,path),{recursive:true});
  }
  const snapshot=()=>paths.map(path=>createHash('sha256').update(readFileSync(resolve(root,path))).digest('hex'));
  const regenerate=()=>{for(const script of ['embed_tv_mobile.py','export_sterling_radio.py'])execFileSync('python3',[resolve(root,'scripts',script)],{cwd:root});};
  const before=snapshot();regenerate();expect(snapshot()).toEqual(before);
  regenerate();expect(snapshot()).toEqual(before);
 } finally {rmSync(root,{recursive:true,force:true});}
});

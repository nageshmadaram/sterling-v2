import ts from 'typescript';
import fs from 'node:fs';
import path from 'node:path';
import {JSDOM,VirtualConsole} from 'jsdom';
const root=path.resolve(import.meta.dirname,'..');
const artifacts=[];
function walk(folder){for(const entry of fs.readdirSync(folder,{withFileTypes:true})){const file=path.join(folder,entry.name);if(entry.isDirectory()&&!['__tests__'].includes(entry.name))walk(file);else if(entry.isFile()&&file.endsWith('.tsx'))inspect(file);}}
function inspect(file){
  const source=ts.createSourceFile(file,fs.readFileSync(file,'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
  const item={file:path.relative(root,file),buttons:0,forms:0,inputs:0,dialogs:[]};
  const visit=node=>{
    if(ts.isJsxOpeningElement(node)||ts.isJsxSelfClosingElement(node)){
      const tag=node.tagName.getText(source),attrs={};
      for(const property of node.attributes.properties)if(ts.isJsxAttribute(property))attrs[property.name.getText(source)]=property.initializer?.getText(source).replace(/^['"]|['"]$/g,'')||true;
      if(tag==='button')item.buttons++;
      if(tag==='form')item.forms++;
      if(['input','select','textarea'].includes(tag))item.inputs++;
      if(tag==='dialog'||['dialog','alertdialog'].includes(attrs.role))item.dialogs.push({line:source.getLineAndCharacterOfPosition(node.pos).line+1,label:attrs['aria-label']||attrs['aria-labelledby']||null,className:attrs.className||null});
    }
    ts.forEachChild(node,visit);
  };visit(source);
  if(item.buttons||item.forms||item.inputs||item.dialogs.length)artifacts.push(item);
}
walk(path.join(root,'src'));
artifacts.sort((a,b)=>a.file.localeCompare(b.file));
const mediaArtifacts=['backend/app/media_templates/tv.html','backend/app/media_templates/radio.html','sterling-iptv/sterling-iptv.html','sterling-radio/sterling-radio.html'].map(file=>{
  const dom=new JSDOM(fs.readFileSync(path.resolve(root,'..',file),'utf8'),{virtualConsole:new VirtualConsole()});
  const doc=dom.window.document;
  const record={file,buttons:doc.querySelectorAll('button').length,inputs:doc.querySelectorAll('input,select,textarea').length,forms:doc.querySelectorAll('form').length,overlays:[...doc.querySelectorAll('dialog,[role=dialog],[role=alertdialog],[id$=\"-modal\"]')].map(el=>({id:el.id,label:el.getAttribute('aria-label')}))};dom.window.close();return record;
});
const report={mediaArtifacts,description:'Static source inventory; runtime coverage is recorded separately. Counts are JSX control sites, not rendered states.',summary:{artifacts:artifacts.length,buttons:artifacts.reduce((n,a)=>n+a.buttons,0),forms:artifacts.reduce((n,a)=>n+a.forms,0),inputs:artifacts.reduce((n,a)=>n+a.inputs,0),dialogs:artifacts.reduce((n,a)=>n+a.dialogs.length,0)},artifacts};
const output=process.argv[2];
if(output){fs.mkdirSync(path.dirname(path.resolve(output)),{recursive:true});fs.writeFileSync(output,JSON.stringify(report,null,2)+'\n');}
console.log(JSON.stringify(report.summary));

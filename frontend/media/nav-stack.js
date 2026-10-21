/* In-app Back for Radio and TV. Each place the user opens (a category, Search, Library, a sheet)
   records how to return to the previous one. Inside Sterling the host owns phone Back, so the page
   reports its depth and the host sends a "back" command; opened on its own, the browser's history
   drives it. Either way one Back press returns one step, to the same view and scroll position. */
(()=>{
 function create({kind,hosted}){
  const stack=[];let restoring=false,skipPop=0;
  const report=()=>{if(hosted())window.parent.postMessage({type:`sterling.${kind}.nav`,depth:stack.length},'*');};
  function push(restore){
   if(restoring||typeof restore!=='function')return null;
   const entry={restore};stack.push(entry);if(stack.length>40)stack.shift();
   if(hosted())report();else{try{history.pushState({sterlingNav:kind,depth:stack.length},'');}catch(_){}}
   return entry;
  }
  function back(){
   const entry=stack.pop();if(!entry){report();return false;}
   restoring=true;try{entry.restore();}catch(error){console.warn('Back could not restore the previous view',error);}finally{restoring=false;}
   report();return true;
  }
  // The view was closed by its own button: forget its entry without running it.
  function drop(entry){
   const index=stack.lastIndexOf(entry);if(index<0)return;stack.splice(index,1);
   if(hosted())report();else{skipPop++;try{history.back();}catch(_){skipPop--;}}
  }
  window.addEventListener('popstate',()=>{if(hosted())return;if(skipPop>0){skipPop--;return;}back();});
  return Object.freeze({push,back,drop,depth:()=>stack.length,isRestoring:()=>restoring,report});
 }
 window.SterlingNav=Object.freeze({create});
})();

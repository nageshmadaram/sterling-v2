import {expect,it} from 'vitest';
import {readMediaCommand,readMediaSuggestionResponse} from '../mediaBridge';

it.each(['tv','radio'] as const)('bounds %s command payloads and strips unrelated fields',kind=>{
 expect(readMediaCommand(kind,{action:'suggest',value:'తెలుగు',requestId:'request-1',token:'private'})).toEqual({action:'suggest',value:'తెలుగు',requestId:'request-1'});
 for(const value of [{action:'suggest',value:'x'.repeat(201)},{action:'suggest',value:'BBC',requestId:{}},{action:'theme',value:'invalid'},{action:'select-suggestion',value:' '},{action:'select-suggestion',value:'x'.repeat(4097)},{action:'search',value:[]},{action:'auth',value:'token'}])expect(readMediaCommand(kind,value)).toBeNull();
 expect(readMediaCommand(kind,{action:kind==='tv'?'minimize':'collapse'})).toBeNull();
 expect(readMediaCommand(kind,{action:'startup',token:'private',value:{malformed:true}})).toEqual({action:'startup'});
});
it('keeps correlated suggestion responses bounded and compatible with legacy display messages',()=>{
 expect(readMediaSuggestionResponse({query:'BBC',requestId:'radio-2',items:[{id:'bbc',label:'BBC'}],token:'private'})).toEqual({query:'BBC',requestId:'radio-2',items:[{id:'bbc',label:'BBC',subtitle:'',artwork:''}]});
 expect(readMediaSuggestionResponse({query:'',items:[]})).toEqual({query:'',items:[]});
 expect(readMediaSuggestionResponse({query:'x'.repeat(201),items:[]})).toBeNull();
 expect(readMediaSuggestionResponse({query:'BBC',requestId:'x'.repeat(65),items:[]})).toBeNull();
});

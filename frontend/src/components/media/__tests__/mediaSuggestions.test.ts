import {expect,it} from 'vitest';
import {readMediaSuggestions} from '../mediaSuggestions';

it('deduplicates channel identities while keeping eight useful, safe suggestions',()=>{
 const rows=[null,{id:'',label:'Empty ID'},{id:'blank',label:' '},
  {id:'bbc',label:'BBC',artwork:'https://user:secret@example.com/logo'},
  {id:'bbc',label:'Duplicate'},...Array.from({length:12},(_,i)=>({id:String(i),label:`Channel ${i}`}))];
 const result=readMediaSuggestions(rows);
 expect(result).toHaveLength(8);
 expect(new Set(result.map(row=>row.id)).size).toBe(8);
 expect(result[0]).toMatchObject({id:'bbc',label:'BBC',artwork:''});
});

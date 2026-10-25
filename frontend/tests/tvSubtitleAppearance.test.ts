import {afterEach,expect,it,vi} from 'vitest';
import html from '../public/tv/index.html?raw';
afterEach(()=>{document.body.innerHTML='';document.documentElement.removeAttribute('style');});
it('applies saved subtitle appearance and persists user edits from the hosted player',()=>{
 const start=html.indexOf("let subStyleConfig = readTvStoredJson('streamos_sub_style_v14'");
 expect(start).toBeGreaterThan(-1);
 const end=html.indexOf('    function toggleSubtitleSettingsPopover',start);
 const code=html.slice(start,end);
 const save=vi.fn(),captions=vi.fn(),toast=vi.fn();
 const api=new Function('readTvStoredJson','tvStorage','toggleClosedCaptions','showToast','isCcEnabled',code+';return {setSubtitleStyle};')(()=>({size:125,color:'#ffcc00',bg:0.7}),{setItem:save},captions,toast,false);
 expect(document.documentElement.style.getPropertyValue('--sub-font-size')).toBe('125%');
 expect(document.documentElement.style.getPropertyValue('--sub-text-color')).toBe('#ffcc00');
 api.setSubtitleStyle({size:150});
 expect(document.documentElement.style.getPropertyValue('--sub-font-size')).toBe('150%');
 expect(save).toHaveBeenCalledWith('streamos_sub_style_v14',JSON.stringify({size:150,color:'#ffcc00',bg:0.7}));
 expect(captions).toHaveBeenCalledOnce();
});

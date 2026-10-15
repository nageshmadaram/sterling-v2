import {it,expect,afterEach} from 'vitest';
import {renderHook,act,cleanup} from '@testing-library/react';
import {useSignalColumns} from '../useSignalColumns';
import {useKiteSettings} from '../../../../store/useKiteSettings';
const initial=useKiteSettings.getState();
afterEach(()=>{cleanup();useKiteSettings.setState(initial);});
it('omits the removed direction column',()=>{
 const {result}=renderHook(()=>useSignalColumns());
 expect(result.current.columns).not.toContain('dir');
});
it('can show a column hidden by both legacy settings and the picker',()=>{
 useKiteSettings.setState({showPriceChange:false,hiddenSignalCols:['chg']});
 const {result}=renderHook(()=>useSignalColumns());
 expect(result.current.hidden.has('chg')).toBe(true);
 act(()=>result.current.toggleColumn('chg'));
 expect(result.current.hidden.has('chg')).toBe(false);
 act(()=>result.current.toggleColumn('chg'));
 expect(result.current.hidden.has('chg')).toBe(true);
});

import React from 'react';
import {it,expect,afterEach} from 'vitest';
import {render,screen,fireEvent,cleanup} from '@testing-library/react';
import {ThemeToggle} from '../ThemeToggle';
import {useAppearance,DEFAULT_APPEARANCE} from '../../../appearance/store';
import {useStore} from '../../../store/useStore';
afterEach(()=>{cleanup();useAppearance.getState().replace(DEFAULT_APPEARANCE);});
it('opens theme options and applies a saved favourite',()=>{
 useAppearance.getState().update({favorites:['aubergine']});
 render(<ThemeToggle/>);
 fireEvent.click(screen.getByRole('button',{name:'Choose theme'}));
 expect(screen.getByRole('menuitemradio',{name:/Dark/})).toBeTruthy();
 expect(screen.getByRole('menuitemradio',{name:/Light/})).toBeTruthy();
 fireEvent.click(screen.getByRole('menuitemradio',{name:/Aubergine/}));
 expect(useAppearance.getState().settings.preset).toBe('aubergine');
 expect(useStore.getState().theme).toBe('dark');
 expect(screen.queryByRole('menu')).toBeNull();
});

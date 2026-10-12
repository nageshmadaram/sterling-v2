import React from 'react';
import {describe,it,expect,beforeEach,afterEach,vi} from 'vitest';
import {render,screen,fireEvent,cleanup,waitFor} from '@testing-library/react';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
const get=vi.fn(),post=vi.fn(),patch=vi.fn(),put=vi.fn();
vi.mock('../../../utils/api',()=>({api:{get:(...a:unknown[])=>get(...a),post:(...a:unknown[])=>post(...a),patch:(...a:unknown[])=>patch(...a),put:(...a:unknown[])=>put(...a),delete:vi.fn()}}));
import {NavigationSettings} from '../NavigationSettings';
import {useExperienceOptions,readExperienceOptions,orderedNavigation} from '../../../store/useExperienceOptions';
import {ProfileMenu} from '../ProfileMenu';
import {FeedTestSettings} from '../FeedTestSettings';
import {PushSettings} from '../PushSettings';
const wrap=(child:React.ReactNode)=>render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}>{child}</QueryClientProvider>);
beforeEach(()=>{localStorage.clear();useExperienceOptions.setState({options:readExperienceOptions()});get.mockReset();post.mockReset();patch.mockReset();put.mockReset();});afterEach(cleanup);
describe('Experience controls',()=>{
 it('hides and reorders navigation while keeping settings accessible',()=>{render(<NavigationSettings/>);fireEvent.click(screen.getByRole('checkbox',{name:'Astrology'}));expect(useExperienceOptions.getState().options.navHidden).toContain('astro');expect(screen.getByRole('checkbox',{name:'Connect'})).toBeDisabled();fireEvent.click(screen.getByRole('button',{name:'Move pcr up'}));expect(useExperienceOptions.getState().options.navOrder[1]).toBe('pcr');expect(orderedNavigation([{id:'astro'},{id:'pcr'}],useExperienceOptions.getState().options)).toEqual([{id:'pcr'}]);});
 it('opens the Sterling profile, not the broker identity, and does not mutate on render',async()=>{get.mockResolvedValue({username:'sterling-user',display_name:'Operator',photo:'',role:'user',user_id:'user'});wrap(<ProfileMenu/>);await screen.findByText('Operator');fireEvent.click(screen.getByRole('button',{name:'Sterling account profile'}));expect(await screen.findByRole('dialog',{name:'Sterling profile'})).toBeInTheDocument();expect(post).not.toHaveBeenCalled();expect(patch).not.toHaveBeenCalled();});
 it('tests only the exact selected provider symbol',async()=>{get.mockResolvedValue({confirmed:null});post.mockResolvedValue({id:'probe',provider:'kite',symbol:'NSE:NIFTY 50',price:100,quote_time:null,observed_at:'now',age_seconds:null,latency_ms:30,market_phase:'closed',freshness:'provider timestamp unavailable',realtime_verified:false,note:'read-only'});wrap(<FeedTestSettings/>);fireEvent.change(screen.getByRole('textbox',{name:'Feed test symbol'}),{target:{value:'NSE:NIFTY 50'}});fireEvent.click(screen.getByRole('button',{name:'Fetch test quote'}));await waitFor(()=>expect(post).toHaveBeenCalledWith('/api/v1/auth/feed-test/probe',{provider:'kite',symbol:'NSE:NIFTY 50'}));expect(await screen.findByText('No — see timestamp and provider limits')).toBeInTheDocument();});
 it('does not request push permission or send a test just by opening settings',async()=>{get.mockResolvedValue({public_key:'key',config:{enabled:false,signals:true,pcr:true,alerts:true,quiet_enabled:false,quiet_start:'22:00',quiet_end:'08:00',private_preview:true,max_per_hour:12},devices:[]});wrap(<PushSettings/>);expect(await screen.findByText('No registered devices.')).toBeInTheDocument();expect(post).not.toHaveBeenCalled();expect(put).not.toHaveBeenCalled();});
});

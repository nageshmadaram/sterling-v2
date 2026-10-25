import React from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
vi.mock('../KiteLayout', () => ({ KiteLayout: ({content,activeNav,onNavClick}:any) => <><div data-testid="active-page">{activeNav}</div><button onClick={()=>onNavClick('orders')}>Go to orders</button>{content}</> }));
vi.mock('../KiteDashboard', () => ({ KiteDashboard: () => <div>Dashboard content</div> }));
vi.mock('../PositionsPane', () => ({ PositionsPane: () => <div>Positions content</div> }));
vi.mock('../PcrPane', () => ({ PcrPane: () => <div>PCR content</div> }));
vi.mock('../OrdersPane', () => ({ OrdersPane: () => <div>Orders content</div> }));
vi.mock('../SterlingWatchListWithHoldingsSync', () => ({ SterlingWatchListWithHoldingsSync: () => null }));
vi.mock('../KiteNotifications', () => ({ KiteNotifications: () => null }));
vi.mock('../PendingGttProtectionWatcher', () => ({ PendingGttProtectionWatcher: () => null }));
vi.mock('../KiteSessionGuard', () => ({ KiteSessionGuard: () => null }));
vi.mock('../KiteLoader', () => ({ KiteAuthOverlay: () => null }));
vi.mock('../EngineTerminal', () => ({ EngineTerminal: () => null }));
vi.mock('../KiteTicker', () => ({ KiteTicker: () => null }));
vi.mock('../../../hooks/useKite', () => ({ useKiteAutoSession: () => {}, useKiteStatus: () => ({data:{},isLoading:false}) }));
vi.mock('../../../hooks/useMobileBackLayer', () => ({ useMobileBackLayer: () => {} }));
vi.mock('../../../store/useKiteSettings', () => { const state={defaultSection:'dashboard'};return {useKiteSettings:Object.assign((select:any)=>select(state),{getState:()=>state})}; });
vi.mock('../KiteStartupSurfaces', () => ({ KiteStartupCoordinator: () => null, WatchlistStartupBoundary: () => null, EngineStartupBoundary: () => null, TickerStartupBoundary: () => null }));
vi.mock('../KiteInteractionMotion', () => ({ KiteInteractionMotion: ({children}:any) => <>{children}</> }));
vi.mock('../AdaptiveEdgeRightSidebar', () => ({ AdaptiveEdgeRightSidebar: () => null }));
import { KiteTab } from '../KiteTab';
beforeEach(()=>{sessionStorage.clear();window.history.replaceState({},'','/');});
afterEach(()=>{cleanup();vi.restoreAllMocks();window.history.replaceState({},'','/');});
it.each([
 ['alerts','positions','Positions content',null],
 ['pcr','pcr','PCR content',null],
 ['signals','dashboard','Dashboard content',null],
 ['alerts','positions','Positions content','dashboard'],
 ['pcr','pcr','PCR content','orders'],
 ['signals','dashboard','Dashboard content','positions'],
])('opens %s in the actual %s pane over saved %s',async(category,page,content,saved)=>{
 if(saved)sessionStorage.setItem('sterling.page.workspace.nav',JSON.stringify(saved));
 window.history.replaceState({},'',`/?notification=${category}&view=live`);
 render(<KiteTab/>);
 expect(await screen.findByText(content)).toBeVisible();
 expect(screen.getByTestId('active-page')).toHaveTextContent(page);
});
it.each(['','?notification=unknown&view=live','?notification=alerts&view=simulation'])('preserves saved navigation for unrelated link %s',async(query)=>{
 sessionStorage.setItem('sterling.page.workspace.nav',JSON.stringify('orders'));
 window.history.replaceState({},'','/'+query);render(<KiteTab/>);
 expect(await screen.findByText('Orders content')).toBeVisible();
});
it('allows ordinary navigation after opening a notification',async()=>{
 window.history.replaceState({},'','/?notification=alerts&view=live');render(<KiteTab/>);
 expect(await screen.findByText('Positions content')).toBeVisible();
 fireEvent.click(screen.getByRole('button',{name:'Go to orders'}));
 expect(await screen.findByText('Orders content')).toBeVisible();
 expect(JSON.parse(sessionStorage.getItem('sterling.page.workspace.nav')!)).toBe('orders');
});

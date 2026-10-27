vi.mock('../board/TodaySignalPriority',()=>({TodaySignalPriorityProvider:({children}:any)=>children,TodaySignalPriorityStatus:()=>null}));
import {recordDiagnostic} from '../../../lib/diagnostics';
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import '@testing-library/jest-dom/vitest';
import { EngineTerminal, TERMINAL_MODE_KEY } from '../EngineTerminal';
import { KiteLayout } from '../KiteLayout';
import { WORKSPACE_LAYOUT_KEY } from '../workspaceLayout';

// KiteLayout reads useQueryClient() directly (to invalidate the signals query
// on scan-state edges), so it needs a real QueryClientProvider ancestor even
// though useEngineActivity itself is mocked below.
function renderLayout(componentProps: typeof props) {
  const qc = new QueryClient();
  return render(<QueryClientProvider client={qc}><KiteLayout {...componentProps} /></QueryClientProvider>);
}

let mockTerminalActivity: any;
vi.mock('../../../hooks/useSterlingKiteEngine', () => ({
  useEngineActivity: () => ({ data: mockTerminalActivity }),
  useEngineServerLogs: () => ({ data: undefined }),
}));
vi.mock('../../../store/useLiveSignalCount', () => ({
  useLiveSignalCount: (selector: (state: { count: number }) => unknown) => selector({ count: 27 }),
}));
// The footer now carries broker and per-strategy state, which reaches for every
// engine's hook. None of that is under test here, and this file is about the
// workspace, so the strip is stubbed rather than each of its six hooks mocked.
vi.mock('../KiteFooterStatus', () => ({ KiteFooterStatus: () => <div data-testid="footer-status" /> }));

const props = {
  activeNav: 'dashboard' as const,
  onNavClick: vi.fn(),
  sidebar: <div>watchlist body</div>,
  rightSidebar: <div>signals body</div>,
  bottomBar: <div>terminal body</div>,
  centerTopBar: <div>ticker strip</div>,
  content: <div>dashboard body</div>,
};

beforeEach(() => {
  mockTerminalActivity = undefined;
  localStorage.clear();sessionStorage.clear();
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    callback(0);
    return 1;
  });
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('KiteLayout advanced workspace', () => {
  it('gives all four panes independent window controls', () => {
    renderLayout(props);

    for (const title of ['Watchlist', 'Dashboard', 'Signals', 'Terminal']) {
      expect(screen.getByLabelText(`${title} pane`)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: `Minimize ${title}` })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: `Half screen ${title}` })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: `Maximize ${title}` })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: `Full screen ${title}` })).toBeInTheDocument();
    }
    expect(screen.queryByTitle('Show / hide left sidebar')).not.toBeInTheDocument();
    expect(screen.queryByText('All panes active')).not.toBeInTheDocument();
  });

  it('prevents outer scrolling on dashboard pane so bottom docks remain fixed', () => {
    renderLayout(props);
    const dashboardPane = screen.getByLabelText('Dashboard pane');
    const contentWrapper = dashboardPane.querySelector('div[style*="overflow: hidden"]');
    expect(contentWrapper).toBeInTheDocument();
  });

  it('minimizes into the dock and restores without changing the pane slot', () => {
    renderLayout(props);
    fireEvent.click(screen.getByRole('button', { name: 'Minimize Watchlist' }));

    expect(screen.queryByLabelText('Watchlist pane')).not.toBeInTheDocument();
    const restore = screen.getByTitle('Restore Watchlist');
    expect(restore).toBeInTheDocument();
    fireEvent.click(restore);

    expect(screen.getByLabelText('Watchlist pane')).toBeInTheDocument();
    const saved = JSON.parse(localStorage.getItem(WORKSPACE_LAYOUT_KEY)!);
    expect(saved.slots.left).toBe('watchlist');
    expect(saved.minimized).toEqual([]);
  });

  it('restores every minimized pane in one click while keeping individual restore controls', () => {
    renderLayout(props);

    for (const title of ['Watchlist', 'Dashboard', 'Signals', 'Terminal']) {
      fireEvent.click(screen.getByRole('button', { name: `Minimize ${title}` }));
    }

    expect(screen.getByRole('button', { name: 'Restore all panes' })).toBeInTheDocument();
    for (const title of ['Watchlist', 'Dashboard', 'Signals', 'Terminal']) {
      expect(screen.getByTitle(`Restore ${title}`)).toBeInTheDocument();
    }

    fireEvent.click(screen.getByRole('button', { name: 'Restore all panes' }));

    expect(screen.getAllByLabelText(/pane$/)).toHaveLength(4);
    expect(screen.queryByText('All panes active')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Restore all panes' })).not.toBeInTheDocument();
    expect(localStorage.getItem(TERMINAL_MODE_KEY)).toBe('normal');
    expect(JSON.parse(localStorage.getItem(WORKSPACE_LAYOUT_KEY)!).minimized).toEqual([]);
  });

  it('supports half, maximize, fullscreen, restore, and Escape focus flows', () => {
    renderLayout(props);

    fireEvent.click(screen.getByRole('button', { name: 'Half screen Dashboard' }));
    expect(screen.getByRole('button', { name: 'Restore Dashboard' })).toBeInTheDocument();
    expect(screen.getAllByLabelText(/pane$/)).toHaveLength(4);

    fireEvent.click(screen.getByRole('button', { name: 'Maximize Dashboard' }));
    expect(screen.getAllByLabelText(/pane$/)).toHaveLength(1);
    expect(screen.getByLabelText('Dashboard pane')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Full screen Dashboard' }));
    expect(screen.getByLabelText('Dashboard pane').parentElement).toHaveStyle({ position: 'fixed' });

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.getAllByLabelText(/pane$/)).toHaveLength(4);
  });

  it('repositions by swapping panes through visible dock targets', () => {
    renderLayout(props);
    const pane = screen.getByLabelText('Signals pane');
    const handle = within(pane).getByTitle(/Drag to reposition/);
    const values: Record<string, string> = {};
    const dataTransfer = {
      effectAllowed: 'none',
      dropEffect: 'none',
      setData: (type: string, value: string) => { values[type] = value; },
      getData: (type: string) => values[type] ?? '',
    };

    fireEvent.dragStart(handle, { dataTransfer });
    expect(screen.getByLabelText('Pane drop targets')).toBeInTheDocument();
    fireEvent.drop(screen.getByRole('button', { name: 'Left dock' }), { dataTransfer });

    const saved = JSON.parse(localStorage.getItem(WORKSPACE_LAYOUT_KEY)!);
    expect(saved.slots.left).toBe('signals');
    expect(saved.slots.right).toBe('watchlist');
    expect(screen.queryByLabelText('Pane drop targets')).not.toBeInTheDocument();
  });

  it('consolidates presets, reset, restore-all, and locking in one menu', () => {
    renderLayout(props);
    fireEvent.click(screen.getByRole('button', { name: 'Minimize Terminal' }));
    fireEvent.click(screen.getByRole('button', { name: 'Layout' }));

    expect(screen.getByRole('dialog', { name: 'Workspace layout menu' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Classic/ }));
    let saved = JSON.parse(localStorage.getItem(WORKSPACE_LAYOUT_KEY)!);
    expect(saved.sizes).toEqual({ left: 420, right: 1290, bottom: 220 });
    expect(saved.minimized).toEqual([]);
    expect(localStorage.getItem(TERMINAL_MODE_KEY)).toBe('normal');
    expect(screen.getByLabelText('Terminal pane')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Layout' }));
    fireEvent.click(screen.getByRole('button', { name: /Chart focus/ }));
    saved = JSON.parse(localStorage.getItem(WORKSPACE_LAYOUT_KEY)!);
    expect(saved.sizes).toEqual({ left: 280, right: 720, bottom: 220 });
    expect(saved.slots).toEqual({ left: 'terminal', center: 'dashboard', right: 'signals', bottom: 'watchlist' });
    expect(saved.minimized).toEqual(['terminal']);
    expect(localStorage.getItem(TERMINAL_MODE_KEY)).toBe('minimized');
    expect(screen.getByLabelText('Dashboard pane').parentElement).toHaveAttribute('data-workspace-slot', 'center');
    expect(screen.getByLabelText('Watchlist pane').parentElement).toHaveAttribute('data-workspace-slot', 'bottom');
    expect(screen.getByLabelText('Signals pane').parentElement).toHaveAttribute('data-workspace-slot', 'right');
    expect(screen.queryByLabelText('Terminal pane')).not.toBeInTheDocument();
    expect(screen.getByTitle('Restore Terminal')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Layout' }));
    fireEvent.click(screen.getByRole('button', { name: 'Lock pane movement' }));
    saved = JSON.parse(localStorage.getItem(WORKSPACE_LAYOUT_KEY)!);
    expect(saved.locked).toBe(true);
    expect(within(screen.getByLabelText('Watchlist pane')).getByTitle('Layout locked')).toBeInTheDocument();
  });

  it('places Layout immediately before the live engine status group', () => {
    renderLayout(props);

    const status = screen.getByLabelText('Workspace and engine status');
    const layoutButton = within(status).getByRole('button', { name: 'Layout' });
    const live = within(status).getByText('27 setups');

    expect(status.firstElementChild).toContainElement(layoutButton);
    expect(layoutButton.compareDocumentPosition(live) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('keeps the legacy terminal event contract for chart-open auto minimization', () => {
    renderLayout(props);

    act(() => window.dispatchEvent(new CustomEvent('kite-terminal-mode', { detail: 'minimized' })));
    expect(screen.queryByLabelText('Terminal pane')).not.toBeInTheDocument();
    expect(screen.getByTitle('Restore Terminal')).toBeInTheDocument();

    act(() => window.dispatchEvent(new CustomEvent('kite-terminal-mode', { detail: 'full' })));
    expect(screen.getByLabelText('Terminal pane').parentElement).toHaveStyle({ position: 'fixed' });

    act(() => window.dispatchEvent(new CustomEvent('kite-terminal-mode', { detail: 'normal' })));
    expect(screen.getAllByLabelText(/pane$/)).toHaveLength(4);
  });

});

describe('EngineTerminal workspace synchronization', () => {
  it('mounts only the latest 120 log lines, with a recoverable full history', () => {
    mockTerminalActivity = {
      scanning: false,
      events: Array.from({ length: 300 }, (_, i) => ({
        ts_ms: i + 1,
        kind: 'info',
        message: `perf-entry-${i}`,
      })),
    };
    render(<EngineTerminal />);
    expect(screen.getByText('perf-entry-299')).toBeInTheDocument();
    expect(screen.queryByText('perf-entry-0')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Show older logs/i }));
    expect(screen.getByText('perf-entry-100')).toBeInTheDocument();
    expect(screen.queryByText('perf-entry-0')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Show older logs/i }));
    expect(screen.getByText('perf-entry-0')).toBeInTheDocument();
  });

  it('offers separate log shortcuts and preserves engine-only view', () => {
    recordDiagnostic('INFO', 'Log shortcut fixture');
    render(<EngineTerminal />);
    fireEvent.click(screen.getByRole('button', { name: 'Terminal log views' }));
    expect(screen.getByRole('button', { name: 'Backend logs' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Frontend logs' }));
    expect(screen.getByText('Frontend: Log shortcut fixture')).toBeInTheDocument();
    expect(localStorage.getItem('sterling.selection.terminal.source')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Engine activity' }));
    expect(screen.queryByText('Frontend: Log shortcut fixture')).not.toBeInTheDocument();
  });
  it('opens diagnostics separately and filters frontend severity', () => {
    recordDiagnostic('DEBUG','Diagnostic debug fixture');
    recordDiagnostic('ERROR','Diagnostic error fixture');
    render(<EngineTerminal />);
    fireEvent.click(screen.getByRole('button',{name:'Toggle diagnostics'}));
    expect(screen.getByText('Diagnostics')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Log source'),{target:{value:'frontend'}});
    fireEvent.change(screen.getByLabelText('Log severity'),{target:{value:'ERROR'}});
    expect(screen.getByText('Frontend: Diagnostic error fixture')).toBeInTheDocument();
    expect(screen.queryByText('Frontend: Diagnostic debug fixture')).toBeNull();
    fireEvent.click(screen.getByRole('button',{name:'Toggle diagnostics'}));
    expect(screen.queryByText('Diagnostics')).toBeNull();
  });
  it('re-reads normal mode after a minimized terminal is restored while unmounted', () => {
    const first = render(<EngineTerminal />);
    fireEvent.click(screen.getByTitle('Minimize terminal'));
    first.unmount();

    localStorage.setItem(TERMINAL_MODE_KEY, 'normal');
    render(<EngineTerminal />);

    expect(screen.getByText(/Waiting for background scan/)).toBeInTheDocument();
    expect(screen.getByTitle('Minimize terminal')).toBeInTheDocument();
  });
});

describe('mobile workspace', () => {
  it('restores the selected mobile pane on mount',()=>{
    vi.stubGlobal('innerWidth',390);
    sessionStorage.setItem('sterling.page.workspace.mobilePane','"signals"');
    renderLayout(props);
    expect(screen.getByRole('tab',{name:/^Signals$/})).toHaveAttribute('aria-selected','true');
    expect(screen.getByRole('button',{name:/^Open replay$/})).toBeInTheDocument();
  });

  it('opens search in a dismissible overlay without switching pages', () => {
    renderLayout(props);
    fireEvent(window,new CustomEvent('kite-open-watchlist-search'));
    expect(screen.getByRole('dialog',{name:'Search instruments'})).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'Close search'}));
    expect(screen.queryByRole('dialog',{name:'Search instruments'})).toBeNull();
  });
  it('expands Terminal whenever its mobile tab is selected',()=>{
    vi.stubGlobal('innerWidth',390);
    renderLayout(props);
    const listener=vi.fn();window.addEventListener('kite-terminal-mode',listener);
    fireEvent.click(screen.getByRole('tab',{name:/^Terminal$/}));
    expect(listener.mock.calls[0][0].detail).toBe('normal');
    window.removeEventListener('kite-terminal-mode',listener);
  });
  it('Home preserves the current page instead of resetting navigation', async () => {
    const {useKiteSettings}=await import('../../../store/useKiteSettings');
    vi.stubGlobal('innerWidth',390);
    useKiteSettings.setState({defaultSection:'pcr'});
    renderLayout(props);
    fireEvent.click(screen.getByRole('tab',{name:/^Home$/}));
    expect(props.onNavClick).not.toHaveBeenCalled();
    useKiteSettings.setState({defaultSection:'dashboard'});
  });
  it('reveals chart and detail content requested from another pane', () => {
    vi.stubGlobal('innerWidth', 390);
    renderLayout(props);
    fireEvent.click(screen.getByRole('tab', { name: /^Signals$/ }));
    act(() => window.dispatchEvent(new Event('kite-focus-main-pane')));
    expect(screen.getByText('dashboard body')).toBeVisible();
    expect(screen.getByRole('tab', { name: /^Home$/ })).toHaveAttribute('aria-selected', 'true');
  });
  it('uses one full-width pane at a time and keeps drafts mounted', () => {
    vi.stubGlobal('innerWidth', 390);
    renderLayout(props);
    const tabs = screen.getByRole('tablist', { name: 'Workspace panes' });
    expect(within(tabs).getByRole('tab', { name: 'Home' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.queryByRole('separator', { name: 'Resize right dock' })).not.toBeInTheDocument();
    fireEvent.click(within(tabs).getByRole('tab', { name: 'Signals' }));
    expect(screen.getByRole('tabpanel', { name: 'Signals' })).toBeVisible();
    expect(screen.getByText('dashboard body')).toBeInTheDocument();
    expect(screen.getByText('dashboard body')).not.toBeVisible();
    act(() => { window.dispatchEvent(new CustomEvent('kite-nav-click', {detail:'dashboard'})); });
    expect(within(tabs).getByRole('tab', {name:'Home'})).toHaveAttribute('aria-selected','true');
    fireEvent.click(within(tabs).getByRole('tab', { name: 'Watchlist' }));
    expect(screen.getByRole('tabpanel', { name: 'Watchlist' })).toBeVisible();
  });
  it('returns navigation to the page without replacing desktop pane preferences', () => {
    vi.stubGlobal('innerWidth', 390);
    const qc = new QueryClient();
    const { rerender } = render(<QueryClientProvider client={qc}><KiteLayout {...props}/></QueryClientProvider>);
    fireEvent.click(screen.getByRole('tab', { name: /^Signals$/ }));
    rerender(<QueryClientProvider client={qc}><KiteLayout {...props} activeNav="connect"/></QueryClientProvider>);
    expect(screen.getByRole('tab', {name:'Home'})).toHaveAttribute('aria-selected','true');
    expect(JSON.parse(localStorage.getItem(WORKSPACE_LAYOUT_KEY)!).minimized).toEqual([]);
  });
});

it('keeps phones in one pane without overwriting stored desktop preferences', async () => {
  const { useMobileExperience } = await import('../../../store/useMobileExperience');
  vi.stubGlobal('innerWidth', 390);
  useMobileExperience.setState({style:'full'});
  renderLayout(props);
  expect(screen.getByRole('tablist',{name:'Workspace panes'})).toBeTruthy();
  expect(useMobileExperience.getState().style).toBe('full');
  act(()=>useMobileExperience.setState({style:'app'}));
  expect(screen.getByRole('tablist',{name:'Workspace panes'})).toBeTruthy();
});


it('returns to Signals after closing a chart opened from search',()=>{
 vi.stubGlobal('innerWidth',390);
 sessionStorage.setItem('sterling.page.workspace.mobilePane','"signals"');
 const qc=new QueryClient();
 const view=render(<QueryClientProvider client={qc}><KiteLayout {...props}/></QueryClientProvider>);
 fireEvent(window,new Event('kite-open-watchlist-search'));
 view.rerender(<QueryClientProvider client={qc}><KiteLayout {...props} mainViewTitle="NIFTY chart"/></QueryClientProvider>);
 fireEvent(window,new Event('kite-focus-main-pane'));
 expect(screen.queryByRole('dialog',{name:'Search instruments'})).toBeNull();
 expect(screen.getByRole('tab',{name:'Home'})).toHaveAttribute('aria-selected','true');
 view.rerender(<QueryClientProvider client={qc}><KiteLayout {...props}/></QueryClientProvider>);
 expect(screen.getByRole('tab',{name:'Signals'})).toHaveAttribute('aria-selected','true');
});
it('returns to Watchlist when an order overlay closes',()=>{
 vi.stubGlobal('innerWidth',390);renderLayout(props);
 fireEvent.click(screen.getByRole('tab',{name:'Watchlist'}));
 fireEvent(window,new Event('sterling-order-window-open'));
 fireEvent.click(screen.getByRole('tab',{name:'Home'}));
 fireEvent(window,new Event('sterling-order-window-close'));
 expect(screen.getByRole('tab',{name:'Watchlist'})).toHaveAttribute('aria-selected','true');
});


it('reveals the main page when another desktop pane is maximized', () => {
  renderLayout(props);
  fireEvent.click(screen.getByRole('button', { name: 'Minimize Dashboard' }));
  fireEvent.click(screen.getByRole('button', { name: 'Maximize Signals' }));
  act(() => window.dispatchEvent(new Event('kite-focus-main-pane')));
  expect(screen.getByText('dashboard body')).toBeVisible();
  expect(screen.queryByTitle('Restore Dashboard')).toBeNull();
});

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useExperienceOptions } from '../../../store/useExperienceOptions';

import { KiteActivityRail } from '../KiteActivityRail';

/**
 * MorePane hosts Operations, Bids, Funds, Mutual Funds and Alerts. Before this
 * entry existed it could only be reached by changing the default section in
 * settings or by dispatching a kite-nav-click event — an operator could not
 * click to the Operations screen at all.
 */
describe('KiteActivityRail — More', () => {
  const originalOptions = useExperienceOptions.getState().options;
  afterEach(() => useExperienceOptions.setState({ options: originalOptions }));
  const props = {
    activeNav: 'dashboard' as const,
    onNavClick: vi.fn(),
    onOpenCommandPalette: vi.fn(),
    onOpenCustomizeLayout: vi.fn(),
  };

  it('renders a More button', () => {
    render(<KiteActivityRail {...props} />);

    expect(screen.getByRole('button', { name: 'More' })).toBeInTheDocument();
  });

  it('gives every rail button a stable test id', () => {
    // Several unrelated controls in this workspace are also called "More", so
    // an end-to-end test addressing it by accessible name is ambiguous.
    render(<KiteActivityRail {...props} />);

    expect(screen.getByTestId('rail-more')).toBeInTheDocument();
    expect(screen.getByTestId('rail-dashboard')).toBeInTheDocument();
  });

  it('navigates to the more section when clicked', () => {
    const onNavClick = vi.fn();
    render(<KiteActivityRail {...props} onNavClick={onNavClick} />);

    fireEvent.click(screen.getByRole('button', { name: 'More' }));

    expect(onNavClick).toHaveBeenCalledWith('more');
  });

  it('marks More active when it is the current section', () => {
    render(<KiteActivityRail {...props} activeNav="more" />);

    const button = screen.getByRole('button', { name: 'More' });

    expect(button).toHaveStyle({ color: 'var(--k-brand)' });
  });

  it('honours the same hidden sections and ordering as the main navigation', () => {
    useExperienceOptions.setState({ options: { ...originalOptions, navHidden: ['astro'], navOrder: ['orders', 'dashboard', ...originalOptions.navOrder.filter(id => !['orders', 'dashboard'].includes(id))] } });
    render(<KiteActivityRail {...props} />);
    expect(screen.queryByTestId('rail-astro')).not.toBeInTheDocument();
    const buttons = screen.getAllByRole('button').filter(button => button.dataset.testid?.startsWith('rail-'));
    expect(buttons.slice(0, 2).map(button => button.dataset.testid)).toEqual(['rail-orders', 'rail-dashboard']);
  });
});

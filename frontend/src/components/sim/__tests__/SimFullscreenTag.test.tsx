import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { SimFullscreenTag } from '../SimFullscreenTag';
import { useViewStore } from '../../../store/useViewStore';

afterEach(() => { cleanup(); useViewStore.setState({ view: 'live' }); });

describe('SimFullscreenTag', () => {
  it('says the money is virtual when a full-screen pane covers the banner', () => {
    useViewStore.setState({ view: 'simulation' });
    render(<SimFullscreenTag />);
    const tag = screen.getByTestId('sim-fullscreen-tag');
    expect(tag).toHaveTextContent('SIMULATION — virtual money, no real orders');
    // It must never sit in the way of the pane's own buttons.
    expect(tag).toHaveStyle({ pointerEvents: 'none' });
  });

  it('shows nothing in the live view', () => {
    const { container } = render(<SimFullscreenTag />);
    expect(container).toBeEmptyDOMElement();
  });
});

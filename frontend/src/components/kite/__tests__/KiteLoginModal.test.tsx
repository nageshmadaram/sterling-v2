/**
 * The login handshake, shown in the app.
 *
 * The login itself must happen on Zerodha's page — their 2FA is interactive by
 * design and cannot be embedded (their headers forbid framing, and so do ours).
 * So this component is not the login; it is the app saying what is happening
 * while a popup does it, instead of leaving the operator on an unchanged screen
 * wondering whether the click registered.
 *
 * Mobile users can leave the waiting overlay to recover a failed localhost
 * callback from the account pane.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { KiteLoginModal } from '../KiteLoginModal';

const props = {
  phase: 'waiting' as const,
  error: null,
  onRetry: vi.fn(),
  onDismiss: vi.fn(),
};

describe('KiteLoginModal', () => {
  it('renders nothing when no login is in flight', () => {
    const { container } = render(<KiteLoginModal {...props} phase="idle" />);
    expect(container).toBeEmptyDOMElement();
  });

  it('directs failed mobile callbacks to recovery in Connect', () => {
    render(<KiteLoginModal {...props} />);
    expect(document.querySelector('input'), 'no paste box').toBeNull();
    expect(screen.getByText(/paste that page’s full address into Connect/i)).toBeInTheDocument();
  });

  it('says what is happening while the operator is on Kite', () => {
    render(<KiteLoginModal {...props} />);
    expect(screen.getByText('Waiting for Kite')).toBeInTheDocument();
  });

  it('lets a waiting phone user reach the full callback URL recovery fields', () => {
    const onDismiss = vi.fn();
    function RecoveryPane() {
      const [phase, setPhase] = React.useState<'waiting' | 'idle'>('waiting');
      return <>
        <KiteLoginModal {...props} phase={phase} onDismiss={() => { onDismiss(); setPhase('idle'); }} />
        <input placeholder="Full callback URL or request_token" />
      </>;
    }
    render(<RecoveryPane />);
    const recover = screen.getByRole('button', { name: 'Finish mobile login' });
    expect(recover).toBeEnabled();
    fireEvent.click(recover);
    expect(onDismiss).toHaveBeenCalledOnce();
    expect(screen.queryByRole('dialog')).toBeNull();
    const recovery = screen.getByPlaceholderText('Full callback URL or request_token');
    fireEvent.change(recovery, { target: { value: 'http://localhost/?request_token=fresh&state=signed' } });
    expect(recovery).toHaveValue('http://localhost/?request_token=fresh&state=signed');
  });

  it('keeps the preparation step visible until the login link is ready', () => {
    const onDismiss = vi.fn();
    render(<KiteLoginModal {...props} phase="opening" onDismiss={onDismiss} />);
    const close = screen.getByRole('button', { name: 'Close' });
    expect(close).toBeDisabled();
    fireEvent.click(close);
    expect(onDismiss).not.toHaveBeenCalled();
  });

  it('confirms the broker login succeeded and Sterling is ready', () => {
    render(<KiteLoginModal {...props} phase="done" />);
    expect(screen.getByText('Connected to Kite')).toBeInTheDocument();
    expect(screen.getByText(/Your Kite login succeeded.*Sterling is connected to your broker account/i)).toBeInTheDocument();
  });

  it('offers a fresh login on failure', () => {
    const onRetry = vi.fn();
    render(<KiteLoginModal {...props} phase="failed" error="The Kite window closed early." onRetry={onRetry} />);
    expect(screen.getByText('The Kite window closed early.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onRetry).toHaveBeenCalled();
    expect(document.querySelector('input'), 'still no paste box').toBeNull();
  });

  it('is dismissible once it has finished, either way', () => {
    for (const phase of ['done', 'failed'] as const) {
      const onDismiss = vi.fn();
      const view = render(<KiteLoginModal {...props} phase={phase} onDismiss={onDismiss} />);
      fireEvent.keyDown(window, { key: 'Escape' });
      expect(onDismiss, phase).toHaveBeenCalled();
      view.unmount();
    }
  });

  it('announces itself as a dialog', () => {
    render(<KiteLoginModal {...props} />);
    expect(screen.getByRole('dialog')).toHaveAttribute('aria-modal', 'true');
  });
});

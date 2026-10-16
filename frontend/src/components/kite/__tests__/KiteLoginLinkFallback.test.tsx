import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { KiteLoginLinkFallback } from '../KiteLoginLinkFallback';

const url = 'https://kite.zerodha.com/connect/login?api_key=test&redirect_params=state%3Dsigned';
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('Kite mobile login link fallback', () => {
  it('opens a fresh broker URL in another tab and resumes verification', () => {
    const onContinue = vi.fn();
    render(<KiteLoginLinkFallback url={url} onContinue={onContinue} />);
    const link = screen.getByRole('link', { name: 'Open Kite login in your browser' });
    expect(link).toHaveAttribute('href', url);
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    fireEvent.click(link);
    expect(onContinue).toHaveBeenCalledOnce();
  });

  it('copies the complete signed URL and resumes session verification', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    const onContinue = vi.fn();
    render(<KiteLoginLinkFallback url={url} onContinue={onContinue} />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy Kite login URL' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Login link copied' })).toBeInTheDocument());
    expect(writeText).toHaveBeenCalledWith(url);
    expect(onContinue).toHaveBeenCalledOnce();
  });

  it('shows a selectable URL when clipboard access is unavailable', async () => {
    vi.stubGlobal('navigator', {});
    render(<KiteLoginLinkFallback url={url} onContinue={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy Kite login URL' }));
    expect(await screen.findByRole('textbox', { name: 'Kite login URL' })).toHaveValue(url);
  });

  it('clears copied feedback for a fresh login URL', async () => {
    vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
    const onContinue = vi.fn();
    const view = render(<KiteLoginLinkFallback url={url} onContinue={onContinue} />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy Kite login URL' }));
    await screen.findByRole('button', { name: 'Login link copied' });
    view.rerender(<KiteLoginLinkFallback url={`${url}-fresh`} onContinue={onContinue} />);
    expect(screen.getByRole('button', { name: 'Copy Kite login URL' })).toBeInTheDocument();
  });
});

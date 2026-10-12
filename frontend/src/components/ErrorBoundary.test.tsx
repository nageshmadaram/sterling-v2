import { render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ErrorBoundary } from './ErrorBoundary';
afterEach(() => vi.restoreAllMocks());
it('offers update recovery for a missing chunk and preserves session credentials', () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  sessionStorage.setItem('sterling_access_token', 'fixture-session');
  function MissingChunk(): never { throw new Error('Failed to fetch dynamically imported module: /assets/old.js'); }
  render(<ErrorBoundary><MissingChunk /></ErrorBoundary>);
  expect(screen.getByText('APP UPDATE AVAILABLE')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Reload app' })).toBeInTheDocument();
  expect(sessionStorage.getItem('sterling_access_token')).toBe('fixture-session');
  sessionStorage.removeItem('sterling_access_token');
});

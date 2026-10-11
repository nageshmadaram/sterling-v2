import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SavedKiteCredentials } from '../SavedKiteCredentials';
const post = vi.hoisted(() => vi.fn());
vi.mock('../../../utils/api', () => ({ api: { post } }));
describe('saved Kite credentials', () => {
  beforeEach(() => post.mockReset());
  it('masks without fetching; reveals requested value only and hides it', async () => {
    post.mockResolvedValue({ api_key: 'test-key', api_secret: 'test-secret' });
    render(<SavedKiteCredentials accountId="a" hasCredentials />);
    expect(post).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Saved API key')).toHaveValue('************');
    fireEvent.click(screen.getByRole('button', { name: 'Show saved API key' }));
    await waitFor(() => expect(screen.getByLabelText('Saved API key')).toHaveValue('test-key'));
    expect(screen.getByLabelText('Saved API secret')).toHaveValue('************');
    fireEvent.click(screen.getByRole('button', { name: 'Hide saved API key' }));
    expect(screen.getByLabelText('Saved API key')).toHaveValue('************');
  });
  it('discards pending secrets after losing focus', async () => {
    let resolve!: (value: unknown) => void;
    post.mockReturnValue(new Promise(r => { resolve = r; }));
    render(<SavedKiteCredentials accountId="a" hasCredentials />);
    fireEvent.click(screen.getByRole('button', { name: 'Show saved API secret' }));
    fireEvent(window, new Event('blur'));
    resolve({ api_key: 'key', api_secret: 'secret' });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Show saved API secret' })).not.toBeDisabled());
    expect(screen.getByLabelText('Saved API secret')).toHaveValue('************');
  });
});

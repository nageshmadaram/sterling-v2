import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { StrategyGuide } from '../StrategyGuide';

beforeEach(() => localStorage.clear());
describe.each(['ema_trend', 'adaptive_edge'])('%s practice disclosure', engine => {
  it.each([false, true])('remains visible when dismissed is %s', hidden => {
    if (hidden) localStorage.setItem(`sterling.strategy-guide.hidden.${engine}`, '1');
    render(<StrategyGuide engine={engine} />);
    expect(screen.getByText('Practice only — no real orders')).toBeVisible();
    expect(screen.queryByText(/To let Sterling trade for you/)).toBeNull();
    expect(screen.queryByText(/Press.*B/)).toBeNull();
  });
});

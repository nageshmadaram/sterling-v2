/** Measure rendered operator text against the real light/dark palette.
 * jsdom retains CSS var() expressions, so resolve those using the shipped tokens.
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { THEME_TOKENS } from '../../../styles/theme';
import { Card, Row } from '../ops/Card';
import { StatusBadge } from '../ops/StatusBadge';
import { ModeBanner } from '../ops/ModeBanner';
import { OperatorActions } from '../ops/OperatorActions';
import { SafetyAdmissionCard } from '../ops/SafetyAdmissionCard';

type RGB = [number, number, number];
function colour(raw: string, theme: 1 | 2): [number, number, number, number] {
  const resolved = raw.replace(/var\(--k-([\w-]+)(?:,\s*([^)]*))?\)/g, (_, name, fallback) => {
    const token = THEME_TOKENS.find(([key]) => key === name);
    if (!token && !fallback) throw new Error(`Undefined theme token: ${name}`);
    return token?.[theme] ?? fallback;
  }).trim();
  if (resolved.startsWith('#')) {
    let hex = resolved.slice(1);
    if (hex.length === 3) hex = [...hex].map(c => c + c).join('');
    return [0, 2, 4].map(i => parseInt(hex.slice(i, i + 2), 16)).concat(1) as [number, number, number, number];
  }
  const channels = resolved.match(/^rgba?\(([^)]+)\)$/)?.[1].split(',').map(Number);
  if (!channels) throw new Error(`Unsupported colour: ${resolved}`);
  return [channels[0], channels[1], channels[2], channels[3] ?? 1];
}
function background(node: HTMLElement | null, theme: 1 | 2): RGB {
  if (!node) return colour('var(--k-bg)', theme).slice(0, 3) as RGB;
  const behind = background(node.parentElement, theme);
  const raw = node.style.backgroundColor || node.style.background;
  if (!raw || raw === 'transparent') return behind;
  const [r, g, b, a] = colour(raw, theme);
  return [r, g, b].map((v, i) => v * a + behind[i] * (1 - a)) as RGB;
}
function contrast(node: HTMLElement, theme: 1 | 2): number {
  let ancestor: HTMLElement | null = node;
  while (ancestor && !ancestor.style.color) ancestor = ancestor.parentElement;
  const ink = colour(ancestor?.style.color || 'var(--k-text)', theme).slice(0, 3) as RGB;
  const luminance = (rgb: RGB) => rgb.map(v => {
    const c = v / 255;
    return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4;
  }).reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
  const [low, high] = [luminance(ink), luminance(background(node, theme))].sort((a, b) => a - b);
  return (high + .05) / (low + .05);
}

describe.each([['light', 1], ['dark', 2]] as const)('Operations small-text contrast in %s theme', (_, theme) => {
  it('keeps safety values, headings, labels and tri-state badges readable', () => {
    render(<div style={{ color: 'var(--k-text)', background: 'var(--k-bg)' }}>
      <Card title="Safety"><Row label="safety state"><strong>RUNNING</strong></Row>
        {(['PASS', 'FAIL', 'UNKNOWN'] as const).map(status => <StatusBadge key={status} status={status} title="gate" />)}
      </Card>
    </div>);
    const nodes = [screen.getByText('RUNNING'), screen.getByRole('heading', { name: 'Safety' }), screen.getByText('safety state'),
      ...(['PASS', 'FAIL', 'UNKNOWN'] as const).map(status => screen.getByRole('status', { name: `gate: ${status}` }))];
    for (const node of nodes) expect(contrast(node, theme), node.textContent || '').toBeGreaterThanOrEqual(4.5);
  });
  it.each(['LIVE', 'LIVE_HALTED', 'RECOVERY_REQUIRED', 'UNKNOWN'] as const)('keeps the %s banner readable', mode => {
    render(<div style={{ color: 'var(--k-text)', background: 'var(--k-bg)' }}><ModeBanner mode={mode} /></div>);
    expect(contrast(screen.getByRole('status', { name: 'trading mode' }), theme)).toBeGreaterThanOrEqual(4.5);
  });
  it.each([false, null])('keeps admission warning readable when permission is %s', allowed => {
    render(<div style={{ color: 'var(--k-text)', background: 'var(--k-bg)' }}>
      <SafetyAdmissionCard safety={{ safety_state: 'HALTED', execution_control_state: 'CLEAN',
        new_risk_allowed: allowed, family_stop_engaged: true, live_execution_enabled: true,
        blockers: [{ code: 'OPERATOR_STOP', message: 'Operator stop engaged', severity: 'BLOCK', source: 'operator' }] }} />
    </div>);
    expect(contrast(screen.getByRole('alert'), theme)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(screen.getByText('OPERATOR_STOP'), theme)).toBeGreaterThanOrEqual(4.5);
  });
  it('keeps enabled operator actions readable without invoking them', () => {
    const stop = vi.fn(), resume = vi.fn(), refresh = vi.fn();
    render(<div style={{ color: 'var(--k-text)', background: 'var(--k-bg)' }}>
      <OperatorActions operator={{ stop_available: true, resume_available: false, stop_engaged: false }} onStop={stop} onResume={resume} onRefresh={refresh} />
    </div>);
    for (const name of ['STOP ALL NEW TRADES', 'Refresh now']) expect(contrast(screen.getByRole('button', { name }), theme), name).toBeGreaterThanOrEqual(4.5);
    expect(stop).not.toHaveBeenCalled(); expect(resume).not.toHaveBeenCalled(); expect(refresh).not.toHaveBeenCalled();
  });
});

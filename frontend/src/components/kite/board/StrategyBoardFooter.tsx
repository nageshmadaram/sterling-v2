import type { ReactNode } from 'react';

/** Shared home for strategy controls and evidence, after the common table. */
export function StrategyBoardFooter({ name, children }: { name: string; children: ReactNode }) {
  return <section className="sb-strategy-footer" aria-label={`${name} controls and information`}>
    {children}
  </section>;
}

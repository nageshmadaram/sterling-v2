import { SimpleTerminal } from './SimpleTerminal';

/**
 * The former Pro terminal depended on components that were deliberately removed
 * when Sterling became a Kite-only Indian-markets application. Leaving this route
 * as a "Coming Soon" placeholder made a persisted `sterling_app_mode=pro` look
 * like the whole application had disappeared.
 *
 * Until a distinct advanced workspace exists again, Pro resolves to the same
 * fully-supported Kite workspace as Basic. This is intentional degradation:
 * every current strategy, broker control, signal board, order surface and
 * settings page remains usable, and no stale legacy terminal is resurrected.
 */
export function Terminal() {
  return <SimpleTerminal kiteOnly />;
}

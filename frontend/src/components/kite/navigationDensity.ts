/** Keep the highest-frequency actions immediately reachable; move the
 * secondary destinations into a keyboard-accessible Tools menu. Ordering and
 * hidden preferences were already applied by orderedNavigation().
 */
const PRIMARY = new Set(['dashboard', 'pcr', 'orders', 'holdings', 'positions']);
export function groupPrimaryNavigation<T extends { id: string }>(
  items: readonly T[],
): { primary: T[]; overflow: T[] } {
  return {
    primary: items.filter(item => PRIMARY.has(item.id)),
    overflow: items.filter(item => !PRIMARY.has(item.id)),
  };
}

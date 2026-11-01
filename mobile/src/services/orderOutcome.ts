/** Validate a broker submission ACK, never infer a fill from HTTP success.
 *
 * An HTTP 200 carrying a blocked/unknown status, an empty order ID or
 * malformed data is NOT an executed trade. Do not retry after an uncertain
 * response: inspect the order book and reconcile first.
 */
export function requireOrderAcknowledgement(outcome: unknown): string {
  if (!outcome || typeof outcome !== 'object') {
    throw new Error('Order acknowledgement is unavailable. Check Orders before retrying.');
  }
  const item = outcome as Record<string, unknown>;
  const status = String(item.status ?? '').toLowerCase();
  if (['blocked', 'error', 'rejected', 'unknown', 'failed'].includes(status)) {
    throw new Error('Order was not confirmed. Check Orders and its rejection details before retrying.');
  }
  const id = item.order_id;
  if (typeof id !== 'string' || !id.trim() || ['PENDING', 'UNKNOWN'].includes(id.trim().toUpperCase())) {
    throw new Error('Order acknowledgement has no broker ID. Check Orders before retrying.');
  }
  return id.trim();
}

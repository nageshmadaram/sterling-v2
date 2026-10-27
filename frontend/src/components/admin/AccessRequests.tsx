import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../utils/api';
import { WORKSPACE_NAMES, type Workspace } from '../WorkspaceChooser';
export type AccessRequest = { id: string; user_id: string; username: string; feature: Workspace; status: 'pending' | 'approved' | 'rejected'; created_at_ms: number };
export function AccessRequests({ requests }: { requests: AccessRequest[] }) {
  const qc = useQueryClient();
  const review = useMutation({ mutationFn: ({ id, decision }: { id: string; decision: 'approved' | 'rejected' }) => api.patch(`/api/v1/admin/access-requests/${encodeURIComponent(id)}`, { decision }),
    onSuccess: () => { for (const key of [['admin-access-requests'], ['admin-features'], ['sterling-session-policy']]) void qc.invalidateQueries({ queryKey: key }); } });
  return <section className="admin-wide"><div className="admin-section-title"><h2>Access requests</h2><p>Review workspace requests from your Sterling users.</p></div>
    {review.error && <p role="alert">{review.error.message}</p>}
    {!requests.length && <div className="admin-empty"><span aria-hidden>✓</span><h3>You’re all caught up</h3><p>New access requests will appear here automatically.</p></div>}
    <div className="admin-request-list">{requests.map(request => <article className="admin-request-card" key={request.id}><div className="admin-user-initial">{request.username.slice(0, 2).toUpperCase()}</div>
      <div className="admin-request-copy"><h3>{request.username}</h3><p>Requested <strong>{WORKSPACE_NAMES[request.feature]}</strong> access</p><small>{new Date(request.created_at_ms).toLocaleString()}</small></div>
      {request.status === 'pending' ? <div className="admin-request-actions"><button className="bac-btn" disabled={review.isPending} onClick={() => review.mutate({ id: request.id, decision: 'rejected' })}>Reject</button><button className="bac-btn bac-btn-primary" disabled={review.isPending} onClick={() => review.mutate({ id: request.id, decision: 'approved' })}>Approve</button></div> : <span className={`admin-request-status is-${request.status}`}>{request.status === 'approved' ? 'Approved' : 'Rejected'}</span>}
    </article>)}</div>
  </section>;
}

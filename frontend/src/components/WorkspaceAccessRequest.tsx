import { useMutation } from '@tanstack/react-query';
import { api } from '../utils/api';
import { WORKSPACE_NAMES, type Workspace } from './WorkspaceChooser';

export function WorkspaceAccessRequest({ feature, onBack }: { feature: Workspace; onBack: () => void }) {
  const request = useMutation({ mutationFn: () =>
    api.post<{ status: string }>('/api/v1/settings/access-requests', { feature }), retry: false });
  const status = request.data?.status;
  const requested = request.isSuccess;
  return <main className="workspace-access-state">
    <button type="button" className="bac-btn workspace-access-back" onClick={onBack}>← Back to workspaces</button>
    <span className="chooser-icon" aria-hidden>↗</span>
    <p className="workspace-eyebrow">WORKSPACE ACCESS</p>
    <h1>{WORKSPACE_NAMES[feature]} is not enabled for your account</h1>
    <p>Only an administrator can enable this workspace. You can ask for access if you need it.</p>
    {!requested && <button type="button" className="bac-btn bac-btn-primary" disabled={request.isPending}
      onClick={() => request.mutate()}>
      {request.isPending ? 'Sending request…' : request.isError ? 'Retry access request' : `Request access to ${WORKSPACE_NAMES[feature]}`}
    </button>}
    {request.isPending && <p role="status">Sending your request to the administrator…</p>}
    {request.isError && <p role="alert">{request.error.message}</p>}
    {requested && <p role="status">{status === 'rejected'
      ? 'Your administrator declined this request. Contact them to discuss access.'
      : status === 'granted' ? 'Access is now enabled. Return to your workspaces to continue.'
      : 'Your request has been sent to your administrator. You can open this workspace after it is approved.'}</p>}
  </main>;
}

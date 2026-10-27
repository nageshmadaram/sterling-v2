import { useEffect } from 'react';
import { useMutation } from '@tanstack/react-query';
import { api } from '../utils/api';
import { WORKSPACE_NAMES, type Workspace } from './WorkspaceChooser';
export function WorkspaceAccessRequest({ feature, onBack }: { feature: Workspace; onBack: () => void }) {
  const request = useMutation({ mutationFn: () => api.post<{ status: string }>('/api/v1/settings/access-requests', { feature }), retry: false });
  useEffect(() => { request.mutate(); }, [feature]);
  return <main className="workspace-access-state"><span className="chooser-icon" aria-hidden>↗</span><p className="workspace-eyebrow">WORKSPACE ACCESS</p><h1>{WORKSPACE_NAMES[feature]} is not enabled for your account</h1>
    {request.isPending ? <p role="status">Sending your request to the administrator…</p> : request.isError ? <><p role="alert">{request.error.message}</p><button onClick={() => request.mutate()}>Retry request</button></> :
      <p role="status">{request.data?.status === 'rejected' ? 'Your administrator declined this request. Contact them to discuss access.' : request.data?.status === 'granted' ? 'Access is now enabled. Choose the workspace again to continue.' : 'Your access request has been sent to your administrator. You’ll be able to open this workspace once it is approved.'}</p>}
    <button type="button" className="bac-btn" onClick={onBack}>Choose another workspace</button>
  </main>;
}

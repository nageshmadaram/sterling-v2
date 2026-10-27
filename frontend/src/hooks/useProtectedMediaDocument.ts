import { useEffect, useState } from 'react';
import { api } from '../utils/api';

/** The media-only HttpOnly cookie never authorizes private trading APIs. */
export function useProtectedMediaDocument(feature: 'tv' | 'radio', revision: number) {
  const [state, setState] = useState({ url: '', error: '' });
  useEffect(() => {
    let alive = true;
    setState({ url: '', error: '' });
    api.post<{ document_url: string }>(`/api/v1/media/${feature}/access`, {}).then(result => {
      if (alive && result.document_url === `/api/v1/media/${feature}/document`)
        setState({ url: `${result.document_url}?load=${revision}`, error: '' });
    }).catch(error => { if (alive) setState({ url: '', error: error.message || 'Player access could not be verified.' }); });
    return () => { alive = false; };
  }, [feature, revision]);
  return state;
}

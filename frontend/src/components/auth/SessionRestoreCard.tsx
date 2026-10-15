import type { ReactNode } from 'react';
export function SessionRestoreCard({stage,message,children}:{stage:'session'|'settings';message:string;children?:ReactNode}) {
 return <div className="sterling-session-validation" role="status" aria-live="polite"><div className="session-validation-card session-restore-card">
  <div className="session-restore-art" aria-hidden="true"><span className="session-restore-orbit"/><svg width="36" height="36" viewBox="0 0 40 40" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M20 5 32 10v10c0 8-12 15-12 15S8 28 8 20V10L20 5Z"/><path d="m14 20 4 4 8-9"/></svg></div>
  <div className="session-restore-copy"><span className="session-restore-brand">STERLING</span><p>{message}</p><div className="session-restore-steps" aria-hidden="true"><span data-active={stage==='session'}>Session</span><i/><span data-active={stage==='settings'}>Saved settings</span></div></div>
  {children}
 </div></div>;
}

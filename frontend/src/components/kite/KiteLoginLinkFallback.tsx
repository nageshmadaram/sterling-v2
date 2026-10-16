import React, { useEffect, useState } from 'react';

export function KiteLoginLinkFallback({ url, onContinue }: { url: string; onContinue: () => void }) {
  const [copyFailed, setCopyFailed] = useState(false);
  const [copied, setCopied] = useState(false);
  useEffect(() => { setCopied(false); setCopyFailed(false); }, [url]);
  const copy = async () => {
    try {
      if (!navigator.clipboard) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(url);
      setCopied(true);
      onContinue();
    } catch { setCopyFailed(true); }
  };
  return (
    <div style={{ fontSize: 12, lineHeight: 1.6, marginTop: 8 }}>
      <span>Having trouble opening the login window? </span>
      <a href={url} target="_blank" rel="noopener noreferrer" onClick={onContinue}>Open Kite login in your browser</a>
      {' · '}
      <button type="button" onClick={() => void copy()} style={{ cursor: 'pointer' }}>
        {copied ? 'Login link copied' : 'Copy Kite login URL'}
      </button>
      {copyFailed && <input aria-label="Kite login URL" readOnly value={url} onFocus={(e) => e.target.select()} style={{ display: 'block', width: '100%', marginTop: 6, boxSizing: 'border-box' }} />}
    </div>
  );
}

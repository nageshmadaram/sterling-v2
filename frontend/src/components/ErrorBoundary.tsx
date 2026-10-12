import React, { ReactNode } from 'react';

interface State { error: Error | null; errorInfo: string }
interface Props { children: ReactNode }

export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null, errorInfo: '' };

  static getDerivedStateFromError(error: Error): State {
    return { error, errorInfo: '' };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[Sterling crash]', error.message, info.componentStack?.slice(0, 300));
    this.setState({ errorInfo: info.componentStack?.slice(0, 200) ?? '' });
  }

  handleRetry = () => {
    this.setState({ error: null, errorInfo: '' });
  };

  render() {
    if (this.state.error) {
      const moduleUpdate = /Failed to fetch dynamically imported module|Importing a module script failed|Loading chunk/i.test(this.state.error.message);
      return (
        <div style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center',
          justifyContent: 'center', minHeight: '100vh', gap: 12,
          background: 'var(--bg)', color: 'var(--danger)',
          fontFamily: 'inherit', padding: 32,
        }}>
          <div style={{ fontSize: 20, fontWeight: 800, letterSpacing: 2 }}>{moduleUpdate ? 'APP UPDATE AVAILABLE' : 'RUNTIME ERROR'}</div>
          <div style={{ color: 'var(--text-muted)', fontSize: 12, maxWidth: 500, textAlign: 'center' }}>
            {moduleUpdate ? 'Reload Sterling to load the current app version. Your login and saved settings are retained.' : this.state.error.message}
          </div>
          {!moduleUpdate && this.state.errorInfo && (
            <pre style={{
              color: 'var(--text-faint)', fontSize: 9, maxWidth: 600,
              overflow: 'auto', maxHeight: 120, background: 'var(--bg-card)',
              padding: '8px 10px', borderRadius: 4, border: '1px solid var(--border)',
            }}>
              {this.state.errorInfo}
            </pre>
          )}
          <div style={{ display: 'flex', gap: 10, marginTop: 8 }}>
            <button
              onClick={moduleUpdate ? () => window.location.reload() : this.handleRetry}
              style={{
                background: 'var(--bg-card)', color: 'var(--text-muted)',
                border: '1px solid var(--border)',
                padding: '8px 20px', borderRadius: 4, cursor: 'pointer',
                fontFamily: 'inherit', fontSize: 12,
              }}
            >
              {moduleUpdate ? 'Reload app' : 'Retry'}
            </button>
            <button
              onClick={() => window.location.reload()}
              style={{
                background: 'var(--bg-card)', color: 'var(--accent)',
                border: '1px solid var(--accent)',
                padding: '8px 20px', borderRadius: 4, cursor: 'pointer',
                fontFamily: 'inherit', fontSize: 12,
              }}
            >
              Reload page
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

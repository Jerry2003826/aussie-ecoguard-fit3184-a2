import { Component } from 'react';
import type { ReactNode } from 'react';
import { AlertCircle } from 'lucide-react';
export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    if (this.state.failed)
      return (
        <main className="app-loading">
          <section className="panel">
            <AlertCircle size={30} />
            <h1>Something interrupted this page.</h1>
            <p>
              Your uploaded files are not affected. Reload to reconnect and check the latest task
              status.
            </p>
            <button className="button primary" onClick={() => window.location.reload()}>
              Reload EcoGuard
            </button>
          </section>
        </main>
      );
    return this.props.children;
  }
}

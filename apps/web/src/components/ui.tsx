import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { AlertCircle, CheckCircle2, LoaderCircle, X, Leaf } from 'lucide-react';
import { ApiError } from '../api/client';
export function ErrorNotice({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null;
  return (
    <div className="notice error" role="alert">
      <AlertCircle size={20} />
      <div>
        <strong>{error instanceof Error ? error.message : String(error)}</strong>
        {error instanceof ApiError && error.requestId && (
          <small>Reference: {error.requestId}</small>
        )}
        {onRetry && (
          <button type="button" className="text-button" onClick={onRetry}>
            Try again
          </button>
        )}
      </div>
    </div>
  );
}
export function Notice({ children }: { children: ReactNode }) {
  return (
    <div className="notice success" role="status">
      <CheckCircle2 size={19} />
      <div>{children}</div>
    </div>
  );
}
export function Spinner({ text = 'Loading…' }: { text?: string }) {
  return (
    <div className="loading" role="status">
      <LoaderCircle className="spin" size={22} />
      {text}
    </div>
  );
}
export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-icon">
        <Leaf size={30} />
      </div>
      <h3>{title}</h3>
      <p>{children}</p>
      {action}
    </div>
  );
}
export function PageHeader({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {action}
    </header>
  );
}
export function StatusBadge({ status }: { status: string }) {
  const green = ['READY', 'QUERY_READY', 'CLEANED', 'COMPLETED', 'CONFIRMED', 'ACTIVE'].includes(
    status,
  );
  const red = ['FAILED', 'EXPIRED'].includes(status);
  return (
    <span className={`status-badge ${green ? 'good' : red ? 'bad' : 'pending'}`}>
      <i />
      {status.toLowerCase().replaceAll('_', ' ')}
    </span>
  );
}
export function Modal({
  title,
  children,
  onClose,
  danger = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  danger?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const old = document.activeElement as HTMLElement;
    const focusable = () =>
      Array.from(
        ref.current?.querySelectorAll<HTMLElement>(
          'button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],[tabindex="0"]',
        ) || [],
      );
    focusable()[0]?.focus();
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close.current();
      if (event.key === 'Tab') {
        const items = focusable();
        if (event.shiftKey && document.activeElement === items[0]) {
          event.preventDefault();
          items.at(-1)?.focus();
        } else if (!event.shiftKey && document.activeElement === items.at(-1)) {
          event.preventDefault();
          items[0]?.focus();
        }
      }
    };
    const prior = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    document.addEventListener('keydown', handler);
    return () => {
      document.body.style.overflow = prior;
      document.removeEventListener('keydown', handler);
      old?.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={ref}
        className={`modal ${danger ? 'danger-modal' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
      >
        <div className="modal-heading">
          <h2 id="modal-title">{title}</h2>
          <button type="button" aria-label="Close dialog" className="icon-button" onClick={onClose}>
            <X size={22} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

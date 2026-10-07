import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { BellPlus, MailCheck, RefreshCw, BellOff, Mail } from 'lucide-react';
import { api } from '../api/client';
import type { Subscription } from '../api/types';
import { label, parseTags } from '../api/media';
import { useAuth } from '../auth/AuthProvider';
import {
  EmptyState,
  ErrorNotice,
  Modal,
  Notice,
  PageHeader,
  Spinner,
  StatusBadge,
} from '../components/ui';
export function Subscriptions() {
  const auth = useAuth();
  const [tags, setTags] = useState('');
  const [error, setError] = useState<unknown>();
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<Subscription | null>(null);
  const query = useQuery({
    queryKey: ['subscriptions'],
    queryFn: ({ signal }) => api.get<{ items: Subscription[] }>('/subscriptions', signal),
  });
  async function save(event: FormEvent) {
    event.preventDefault();
    const parsed = parseTags(tags);
    if (!parsed.length) return;
    setBusy(true);
    setError(undefined);
    setMessage('');
    try {
      const result = await api.post<Subscription>('/subscriptions', { tags: parsed });
      setTags('');
      setMessage(
        result.confirmationStatus === 'CONFIRMED' || result.status === 'ACTIVE'
          ? 'Your subscription is active. Matching sightings will be sent to your verified email.'
          : 'Subscription saved. Open the confirmation email from AWS Notifications and confirm this email channel, then refresh the status below.',
      );
      await query.refetch();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    if (!removing) return;
    setBusy(true);
    setError(undefined);
    try {
      await api.delete(`/subscriptions/${encodeURIComponent(removing.subscriptionId)}`);
      setRemoving(null);
      setMessage('Subscription cancelled. You will not receive new alerts for this subscription.');
      await query.refetch();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageHeader
        eyebrow="KEEP AN EYE OUT"
        title="Stay in the loop"
        description="Choose the tags you care about. We’ll email you when a matching sighting is added or updated."
      />
      <div className="subscriptions-layout">
        <section className="panel">
          <div className="section-icon">
            <BellPlus size={25} />
          </div>
          <h2>Create an email alert</h2>
          <form className="form-stack" onSubmit={save}>
            <label>
              Tags to watch
              <input
                required
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                placeholder="cat, ginger, fox"
              />
              <small>Separate tags with commas.</small>
            </label>
            <div className="email-destination">
              <Mail size={19} />
              <span>
                <small>Deliver to your verified account email</small>
                <strong>{auth.user?.email}</strong>
              </span>
            </div>
            <button className="button primary" disabled={busy || !parseTags(tags).length}>
              <BellPlus size={17} />
              {busy ? 'Saving…' : 'Create alert'}
            </button>
          </form>
          <p className="small muted">
            You’ll need to confirm the AWS email subscription separately from your account
            verification. Alerts only include sightings you can access.
          </p>
        </section>
        <section className="panel tip-panel">
          <MailCheck size={29} />
          <h2>A heads-up when it matters.</h2>
          <p>
            New sightings and meaningful tag updates can trigger an alert. Open the link in your
            email and sign in to view the original.
          </p>
          <p>You can cancel an alert here at any time.</p>
        </section>
      </div>
      <ErrorNotice error={error} />
      {message && <Notice>{message}</Notice>}
      <div className="results-heading">
        <h2>Your alerts</h2>
        <button
          className="button secondary small-button"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          <RefreshCw size={15} /> Refresh email status
        </button>
      </div>
      <ErrorNotice error={query.error} onRetry={() => void query.refetch()} />
      {query.isPending ? (
        <Spinner />
      ) : query.data?.items.length ? (
        <div className="subscription-list">
          {query.data.items.map((subscription) => (
            <article className="panel subscription-card" key={subscription.subscriptionId}>
              <div>
                <div className="tags">
                  {subscription.tags.map((tag) => (
                    <span key={tag}>{label(tag)}</span>
                  ))}
                </div>
                <StatusBadge
                  status={
                    subscription.status || subscription.confirmationStatus || 'PENDING_CONFIRMATION'
                  }
                />
                {subscription.status === 'PENDING_CONFIRMATION' && (
                  <p className="small muted">
                    Check your inbox, confirm the email channel, then refresh.
                  </p>
                )}
              </div>
              <button
                className="button danger-ghost small-button"
                onClick={() => setRemoving(subscription)}
              >
                <BellOff size={15} /> Cancel alert
              </button>
            </article>
          ))}
        </div>
      ) : (
        !query.error && (
          <EmptyState title="A quieter inbox, until you choose.">
            Create your first tag alert above to follow the species or pet descriptions that matter
            to you.
          </EmptyState>
        )
      )}
      {removing && (
        <Modal
          title="Cancel this alert?"
          onClose={() => {
            if (!busy) setRemoving(null);
          }}
        >
          <p>
            You will stop receiving updates for this subscription. You can create another alert
            whenever you need it.
          </p>
          <ErrorNotice error={error} />
          <div className="modal-actions">
            <button className="button secondary" disabled={busy} onClick={() => setRemoving(null)}>
              Keep alert
            </button>
            <button className="button danger" disabled={busy} onClick={() => void remove()}>
              {busy ? 'Cancelling…' : 'Cancel alert'}
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}

import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Tag, Trash2, X } from 'lucide-react';
import { api } from '../api/client';
import type { Media } from '../api/types';
import { mediaUrl, parseTags } from '../api/media';
import { ErrorNotice, Modal, Notice } from './ui';
import { JobStatus } from './JobStatus';
export function BulkActions({
  items,
  onClear,
}: {
  items: Media[];
  onClear: (action?: 'tag' | 'delete') => void;
}) {
  const [action, setAction] = useState<'tag' | 'delete' | null>(null);
  const [targets, setTargets] = useState<Media[]>([]);
  const [tags, setTags] = useState('');
  const [operation, setOperation] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [message, setMessage] = useState('');
  const [jobIds, setJobIds] = useState<string[]>([]);
  const client = useQueryClient();
  function open(next: 'tag' | 'delete') {
    setTargets(items);
    setAction(next);
    setError(undefined);
    setMessage('');
    setJobIds([]);
  }
  async function submit() {
    setBusy(true);
    setError(undefined);
    try {
      const response = await api.post<{
        jobId?: string;
        results?: {
          success?: boolean;
          status?: string;
          jobId?: string;
          error?: string | { message?: string };
          url?: string;
        }[];
        items?: {
          success?: boolean;
          status?: string;
          jobId?: string;
          error?: string | { message?: string };
          url?: string;
        }[];
      }>(action === 'tag' ? '/media/tags' : '/media/delete', {
        urls: targets.map(mediaUrl),
        ...(action === 'tag' ? { tags: parseTags(tags), operation } : {}),
      });
      const outcomes = response.results || response.items || [];
      const failures = outcomes.filter(
        (row) => row.success === false || row.error || row.status?.toUpperCase() === 'FAILED',
      );
      if (failures.length)
        setError(
          new Error(
            `${failures.length} file(s) could not be updated: ${failures.map((row) => (typeof row.error === 'object' ? row.error.message : row.error || row.url)).join('; ')}`,
          ),
        );
      else if (response.jobId || outcomes.some((row) => row.jobId)) {
        setJobIds(
          response.jobId
            ? [response.jobId]
            : outcomes.flatMap((row) => (row.jobId ? [row.jobId] : [])),
        );
        setMessage(
          'Deletion requested. It is complete only when all associated files and records are removed.',
        );
      } else
        setMessage(
          action === 'tag'
            ? 'Tags updated. Search results will reflect the changes.'
            : 'The deletion request was accepted. Check My media for completion.',
        );
      // Keep the task dialog mounted until the user has seen its status.
      await client.invalidateQueries({ queryKey: ['media'], refetchType: 'none' });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  function close() {
    if (busy) return;
    setAction(null);
    if (message && !error) onClear(action || undefined);
  }
  if (!items.length && !action) return null;
  return (
    <>
      <div className="bulk-toolbar">
        <strong>{items.length} selected</strong>
        <div className="button-row">
          <button className="button secondary small-button" onClick={() => open('tag')}>
            <Tag size={16} /> Edit tags
          </button>
          <button className="button danger-ghost small-button" onClick={() => open('delete')}>
            <Trash2 size={16} /> Delete
          </button>
          <button className="icon-button" onClick={() => onClear()} aria-label="Clear selection">
            <X size={18} />
          </button>
        </div>
      </div>
      {action && (
        <Modal
          title={
            action === 'tag'
              ? `Edit tags on ${targets.length} sighting${targets.length === 1 ? '' : 's'}`
              : `Delete ${targets.length} sighting${targets.length === 1 ? '' : 's'}?`
          }
          onClose={close}
          danger={action === 'delete'}
        >
          <ErrorNotice error={error} />
          {message && <Notice>{message}</Notice>}
          {action === 'tag' ? (
            <div className="form-stack">
              <label>
                Action
                <select
                  value={operation}
                  onChange={(e) => setOperation(Number(e.target.value))}
                  disabled={busy}
                >
                  <option value={1}>Add tags</option>
                  <option value={0}>Remove tags</option>
                </select>
              </label>
              <label>
                Tags, separated by commas
                <input
                  value={tags}
                  onChange={(e) => setTags(e.target.value)}
                  placeholder="ginger, cat"
                  disabled={busy}
                />
                <small>Repeated additions and missing tags are safely ignored.</small>
              </label>
            </div>
          ) : (
            <>
              <p>
                Original files, thumbnails, video frames and their search records will be removed.
                This cannot be undone.
              </p>
              <p className="small muted">
                If any part fails, deletion remains pending and can be retried.
              </p>
            </>
          )}
          {jobIds.map((id) => (
            <JobStatus key={id} id={id} />
          ))}
          <div className="modal-actions">
            <button className="button secondary" onClick={close} disabled={busy}>
              {message ? 'Done' : 'Cancel'}
            </button>
            {jobIds.length === 0 && (
              <button
                className={`button ${action === 'delete' ? 'danger' : 'primary'}`}
                disabled={
                  busy ||
                  (action === 'tag' && !parseTags(tags).length) ||
                  Boolean(message && !error)
                }
                onClick={() => void submit()}
              >
                {busy ? 'Working…' : action === 'delete' ? 'Delete sightings' : 'Apply tags'}
              </button>
            )}
          </div>
        </Modal>
      )}
    </>
  );
}

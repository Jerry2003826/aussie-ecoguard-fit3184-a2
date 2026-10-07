import { useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { CloudUpload, FileImage, Check, X, RotateCcw } from 'lucide-react';
import { api, uploadFile } from '../api/client';
import { checksum, validateFile, videoDuration } from '../api/media';
import type { UploadAuthorization } from '../api/types';
import { ErrorNotice, Modal } from './ui';
import { JobStatus } from './JobStatus';
type Row = {
  id: string;
  file: File;
  progress: number;
  status: string;
  error?: string;
  jobId?: string;
  mediaId?: string;
};
export function UploadPanel({ onClose }: { onClose: () => void }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [visibility, setVisibility] = useState('private');
  const [observedAt, setObservedAt] = useState('');
  const [error, setError] = useState<unknown>();
  const [working, setWorking] = useState(false);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const controller = useRef<AbortController | null>(null);
  const queryClient = useQueryClient();
  function add(files: FileList | null) {
    if (!files || working) return;
    const incoming = Array.from(files);
    if (rows.length + incoming.length > 10) {
      setError(new Error('Upload up to 10 files in one batch.'));
      return;
    }
    setError(undefined);
    setRows((current) => [
      ...current,
      ...incoming.map((file) => ({
        id: crypto.randomUUID(),
        file,
        progress: 0,
        status: 'Ready to upload',
        error: validateFile(file) || undefined,
      })),
    ]);
  }
  function patch(id: string, update: Partial<Row>) {
    setRows((current) => current.map((row) => (row.id === id ? { ...row, ...update } : row)));
  }
  async function send() {
    setWorking(true);
    setError(undefined);
    const abort = new AbortController();
    controller.current = abort;
    try {
      for (const row of rows) {
        if (abort.signal.aborted) break;
        if (row.mediaId || validateFile(row.file)) continue;
        try {
          patch(row.id, { status: 'Checking file', error: undefined, progress: 0 });
          await videoDuration(row.file);
          const sha256 = await checksum(row.file);
          if (abort.signal.aborted) break;
          const authorization = await api.post<UploadAuthorization>('/uploads', {
            filename: row.file.name,
            contentType: row.file.type,
            sizeBytes: row.file.size,
            sha256,
            visibility,
            ...(observedAt ? { observedAt: new Date(observedAt).toISOString() } : {}),
          });
          if (authorization.duplicate || authorization.status === 'DUPLICATE') {
            patch(row.id, {
              status: 'Already in your library',
              mediaId: authorization.mediaId,
              progress: 100,
            });
            continue;
          }
          if (!authorization.uploadUrl || !authorization.uploadId)
            throw new Error('The service did not provide a valid upload authorization.');
          patch(row.id, { status: 'Uploading' });
          await uploadFile(
            authorization.uploadUrl,
            row.file,
            authorization.headers || authorization.uploadHeaders || {},
            (progress) => patch(row.id, { progress }),
            abort.signal,
            authorization.uploadMethod || 'PUT',
          );
          patch(row.id, { status: 'Confirming upload' });
          const completed = await api.post<{ jobId: string }>(
            `/uploads/${encodeURIComponent(authorization.uploadId)}/complete`,
          );
          patch(row.id, {
            status: 'Processing',
            jobId: completed.jobId || authorization.jobId,
            mediaId: authorization.mediaId,
          });
          await queryClient.invalidateQueries({ queryKey: ['media'] });
        } catch (e) {
          patch(row.id, {
            status:
              e instanceof DOMException && e.name === 'AbortError'
                ? 'Upload stopped'
                : 'Needs attention',
            error: e instanceof Error ? e.message : 'Upload failed',
          });
        }
      }
    } finally {
      setWorking(false);
      controller.current = null;
      void queryClient.invalidateQueries({ queryKey: ['media'] });
    }
  }
  function close() {
    if (working) return;
    onClose();
  }
  return (
    <Modal title="Add a sighting" onClose={close}>
      <p className="muted">
        A photo or a short video is a good place to start. We’ll organise the rest.
      </p>
      <ErrorNotice error={error} />
      <input
        ref={input}
        hidden
        type="file"
        multiple
        accept="image/jpeg,image/png,image/webp,video/mp4"
        onChange={(event) => {
          add(event.target.files);
          event.target.value = '';
        }}
        disabled={working}
      />
      <button
        type="button"
        className={`dropzone ${dragging ? 'dragging' : ''}`}
        disabled={working}
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          add(e.dataTransfer.files);
        }}
      >
        <span className="upload-icon">
          <CloudUpload size={27} />
        </span>
        <strong>Choose files or drop them here</strong>
        <span>JPEG, PNG, WebP up to 20 MB · MP4 up to 100 MB / 2 minutes</span>
        <small>Up to 10 files. Duplicates are detected by their content.</small>
      </button>
      <div className="form-grid upload-options">
        <label>
          Visibility
          <select
            value={visibility}
            onChange={(e) => setVisibility(e.target.value)}
            disabled={working}
          >
            <option value="private">Private — only you</option>
            <option value="public">Public sighting — signed-in community</option>
          </select>
        </label>
        <label>
          When did you see it? <small>(optional, your local time)</small>
          <input
            type="datetime-local"
            value={observedAt}
            onChange={(e) => setObservedAt(e.target.value)}
            max={new Date(Date.now() - new Date().getTimezoneOffset() * 60000)
              .toISOString()
              .slice(0, 16)}
            disabled={working}
          />
        </label>
      </div>
      {rows.length > 0 && (
        <ul className="upload-list">
          {rows.map((row) => (
            <li key={row.id}>
              <div className="upload-row">
                <FileImage size={22} />
                <div className="grow">
                  <strong>{row.file.name}</strong>
                  <small>
                    {(row.file.size / 1024 / 1024).toFixed(1)} MB · {row.status}
                  </small>
                  {row.error && <span className="field-error">{row.error}</span>}
                </div>
                {row.mediaId ? (
                  <Check className="green" size={20} />
                ) : (
                  !working && (
                    <button
                      className="icon-button"
                      aria-label={`Remove ${row.file.name}`}
                      onClick={() => setRows((current) => current.filter((x) => x.id !== row.id))}
                    >
                      <X size={17} />
                    </button>
                  )
                )}
              </div>
              {row.status === 'Uploading' && (
                <progress
                  aria-label={`Uploading ${row.file.name}`}
                  value={row.progress}
                  max={100}
                />
              )}{' '}
              {row.jobId && (
                <JobStatus
                  id={row.jobId}
                  onUpdate={(job) => {
                    const status =
                      job.status === 'READY'
                        ? 'Ready to view'
                        : job.status === 'FAILED'
                          ? 'Processing needs attention'
                          : 'Processing';
                    if (row.status !== status) patch(row.id, { status });
                  }}
                />
              )}
            </li>
          ))}
        </ul>
      )}
      <div className="modal-actions">
        <button
          className="button secondary"
          onClick={working ? () => controller.current?.abort() : close}
        >
          {working ? 'Stop uploading' : 'Close'}
        </button>
        <button
          className="button primary"
          disabled={working || !rows.some((row) => !row.mediaId && !validateFile(row.file))}
          onClick={() => void send()}
        >
          {rows.some((row) => row.error) ? <RotateCcw size={17} /> : <CloudUpload size={17} />}{' '}
          {working
            ? 'Uploading…'
            : rows.some((row) => row.error)
              ? 'Retry remaining files'
              : 'Upload & identify'}
        </button>
      </div>
      <p className="small muted">
        Closing after upload does not stop processing. Track your sightings in My media.
      </p>
    </Modal>
  );
}

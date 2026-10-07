import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { MessageCircle, X, Send, ArrowUpRight, Leaf } from 'lucide-react';
import { api } from '../api/client';
import type { ChatResponse, Media } from '../api/types';
import { mediaName } from '../api/media';
import { ErrorNotice } from './ui';
type Message = { id: string; role: 'user' | 'assistant'; text: string; media?: Media[] };
export function Chat() {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessionId, setSessionId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const tail = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (open) input.current?.focus();
  }, [open]);
  useEffect(() => {
    tail.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [messages, busy]);
  function close() {
    setOpen(false);
    toggle.current?.focus();
  }
  async function send(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    setBusy(true);
    setError(undefined);
    try {
      let session = sessionId;
      if (!session) {
        const created = await api.post<{ sessionId: string }>('/chat/sessions');
        if (!created.sessionId) throw new Error('The service could not create a chat session.');
        session = created.sessionId;
        setSessionId(session);
      }
      const reply = await api.post<ChatResponse>('/chat/messages', { sessionId: session, text });
      const texts = (reply.messages || [])
        .map((message) => (typeof message === 'string' ? message : message.content || ''))
        .filter(Boolean);
      const resultMedia = reply.items || reply.results || reply.cards || [];
      if (!texts.length && !resultMedia.length)
        throw new Error('The assistant returned no response. Please try again.');
      setMessages((current) => [
        ...current,
        { id: crypto.randomUUID(), role: 'user', text },
        { id: crypto.randomUUID(), role: 'assistant', text: texts.join('\n'), media: resultMedia },
      ]);
      setDraft('');
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
      input.current?.focus();
    }
  }
  return (
    <>
      <button
        ref={toggle}
        className={`chat-toggle ${open ? 'chat-open' : ''}`}
        aria-label={open ? 'Close sighting assistant' : 'Open sighting assistant'}
        aria-expanded={open}
        aria-controls="sighting-assistant"
        onClick={() => (open ? close() : setOpen(true))}
      >
        {open ? (
          <X size={22} />
        ) : (
          <>
            <MessageCircle size={21} />
            <span>Ask EcoGuard</span>
          </>
        )}
      </button>
      {open && (
        <section
          className="chat-panel"
          id="sighting-assistant"
          aria-label="Sighting assistant"
          onKeyDown={(event) => {
            if (event.key === 'Escape') close();
          }}
        >
          <header>
            <span className="chat-avatar">
              <Leaf size={20} />
            </span>
            <div>
              <h2>Your sighting assistant</h2>
              <small>Species, dates & possible pet sightings</small>
            </div>
            <button className="icon-button" aria-label="Close chat" onClick={close}>
              <X size={20} />
            </button>
          </header>
          <div className="chat-body" role="log" aria-live="polite" aria-relevant="additions">
            <div className="chat-intro">
              <h3>What are you looking for?</h3>
              <p>
                Ask in your own words. Your request is sent to AWS Lex, and results use the same
                permissions as search.
              </p>
              {!messages.length && (
                <div className="chat-suggestions">
                  {[
                    'Show me photos of foxes spotted yesterday',
                    'Help me find a lost ginger cat',
                  ].map((text) => (
                    <button
                      key={text}
                      onClick={() => {
                        setDraft(text);
                        input.current?.focus();
                      }}
                    >
                      {text}
                      <ArrowUpRight size={14} />
                    </button>
                  ))}
                </div>
              )}
            </div>
            {messages.map((message) => (
              <div key={message.id} className={`chat-message ${message.role}`}>
                <small>{message.role === 'user' ? 'You' : 'EcoGuard'}</small>
                {message.text && <p>{message.text}</p>}
                {message.media?.map((media) => (
                  <Link
                    key={media.mediaId}
                    to={`/media/${encodeURIComponent(media.mediaId)}`}
                    className="chat-result"
                    onClick={close}
                  >
                    {mediaName(media)}
                    <ArrowUpRight size={15} />
                  </Link>
                ))}
              </div>
            ))}
            {busy && (
              <p className="chat-thinking" role="status">
                Looking into that…
              </p>
            )}
            <div ref={tail} />
          </div>
          <ErrorNotice error={error} />
          <form onSubmit={send} className="chat-form">
            <label className="sr-only" htmlFor="chat-input">
              Your message
            </label>
            <input
              id="chat-input"
              ref={input}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Ask about a sighting…"
              maxLength={1000}
              disabled={busy}
            />
            <button aria-label="Send message" type="submit" disabled={busy || !draft.trim()}>
              <Send size={18} />
            </button>
          </form>
          <p className="chat-footer">Possible pet matches need your careful review.</p>
        </section>
      )}
    </>
  );
}

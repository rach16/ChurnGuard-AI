'use client';

/**
 * Assistant (4.6.3, ADR-0016 section 12).
 *
 * A question goes to POST /api/assistant/ask, which streams server-sent events:
 * the steps as the agents work, then one answer, sent only after every figure in
 * it was checked against the data. EventSource cannot POST, so the stream is
 * read from fetch. Leaving the page or asking again aborts the request, and the
 * backend stops the question at its next step.
 *
 * Every claim carries its sources; one about an account links to it. What the
 * checker removed is listed under "Could not confirm", never silently dropped.
 * Monochrome, like the rest of the site since 8.5.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUp, Check, ChevronDown, Loader2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';

import { BootGate, Shell } from '../shell';

const API = '/api';

interface Citation { ref: string; tool: string | null; args: Record<string, unknown> | null }
interface Claim { text: string; citations: Citation[] }
interface Answer {
  out_of_scope: boolean;
  answer: Claim[];
  could_not_confirm: { text: string; why: string }[];
  limitation: string | null;
  versions_note: string | null;
  fallback: { ref: string; tool: string; data: Record<string, unknown> }[] | null;
  tool_calls: { ref: string; name: string; ok: boolean }[];
  seconds: number;
  revisions: number;
}
interface Turn { question: string; steps: string[]; answer?: Answer; error?: string; pending: boolean }
interface Status { available: boolean; reason: string | null; notes: string }

const SOURCE: Record<string, string> = {
  get_story: 'account story',
  get_plays: 'save plays',
  find_accounts: 'account list',
  get_book_summary: 'book totals',
  get_causes: 'churn causes',
  search_notes: 'CSM notes',
};

function money(v: number): string {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

/** Server-sent events from a fetch body: "event: x\ndata: {...}\n\n" blocks. */
async function readEvents(
  body: ReadableStream<Uint8Array>,
  onEvent: (kind: string, data: unknown) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut = buffer.indexOf('\n\n');
    while (cut >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let kind = 'message';
      let data = '';
      for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) kind = line.slice(7);
        else if (line.startsWith('data: ')) data += line.slice(6);
      }
      if (data) onEvent(kind, JSON.parse(data));
      cut = buffer.indexOf('\n\n');
    }
  }
}

function AskPage() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<Status | null>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [accountId, setAccountId] = useState<number | null>(null);
  const abort = useRef<AbortController | null>(null);
  const count = useRef(0);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get('account');
    const account = id && /^\d+$/.test(id) ? Number(id) : null;
    setAccountId(account);
    fetch(`${API}/assistant/status`, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then(setStatus)
      .catch(() => setStatus(null));
    fetch(`${API}/assistant/suggestions${account !== null ? `?account_id=${account}` : ''}`, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : { suggestions: [] }))
      .then((b) => setSuggestions(b.suggestions ?? []))
      .catch(() => undefined);
    return () => abort.current?.abort();
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [turns]);

  const update = useCallback((i: number, change: (t: Turn) => Turn) => {
    setTurns((all) => all.map((t, n) => (n === i ? change(t) : t)));
  }, []);

  async function send(text: string) {
    const question = text.trim();
    if (!question) return;
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    setQuery('');
    const index = count.current++;
    setTurns((all) => [...all, { question, steps: [], pending: true }]);
    try {
      const r = await fetch(`${API}/assistant/ask`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ question, account_id: accountId }),
        signal: controller.signal,
      });
      if (!r.ok || !r.body) {
        const body = await r.json().catch(() => ({}));
        const detail =
          r.status === 401
            ? 'Your sign-in has ended. Sign in again (top right) and ask again.'
            : String(body.detail || body.error || `HTTP ${r.status}`);
        update(index, (t) => ({ ...t, pending: false, error: detail }));
        return;
      }
      await readEvents(r.body, (kind, data) => {
        if (kind === 'step') {
          const text = (data as { text: string }).text;
          update(index, (t) => (t.steps[t.steps.length - 1] === text ? t : { ...t, steps: [...t.steps, text] }));
        } else if (kind === 'answer') {
          update(index, (t) => ({ ...t, answer: data as Answer, pending: false }));
        } else if (kind === 'error') {
          update(index, (t) => ({ ...t, error: (data as { detail: string }).detail, pending: false }));
        }
      });
      update(index, (t) => (t.pending ? { ...t, pending: false, error: t.answer ? undefined : 'No answer came back.' } : t));
    } catch (e) {
      if ((e as Error).name === 'AbortError') {
        update(index, (t) => ({ ...t, pending: false, error: 'Stopped.' }));
      } else {
        update(index, (t) => ({ ...t, pending: false, error: 'The connection dropped before the answer arrived.' }));
      }
    }
  }

  const busy = turns.some((t) => t.pending);
  const disabled = status !== null && !status.available;

  return (
    <Shell title="Assistant" description="Answers from your Salesforce data; every figure is checked before it is shown">
      <div className="mx-auto flex min-h-full max-w-3xl flex-col">
        <div className="flex-1 space-y-8 px-4 py-6 sm:px-6 sm:py-8">
          {turns.length === 0 && (
            <div className="pt-4 sm:pt-8">
              <h2 className="text-lg font-semibold">Ask about your book</h2>
              <p className="mt-2 text-sm text-muted-foreground">
                Accounts and their risk, renewals, why customers left, what has worked, and what customers said.
                It reads the same data the site shows and cannot change anything.
              </p>
              {accountId !== null && (
                <p className="mt-2 text-xs text-muted-foreground">Questions here are about the account you came from.</p>
              )}
              <div className="mt-5 flex flex-col items-start gap-2">
                {suggestions.map((s) => (
                  <button
                    key={s}
                    disabled={disabled || busy}
                    onClick={() => send(s)}
                    className="rounded-md border px-3 py-1.5 text-left text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {turns.map((t, i) => (
            <div key={i} className="space-y-3">
              <p className="text-sm font-medium">{t.question}</p>
              <Steps steps={t.steps} pending={t.pending} />
              {t.error && <p className="text-sm text-muted-foreground">{t.error}</p>}
              {t.answer && <AnswerView answer={t.answer} />}
            </div>
          ))}
          <div ref={endRef} />
        </div>

        <div className="sticky bottom-0 border-t bg-background px-4 py-3 sm:px-6 sm:py-4">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              send(query);
            }}
            className="flex gap-2"
          >
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              disabled={disabled}
              maxLength={2000}
              placeholder={disabled ? 'Unavailable here' : 'Which at-risk accounts renew this quarter?'}
            />
            {busy ? (
              <Button type="button" variant="outline" onClick={() => abort.current?.abort()}>
                Stop
              </Button>
            ) : (
              <Button type="submit" size="icon" disabled={disabled || !query.trim()} aria-label="Ask">
                <ArrowUp className="h-4 w-4" />
              </Button>
            )}
          </form>
          {disabled && status?.reason && <p className="mt-2 text-xs text-muted-foreground">{status.reason}</p>}
        </div>
      </div>
    </Shell>
  );
}

function Steps({ steps, pending }: { steps: string[]; pending: boolean }) {
  const [open, setOpen] = useState(false);
  if (steps.length === 0 && !pending) return null;
  if (pending) {
    return (
      <div className="space-y-1 text-xs text-muted-foreground">
        {steps.map((s, i) => (
          <div key={i} className="flex items-center gap-2">
            {i === steps.length - 1 ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
            {s}
          </div>
        ))}
        {steps.length === 0 && (
          <div className="flex items-center gap-2">
            <Loader2 className="h-3 w-3 animate-spin" />
            Starting
          </div>
        )}
      </div>
    );
  }
  return (
    <button onClick={() => setOpen((v) => !v)} className="text-left text-xs text-muted-foreground hover:text-foreground">
      <span className="inline-flex items-center gap-1">
        {steps.length} step{steps.length === 1 ? '' : 's'}
        <ChevronDown className={cn('h-3 w-3 transition-transform', open && 'rotate-180')} />
      </span>
      {open && (
        <span className="mt-1 block space-y-0.5">
          {steps.map((s, i) => (
            <span key={i} className="block">· {s}</span>
          ))}
        </span>
      )}
    </button>
  );
}

function sourceLabel(c: Citation): string {
  return (c.tool && SOURCE[c.tool]) || c.tool || c.ref;
}

function AnswerView({ answer }: { answer: Answer }) {
  const [showRemoved, setShowRemoved] = useState(false);
  return (
    <div className="space-y-3">
      {answer.limitation && (
        <p className="rounded-md border px-3 py-2 text-sm text-muted-foreground">{answer.limitation}</p>
      )}
      {answer.answer.map((c, i) => (
        <div key={i}>
          <p className="text-sm leading-relaxed">{c.text}</p>
          {c.citations.length > 0 && (
            <p className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
              {c.citations.map((s) => {
                const id = s.args && typeof s.args.account_id === 'number' ? (s.args.account_id as number) : null;
                return id !== null ? (
                  <a key={s.ref} href={`/customer/${id}`} className="underline underline-offset-2 hover:text-foreground">
                    {sourceLabel(s)}
                  </a>
                ) : (
                  <span key={s.ref}>{sourceLabel(s)}</span>
                );
              })}
            </p>
          )}
        </div>
      ))}
      {answer.fallback && answer.fallback.length > 0 && <Fallback items={answer.fallback} />}
      {answer.versions_note && <p className="text-xs text-muted-foreground">{answer.versions_note}</p>}
      {answer.could_not_confirm.length > 0 && (
        <div className="text-xs text-muted-foreground">
          <button onClick={() => setShowRemoved((v) => !v)} className="inline-flex items-center gap-1 hover:text-foreground">
            Could not confirm {answer.could_not_confirm.length}
            <ChevronDown className={cn('h-3 w-3 transition-transform', showRemoved && 'rotate-180')} />
          </button>
          {showRemoved && (
            <ul className="mt-1 space-y-1">
              {answer.could_not_confirm.map((c, i) => (
                <li key={i} className="break-words">
                  <span className="line-through">{c.text}</span>
                  <span className="ml-1">({c.why})</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {!answer.out_of_scope && (
        <p className="text-[11px] text-muted-foreground">
          Checked against the data · {answer.tool_calls.filter((c) => c.ok).length} lookup
          {answer.tool_calls.filter((c) => c.ok).length === 1 ? '' : 's'} · {answer.seconds}s
          {answer.revisions ? ' · one correction' : ''}
        </p>
      )}
    </div>
  );
}

/** When no written claim survived the checker, the tool results themselves. */
function Fallback({ items }: { items: Answer['fallback'] }) {
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">No written answer passed the check. These are the figures it found:</p>
      {(items ?? []).map((f) => {
        const accounts = Array.isArray(f.data.accounts) ? (f.data.accounts as Record<string, unknown>[]) : null;
        if (accounts) {
          return (
            <table key={f.ref} className="w-full text-xs">
              <tbody>
                {accounts.map((a) => (
                  <tr key={String(a.account_id)} className="border-t">
                    <td className="py-1 pr-2">{String(a.name)}</td>
                    <td className="py-1 pr-2 text-right tabular">{money(Number(a.arr))}</td>
                    <td className="py-1 text-right tabular">{String(a.risk_score)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          );
        }
        const scalars = Object.entries(f.data).filter(([k, v]) => k !== 'about' && (typeof v === 'number' || typeof v === 'string'));
        return (
          <dl key={f.ref} className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
            {scalars.map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-muted-foreground">{k.replace(/_/g, ' ')}</dt>
                <dd className="tabular">{typeof v === 'number' && /arr/.test(k) ? money(v) : String(v)}</dd>
              </div>
            ))}
          </dl>
        );
      })}
    </div>
  );
}

export default function Page() {
  return (
    <BootGate>
      <AskPage />
    </BootGate>
  );
}

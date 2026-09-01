'use client';

/**
 * Assistant.
 *
 * Degraded is not offline. /health reports which subsystems came up, so when the
 * LLM stack is missing this says which dependency is absent rather than claiming
 * the service is down -- a 503 from an LLM route is proof the API is reachable.
 */

import { useEffect, useRef, useState } from 'react';
import { ArrowUp, Bot, Loader2, User } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';

import { apiClient } from '../api-client';
import { Shell } from '../shell';

interface Msg { role: 'user' | 'assistant'; content: string; sources?: number }

const SUGGESTIONS = [
  'Which segment has the highest churn risk?',
  'Why did SecurityFirst LLC become high risk?',
  'What are the most common reasons for churn?',
];

export default function AskPage() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [reachable, setReachable] = useState<boolean | null>(null);
  const [aiReason, setAiReason] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    apiClient
      .health()
      .then((h: any) => {
        setReachable(true);
        const errors = h?.errors ?? {};
        setAiReason(
          h?.degraded
            ? errors.rag_retriever ?? errors.multi_agent_system ?? 'AI stack unavailable'
            : null
        );
      })
      .catch(() => setReachable(false));
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  const disabled = loading || reachable === false || Boolean(aiReason);

  async function send(text: string) {
    if (!text.trim() || disabled) return;
    setMessages((m) => [...m, { role: 'user', content: text }]);
    setQuery('');
    setLoading(true);
    try {
      const res = await apiClient.ask(text);
      setMessages((m) => [
        ...m,
        { role: 'assistant', content: res.answer, sources: res.sources?.length ?? 0 },
      ]);
    } catch (e: any) {
      setMessages((m) => [
        ...m,
        { role: 'assistant', content: `Could not answer: ${e?.message ?? e}` },
      ]);
      // Deliberately does not mark the backend unreachable -- see the note above.
    } finally {
      setLoading(false);
    }
  }

  return (
    <Shell
      title="Assistant"
      description="Grounded in the customer corpus"
      actions={
        aiReason ? (
          <Badge variant="outline" className="font-normal text-muted-foreground">
            Unavailable
          </Badge>
        ) : reachable ? (
          <Badge variant="outline" className="font-normal text-muted-foreground">
            Ready
          </Badge>
        ) : null
      }
    >
      <div className="mx-auto flex h-full max-w-3xl flex-col">
        <div className="flex-1 space-y-6 px-6 py-8">
          {messages.length === 0 && (
            <div className="pt-8">
              <h2 className="text-lg font-semibold">Ask about the customer base</h2>
              <p className="mt-2 text-sm text-muted-foreground">
                Answers are retrieved from customer profiles, churn analyses, support
                history and success stories.
              </p>
              <div className="mt-5 flex flex-col items-start gap-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    disabled={disabled}
                    onClick={() => send(s)}
                    className="rounded-md border px-3 py-1.5 text-left text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m, i) => (
            <div key={i} className="flex gap-3">
              <div
                className={cn(
                  'flex h-7 w-7 shrink-0 items-center justify-center rounded-md border',
                  m.role === 'user' ? 'bg-muted' : 'bg-primary text-primary-foreground'
                )}
              >
                {m.role === 'user' ? <User className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
              </div>
              <div className="min-w-0 flex-1">
                <p className="whitespace-pre-wrap text-sm leading-relaxed">{m.content}</p>
                {m.sources !== undefined && m.sources > 0 && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    {m.sources} sources retrieved
                  </p>
                )}
              </div>
            </div>
          ))}

          {loading && (
            <div className="flex items-center gap-3 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Retrieving
            </div>
          )}
          <div ref={endRef} />
        </div>

        <div className="sticky bottom-0 border-t bg-background px-6 py-4">
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
              placeholder={disabled ? 'Unavailable' : 'Ask about churn, accounts, or patterns'}
            />
            <Button type="submit" size="icon" disabled={disabled || !query.trim()}>
              <ArrowUp className="h-4 w-4" />
            </Button>
          </form>

          {reachable === false ? (
            <p className="mt-2 text-xs text-sev-critical">
              Backend unreachable. Start it with{' '}
              <code className="font-mono">uv run python src/backend/api.py</code>
            </p>
          ) : aiReason ? (
            <p className="mt-2 text-xs text-muted-foreground">
              AI features unavailable — {aiReason}. The queue and analytics work without it.
            </p>
          ) : null}
        </div>
      </div>
    </Shell>
  );
}

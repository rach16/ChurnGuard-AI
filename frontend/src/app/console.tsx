'use client';

/**
 * The at-risk console (8.5, ADR-0015).
 *
 * Three panes: a rail, a queue, and the selected account's story. Selecting a
 * row swaps the right pane rather than navigating, because triage is a loop:
 * scan, open, judge, next.
 *
 * The queue comes from /book/queue: every active account, riskiest first, with
 * the renewal date and an evidence phrase ("Champion changed roles", "2 of 59
 * seats active last week") instead of the scorer's label, which read "Low
 * engagement" on 109 of 132 rows. The story comes from /customer/{id}/story:
 * a headline sentence, the signals with their source and date, what to do, and
 * the weight table behind a disclosure. Nothing here recomputes a score.
 *
 * On a phone the queue is the page and a tap opens the story full-width with a
 * back button; the old layout overflowed to the right with no way to the detail.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  Circle,
  ExternalLink,
  MessageSquare,
  Search,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Separator } from '@/components/ui/separator';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

import { COLD_START_HINT_MS } from './api-client';
import { Rail } from './shell';
import { AccountMenu } from './account';
import { SourceLabel, useSource } from './source';

const API = '/api';
const RENEWAL_WINDOW = 90;

export interface QueueRow {
  id: number;
  customer_id: string;
  name: string;
  segment: string;
  arr: number;
  risk_score: number;
  risk_level: 'Critical' | 'High' | 'Medium' | 'Low';
  at_risk: boolean;
  renewal_days: number | null;
  reason: string | null;
  reason_kind: 'adverse' | 'neutral' | 'good' | null;
  reason_date: string | null;
}

interface Queue {
  accounts: QueueRow[];
  total: number;
  at_risk: number;
  arr_at_risk: number;
  as_of: string;
}

export interface Signal {
  key: string;
  kind: 'adverse' | 'neutral' | 'good';
  text: string;
  brief: string;
  source: string;
  date: string | null;
}

export interface Play {
  kind?: 'outcome';
  action: string;
  cases: number;
  confidence: 'strong' | 'moderate' | 'limited';
  same_segment_cases?: number;
  median_adoption_gain?: number;
  median_support_reduction?: number;
  scope?: 'this challenge' | 'any challenge';
  renewed?: number;
  decided?: number;
  pending?: number;
  save_rate?: number;
}

export interface Story {
  id: number;
  customer_id: string;
  name: string;
  segment: string;
  industry: string;
  arr: number;
  tenure_years: number;
  crm_created_date: string | null;
  headline: string;
  risk: { score: number; band: QueueRow['risk_level']; at_risk: boolean; top_reason: string };
  renewal: { date: string | null; days: number | null } | null;
  signals: Signal[];
  plays: Play[];
  how_built: {
    factors: { factor: string; weight: number; adverse_fraction: number; contribution: number }[];
    as_of: string;
    history_weeks: number;
    at_risk_rule: string;
  };
  salesforce_url: string | null;
}

export function money(value: number): string {
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `$${(value / 1_000).toFixed(0)}K`;
  return `$${value.toFixed(0)}`;
}

/** Severity is the only thing on the page allowed to carry colour. */
export function severity(band: QueueRow['risk_level']) {
  return {
    Critical: 'text-sev-critical',
    High: 'text-sev-high',
    Medium: 'text-sev-medium',
    Low: 'text-sev-low',
  }[band];
}

export function formatDay(iso: string | null): string {
  if (!iso) return '';
  return new Date(`${iso}T00:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function renewalText(days: number | null): string | null {
  if (days === null) return null;
  if (days < 0) return `renewal ${-days}d ago`;
  return `renews in ${days}d`;
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="px-3 sm:px-4">
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-0.5 whitespace-nowrap text-base font-semibold tabular sm:text-lg">
        {value}
        {hint && <span className="ml-1.5 text-xs font-normal text-muted-foreground">{hint}</span>}
      </p>
    </div>
  );
}

export function Console() {
  const [queue, setQueue] = useState<Queue | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [story, setStory] = useState<Story | null>(null);
  const [query, setQuery] = useState('');
  const [soonOnly, setSoonOnly] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [waking, setWaking] = useState(false);
  // On a phone the queue is the page; opening a row shows the story instead.
  const [mobileOpen, setMobileOpen] = useState(false);
  const source = useSource();

  useEffect(() => {
    const hint = setTimeout(() => setWaking(true), COLD_START_HINT_MS);
    (async () => {
      try {
        const r = await fetch(`${API}/book/queue`, { cache: 'no-store' });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const q: Queue = await r.json();
        setQueue(q);
        setSelectedId(q.accounts[0]?.id ?? null);
        setLoadError(null);
      } catch (e) {
        setLoadError(e instanceof Error ? e.message : 'Could not reach the backend');
      } finally {
        clearTimeout(hint);
        setWaking(false);
        setLoading(false);
      }
    })();
    return () => clearTimeout(hint);
  }, []);

  useEffect(() => {
    if (selectedId === null) return;
    setStory(null);
    let cancelled = false;
    fetch(`${API}/customer/${selectedId}/story`, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((s: Story | null) => {
        if (!cancelled) setStory(s);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const rows = useMemo(() => queue?.accounts ?? [], [queue]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter(
      (c) =>
        (!q || c.name.toLowerCase().includes(q) || c.segment.toLowerCase().includes(q) ||
          (c.reason ?? '').toLowerCase().includes(q)) &&
        (!soonOnly || (c.renewal_days !== null && c.renewal_days >= 0 && c.renewal_days <= RENEWAL_WINDOW)),
    );
  }, [rows, query, soonOnly]);
  const soonCount = useMemo(
    () => rows.filter((c) => c.renewal_days !== null && c.renewal_days >= 0 && c.renewal_days <= RENEWAL_WINDOW).length,
    [rows],
  );

  const open = useCallback((id: number) => {
    setSelectedId(id);
    setMobileOpen(true);
  }, []);

  const selected = rows.find((c) => c.id === selectedId) ?? null;

  return (
    <div className="flex h-screen overflow-hidden">
      <Rail />

      {/* Queue */}
      <div
        className={cn(
          'flex min-w-0 flex-1 flex-col border-r md:w-[380px] md:flex-none',
          mobileOpen && 'hidden md:flex',
        )}
      >
        <div className="flex h-14 items-center gap-2 border-b px-3">
          <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter accounts"
            className="h-8 border-0 px-0 shadow-none focus-visible:ring-0"
          />
          <Badge variant="secondary" className="tabular shrink-0">
            {filtered.length}
          </Badge>
        </div>
        <div className="flex items-center gap-2 border-b px-3 py-2 text-xs">
          <button
            onClick={() => setSoonOnly(false)}
            className={cn('rounded-full border px-2.5 py-0.5', !soonOnly ? 'bg-accent' : 'text-muted-foreground')}
          >
            All active
          </button>
          <button
            onClick={() => setSoonOnly(true)}
            className={cn('rounded-full border px-2.5 py-0.5 tabular', soonOnly ? 'bg-accent' : 'text-muted-foreground')}
          >
            Renews in {RENEWAL_WINDOW} days · {soonCount}
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {loading && waking ? (
            <div className="px-4 py-8 text-sm">
              <p className="font-medium">Waking the server</p>
              <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                The backend sleeps when idle to keep this demo free to run. First
                load takes up to a minute; after that it is instant.
              </p>
            </div>
          ) : loading ? (
            Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="border-b px-4 py-3">
                <Skeleton className="h-4 w-40" />
                <Skeleton className="mt-2 h-3 w-56" />
              </div>
            ))
          ) : loadError ? (
            <div className="px-4 py-8 text-sm">
              <p className="font-medium">Backend unreachable</p>
              <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{loadError}</p>
              <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
                This site forwards <code>/api</code> to the backend named by
                <code>BACKEND_ORIGIN</code>. Either that backend is down, or the
                setting points somewhere unreachable. Locally it defaults to
                <code>http://localhost:8000</code>, so check the backend is
                running.
              </p>
            </div>
          ) : (
            filtered.map((c) => {
              const active = selectedId === c.id;
              const renewal = renewalText(c.renewal_days);
              return (
                <button
                  key={c.id}
                  onClick={() => open(c.id)}
                  className={cn(
                    'flex w-full items-start gap-3 border-b px-4 py-3 text-left transition-colors',
                    active ? 'bg-accent' : 'hover:bg-muted/60',
                  )}
                >
                  <span className={cn('w-7 shrink-0 text-lg font-semibold tabular', severity(c.risk_level))}>
                    {Math.round(c.risk_score)}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-baseline justify-between gap-2">
                      <span className="block min-w-0 truncate text-sm font-medium">{c.name}</span>
                      {c.at_risk && (
                        <Badge variant="outline" className="shrink-0 text-[10px] font-normal">
                          at risk
                        </Badge>
                      )}
                    </span>
                    <span className="mt-0.5 block truncate text-xs text-muted-foreground tabular">
                      {money(c.arr)} · {c.segment}
                      {renewal ? ` · ${renewal}` : ''}
                    </span>
                    {c.reason && (
                      <span
                        className={cn(
                          'mt-0.5 block truncate text-xs',
                          c.reason_kind === 'adverse' ? 'text-sev-medium' : 'text-muted-foreground',
                        )}
                      >
                        {c.reason}
                        {c.reason_date ? ` · ${formatDay(c.reason_date)}` : ''}
                      </span>
                    )}
                  </span>
                </button>
              );
            })
          )}
        </div>
      </div>

      {/* Story */}
      <div className={cn('flex min-w-0 flex-1 flex-col', !mobileOpen && 'hidden md:flex')}>
        <div className="flex min-h-14 flex-wrap items-center justify-between gap-y-1 border-b px-2 py-1 sm:px-5 sm:py-0">
          <div className="flex items-center divide-x">
            <button
              onClick={() => setMobileOpen(false)}
              className="mr-1 flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent md:hidden"
              aria-label="Back to the queue"
            >
              <ArrowLeft className="h-4 w-4" />
            </button>
            <Stat label="At risk" value={String(queue?.at_risk ?? 0)} hint="top 20%" />
            <Stat label="ARR at risk" value={queue ? money(queue.arr_at_risk) : '$0'} />
            <Stat label="Active" value={String(queue?.total ?? 0)} />
          </div>
          <div className="flex min-w-0 max-w-full items-center gap-3">
            <div className="min-w-0 truncate">
              <SourceLabel />
            </div>
            <Button variant="outline" size="sm" asChild className="hidden sm:inline-flex">
              <a href="/ask">
                <MessageSquare className="mr-2 h-4 w-4" />
                Ask
              </a>
            </Button>
            <AccountMenu />
          </div>
        </div>

        {selected ? (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto max-w-3xl px-4 py-6 sm:px-8 sm:py-8">
              {story ? (
                <StoryView story={story} synthetic={!!source?.synthetic} />
              ) : (
                <div>
                  <Skeleton className="h-7 w-3/4" />
                  <Skeleton className="mt-3 h-4 w-full" />
                  <Skeleton className="mt-2 h-4 w-5/6" />
                  <Skeleton className="mt-8 h-4 w-1/2" />
                  <Skeleton className="mt-2 h-4 w-2/3" />
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="flex flex-1 items-center justify-center">
            <p className="text-sm text-muted-foreground">Select an account</p>
          </div>
        )}
      </div>
    </div>
  );
}

const SIGNAL_ICON = {
  adverse: <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-sev-medium" aria-hidden="true" />,
  neutral: <Circle className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground/60" aria-hidden="true" />,
  good: <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" aria-hidden="true" />,
};

/** The whole story, shared by the console pane and the customer page. */
export function StoryView({ story, synthetic, full = false }: { story: Story; synthetic: boolean; full?: boolean }) {
  const [showMaths, setShowMaths] = useState(false);
  const brief = () =>
    [
      story.headline,
      ...story.signals.filter((s) => s.kind !== 'neutral').map((s) => `- ${s.text} (${s.source}${s.date ? `, ${s.date}` : ''})`),
      story.plays[0] ? `Try: ${story.plays[0].action}` : '',
      story.salesforce_url ?? '',
    ]
      .filter(Boolean)
      .join('\n');

  return (
    <div>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold leading-snug tracking-tight sm:text-2xl">{story.headline}</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            {story.segment} · {story.industry} · {story.tenure_years}y customer
            {story.crm_created_date ? ` · in Salesforce since ${formatDay(story.crm_created_date)}, ${story.crm_created_date.slice(0, 4)}` : ''}
          </p>
        </div>
        <div className="shrink-0 text-right">
          <p className={cn('text-3xl font-semibold tabular sm:text-4xl', severity(story.risk.band))}>
            {Math.round(story.risk.score)}
          </p>
          <Badge variant="outline" className="mt-1">
            {story.risk.band}
            {story.risk.at_risk ? ' · at risk' : ''}
          </Badge>
        </div>
      </div>

      <Separator className="my-5" />

      <h2 className="text-sm font-semibold">Signals</h2>
      <ul className="mt-2 divide-y">
        {story.signals.map((s, i) => (
          <li key={`${s.key}-${i}`} className="flex items-start gap-3 py-2 text-sm">
            {SIGNAL_ICON[s.kind]}
            <span className={cn('min-w-0 flex-1', s.kind === 'neutral' && 'text-muted-foreground')}>{s.text}</span>
            <span className="shrink-0 text-xs text-muted-foreground">
              {s.source}
              {s.date ? ` · ${formatDay(s.date)}` : ''}
            </span>
          </li>
        ))}
      </ul>
      {story.how_built.history_weeks < 4 && (
        <p className="mt-2 text-xs text-muted-foreground">
          The org holds {story.how_built.history_weeks} week{story.how_built.history_weeks === 1 ? '' : 's'} of usage,
          so no usage trend is shown and the score cannot read above the medium band.
        </p>
      )}

      <Separator className="my-5" />

      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold">What to do</h2>
        <span className="text-xs text-muted-foreground">
          what worked on accounts with {story.risk.top_reason.toLowerCase()}
        </span>
      </div>
      {story.plays.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">
          {synthetic
            ? 'No resolved case has enough evidence behind it to recommend. Better to say so than to invent a play.'
            : 'No recorded outcomes in this org yet. Plays appear once interventions and their results are recorded.'}
        </p>
      ) : (
        <div className="mt-3 space-y-2">
          {story.plays.map((p) => (
            <div key={p.action} className="rounded-md border p-3">
              <div className="flex items-start justify-between gap-4">
                <span className="text-sm font-medium">{p.action}</span>
                <Badge variant={p.confidence === 'strong' ? 'default' : 'outline'} className="shrink-0 text-[11px] capitalize">
                  {p.confidence}
                </Badge>
              </div>
              {p.kind === 'outcome' ? (
                <p className="mt-1.5 text-xs text-muted-foreground">
                  Renewed <span className="tabular font-medium text-foreground">{p.renewed} of {p.decided}</span>{' '}
                  ({Math.round((p.save_rate ?? 0) * 100)}%) after this play
                  {p.scope === 'any challenge' ? ', across all challenges' : ', on this challenge'}
                  {p.pending ? ` · ${p.pending} renewal${p.pending === 1 ? '' : 's'} not decided yet` : ''}
                </p>
              ) : (
                <p className="mt-1.5 text-xs text-muted-foreground">
                  {p.cases} comparable {p.cases === 1 ? 'case' : 'cases'}
                  {(p.same_segment_cases ?? 0) > 0 && `, ${p.same_segment_cases} in ${story.segment}`}
                  {' · '}adoption <span className="tabular">+{p.median_adoption_gain}</span> pts
                  {' · '}support <span className="tabular">−{p.median_support_reduction}%</span>
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      <Separator className="my-5" />

      <div className="flex flex-wrap gap-2">
        {!full && (
          <Button asChild size="sm">
            <a href={`/customer/${story.id}`}>Full history</a>
          </Button>
        )}
        <Button variant="outline" size="sm" onClick={() => navigator.clipboard.writeText(brief())}>
          Copy brief
        </Button>
        {story.salesforce_url && (
          <Button variant="outline" size="sm" asChild>
            <a href={story.salesforce_url} target="_blank" rel="noreferrer">
              Open in Salesforce
              <ExternalLink className="ml-2 h-3.5 w-3.5" />
            </a>
          </Button>
        )}
        <Button variant="ghost" size="sm" onClick={() => setShowMaths((v) => !v)} aria-expanded={showMaths}>
          How this score is built
          <ChevronDown className={cn('ml-1.5 h-3.5 w-3.5 transition-transform', showMaths && 'rotate-180')} />
        </Button>
      </div>

      {showMaths && (
        <div className="mt-4 rounded-md border p-3 text-xs">
          <p className="text-muted-foreground">
            Five signals, each scored 0 to 100% adverse and weighted. The score is the weighted sum; the
            top reason is the largest contribution. As of {story.how_built.as_of}, {story.how_built.history_weeks} week
            {story.how_built.history_weeks === 1 ? '' : 's'} of usage. &ldquo;At risk&rdquo; is {story.how_built.at_risk_rule}.
          </p>
          <table className="mt-2 w-full">
            <tbody>
              {story.how_built.factors.map((f) => (
                <tr key={f.factor} className="border-t">
                  <td className="py-1 pr-2">{f.factor}</td>
                  <td className="py-1 pr-2 text-right tabular text-muted-foreground">
                    {Math.round(f.weight * 100)}% × {Math.round(f.adverse_fraction * 100)}%
                  </td>
                  <td className="py-1 text-right tabular font-medium">{f.contribution.toFixed(1)}</td>
                </tr>
              ))}
              <tr className="border-t">
                <td className="py-1 pr-2 font-medium">Score</td>
                <td />
                <td className="py-1 text-right tabular font-medium">{story.risk.score.toFixed(1)}</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

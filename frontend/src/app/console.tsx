'use client';

/**
 * The at-risk console.
 *
 * Three panes: a rail, a queue, and the selected account. Selecting a row swaps
 * the right pane rather than navigating, because triage is a loop -- scan, open,
 * judge, next -- and a page transition per account breaks it.
 *
 * The previous layout was a centred column under a large heading, which is a
 * landing-page shape: it spent roughly 600px before the first account and made
 * you leave the screen to see any detail.
 */

import { useEffect, useMemo, useState } from 'react';
import {
  Building2,
  MessageSquare,
  Search,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

import { apiClient, type AtRiskCustomer, type DashboardStats } from './api-client';

const API = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';

interface Likelihood {
  band: 'Very high' | 'High' | 'Moderate' | 'Low';
  lift: number;
  probability: number;
  horizon_weeks: number;
}

interface EvidenceItem { text: string; source: string; doc_id: string }

interface Play {
  action: string;
  cases: number;
  same_segment_cases: number;
  median_adoption_gain: number;
  median_support_reduction: number;
  confidence: 'strong' | 'moderate' | 'limited';
}
import { Rail } from './shell';

function money(value: number): string {
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `$${(value / 1_000).toFixed(0)}K`;
  return `$${value.toFixed(0)}`;
}

/** Severity is the only thing on the page allowed to carry colour. */
function severity(score: number) {
  if (score >= 80) return { label: 'Critical', cls: 'text-sev-critical' };
  if (score >= 60) return { label: 'High', cls: 'text-sev-high' };
  if (score >= 40) return { label: 'Medium', cls: 'text-sev-medium' };
  return { label: 'Low', cls: 'text-sev-low' };
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="px-4">
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className={cn('mt-0.5 text-lg font-semibold tabular', tone)}>{value}</p>
    </div>
  );
}

export function Console() {
  const [customers, setCustomers] = useState<AtRiskCustomer[]>([]);
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [selected, setSelected] = useState<AtRiskCustomer | null>(null);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [plays, setPlays] = useState<Play[] | null>(null);
  const [likelihood, setLikelihood] = useState<Likelihood | null>(null);
  const [evidence, setEvidence] = useState<EvidenceItem[] | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [c, s] = await Promise.all([
          apiClient.getAtRiskCustomers(0, 50),
          apiClient.getDashboardStats(),
        ]);
        setCustomers(c.at_risk_customers);
        setStats(s);
        setSelected(c.at_risk_customers[0] ?? null);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  // Recommendations are computed from recorded outcomes, so this is a cheap
  // lookup rather than a model call -- it works with the LLM stack disabled.
  useEffect(() => {
    if (!selected) return;
    setPlays(null);
    fetch(`${API}/customer/${selected.id}/plays`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((d) => setPlays(d.plays ?? []))
      .catch(() => setPlays([]));

    setEvidence(null);
    fetch(`${API}/customer/${selected.id}/evidence`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((d) => setEvidence(d.evidence ?? []))
      .catch(() => setEvidence([]));

    setLikelihood(null);
    fetch(`${API}/customer/${selected.id}/likelihood`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setLikelihood)
      .catch(() => setLikelihood(null));
  }, [selected]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return customers;
    return customers.filter(
      (c) => c.name.toLowerCase().includes(q) || c.segment.toLowerCase().includes(q)
    );
  }, [customers, query]);

  return (
    <div className="flex h-screen overflow-hidden">
      <Rail />

      {/* Queue */}
      <div className="flex w-[380px] shrink-0 flex-col border-r">
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

        <ScrollArea className="flex-1">
          {loading
            ? Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="border-b px-4 py-3">
                  <Skeleton className="h-4 w-40" />
                  <Skeleton className="mt-2 h-3 w-56" />
                </div>
              ))
            : filtered.map((c) => {
                const sev = severity(c.risk_score);
                const active = selected?.id === c.id;
                return (
                  <button
                    key={c.id}
                    onClick={() => setSelected(c)}
                    className={cn(
                      'flex w-full items-start gap-3 border-b px-4 py-3 text-left transition-colors',
                      active ? 'bg-accent' : 'hover:bg-muted/60'
                    )}
                  >
                    <span className={cn('w-7 shrink-0 text-lg font-semibold tabular', sev.cls)}>
                      {Math.round(c.risk_score)}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">{c.name}</span>
                      <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                        {c.segment} · {money(c.arr)} · {c.risk_reason}
                      </span>
                    </span>
                  </button>
                );
              })}
        </ScrollArea>
      </div>

      {/* Detail */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-14 items-center justify-between border-b px-5">
          <div className="flex items-center divide-x">
            <Stat label="At risk" value={String(stats?.total_at_risk ?? 0)} />
            <Stat label="ARR at risk" value={stats ? money(stats.total_arr_at_risk) : '$0'} />
            <Stat
              label="Churn rate"
              value={stats ? `${(stats.historical_churn_rate * 100).toFixed(1)}%` : '—'}
            />
            <Stat label="Median warning" value={stats ? `${Math.round(stats.avg_days_to_churn)}d` : '—'} />
          </div>
          <Button variant="outline" size="sm" asChild>
            <a href="/ask">
              <MessageSquare className="mr-2 h-4 w-4" />
              Ask
            </a>
          </Button>
        </div>

        {selected ? (
          <ScrollArea className="flex-1">
            <div className="mx-auto max-w-3xl px-8 py-8">
              <div className="flex items-start justify-between gap-6">
                <div className="min-w-0">
                  <h1 className="truncate text-2xl font-semibold tracking-tight">{selected.name}</h1>
                  <p className="mt-1 flex items-center gap-2 text-sm text-muted-foreground">
                    <Building2 className="h-3.5 w-3.5" />
                    {selected.segment} · {money(selected.arr)} ARR · {selected.tenure_years}y tenure
                  </p>
                </div>
                <div className="shrink-0 text-right">
                  <p className={cn('text-4xl font-semibold tabular', severity(selected.risk_score).cls)}>
                    {Math.round(selected.risk_score)}
                  </p>
                  <Badge variant="outline" className="mt-1">
                    {severity(selected.risk_score).label}
                  </Badge>
                </div>
              </div>

              <Separator className="my-6" />

              <dl className="grid grid-cols-2 gap-x-8 gap-y-5 sm:grid-cols-4">
                {[
                  [
                    'Churn likelihood, 1 quarter',
                    likelihood
                      ? `${likelihood.band}${likelihood.lift >= 1 ? ` · ${likelihood.lift.toFixed(1)}× average` : ''}`
                      : '—',
                  ],
                  ['Primary driver', selected.risk_reason],
                  ['Adoption', `${Math.round(selected.feature_adoption_rate * 100)}%`],
                  ['Tickets / 30d', String(selected.support_tickets_30d)],
                  ['Last contact', `${selected.last_engagement_days}d ago`],
                  ['Trend', selected.trend],
                  ['Competitor', selected.competitor || 'None recorded'],
                  ['Segment', selected.segment],
                ].map(([k, v]) => (
                  <div key={k}>
                    <dt className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                      {k}
                    </dt>
                    <dd className="mt-1 text-sm">{v}</dd>
                  </div>
                ))}
              </dl>

              <Separator className="my-6" />

              {/* Why the score is what it is, quoted from this account's own
                  record. Selected by key rather than similarity, so another
                  customer's story cannot appear here. */}
              <div className="mb-6">
                <h2 className="text-sm font-semibold">Why</h2>
                {evidence === null ? (
                  <p className="mt-2 text-sm text-muted-foreground">Reading the record…</p>
                ) : evidence.length === 0 ? (
                  <p className="mt-2 text-sm text-muted-foreground">
                    Nothing in this account&rsquo;s record speaks to {selected.risk_reason.toLowerCase()}.
                  </p>
                ) : (
                  <div className="mt-2 space-y-2">
                    {evidence.map((e) => (
                      <div key={e.doc_id} className="border-l-2 pl-3">
                        <p className="text-sm leading-relaxed text-muted-foreground">{e.text}</p>
                        <p className="mt-1 text-[11px] text-muted-foreground/70">
                          {e.source} · {e.doc_id}
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* The point of the whole screen. "Who is at risk" is a report;
                  "what has worked on accounts like this" is the job. Every play
                  carries its case count so it can be argued with rather than
                  taken on trust. */}
              <div>
                <div className="flex items-baseline justify-between">
                  <h2 className="text-sm font-semibold">What has worked</h2>
                  <span className="text-xs text-muted-foreground">
                    on accounts facing {selected.risk_reason.toLowerCase()}
                  </span>
                </div>

                {plays === null ? (
                  <p className="mt-3 text-sm text-muted-foreground">Looking for comparable cases…</p>
                ) : plays.length === 0 ? (
                  <p className="mt-3 text-sm text-muted-foreground">
                    No resolved case has enough evidence behind it to recommend. Better
                    to say so than to invent a play.
                  </p>
                ) : (
                  <div className="mt-3 space-y-2">
                    {plays.map((p) => (
                      <div key={p.action} className="rounded-md border p-3">
                        <div className="flex items-start justify-between gap-4">
                          <span className="text-sm font-medium">{p.action}</span>
                          <Badge
                            variant={p.confidence === 'strong' ? 'default' : 'outline'}
                            className="shrink-0 text-[11px] capitalize"
                          >
                            {p.confidence}
                          </Badge>
                        </div>
                        <p className="mt-1.5 text-xs text-muted-foreground">
                          {p.cases} comparable {p.cases === 1 ? 'case' : 'cases'}
                          {p.same_segment_cases > 0 && `, ${p.same_segment_cases} in ${selected.segment}`}
                          {' · '}adoption <span className="tabular">+{p.median_adoption_gain}</span> pts
                          {' · '}support <span className="tabular">−{p.median_support_reduction}%</span>
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <Separator className="my-6" />

              <div className="flex gap-2">
                <Button asChild size="sm">
                  <a href={`/customer/${selected.id}`}>Full history</a>
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    navigator.clipboard.writeText(
                      `Follow up with ${selected.name}\nRisk ${Math.round(selected.risk_score)}${
                        likelihood
                          ? ` · ${likelihood.band} likelihood this quarter, ${likelihood.lift.toFixed(1)}× book average`
                          : ''
                      }\nDriver: ${selected.risk_reason}`
                    )
                  }
                >
                  Copy task
                </Button>
              </div>

              <p className="mt-8 text-xs leading-relaxed text-muted-foreground">
                Likelihood is a band and a lift against the book average over one
                quarter, not a date. The survival model ranks accounts well but its
                median-survival date is unusable, and its absolute probability runs
                about 2&times; low, so a lift is reported instead &mdash; it is
                unaffected by that bias. Roughly 40% of accounts sit at the low end
                of the calibrator and carry no ordering between them. See ADR-0009.
              </p>
            </div>
          </ScrollArea>
        ) : (
          <div className="flex flex-1 items-center justify-center">
            <p className="text-sm text-muted-foreground">Select an account</p>
          </div>
        )}
      </div>
    </div>
  );
}

'use client';

/**
 * Why customers leave (8.5, ADR-0015 section 4).
 *
 * The question a VP of customer success asks first, answered from the accounts
 * that actually churned: what caused it, how much it cost, who they went to.
 * Then where the risk sits now in the active book, by the first adverse signal
 * on each account, and what has worked. The previous page drew a donut of the
 * scorer's bands (27 Medium, 105 Low), which is not information.
 *
 * Everything on this page is a count, a sum or a date from the served snapshot
 * (/book/causes and /book/queue). No chart library: a bar is a div, so it reads
 * the same in both themes and at phone width.
 */

import { useEffect, useMemo, useState } from 'react';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

import { money, type QueueRow } from '../console';
import { BootGate, Shell } from '../shell';

const API = '/api';

interface Causes {
  as_of: string;
  churned: {
    accounts: number;
    arr_lost: number;
    by_cause: { cause: string; accounts: number; arr_lost: number }[];
    competitors: { name: string; accounts: number }[];
  };
  active: {
    accounts: number;
    arr: number;
    by_signal: { signal: string; accounts: number; arr: number }[];
    renewing_soon: { days: number; accounts: number; arr: number };
  };
  plays: { action: string; renewed: number; decided: number; pending: number }[];
}

function Bars({
  rows,
  label,
  valueLabel,
}: {
  rows: { name: string; count: number; value: number; hint?: string }[];
  label: string;
  valueLabel: string;
}) {
  const max = Math.max(1, ...rows.map((r) => r.count));
  return (
    <div>
      <div className="mb-1 flex justify-between text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        <span>{label}</span>
        <span>
          Accounts · {valueLabel}
        </span>
      </div>
      <ul className="space-y-2">
        {rows.map((r) => (
          <li key={r.name} className="text-sm">
            <div className="flex items-baseline justify-between gap-3">
              <span className="min-w-0 truncate">
                {r.name}
                {r.hint && <span className="ml-2 text-xs text-muted-foreground">{r.hint}</span>}
              </span>
              <span className="shrink-0 tabular text-muted-foreground">
                <span className="font-medium text-foreground">{r.count}</span> · {money(r.value)}
              </span>
            </div>
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full bg-foreground/70" style={{ width: `${(r.count / max) * 100}%` }} />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card>
      <CardContent className="p-4">
        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
        <p className="mt-1 truncate text-lg font-semibold tabular">{value}</p>
        {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
      </CardContent>
    </Card>
  );
}

function WhyPage() {
  const [causes, setCauses] = useState<Causes | null>(null);
  const [queue, setQueue] = useState<QueueRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [c, q] = await Promise.all([
          fetch(`${API}/book/causes`, { cache: 'no-store' }),
          fetch(`${API}/book/queue`, { cache: 'no-store' }),
        ]);
        if (!c.ok || !q.ok) throw new Error(`HTTP ${c.ok ? q.status : c.status}`);
        setCauses(await c.json());
        setQueue((await q.json()).accounts);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Could not load');
      }
    })();
  }, []);

  const bySegment = useMemo(() => {
    const acc = new Map<string, { segment: string; arr: number; count: number; risk: number; atRisk: number }>();
    for (const c of queue) {
      const e = acc.get(c.segment) ?? { segment: c.segment, arr: 0, count: 0, risk: 0, atRisk: 0 };
      e.arr += c.arr;
      e.count += 1;
      e.risk += c.risk_score;
      e.atRisk += c.at_risk ? 1 : 0;
      acc.set(c.segment, e);
    }
    return [...acc.values()].sort((a, b) => b.arr - a.arr);
  }, [queue]);

  if (error) {
    return (
      <Shell title="Why customers leave" description="Could not load">
        <p className="p-6 text-sm text-muted-foreground">{error}</p>
      </Shell>
    );
  }
  if (!causes) {
    return (
      <Shell title="Why customers leave" description="Loading">
        <div className="grid gap-4 p-4 sm:p-6 lg:grid-cols-2">
          <Skeleton className="h-64" />
          <Skeleton className="h-64" />
        </div>
      </Shell>
    );
  }

  const top = causes.churned.by_cause[0];
  const bestPlay = causes.plays[0];
  const soon = causes.active.renewing_soon;

  return (
    <Shell
      title="Why customers leave"
      description={`${causes.churned.accounts} churned, ${causes.active.accounts} active · as of ${causes.as_of}`}
    >
      <div className="mx-auto max-w-6xl space-y-4 p-4 sm:space-y-6 sm:p-6">
        <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
          <Metric label="Churned" value={String(causes.churned.accounts)} hint={`${money(causes.churned.arr_lost)} ARR lost`} />
          <Metric label="Top cause" value={top?.cause ?? '—'} hint={top ? `${top.accounts} accounts` : undefined} />
          <Metric label={`Renew in ${soon.days} days`} value={String(soon.accounts)} hint={`${money(soon.arr)} ARR`} />
          <Metric
            label="Best play"
            value={bestPlay ? `${bestPlay.renewed} of ${bestPlay.decided}` : '—'}
            hint={bestPlay ? `renewed after "${bestPlay.action}"` : 'no outcomes recorded'}
          />
        </div>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-semibold">What caused the {causes.churned.accounts} churns</CardTitle>
            <p className="text-xs text-muted-foreground">
              The churn category on each churned account in Salesforce, and the ARR it took with it
            </p>
          </CardHeader>
          <CardContent>
            <Bars
              label="Cause"
              valueLabel="ARR lost"
              rows={causes.churned.by_cause.map((c) => ({ name: c.cause, count: c.accounts, value: c.arr_lost }))}
            />
            {causes.churned.competitors.length > 0 && (
              <p className="mt-4 text-xs text-muted-foreground">
                Competitors named:{' '}
                {causes.churned.competitors.map((c) => `${c.name} ${c.accounts}`).join(' · ')}
              </p>
            )}
          </CardContent>
        </Card>

        <div className="grid gap-4 sm:gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-semibold">Where the risk is now</CardTitle>
              <p className="text-xs text-muted-foreground">
                The {causes.active.accounts} active accounts by the first adverse signal on each
              </p>
            </CardHeader>
            <CardContent>
              <Bars
                label="Signal"
                valueLabel="ARR"
                rows={causes.active.by_signal.map((s) => ({ name: s.signal, count: s.accounts, value: s.arr }))}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-semibold">What has worked</CardTitle>
              <p className="text-xs text-muted-foreground">
                Save plays recorded in Salesforce and the renewal after each, failures counted
              </p>
            </CardHeader>
            <CardContent className="px-0">
              {causes.plays.length === 0 ? (
                <p className="px-6 text-sm text-muted-foreground">No play outcomes recorded on this source.</p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="pl-6">Play</TableHead>
                      <TableHead className="text-right">Renewed</TableHead>
                      <TableHead className="pr-6 text-right">Pending</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {causes.plays.map((p) => (
                      <TableRow key={p.action}>
                        <TableCell className="pl-6">{p.action}</TableCell>
                        <TableCell className="text-right tabular">
                          <span className="font-medium">{p.renewed} of {p.decided}</span>
                          <span className="ml-1 text-xs text-muted-foreground">
                            ({p.decided ? Math.round((p.renewed / p.decided) * 100) : 0}%)
                          </span>
                        </TableCell>
                        <TableCell className="pr-6 text-right tabular text-muted-foreground">{p.pending || '—'}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold">By segment</CardTitle>
          </CardHeader>
          <CardContent className="px-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-6">Segment</TableHead>
                  <TableHead className="text-right">Active</TableHead>
                  <TableHead className="text-right">At risk</TableHead>
                  <TableHead className="text-right">Mean score</TableHead>
                  <TableHead className="pr-6 text-right">ARR</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {bySegment.map((s) => (
                  <TableRow key={s.segment}>
                    <TableCell className="pl-6 font-medium">{s.segment}</TableCell>
                    <TableCell className="text-right tabular">{s.count}</TableCell>
                    <TableCell className="text-right tabular">{s.atRisk}</TableCell>
                    <TableCell className="text-right tabular">{(s.risk / s.count).toFixed(1)}</TableCell>
                    <TableCell className="pr-6 text-right tabular">{money(s.arr)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
    </Shell>
  );
}

export default function Page() {
  return (
    <BootGate>
      <WhyPage />
    </BootGate>
  );
}

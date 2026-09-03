'use client';

/**
 * Customer detail.
 *
 * The engagement timeline is the argument this page makes -- an account declining
 * from 0.7 to near zero over eighteen months is more convincing than any score --
 * so it gets the width and everything else supports it.
 *
 * Contributing factors are shown as weight x adverse fraction rather than a
 * single number, because a customer success lead should be able to see which
 * signal drove the score and disagree with the weighting.
 */

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { ArrowLeft } from 'lucide-react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { cn } from '@/lib/utils';

import { Shell } from '../../shell';

const API = '/api';

function money(v: number) {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

function sevClass(score: number) {
  if (score >= 80) return 'text-sev-critical';
  if (score >= 60) return 'text-sev-high';
  if (score >= 40) return 'text-sev-medium';
  return 'text-sev-low';
}

interface Detail {
  customer: Record<string, any>;
  analysis: {
    engagement_history: { date: string; engagement_score: number; feature_adoption_rate: number }[];
    support_tickets: Record<string, any>[];
    feature_usage: { feature: string; usage_rate: number }[];
    interactions: { date: string; type: string; content: string }[];
    predictions: {
      churn_probability: number;
      days_until_churn: number;
      contributing_factors: {
        factor: string;
        weight: number;
        adverse_fraction: number;
        contribution: number;
      }[];
    };
  };
  health_indicators: Record<string, string>;
}

export default function CustomerPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [data, setData] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/customer/${id}/detailed-analysis`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setData)
      .catch((e) => setError(String(e.message ?? e)));
  }, [id]);

  if (error) {
    return (
      <Shell title="Customer" description={`Could not load account ${id}`}>
        <div className="mx-auto max-w-md px-6 py-24 text-center">
          <p className="text-sm text-muted-foreground">{error}</p>
          <Button variant="outline" size="sm" className="mt-4" onClick={() => router.push('/')}>
            Back to queue
          </Button>
        </div>
      </Shell>
    );
  }

  if (!data) {
    return (
      <Shell title="Customer" description="Loading">
        <div className="space-y-4 p-6">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-64 w-full" />
        </div>
      </Shell>
    );
  }

  const { customer: c, analysis: a, health_indicators: hi } = data;
  const factors = [...a.predictions.contributing_factors].sort(
    (x, y) => y.contribution - x.contribution
  );

  return (
    <Shell
      title={c.name}
      description={`${c.segment} · ${money(c.arr)} ARR · ${c.tenure_years}y tenure · ${c.industry}`}
      actions={
        <Button variant="ghost" size="sm" onClick={() => router.push('/')}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          Queue
        </Button>
      }
    >
      <div className="mx-auto max-w-6xl space-y-6 p-6">
        {/* Score and the indicators that produced it */}
        <div className="grid gap-4 md:grid-cols-[220px_minmax(0,1fr)]">
          <Card>
            <CardContent className="flex flex-col items-center justify-center py-6">
              <p className={cn('text-5xl font-semibold tabular', sevClass(c.risk_score))}>
                {Math.round(c.risk_score)}
              </p>
              <Badge variant="outline" className="mt-2">
                {c.risk_level}
              </Badge>
              <p className="mt-3 text-center text-xs text-muted-foreground">
                ~{a.predictions.days_until_churn} days · heuristic, not modelled
              </p>
            </CardContent>
          </Card>

          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            {Object.entries(hi).map(([k, v]) => (
              <Card key={k}>
                <CardContent className="p-4">
                  <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    {k.replace(/_/g, ' ')}
                  </p>
                  <p
                    className={cn(
                      'mt-1 text-lg font-semibold',
                      /poor|weak|at risk/i.test(v) && 'text-sev-critical'
                    )}
                  >
                    {v}
                  </p>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>

        {/* The argument */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold">Engagement</CardTitle>
            <p className="text-xs text-muted-foreground">
              {a.engagement_history.length} weekly observations
            </p>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={260}>
              <AreaChart data={a.engagement_history} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
                <defs>
                  <linearGradient id="eng" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="hsl(var(--chart-1))" stopOpacity={0.35} />
                    <stop offset="100%" stopColor="hsl(var(--chart-1))" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="hsl(var(--chart-grid))" vertical={false} />
                <XAxis
                  dataKey="date"
                  stroke="hsl(var(--chart-axis))"
                  tick={{ fontSize: 11 }}
                  tickLine={false}
                  minTickGap={48}
                />
                <YAxis
                  domain={[0, 1]}
                  stroke="hsl(var(--chart-axis))"
                  tick={{ fontSize: 11 }}
                  tickLine={false}
                  axisLine={false}
                />
                <RTooltip
                  contentStyle={{
                    background: 'hsl(var(--popover))',
                    border: '1px solid hsl(var(--border))',
                    borderRadius: 8,
                    fontSize: 12,
                    color: 'hsl(var(--popover-foreground))',
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="engagement_score"
                  stroke="hsl(var(--chart-1))"
                  strokeWidth={2}
                  fill="url(#eng)"
                  isAnimationActive={false}
                  name="Engagement"
                />
              </AreaChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <div className="grid gap-6 lg:grid-cols-2">
          {/* How the score was reached */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold">Why this score</CardTitle>
              <p className="text-xs text-muted-foreground">
                Weight applied to how adverse each signal is
              </p>
            </CardHeader>
            <CardContent className="space-y-3">
              {factors.map((f) => (
                <div key={f.factor}>
                  <div className="flex items-baseline justify-between text-sm">
                    <span>{f.factor}</span>
                    <span className="tabular text-muted-foreground">
                      {(f.weight * 100).toFixed(0)}% × {(f.adverse_fraction * 100).toFixed(0)}% ={' '}
                      <span className="font-medium text-foreground">
                        {f.contribution.toFixed(1)}
                      </span>
                    </span>
                  </div>
                  <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-foreground/70"
                      style={{ width: `${Math.min(100, (f.contribution / 35) * 100)}%` }}
                    />
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>

          {/* Support history */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold">Support</CardTitle>
              <p className="text-xs text-muted-foreground">
                {a.support_tickets.length} tickets on record
              </p>
            </CardHeader>
            <CardContent className="px-0">
              <div className="max-h-[260px] overflow-y-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="pl-6">Date</TableHead>
                      <TableHead>Issue</TableHead>
                      <TableHead>Severity</TableHead>
                      <TableHead className="pr-6 text-right">CSAT</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {a.support_tickets.slice(0, 12).map((t) => (
                      <TableRow key={t.ticket_id}>
                        <TableCell className="pl-6 tabular text-muted-foreground">{t.date}</TableCell>
                        <TableCell className="max-w-[180px] truncate">{t.type}</TableCell>
                        <TableCell>
                          <Badge
                            variant={
                              ['High', 'Critical'].includes(t.severity) ? 'destructive' : 'secondary'
                            }
                          >
                            {t.severity}
                          </Badge>
                        </TableCell>
                        <TableCell className="pr-6 text-right tabular">{t.csat_score}/5</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Recent contact */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold">Recent contact</CardTitle>
          </CardHeader>
          <CardContent className="space-y-0">
            {a.interactions.slice(0, 6).map((i, n) => (
              <div key={n}>
                {n > 0 && <Separator />}
                <div className="flex gap-4 py-3">
                  <span className="w-24 shrink-0 tabular text-xs text-muted-foreground">{i.date}</span>
                  <Badge variant="outline" className="h-5 shrink-0 text-[11px]">
                    {i.type}
                  </Badge>
                  <span className="min-w-0 flex-1 text-sm text-muted-foreground">{i.content}</span>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>

        <p className="pb-2 text-xs leading-relaxed text-muted-foreground">
          Feature usage is spread deterministically around the observed adoption rate;
          the dataset records one overall figure rather than per-feature telemetry.
          The predicted horizon comes from the heuristic score and carries no
          confidence interval until the survival model is calibrated.
        </p>
      </div>
    </Shell>
  );
}

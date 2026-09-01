'use client';

/**
 * Portfolio analytics.
 *
 * Deliberately answers questions the queue cannot: where risk concentrates, what
 * drives it, and how much revenue sits behind each answer. Charts read their
 * colours from CSS variables so they follow the theme rather than holding fixed
 * hex values -- which is how a series ends up invisible after a theme change.
 */

import { useEffect, useMemo, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from 'recharts';

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

import { apiClient, type AtRiskCustomer, type DashboardStats } from '../api-client';
import { Shell } from '../shell';

function money(v: number) {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

const TOOLTIP = {
  background: 'hsl(var(--popover))',
  border: '1px solid hsl(var(--border))',
  borderRadius: 8,
  fontSize: 12,
  color: 'hsl(var(--popover-foreground))',
};

// Severity, not a categorical palette. The bands are ordered, so the colours are
// a ramp rather than four unrelated hues.
const BANDS = [
  { name: 'Critical', min: 80, fill: 'hsl(var(--sev-critical))' },
  { name: 'High', min: 60, fill: 'hsl(var(--sev-high))' },
  { name: 'Medium', min: 40, fill: 'hsl(var(--sev-medium))' },
  { name: 'Low', min: 0, fill: 'hsl(var(--sev-low))' },
];

export default function AnalyticsPage() {
  const [customers, setCustomers] = useState<AtRiskCustomer[]>([]);
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const [c, s] = await Promise.all([
          apiClient.getAtRiskCustomers(0, 200),
          apiClient.getDashboardStats(),
        ]);
        setCustomers(c.at_risk_customers);
        setStats(s);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const distribution = useMemo(
    () =>
      BANDS.map((b, i) => ({
        ...b,
        value: customers.filter(
          (c) => c.risk_score >= b.min && (i === 0 || c.risk_score < BANDS[i - 1].min)
        ).length,
      })).filter((b) => b.value > 0),
    [customers]
  );

  const bySegment = useMemo(() => {
    const acc = new Map<string, { segment: string; arr: number; count: number; risk: number }>();
    for (const c of customers) {
      const e = acc.get(c.segment) ?? { segment: c.segment, arr: 0, count: 0, risk: 0 };
      e.arr += c.arr;
      e.count += 1;
      e.risk += c.risk_score;
      acc.set(c.segment, e);
    }
    return [...acc.values()]
      .map((e) => ({ ...e, avgRisk: e.risk / e.count }))
      .sort((a, b) => b.arr - a.arr);
  }, [customers]);

  const byReason = useMemo(() => {
    const acc = new Map<string, { reason: string; count: number; arr: number }>();
    for (const c of customers) {
      const e = acc.get(c.risk_reason) ?? { reason: c.risk_reason, count: 0, arr: 0 };
      e.count += 1;
      e.arr += c.arr;
      acc.set(c.risk_reason, e);
    }
    return [...acc.values()].sort((a, b) => b.arr - a.arr);
  }, [customers]);

  return (
    <Shell
      title="Analytics"
      description={
        stats
          ? `${stats.total_active_customers} active accounts · as of ${stats.as_of}`
          : 'Loading'
      }
    >
      <div className="mx-auto max-w-6xl space-y-6 p-6">
        {loading ? (
          <div className="grid gap-6 lg:grid-cols-2">
            <Skeleton className="h-72" />
            <Skeleton className="h-72" />
          </div>
        ) : (
          <>
            <div className="grid gap-6 lg:grid-cols-2">
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-semibold">Risk distribution</CardTitle>
                  <p className="text-xs text-muted-foreground">
                    {customers.length} accounts by band
                  </p>
                </CardHeader>
                <CardContent>
                  <ResponsiveContainer width="100%" height={240}>
                    <PieChart>
                      <Pie
                        data={distribution}
                        dataKey="value"
                        nameKey="name"
                        cx="50%"
                        cy="50%"
                        innerRadius={54}
                        outerRadius={88}
                        paddingAngle={2}
                        // recharts fails to mount the Pie under strict mode with
                        // its enter animation running -- zero sectors, no error.
                        isAnimationActive={false}
                      >
                        {distribution.map((d) => (
                          <Cell key={d.name} fill={d.fill} stroke="hsl(var(--background))" />
                        ))}
                      </Pie>
                      <RTooltip contentStyle={TOOLTIP} />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="mt-2 flex flex-wrap justify-center gap-x-5 gap-y-1.5">
                    {distribution.map((d) => (
                      <span key={d.name} className="flex items-center gap-1.5 text-xs">
                        <span
                          className="h-2 w-2 rounded-full"
                          style={{ background: d.fill }}
                        />
                        {d.name}
                        <span className="tabular text-muted-foreground">{d.value}</span>
                      </span>
                    ))}
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-semibold">ARR by segment</CardTitle>
                  <p className="text-xs text-muted-foreground">Revenue exposure</p>
                </CardHeader>
                <CardContent>
                  <ResponsiveContainer width="100%" height={240}>
                    <BarChart data={bySegment} margin={{ top: 4, right: 8, bottom: 0, left: -8 }}>
                      <CartesianGrid stroke="hsl(var(--chart-grid))" vertical={false} />
                      <XAxis
                        dataKey="segment"
                        stroke="hsl(var(--chart-axis))"
                        tick={{ fontSize: 11 }}
                        tickLine={false}
                      />
                      <YAxis
                        stroke="hsl(var(--chart-axis))"
                        tick={{ fontSize: 11 }}
                        tickLine={false}
                        axisLine={false}
                        width={56}
                        tickFormatter={money}
                      />
                      <RTooltip
                        contentStyle={TOOLTIP}
                        formatter={(v: number) => [money(v), 'ARR']}
                        cursor={{ fill: 'hsl(var(--muted))' }}
                      />
                      <Bar
                        dataKey="arr"
                        fill="hsl(var(--chart-1))"
                        radius={[4, 4, 0, 0]}
                        isAnimationActive={false}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>
            </div>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-semibold">Risk drivers</CardTitle>
                <p className="text-xs text-muted-foreground">
                  Ranked by revenue behind each cause, not by count
                </p>
              </CardHeader>
              <CardContent className="px-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="pl-6">Driver</TableHead>
                      <TableHead className="text-right">Accounts</TableHead>
                      <TableHead className="pr-6 text-right">ARR exposed</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {byReason.map((r) => (
                      <TableRow key={r.reason}>
                        <TableCell className="pl-6 font-medium">{r.reason}</TableCell>
                        <TableCell className="text-right tabular">{r.count}</TableCell>
                        <TableCell className="pr-6 text-right tabular">{money(r.arr)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-semibold">Segment detail</CardTitle>
              </CardHeader>
              <CardContent className="px-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="pl-6">Segment</TableHead>
                      <TableHead className="text-right">Accounts</TableHead>
                      <TableHead className="text-right">Mean risk</TableHead>
                      <TableHead className="pr-6 text-right">ARR</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {bySegment.map((s) => (
                      <TableRow key={s.segment}>
                        <TableCell className="pl-6 font-medium">{s.segment}</TableCell>
                        <TableCell className="text-right tabular">{s.count}</TableCell>
                        <TableCell className="text-right tabular">{s.avgRisk.toFixed(1)}</TableCell>
                        <TableCell className="pr-6 text-right tabular">{money(s.arr)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </Shell>
  );
}

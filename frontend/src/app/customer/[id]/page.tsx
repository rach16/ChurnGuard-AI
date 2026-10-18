'use client';

/**
 * Customer page (8.5, ADR-0015 section 6).
 *
 * The same story the queue pane shows, at full width, followed by the three
 * things that only make sense in full: the ticket list, the CSM note timeline
 * (newest first, the notes that became signals marked), and the usage chart
 * only when there are four or more weeks of it. On the Salesforce org there is
 * one week, so the chart that used to take a third of the screen to draw one
 * point is gone until the history exists.
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

import { formatDay, money, StoryView, type Story } from '../../console';
import { BootGate, Shell } from '../../shell';
import { useSource } from '../../source';

const API = '/api';
const MIN_CHART_WEEKS = 4;

interface Detail {
  analysis: {
    engagement_history: { date: string; engagement_score: number; feature_adoption_rate: number }[];
    support_tickets: Record<string, string | number>[];
    interactions: { date: string; type: string; content: string }[];
  };
}

function CustomerPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const source = useSource();
  const [story, setStory] = useState<Story | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/customer/${id}/story`, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setStory)
      .catch((e) => setError(String(e.message ?? e)));
    fetch(`${API}/customer/${id}/detailed-analysis`, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then(setDetail)
      .catch(() => undefined);
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

  if (!story) {
    return (
      <Shell title="Customer" description="Loading">
        <div className="space-y-4 p-6">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-64 w-full" />
        </div>
      </Shell>
    );
  }

  const history = detail?.analysis.engagement_history ?? [];
  const tickets = detail?.analysis.support_tickets ?? [];
  const notes = [...(detail?.analysis.interactions ?? [])].sort((a, b) => (a.date < b.date ? 1 : -1));
  // A note on the same day as an adverse CSM-note signal is the note that made it.
  const flagged = new Set(story.signals.filter((s) => s.source === 'CSM note' && s.kind === 'adverse').map((s) => s.date));

  return (
    <Shell
      title={story.name}
      description={`${story.segment} · ${money(story.arr)} ARR · ${story.tenure_years}y customer · ${story.industry}`}
      actions={
        <Button variant="ghost" size="sm" onClick={() => router.push('/')}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          Queue
        </Button>
      }
    >
      <div className="mx-auto max-w-4xl space-y-6 p-4 sm:p-6">
        <StoryView story={story} synthetic={!!source?.synthetic} full />

        {history.length >= MIN_CHART_WEEKS && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold">Usage</CardTitle>
              <p className="text-xs text-muted-foreground">{history.length} weekly observations</p>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={220}>
                <AreaChart data={history} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
                  <defs>
                    <linearGradient id="eng" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="hsl(var(--chart-1))" stopOpacity={0.35} />
                      <stop offset="100%" stopColor="hsl(var(--chart-1))" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="hsl(var(--chart-grid))" vertical={false} />
                  <XAxis dataKey="date" stroke="hsl(var(--chart-axis))" tick={{ fontSize: 11 }} tickLine={false} minTickGap={48} />
                  <YAxis domain={[0, 1]} stroke="hsl(var(--chart-axis))" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
                  <RTooltip
                    contentStyle={{
                      background: 'hsl(var(--popover))',
                      border: '1px solid hsl(var(--border))',
                      borderRadius: 8,
                      fontSize: 12,
                      color: 'hsl(var(--popover-foreground))',
                    }}
                  />
                  <Area type="monotone" dataKey="engagement_score" stroke="hsl(var(--chart-1))" strokeWidth={2} fill="url(#eng)" isAnimationActive={false} name="Engagement" />
                </AreaChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        )}

        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold">CSM notes</CardTitle>
              <p className="text-xs text-muted-foreground">
                {notes.length} on record, newest first · marked ones became signals
              </p>
            </CardHeader>
            <CardContent className="space-y-0">
              {notes.length === 0 && <p className="text-sm text-muted-foreground">No notes on record.</p>}
              {notes.slice(0, 12).map((n, i) => (
                <div key={i}>
                  {i > 0 && <Separator />}
                  <div className="flex gap-3 py-2.5">
                    <span className="w-14 shrink-0 tabular text-xs text-muted-foreground">{formatDay(n.date)}</span>
                    <span className="min-w-0 flex-1 text-sm text-muted-foreground">
                      {flagged.has(n.date) && (
                        <Badge variant="outline" className="mr-2 h-5 align-middle text-[10px] font-normal">signal</Badge>
                      )}
                      {n.content}
                    </span>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold">Tickets</CardTitle>
              <p className="text-xs text-muted-foreground">{tickets.length} on record</p>
            </CardHeader>
            <CardContent className="px-0">
              {tickets.length === 0 ? (
                <p className="px-6 text-sm text-muted-foreground">No tickets on record.</p>
              ) : (
                <div className="max-h-[320px] overflow-y-auto">
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
                      {tickets.slice(0, 12).map((t) => (
                        <TableRow key={String(t.ticket_id)}>
                          <TableCell className="pl-6 tabular text-muted-foreground">{formatDay(String(t.date))}</TableCell>
                          <TableCell className="max-w-[160px] truncate">{t.type}</TableCell>
                          <TableCell>
                            <Badge variant="outline" className="font-normal">{t.severity}</Badge>
                          </TableCell>
                          <TableCell className="pr-6 text-right tabular">{t.csat_score}/5</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </Shell>
  );
}

export default function Page() {
  return (
    <BootGate>
      <CustomerPage />
    </BootGate>
  );
}

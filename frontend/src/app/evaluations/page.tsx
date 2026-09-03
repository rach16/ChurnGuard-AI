'use client';

/**
 * Retrieval evaluation.
 *
 * No baseline is the expected state, not a failure. The previous results file was
 * deleted deliberately -- it had been scored against a golden set whose questions
 * referenced customers present in no data file -- so this page explains that and
 * gives the command that produces a real one.
 */

import { useEffect, useState } from 'react';
import { Terminal } from 'lucide-react';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';

import { Shell } from '../shell';

const API = '/api';

export default function EvaluationsPage() {
  const [data, setData] = useState<any>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'empty'>('loading');

  useEffect(() => {
    fetch(`${API}/evaluation-results`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((d) => { setData(d); setState('ready'); })
      .catch(() => setState('empty'));
  }, []);

  if (state === 'loading') {
    return (
      <Shell title="Evaluation" description="Loading">
        <div className="p-6"><Skeleton className="h-64 w-full" /></div>
      </Shell>
    );
  }

  if (state === 'empty') {
    return (
      <Shell title="Evaluation" description="No baseline recorded">
        <div className="mx-auto max-w-2xl px-6 py-16">
          <h2 className="text-lg font-semibold">Nothing has been measured yet</h2>
          <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
            Retrieval quality has not been scored against the current corpus and
            golden set. The previous results were discarded rather than kept: they
            were measured against questions referencing customers that exist in no
            data file, over a corpus a fraction of the current size.
          </p>

          <Card className="mt-6">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                <Terminal className="h-4 w-4" />
                Produce a baseline
              </CardTitle>
            </CardHeader>
            <CardContent>
              <code className="block rounded-md bg-muted px-3 py-2 font-mono text-[13px]">
                uv run python scripts/benchmark_retrieval.py
              </code>
              <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
                Scores retrieval against the golden set&rsquo;s expected_context —
                ground truth rather than a model&rsquo;s opinion, so it costs nothing
                and runs in seconds. A full RAGAS run is a separate, paid step.
              </p>
            </CardContent>
          </Card>
        </div>
      </Shell>
    );
  }

  const rows: any[] = data?.results ?? [];
  const metrics = rows.length
    ? Object.keys(rows[0]).filter((k) => k !== 'method' && k !== 'Method')
    : [];

  return (
    <Shell title="Evaluation" description={`${rows.length} retrieval methods compared`}>
      <div className="mx-auto max-w-5xl p-6">
        <Card>
          <CardContent className="px-0 pt-6">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-6">Method</TableHead>
                  {metrics.map((m) => (
                    <TableHead key={m} className="text-right last:pr-6">
                      {m.replace(/_/g, ' ')}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((r, i) => (
                  <TableRow key={i}>
                    <TableCell className="pl-6 font-medium">{r.method ?? r.Method}</TableCell>
                    {metrics.map((m) => (
                      <TableCell key={m} className="text-right tabular last:pr-6">
                        {typeof r[m] === 'number' ? r[m].toFixed(3) : String(r[m])}
                      </TableCell>
                    ))}
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

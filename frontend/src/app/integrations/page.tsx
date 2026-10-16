'use client';

/**
 * Integrations.
 *
 * Salesforce is the one source that exists, so it leads, and everything shown about
 * it comes from the connector's own report of its last pull: how many records came
 * in, how many were set aside, and every judgement it made about messy data. That
 * report is the point of the page. Cleaning a real CRM is most of the work of an
 * integration, and it is invisible unless something shows it.
 *
 * The other sources are listed as planned, because they are.
 */

import { useEffect, useState } from 'react';
import { ArrowRight } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';

import { BootGate, Shell } from '../shell';
import { useSource } from '../source';
import { ConnectionPanel } from './connection';

const PLANNED = [
  { name: 'HubSpot', category: 'CRM', supplies: 'Lifecycle stage, deal history' },
  { name: 'Stripe', category: 'Billing', supplies: 'Subscription value, payment failures' },
  { name: 'Intercom', category: 'Support', supplies: 'Conversation volume, sentiment' },
  { name: 'Zendesk', category: 'Support', supplies: 'Ticket volume, CSAT, resolution time' },
  { name: 'Mixpanel', category: 'Product', supplies: 'Weekly usage history, feature adoption' },
];

type Group = 'excluded' | 'inferred' | 'dropped' | 'label' | 'plays' | 'limit' | 'unlabelled';

interface Decision {
  key: string;
  group: Group;
  label: string;
  count: number;
  examples: string[];
}

interface Report {
  as_of: string;
  funnel: {
    accounts_read: number;
    prospects_excluded: number;
    sample_records_excluded: number;
    customers_written: number;
  };
  decisions: Decision[];
}

const SECTIONS: { group: Group; title: string }[] = [
  { group: 'inferred', title: 'Fixed or filled in' },
  { group: 'dropped', title: 'Set aside' },
  { group: 'label', title: 'How churn was decided' },
  { group: 'plays', title: 'Save plays' },
  { group: 'unlabelled', title: 'Other' },
];

function formatDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

function FunnelStep({ value, label, last }: { value: number; label: string; last?: boolean }) {
  return (
    <div className="flex items-center gap-3">
      <div>
        <div className="text-2xl font-semibold tabular-nums">{value.toLocaleString()}</div>
        <div className="text-xs text-muted-foreground">{label}</div>
      </div>
      {!last && <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" />}
    </div>
  );
}

function SalesforcePanel() {
  const source = useSource();
  const [report, setReport] = useState<Report | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'none' | 'error'>('loading');

  useEffect(() => {
    fetch('/api/integrations/salesforce')
      .then(async (r) => {
        if (r.status === 404) return setState('none');
        if (!r.ok) throw new Error(String(r.status));
        setReport(await r.json());
        setState('ready');
      })
      .catch(() => setState('error'));
  }, []);

  if (state === 'loading') return <Skeleton className="h-80 w-full" />;

  if (state !== 'ready' || !report) {
    return (
      <Card>
        <CardContent className="p-5">
          <span className="font-medium">Salesforce</span>
          <p className="mt-2 text-sm text-muted-foreground">
            {state === 'none'
              ? 'No Salesforce pull has been recorded on this server yet.'
              : 'The Salesforce report could not be loaded. Try again shortly.'}
          </p>
        </CardContent>
      </Card>
    );
  }

  const { funnel } = report;
  const limits = report.decisions.filter((d) => d.group === 'limit');

  return (
    <Card>
      <CardContent className="space-y-6 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-base font-semibold">Salesforce</span>
              <Badge variant="secondary" className="text-[11px]">CRM</Badge>
              <Badge variant="outline" className="text-[11px] font-normal">Seeded test org</Badge>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              The figures below are from the last pull, run from the command line. Syncing
              through the connection is the next step.
            </p>
          </div>
          <div className="text-right text-xs text-muted-foreground">
            Last pulled
            <div className="text-sm font-medium text-foreground">{formatDate(report.as_of)}</div>
          </div>
        </div>

        <ConnectionPanel />

        <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
          <FunnelStep value={funnel.accounts_read} label="accounts read" />
          <FunnelStep value={funnel.prospects_excluded} label="prospects excluded" />
          <FunnelStep value={funnel.sample_records_excluded} label="demo records excluded" />
          <FunnelStep value={funnel.customers_written} label="customers" last />
        </div>

        <div className="grid gap-6 md:grid-cols-3">
          {SECTIONS.map(({ group, title }) => {
            const rows = report.decisions.filter((d) => d.group === group);
            if (rows.length === 0) return null;
            return (
              <div key={group}>
                <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  {title}
                </h3>
                <ul className="mt-2 space-y-2.5">
                  {rows.map((d) => (
                    <li key={d.key} className="text-sm">
                      <div className="flex items-baseline gap-2">
                        <span className="w-10 shrink-0 text-right font-semibold tabular-nums">
                          {d.count.toLocaleString()}
                        </span>
                        <span>{d.label}</span>
                      </div>
                      {d.examples[0] && (
                        <div className="ml-12 truncate text-xs text-muted-foreground" title={d.examples.join(' · ')}>
                          e.g. {d.examples[0]}
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>

        {limits.map((d) => (
          <div key={d.key} className="rounded-md border border-dashed px-4 py-3 text-sm">
            <span>{d.label}:</span>{' '}
            <span className="font-semibold tabular-nums">{d.count}</span>.{' '}
            <span className="text-muted-foreground">
              Salesforce holds one usage snapshot per account. Weekly history comes from
              product analytics, so until that source is connected the site can rank
              accounts but not predict when they leave.
            </span>
          </div>
        ))}

        <p className="text-xs text-muted-foreground">
          {source?.source === 'salesforce'
            ? 'Every page on this site runs on this data.'
            : 'The rest of the site still shows the synthetic test data. Switching it to this data is the next step.'}
        </p>
      </CardContent>
    </Card>
  );
}

function IntegrationsPage() {
  return (
    <Shell title="Integrations" description={`1 source connected, ${PLANNED.length} planned`}>
      <div className="mx-auto max-w-4xl space-y-8 p-6">
        <p className="text-sm leading-relaxed text-muted-foreground">
          The sources ChurnGuard ingests. Every figure below comes from the connector&rsquo;s
          own report of its last run, including each judgement it made about messy data.
        </p>

        <section className="space-y-3">
          <h2 className="text-sm font-semibold">Connected</h2>
          <SalesforcePanel />
        </section>

        <section className="space-y-3">
          <h2 className="text-sm font-semibold">Planned</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            {PLANNED.map((i) => (
              <Card key={i.name}>
                <CardContent className="p-4">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="font-medium">{i.name}</span>
                    <Badge variant="secondary" className="shrink-0 text-[11px]">
                      {i.category}
                    </Badge>
                  </div>
                  <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
                    {i.supplies}
                  </p>
                </CardContent>
              </Card>
            ))}
          </div>
        </section>
      </div>
    </Shell>
  );
}

export default function Page() {
  return (
    <BootGate>
      <IntegrationsPage />
    </BootGate>
  );
}

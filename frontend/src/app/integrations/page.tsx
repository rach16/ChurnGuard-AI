'use client';

/**
 * Integrations.
 *
 * Nothing here is connected. The page previously presented six live connectors
 * with sync times and record counts, which is the same species of claim as the
 * fabricated accuracy figure removed earlier. It is labelled as a roadmap because
 * that is what it is.
 */

import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';

import { Shell } from '../shell';

const PLANNED = [
  { name: 'Salesforce', category: 'CRM', supplies: 'Accounts, opportunities, close reasons' },
  { name: 'HubSpot', category: 'CRM', supplies: 'Lifecycle stage, deal history' },
  { name: 'Stripe', category: 'Billing', supplies: 'Subscription value, payment failures' },
  { name: 'Intercom', category: 'Support', supplies: 'Conversation volume, sentiment' },
  { name: 'Zendesk', category: 'Support', supplies: 'Ticket volume, CSAT, resolution time' },
  { name: 'Mixpanel', category: 'Product', supplies: 'Feature adoption, session frequency' },
];

export default function IntegrationsPage() {
  return (
    <Shell
      title="Integrations"
      description="Planned connectors — none are live"
      actions={<Badge variant="outline" className="font-normal text-muted-foreground">Roadmap</Badge>}
    >
      <div className="mx-auto max-w-4xl space-y-6 p-6">
        <p className="text-sm leading-relaxed text-muted-foreground">
          The current dataset is synthetic and generated in-repo. These are the
          sources a deployment would ingest, and what each would contribute to the
          risk model. No connection exists, and no status on this page reflects a
          live system.
        </p>

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

        <p className="text-xs leading-relaxed text-muted-foreground">
          Ingestion is not scoped in the current plan. It is the largest piece of
          work between this demonstration and something a customer could use, and
          pretending otherwise on this page would misrepresent the project.
        </p>
      </div>
    </Shell>
  );
}

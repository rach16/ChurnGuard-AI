'use client';

/**
 * Which data the deployment is serving.
 *
 * The site shows integration data only; the synthetic set is test data. Every page
 * says which it is showing, so a reader never has to guess whether a customer on
 * screen came from a CRM or from a generator.
 */

import { useEffect, useState } from 'react';

export interface DataSource {
  source: string;
  label: string;
  synthetic: boolean;
  as_of: string | null;
  seeded: boolean;
  served_from?:
    | { kind: 'file'; path: string }
    | { kind: 'sync'; snapshot_id: string; run_id: string; as_of: string; created_at: string; row_counts: Record<string, number> };
}

// One request per page load, shared by every component that asks, until
// something changes what is served (a sync promoted, a roll back): then
// refreshSource() asks again and every label on the page updates.
let pending: Promise<DataSource | null> | null = null;
const listeners = new Set<(s: DataSource | null) => void>();

export function fetchSource(): Promise<DataSource | null> {
  pending ??= fetch('/api/source', { cache: 'no-store' })
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
  return pending;
}

export function refreshSource(): void {
  pending = null;
  fetchSource().then((s) => listeners.forEach((l) => l(s)));
}

/** undefined while loading, null if the backend could not say. */
export function useSource(): DataSource | null | undefined {
  const [source, setSource] = useState<DataSource | null | undefined>(undefined);
  useEffect(() => {
    fetchSource().then(setSource);
    listeners.add(setSource);
    return () => {
      listeners.delete(setSource);
    };
  }, []);
  return source;
}

function formatDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

function formatWhen(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

export function describeSource(s: DataSource): string {
  if (s.synthetic) return 'Synthetic test data';
  const when =
    s.served_from?.kind === 'sync'
      ? `synced ${formatWhen(s.served_from.created_at)}`
      : s.as_of
        ? `pulled ${formatDate(s.as_of)}`
        : null;
  return [s.label, s.seeded ? 'seeded test org' : null, when].filter(Boolean).join(' · ');
}

export function SourceLabel() {
  const source = useSource();
  if (!source) return null;
  return (
    <span className="whitespace-nowrap text-xs text-muted-foreground">
      Data: {describeSource(source)}
    </span>
  );
}

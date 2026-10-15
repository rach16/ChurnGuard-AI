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
}

// One request per page load, shared by every component that asks.
let pending: Promise<DataSource | null> | null = null;

export function fetchSource(): Promise<DataSource | null> {
  pending ??= fetch('/api/source')
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
  return pending;
}

/** undefined while loading, null if the backend could not say. */
export function useSource(): DataSource | null | undefined {
  const [source, setSource] = useState<DataSource | null | undefined>(undefined);
  useEffect(() => {
    fetchSource().then(setSource);
  }, []);
  return source;
}

function formatDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

export function describeSource(s: DataSource): string {
  if (s.synthetic) return 'Synthetic test data';
  return [s.label, s.seeded ? 'seeded test org' : null, s.as_of ? `pulled ${formatDate(s.as_of)}` : null]
    .filter(Boolean)
    .join(' · ');
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

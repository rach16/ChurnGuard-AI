'use client';

/**
 * Access tokens (4.6.5, ADR-0016 §10a). A signed-in person makes a read-only
 * token for Claude Code or Claude Desktop, which then reads the book through
 * ChurnGuard's MCP server as that person.
 *
 * The value is shown once, right after it is made, with the command to add it
 * to each client; the backend keeps only its hash, so the list shows the prefix.
 * The backend enforces the rules (a name, 30 or 90 days, five live); this page
 * shows its reason when something is refused.
 */

import { useCallback, useEffect, useState } from 'react';
import { Check, Copy, KeyRound } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';

import { useMe } from '../account';
import { BootGate, Shell } from '../shell';

interface Token {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  expires_at: string;
  last_used_at: string | null;
}

interface Listing {
  tokens: Token[];
  mcp_url: string;
  lifetimes: number[];
  max_live: number;
}

function day(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function ago(iso: string | null): string {
  if (!iso) return 'never used';
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 60) return `last used ${Math.max(mins, 1)} min ago`;
  if (mins < 48 * 60) return `last used ${Math.round(mins / 60)} h ago`;
  return `last used ${day(iso)}`;
}

async function post(path: string, body?: unknown): Promise<Record<string, unknown>> {
  const r = await fetch(`/api/${path}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    if (r.status === 401) throw new Error('Your sign-in has ended. Sign in again and retry.');
    const detail = data.detail;
    throw new Error(typeof detail === 'string' ? detail : `HTTP ${r.status}`);
  }
  return data;
}

function CopyBlock({ label, text }: { label?: string; text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="space-y-1">
      {label && <div className="text-xs text-muted-foreground">{label}</div>}
      <div className="flex min-w-0 items-start gap-2">
        <code className="min-w-0 flex-1 break-all rounded-md bg-muted px-2 py-1.5 font-mono text-xs">{text}</code>
        <Button
          type="button"
          size="sm"
          variant="outline"
          aria-label={`Copy ${label ?? 'token'}`}
          onClick={() => {
            navigator.clipboard.writeText(text).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            });
          }}
        >
          {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
        </Button>
      </div>
    </div>
  );
}

function TokensPage() {
  const me = useMe();
  const [listing, setListing] = useState<Listing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [days, setDays] = useState(90);
  const [busy, setBusy] = useState<string | null>(null);
  const [made, setMade] = useState<{ value: string; mcp_url: string } | null>(null);

  const load = useCallback(() => {
    fetch('/api/tokens', { cache: 'no-store' })
      .then(async (r) => {
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${r.status}`);
        setListing(data);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Your tokens could not be loaded.'));
  }, []);

  useEffect(() => {
    if (me?.user && !me.user.demo) load();
  }, [me, load]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setNotice(null);
    setBusy('create');
    try {
      const body = await post('tokens', { name, days });
      setMade({ value: body.value as string, mcp_url: body.mcp_url as string });
      setName('');
      load();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'The token could not be made.');
    } finally {
      setBusy(null);
    }
  }

  async function revoke(t: Token) {
    if (!window.confirm(`Revoke "${t.name}"? Any client using it stops working at once.`)) return;
    setNotice(null);
    setBusy(t.id);
    try {
      await post(`tokens/${t.id}/revoke`);
      setNotice(`"${t.name}" was revoked.`);
      load();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'The token could not be revoked.');
    } finally {
      setBusy(null);
    }
  }

  if (me === undefined) return null;
  if (!me?.enabled || !me.user) {
    return (
      <Shell title="Access tokens">
        <div className="p-8 text-sm text-muted-foreground">Sign in to manage your access tokens.</div>
      </Shell>
    );
  }
  if (me.user.demo) {
    return (
      <Shell title="Access tokens">
        <div className="p-8 text-sm text-muted-foreground">Access tokens are for team members, not demo accounts.</div>
      </Shell>
    );
  }

  const url = made?.mcp_url ?? listing?.mcp_url ?? '';
  const header = made ? `Authorization: Bearer ${made.value}` : '';

  return (
    <Shell title="Access tokens" description="Read your book from Claude Code or Claude Desktop">
      <div className="mx-auto max-w-3xl space-y-6 p-4 sm:p-6">
        <p className="text-sm text-muted-foreground">
          A token lets an MCP client read your book through ChurnGuard&apos;s six read-only tools. It reads as you, in
          your organization, and can&apos;t change anything.
        </p>

        <Card>
          <CardContent className="p-5">
            <form onSubmit={create} className="flex flex-col gap-3 sm:flex-row sm:items-end">
              <label className="min-w-0 space-y-1 sm:flex-1">
                <span className="text-xs text-muted-foreground">Name</span>
                <Input
                  required
                  maxLength={60}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Claude Code on my laptop"
                  autoComplete="off"
                />
              </label>
              <label className="space-y-1">
                <span className="block text-xs text-muted-foreground">Expires after</span>
                <select
                  value={days}
                  onChange={(e) => setDays(Number(e.target.value))}
                  className="h-9 rounded-md border bg-background px-2 text-sm"
                >
                  {(listing?.lifetimes ?? [30, 90]).slice().reverse().map((d) => (
                    <option key={d} value={d}>
                      {d} days
                    </option>
                  ))}
                </select>
              </label>
              <Button type="submit" disabled={busy !== null}>
                <KeyRound className="mr-2 h-4 w-4" />
                {busy === 'create' ? 'Creating…' : 'Create token'}
              </Button>
            </form>
          </CardContent>
        </Card>

        {made && (
          <Card className="border-foreground/30">
            <CardContent className="space-y-3 p-5">
              <div className="text-sm font-medium">Copy this token now. You won&apos;t see it again.</div>
              <CopyBlock text={made.value} />
              <CopyBlock
                label="Claude Code"
                text={`claude mcp add --transport http churnguard ${url} --header "${header}"`}
              />
              <CopyBlock
                label="Claude Desktop (claude_desktop_config.json, under mcpServers)"
                text={`"churnguard": {"command": "npx", "args": ["mcp-remote", "${url}", "--header", "${header}"]}`}
              />
              <Button type="button" size="sm" variant="ghost" onClick={() => setMade(null)}>
                Done
              </Button>
            </CardContent>
          </Card>
        )}

        {notice && <div className="text-sm text-muted-foreground">{notice}</div>}
        {error && <div className="text-sm text-red-600">{error}</div>}

        {listing === null && !error ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          listing && (
            <div>
              <div className="mb-1 text-xs text-muted-foreground">
                Your tokens · {listing.tokens.length} of {listing.max_live}
              </div>
              {listing.tokens.length === 0 ? (
                <div className="border-t py-4 text-sm text-muted-foreground">
                  No tokens yet. Create one to connect Claude Code or Claude Desktop.
                </div>
              ) : (
                <ul className="border-t">
                  {listing.tokens.map((t) => (
                    <li key={t.id} className="flex items-center gap-3 border-b py-3">
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium">{t.name}</div>
                        <div className="text-xs text-muted-foreground sm:truncate">
                          {t.prefix}… · created {day(t.created_at)} · expires {day(t.expires_at)} · {ago(t.last_used_at)}
                        </div>
                      </div>
                      <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => revoke(t)}>
                        Revoke
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )
        )}
      </div>
    </Shell>
  );
}

export default function Page() {
  return (
    <BootGate>
      <TokensPage />
    </BootGate>
  );
}

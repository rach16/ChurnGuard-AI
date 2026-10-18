'use client';

/**
 * Team (2.6.5, ADR-0013 §3a). Admins invite people, change their role, and
 * remove them, here rather than in the Cognito console.
 *
 * The backend does the work and enforces the rules (one tenant, one role, the
 * last admin stays, nobody removes themselves); this page shows the list and
 * the backend's reason when something is refused. Non-admins never see the
 * menu entry, and the backend refuses them if they come here directly.
 */

import { useCallback, useEffect, useState } from 'react';
import { UserPlus } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

import { useMe } from '../account';
import { BootGate, Shell } from '../shell';

type Role = 'admin' | 'operator' | 'viewer';

interface Member {
  username: string;
  email: string;
  role: Role | null;
  status: 'invited' | 'active';
  created_at: string | null;
  last_sign_in: string | null;
}

const ROLES: { value: Role; label: string; can: string }[] = [
  { value: 'admin', label: 'Admin', can: 'everything, including connecting Salesforce and managing the team' },
  { value: 'operator', label: 'Operator', can: 'sync, test the connection, roll back' },
  { value: 'viewer', label: 'Viewer', can: 'read every page' },
];

function when(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
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

function TeamPage() {
  const me = useMe();
  const [members, setMembers] = useState<Member[] | null>(null);
  const [you, setYou] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<Role>('viewer');
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    fetch('/api/team', { cache: 'no-store' })
      .then(async (r) => {
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${r.status}`);
        setMembers(data.members ?? []);
        setYou(data.you ?? null);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'The team could not be loaded.'));
  }, []);

  useEffect(() => {
    if (me?.user?.role === 'admin') load();
  }, [me, load]);

  async function invite(e: React.FormEvent) {
    e.preventDefault();
    setNotice(null);
    setBusy('invite');
    try {
      const body = await post('team/invite', { email, role });
      const m = body.member as Member;
      setNotice(
        body.reenabled
          ? `${m.email} is back on the team as ${m.role}. Their previous password still works.`
          : `Invited ${m.email} as ${m.role}. Cognito has emailed them a temporary password.`,
      );
      setEmail('');
      load();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'The invitation failed.');
    } finally {
      setBusy(null);
    }
  }

  async function changeRole(m: Member, next: Role) {
    if (next === m.role) return;
    setNotice(null);
    setBusy(m.username);
    try {
      await post(`team/${m.username}/role`, { role: next });
      setNotice(`${m.email} is now ${next}. It takes effect at their next action.`);
      load();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'The role could not be changed.');
      load();
    } finally {
      setBusy(null);
    }
  }

  async function remove(m: Member) {
    if (!window.confirm(`Remove ${m.email}? They are signed out everywhere at once and can no longer sign in.`)) return;
    setNotice(null);
    setBusy(m.username);
    try {
      await post(`team/${m.username}/remove`);
      setNotice(`${m.email} was removed and signed out.`);
      load();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'They could not be removed.');
    } finally {
      setBusy(null);
    }
  }

  if (me === undefined) return null;
  if (!me?.enabled || me.user?.role !== 'admin') {
    return (
      <Shell title="Team">
        <div className="p-8 text-sm text-muted-foreground">Only an admin manages the team.</div>
      </Shell>
    );
  }

  return (
    <Shell title="Team" description="Who can sign in, and what they can do">
      <div className="mx-auto max-w-4xl space-y-6 p-6">
        <Card>
          <CardContent className="p-5">
            <form onSubmit={invite} className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-end">
              <label className="space-y-1 sm:min-w-[16rem] sm:flex-1">
                <span className="text-xs text-muted-foreground">Email</span>
                <Input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="name@company.com"
                  autoComplete="off"
                />
              </label>
              <label className="space-y-1">
                <span className="block text-xs text-muted-foreground">Role</span>
                <select
                  value={role}
                  onChange={(e) => setRole(e.target.value as Role)}
                  className="h-9 rounded-md border bg-background px-2 text-sm"
                >
                  {ROLES.map((r) => (
                    <option key={r.value} value={r.value}>
                      {r.label}
                    </option>
                  ))}
                </select>
              </label>
              <Button type="submit" disabled={busy !== null}>
                <UserPlus className="mr-2 h-4 w-4" />
                {busy === 'invite' ? 'Inviting…' : 'Invite'}
              </Button>
            </form>
            <ul className="mt-3 space-y-0.5 text-xs text-muted-foreground">
              {ROLES.map((r) => (
                <li key={r.value}>
                  <span className="font-medium text-foreground">{r.label}</span>: {r.can}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>

        {notice && <div className="text-sm text-amber-700 dark:text-amber-400">{notice}</div>}
        {error && <div className="text-sm text-red-600">{error}</div>}

        {members === null && !error ? (
          <Skeleton className="h-40 w-full" />
        ) : (
          members && (
            <Card>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Email</TableHead>
                    <TableHead>Role</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Last sign-in</TableHead>
                    <TableHead className="w-24" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {members.map((m) => (
                    <TableRow key={m.username}>
                      <TableCell className="font-medium">
                        {m.email}
                        {m.username === you && <span className="ml-2 text-xs text-muted-foreground">(you)</span>}
                      </TableCell>
                      <TableCell>
                        <select
                          value={m.role ?? ''}
                          disabled={busy !== null}
                          onChange={(e) => changeRole(m, e.target.value as Role)}
                          className="h-8 rounded-md border bg-background px-2 text-sm"
                          aria-label={`Role for ${m.email}`}
                        >
                          {!m.role && <option value="">none</option>}
                          {ROLES.map((r) => (
                            <option key={r.value} value={r.value}>
                              {r.label}
                            </option>
                          ))}
                        </select>
                      </TableCell>
                      <TableCell>
                        <Badge variant={m.status === 'active' ? 'secondary' : 'outline'} className="text-[11px] font-normal">
                          {m.status === 'active' ? 'Active' : 'Invited'}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">{when(m.last_sign_in)}</TableCell>
                      <TableCell className="text-right">
                        {m.username !== you && (
                          <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => remove(m)}>
                            Remove
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Card>
          )
        )}
      </div>
    </Shell>
  );
}

export default function Page() {
  return (
    <BootGate>
      <TeamPage />
    </BootGate>
  );
}

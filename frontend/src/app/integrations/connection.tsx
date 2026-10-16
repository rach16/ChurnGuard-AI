'use client';

/**
 * Connect Salesforce (1.9, ADR-0012).
 *
 * What the operator sees and clicks. The flow itself runs server-side: this asks
 * /ops/connections/start for the authorise URL, opens it in a popup, and waits for
 * the popup to say it is done. It never sees a code, a state or a token. The
 * popup's final page posts one message, {type, ok, event}, and this refetches the
 * connection from the API like any other reader would.
 *
 * Three states to be honest about: connecting from the page is off on this
 * deployment (production today); it is on and nothing is connected; it is on and
 * an org is connected, active or needing a fresh login. A connected org gets
 * Test connection (refresh and ask the org who it is) and Disconnect (revoke at
 * Salesforce, delete here). Both go through /ops/* like Connect does.
 *
 * Sync now (1.10) starts a run on the server and polls the run list until it
 * finishes. A run row is counts and timings only; the records themselves never
 * come to the browser.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { CheckCircle2, AlertTriangle, Link2 } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

export interface ConnectionInfo {
  id: string;
  org_id: string;
  instance_url: string;
  login_host: string;
  user_id: string;
  scopes: string;
  status: 'active' | 'needs_reauth' | 'revoked' | 'disabled';
  status_reason: string | null;
  token_issued_at: string;
  last_refreshed_at: string | null;
  last_used_at: string | null;
  created_at: string;
}

export interface SyncRun {
  id: string;
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'degraded';
  trigger: 'manual' | 'schedule' | 'wake';
  mode: 'full' | 'incremental' | null;
  as_of: string;
  objects: Record<string, { mode: string; fetched: number; tombstones: number; requests: number }>;
  records_fetched: number;
  tombstones: number;
  api_requests: number;
  api_usage: { used: number; max: number } | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
}

export interface ConnectionStatus {
  enabled: boolean;
  reason: string | null;
  operator_actions: boolean;
  pinned_org_id?: string | null;
  connection: ConnectionInfo | null;
  last_run?: SyncRun | null;
}

const OBJECT_LABELS: Record<string, string> = {
  Account: 'accounts',
  Opportunity: 'deals',
  OpportunityCompetitor: 'competitors',
  Case: 'cases',
  Task: 'activities',
};

const RUN_LABELS: Record<SyncRun['status'], string> = {
  pending: 'Queued',
  running: 'Running',
  succeeded: 'Succeeded',
  failed: 'Failed',
  degraded: 'Not promoted',
};

function isUnfinished(run: SyncRun | undefined | null): boolean {
  return !!run && (run.status === 'pending' || run.status === 'running');
}

const MESSAGE_TYPE = 'churnguard:salesforce-connect';

function formatWhen(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString('en-US', { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

export function useConnection() {
  const [status, setStatus] = useState<ConnectionStatus | null | undefined>(undefined);
  const refresh = useCallback(() => {
    return fetch('/api/connections', { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);
  useEffect(() => {
    refresh();
  }, [refresh]);
  return { status, refresh };
}

export function ConnectionPanel() {
  const { status, refresh } = useConnection();
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [verified, setVerified] = useState<string | null>(null);
  const [working, setWorking] = useState<'verify' | 'disconnect' | 'sync' | null>(null);
  const [runs, setRuns] = useState<SyncRun[]>([]);
  const popup = useRef<Window | null>(null);
  const connectionId = status?.connection?.id ?? null;

  const loadRuns = useCallback((id: string) => {
    return fetch(`/api/connections/${id}/runs?limit=5`, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : { runs: [] }))
      .then((body) => setRuns(body.runs ?? []))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (connectionId) loadRuns(connectionId);
    else setRuns([]);
  }, [connectionId, loadRuns]);

  // While a run is going, ask again every two seconds; stop when it is not.
  useEffect(() => {
    if (!connectionId || !isUnfinished(runs[0])) return;
    const timer = setInterval(() => loadRuns(connectionId), 2000);
    return () => clearInterval(timer);
  }, [connectionId, runs, loadRuns]);

  // The popup reports back once; a popup closed by hand reports nothing, so a
  // poll on `closed` refetches in that case too.
  useEffect(() => {
    function onMessage(e: MessageEvent) {
      if (e.origin !== window.location.origin || e.data?.type !== MESSAGE_TYPE) return;
      setBusy(false);
      setNotice(e.data.ok ? null : 'Salesforce did not connect. The popup said why; the audit log has the detail.');
      refresh();
    }
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [refresh]);

  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => {
      if (popup.current && popup.current.closed) {
        setBusy(false);
        refresh();
      }
    }, 800);
    return () => clearInterval(timer);
  }, [busy, refresh]);

  async function connect() {
    setNotice(null);
    setBusy(true);
    // Open synchronously on the click so the browser does not block it, then
    // point it at the authorise URL once the server has issued one.
    const win = window.open('about:blank', 'churnguard-salesforce', 'width=620,height=760,noopener=no');
    popup.current = win;
    try {
      const r = await fetch('/ops/connections/start', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ environment: 'production' }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || body.error || `HTTP ${r.status}`);
      if (win) win.location.href = body.authorize_url;
      else window.location.href = body.authorize_url;
    } catch (e) {
      win?.close();
      setBusy(false);
      setNotice(e instanceof Error ? e.message : 'Could not start the Salesforce sign-in.');
    }
  }

  async function operatorPost(path: string): Promise<Record<string, unknown>> {
    const r = await fetch(`/ops/${path}`, { method: 'POST', headers: { 'content-type': 'application/json' } });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(String(body.detail || body.error || `HTTP ${r.status}`));
    return body;
  }

  // Refreshes the token and asks the org who it is. Also what resets the org's
  // 30-day idle limit on the refresh token, so it is worth a click now and then.
  async function verify(id: string) {
    setNotice(null);
    setVerified(null);
    setWorking('verify');
    try {
      const body = await operatorPost(`connections/${id}/verify`);
      const api = body.api_requests as { max: number | null; remaining: number | null } | undefined;
      setVerified(
        `Works: ${body.org_name ?? 'org'} (${body.org_type ?? 'unknown edition'}), ` +
          `${api?.remaining?.toLocaleString() ?? '?'} of ${api?.max?.toLocaleString() ?? '?'} API calls left today` +
          (body.token_rotated ? ', token rotated' : ''),
      );
    } catch (e) {
      setNotice(e instanceof Error ? e.message : 'The connection could not be verified.');
    } finally {
      setWorking(null);
      refresh();
    }
  }

  // Starts a run on the server (or joins the one running) and lets the poll
  // above follow it. The response is the run row as created, so the list is
  // refetched rather than trusted.
  async function sync(id: string) {
    setNotice(null);
    setVerified(null);
    setWorking('sync');
    try {
      const body = await operatorPost(`connections/${id}/sync`);
      if (body.joined) setNotice('A sync is already running; showing it.');
    } catch (e) {
      setNotice(e instanceof Error ? e.message : 'Could not start the sync.');
    } finally {
      setWorking(null);
      loadRuns(id);
    }
  }

  async function disconnect(id: string, orgId: string) {
    if (!window.confirm(`Disconnect org ${orgId}? The token is revoked at Salesforce and deleted here.`)) return;
    setNotice(null);
    setVerified(null);
    setWorking('disconnect');
    try {
      const body = await operatorPost(`connections/${id}/disconnect`);
      if (!body.revoked_at_salesforce) setNotice('Disconnected here. Salesforce did not confirm the revocation; the token is deleted regardless.');
    } catch (e) {
      setNotice(e instanceof Error ? e.message : 'Could not disconnect.');
    } finally {
      setWorking(null);
      refresh();
    }
  }

  if (status === undefined) return null;

  if (!status || !status.enabled) {
    return (
      <div className="rounded-md border border-dashed px-4 py-3 text-sm text-muted-foreground">
        Connecting from this page is not enabled on this deployment
        {status?.reason ? `: ${status.reason}` : '.'}
      </div>
    );
  }

  const c = status.connection;
  const canAct = status.operator_actions;

  return (
    <div className="rounded-md border px-4 py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 text-sm">
          {c ? (
            <>
              <div className="flex items-center gap-2">
                {c.status === 'active' ? (
                  <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
                ) : (
                  <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
                )}
                <span className="font-medium">
                  {c.status === 'active' ? 'Connected' : c.status === 'needs_reauth' ? 'Needs a fresh login' : c.status}
                </span>
                <Badge variant="outline" className="font-mono text-[11px] font-normal">{c.org_id}</Badge>
              </div>
              <div className="mt-1 truncate text-xs text-muted-foreground" title={c.instance_url}>
                {c.instance_url.replace(/^https:\/\//, '')} · via {c.login_host} · since {formatWhen(c.created_at)}
                {c.last_refreshed_at ? ` · token refreshed ${formatWhen(c.last_refreshed_at)}` : ''}
              </div>
              {c.status_reason && (
                <div className="mt-1 text-xs text-amber-700 dark:text-amber-400">{c.status_reason}</div>
              )}
            </>
          ) : (
            <>
              <div className="flex items-center gap-2">
                <Link2 className="h-4 w-4 shrink-0 text-muted-foreground" />
                <span className="font-medium">Not connected</span>
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                Sign in with Salesforce to let this service read the org through its own connection.
                {status.pinned_org_id ? ` Only org ${status.pinned_org_id} is accepted.` : ''}
              </div>
            </>
          )}
        </div>

        {canAct ? (
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {c && c.status === 'active' && (
              <>
                <Button
                  size="sm"
                  onClick={() => sync(c.id)}
                  disabled={busy || working !== null || isUnfinished(runs[0])}
                >
                  {working === 'sync' || isUnfinished(runs[0]) ? 'Syncing…' : 'Sync now'}
                </Button>
                <Button size="sm" variant="outline" onClick={() => verify(c.id)} disabled={busy || working !== null}>
                  {working === 'verify' ? 'Checking…' : 'Test connection'}
                </Button>
              </>
            )}
            <Button
              size="sm"
              variant={c && c.status === 'active' ? 'outline' : 'default'}
              onClick={connect}
              disabled={busy || working !== null}
            >
              {busy ? 'Waiting for Salesforce…' : c ? 'Reconnect' : 'Connect Salesforce'}
            </Button>
            {c && (
              <Button size="sm" variant="ghost" onClick={() => disconnect(c.id, c.org_id)} disabled={busy || working !== null}>
                {working === 'disconnect' ? 'Disconnecting…' : 'Disconnect'}
              </Button>
            )}
          </div>
        ) : (
          <span className="text-xs text-muted-foreground">Operator actions are off here.</span>
        )}
      </div>
      {verified && <div className="mt-2 text-xs text-emerald-700 dark:text-emerald-400">{verified}</div>}
      {notice && <div className="mt-2 text-xs text-amber-700 dark:text-amber-400">{notice}</div>}
      {c && <RunList runs={runs} />}
    </div>
  );
}

function RunList({ runs }: { runs: SyncRun[] }) {
  if (runs.length === 0) {
    return (
      <div className="mt-3 border-t pt-3 text-xs text-muted-foreground">
        No sync has run through this connection yet. Sync now reads the org and lands every record
        here; the figures on this page still come from the last command-line pull until a landed run is
        promoted.
      </div>
    );
  }
  return (
    <div className="mt-3 border-t pt-3">
      <div className="mb-1.5 text-xs font-medium">Recent syncs</div>
      <ul className="space-y-1.5">
        {runs.map((run) => (
          <RunRow key={run.id} run={run} />
        ))}
      </ul>
      <div className="mt-2 text-[11px] text-muted-foreground">
        Landed records are not yet what the site serves; promotion follows in the next step.
      </div>
    </div>
  );
}

function RunRow({ run }: { run: SyncRun }) {
  const when = run.started_at ?? run.created_at;
  const objects = Object.entries(run.objects)
    .map(([name, o]) => `${o.fetched.toLocaleString()} ${OBJECT_LABELS[name] ?? name}`)
    .join(' · ');
  const tone =
    run.status === 'succeeded'
      ? 'text-emerald-700 dark:text-emerald-400'
      : run.status === 'failed' || run.status === 'degraded'
        ? 'text-amber-700 dark:text-amber-400'
        : 'text-muted-foreground';
  return (
    <li className="text-xs">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
        <span className={`font-medium ${tone}`}>
          {RUN_LABELS[run.status]}
          {isUnfinished(run) ? '…' : ''}
        </span>
        <span className="text-muted-foreground">{formatWhen(when)}</span>
        {run.mode && (
          <Badge variant="outline" className="text-[10px] font-normal">
            {run.mode}
          </Badge>
        )}
        {run.finished_at && (
          <span className="text-muted-foreground">
            {run.records_fetched.toLocaleString()} records
            {run.tombstones > 0 ? `, ${run.tombstones.toLocaleString()} deleted` : ''} ·{' '}
            {run.api_requests} API call{run.api_requests === 1 ? '' : 's'}
            {run.api_usage ? ` (${run.api_usage.used.toLocaleString()} of ${run.api_usage.max.toLocaleString()} used today)` : ''}
            {run.duration_seconds !== null ? ` · ${run.duration_seconds}s` : ''}
          </span>
        )}
      </div>
      {objects && <div className="text-muted-foreground">{objects}</div>}
      {run.error && <div className="text-amber-700 dark:text-amber-400">{run.error}</div>}
    </li>
  );
}

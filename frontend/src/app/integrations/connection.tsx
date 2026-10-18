'use client';

/**
 * Connect Salesforce (1.9, ADR-0012).
 *
 * What the operator sees and clicks. The flow itself runs server-side: this asks
 * the backend for the authorise URL, opens it in a popup, and waits for
 * the popup to say it is done. It never sees a code, a state or a token. The
 * popup's final page posts one message, {type, ok, event}, and this refetches the
 * connection from the API like any other reader would.
 *
 * Three states to be honest about: connecting from the page is off on this
 * deployment (production today); it is on and nothing is connected; it is on and
 * an org is connected, active or needing a fresh login. A connected org gets
 * Test connection (refresh and ask the org who it is) and Disconnect (revoke at
 * Salesforce, delete here).
 *
 * Who may press what (2.6.2, ADR-0013): signed in, the buttons follow the role
 * (operator: sync, test, roll back; admin: also connect and disconnect) and the
 * requests go straight to /api with the session cookie, re-checked by the
 * backend. Where sign-in is off there are no actions at all.
 *
 * Sync now (1.10) starts a run on the server and polls the run list until it
 * finishes. A run row is counts and timings only; the records themselves never
 * come to the browser.
 *
 * Write-back (9.2, ADR-0014): off until an admin turns it on, which creates the
 * three ChurnGuard fields in the org; then every promotion and roll back writes
 * the site's readings to each active Account. The block shows the switch, the
 * last attempt as counts, and Write back now for operators.
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
  writeback_enabled?: boolean;
}

export interface WriteBackAttempt {
  id: string;
  snapshot_id: string | null;
  trigger: 'promotion' | 'rollback' | 'manual';
  status: 'succeeded' | 'partial' | 'failed' | 'deferred' | 'skipped';
  counts: { candidates?: number; resolved?: number; unchanged?: number; written?: number; failed?: number };
  error: string | null;
  api_requests: number;
  actor: string;
  started_at: string;
  finished_at: string | null;
}

export interface WriteBackStatus {
  enabled: boolean;
  fields: string[];
  last: WriteBackAttempt | null;
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
  snapshot_id: string | null;
  contracts: { passed: number; failed: number; skipped: number; failures: string[] } | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
}

export interface SyncMetrics {
  days: number;
  runs: number;
  succeeded: number;
  failed: number;
  degraded: number;
  manual: number;
  success_rate: number | null;
  records_fetched: number;
  tombstones: number;
  api_requests: number;
  api_requests_per_day: number;
  median_seconds: number | null;
  last_succeeded_at: string | null;
  last_finished_at: string | null;
}

export interface Attention {
  connection_id: string;
  org_id: string;
  kind: 'needs_reauth' | 'runs_failing' | 'writeback_failed';
  reason: string;
  since?: string | null;
}

export interface ConnectionStatus {
  enabled: boolean;
  reason: string | null;
  operator_actions: boolean;
  admin_actions?: boolean;
  role?: 'admin' | 'operator' | 'viewer' | null;
  pinned_org_id?: string | null;
  connection: ConnectionInfo | null;
  last_run?: SyncRun | null;
  serving?: {
    snapshot_id: string;
    run_id: string | null;
    previous_snapshot_id: string | null;
    promoted_at: string;
    promoted_by: string;
    as_of: string | null;
    created_at: string | null;
    row_counts: Record<string, number> | null;
  } | null;
  attention?: Attention[];
}

// Display order and names. The run stores objects as JSON, whose key order
// Postgres does not keep, so the order comes from here.
const OBJECTS: [string, string][] = [
  ['Account', 'accounts'],
  ['Opportunity', 'deals'],
  ['OpportunityCompetitor', 'competitors'],
  ['Case', 'cases'],
  ['Task', 'activities'],
];

const RUN_LABELS: Record<SyncRun['status'], string> = {
  pending: 'Queued',
  running: 'Running',
  succeeded: 'Succeeded',
  failed: 'Failed',
  degraded: 'Contracts failed',
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

export function ConnectionPanel({ onChange }: { onChange?: () => void } = {}) {
  const { status, refresh: refreshStatus } = useConnection();
  // Anything that can move the serving pointer also tells the page, so the
  // report above the panel refetches instead of showing the previous sync.
  const refresh = useCallback(() => refreshStatus().then(() => onChange?.()), [refreshStatus, onChange]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [verified, setVerified] = useState<string | null>(null);
  const [working, setWorking] = useState<'verify' | 'disconnect' | 'sync' | 'rollback' | null>(null);
  const [runs, setRuns] = useState<SyncRun[]>([]);
  const [metrics, setMetrics] = useState<SyncMetrics | null>(null);
  const popup = useRef<Window | null>(null);
  const connectionId = status?.connection?.id ?? null;

  const loadRuns = useCallback((id: string) => {
    fetch(`/api/connections/${id}/metrics`, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : { metrics: null }))
      .then((body) => setMetrics(body.metrics ?? null))
      .catch(() => undefined);
    return fetch(`/api/connections/${id}/runs?limit=5`, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : { runs: [] }))
      .then((body) => setRuns(body.runs ?? []))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (connectionId) loadRuns(connectionId);
    else {
      setRuns([]);
      setMetrics(null);
    }
  }, [connectionId, loadRuns]);

  // While a run is going, ask again every two seconds; stop when it is not.
  useEffect(() => {
    if (!connectionId || !isUnfinished(runs[0])) return;
    const timer = setInterval(() => loadRuns(connectionId), 2000);
    return () => clearInterval(timer);
  }, [connectionId, runs, loadRuns]);

  // A run that just finished may have been promoted: the serving line comes
  // from the connection status, so refetch that too. Seen on staging 2026-10-16:
  // the badge stayed on the old run until a reload.
  const wasRunning = useRef(false);
  useEffect(() => {
    const running = isUnfinished(runs[0]);
    if (wasRunning.current && !running) refresh();
    wasRunning.current = running;
  }, [runs, refresh]);

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

  // Straight to the backend with the session cookie; the backend checks the
  // role and re-checks the person with Cognito (ADR-0013).
  function actionUrl(path: string): string {
    return `/api/${path}`;
  }

  function explain(statusCode: number, detail: string): string {
    if (statusCode === 401) return 'Your sign-in has ended. Sign in again (top right) and retry.';
    return detail;
  }

  async function connect() {
    setNotice(null);
    setBusy(true);
    // Open synchronously on the click so the browser does not block it, then
    // point it at the authorise URL once the server has issued one.
    const win = window.open('about:blank', 'churnguard-salesforce', 'width=620,height=760,noopener=no');
    popup.current = win;
    try {
      const r = await fetch(actionUrl('connections/start'), {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ environment: 'production' }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(explain(r.status, body.detail || body.error || `HTTP ${r.status}`));
      if (win) win.location.href = body.authorize_url;
      else window.location.href = body.authorize_url;
    } catch (e) {
      win?.close();
      setBusy(false);
      setNotice(e instanceof Error ? e.message : 'Could not start the Salesforce sign-in.');
    }
  }

  async function operatorPost(path: string): Promise<Record<string, unknown>> {
    const r = await fetch(actionUrl(path), { method: 'POST', headers: { 'content-type': 'application/json' } });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(explain(r.status, String(body.detail || body.error || `HTTP ${r.status}`)));
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

  // Moves the serving pointer back to the snapshot served before this one and
  // serves it. The pair is a toggle: rolling back twice returns to the newer.
  async function rollback(id: string) {
    const previous = status?.serving?.previous_snapshot_id;
    if (!previous) return;
    if (!window.confirm('Roll back to the snapshot served before this one? Every page on this site will show it.')) return;
    setNotice(null);
    setVerified(null);
    setWorking('rollback');
    try {
      const body = await operatorPost(`connections/${id}/rollback`);
      if (body.served === false) setNotice('The pointer moved, but serving the snapshot failed. A restart of the backend restores it.');
    } catch (e) {
      setNotice(e instanceof Error ? e.message : 'Could not roll back.');
    } finally {
      setWorking(null);
      refresh();
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
  const canOperate = status.operator_actions;
  const canAdminister = status.admin_actions ?? status.operator_actions;

  const needs = status.attention ?? [];

  return (
    <div className="rounded-md border px-4 py-3">
      {needs.length > 0 && (
        <div className="mb-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200">
          <div className="font-medium">
            {needs[0].kind === 'needs_reauth'
              ? 'This connection needs a fresh login.'
              : needs[0].kind === 'writeback_failed'
                ? 'The write-back to Salesforce needs attention.'
                : 'The sync needs attention.'}
          </div>
          <div className="break-words">{needs[0].reason}</div>
          <div className="mt-0.5 text-[11px] opacity-80">
            {needs[0].kind === 'needs_reauth'
              ? 'Until then nothing syncs and the site keeps serving the last promoted snapshot. Reconnect to fix it.'
              : needs[0].kind === 'writeback_failed'
                ? 'The site is unaffected; the org holds the readings of an earlier sync for those accounts.'
                : 'The site keeps serving the last promoted snapshot. The morning email carries the same message.'}
          </div>
        </div>
      )}
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

        {canOperate ? (
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
                {status.serving?.previous_snapshot_id && (
                  <Button size="sm" variant="outline" onClick={() => rollback(c.id)} disabled={busy || working !== null}>
                    {working === 'rollback' ? 'Rolling back…' : 'Roll back'}
                  </Button>
                )}
              </>
            )}
            {canAdminister && (
              <>
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
              </>
            )}
          </div>
        ) : (
          <span className="text-xs text-muted-foreground">
            {status.role === 'viewer'
              ? 'You can view this connection. An operator or admin runs syncs.'
              : 'Operator actions are off here.'}
          </span>
        )}
      </div>
      {verified && <div className="mt-2 text-xs text-emerald-700 dark:text-emerald-400">{verified}</div>}
      {notice && <div className="mt-2 text-xs text-amber-700 dark:text-amber-400">{notice}</div>}
      {c && <RunList runs={runs} serving={status.serving ?? null} />}
      {c && metrics && metrics.runs > 0 && <MetricsBlock m={metrics} />}
      {c && c.status === 'active' && (
        <WriteBackBlock
          connectionId={c.id}
          canOperate={canOperate}
          canAdminister={canAdminister}
          othersBusy={busy || working !== null || isUnfinished(runs[0])}
          changedAt={`${status.serving?.snapshot_id ?? ''}|${status.serving?.promoted_at ?? ''}|${runs[0]?.finished_at ?? ''}`}
          onChange={refresh}
        />
      )}
    </div>
  );
}

const WRITEBACK_LABELS: Record<WriteBackAttempt['status'], string> = {
  succeeded: 'Written',
  partial: 'Partly written',
  failed: 'Failed',
  deferred: 'Deferred',
  skipped: 'Skipped',
};

function describeAttempt(a: WriteBackAttempt): string {
  const n = a.counts;
  const parts = [
    `${(n.written ?? 0).toLocaleString()} written`,
    `${(n.unchanged ?? 0).toLocaleString()} unchanged`,
  ];
  if (n.failed) parts.push(`${n.failed.toLocaleString()} refused`);
  const unresolved = (n.candidates ?? 0) - (n.resolved ?? 0);
  if (unresolved > 0) parts.push(`${unresolved.toLocaleString()} without a Salesforce Id`);
  return parts.join(', ');
}

/**
 * The switch, the last attempt, and Write back now. Refetched whenever the
 * serving pointer or the latest run changes, because a promotion or roll back
 * is what produces an attempt.
 */
function WriteBackBlock({
  connectionId,
  canOperate,
  canAdminister,
  othersBusy,
  changedAt,
  onChange,
}: {
  connectionId: string;
  canOperate: boolean;
  canAdminister: boolean;
  othersBusy: boolean;
  changedAt: string;
  onChange: () => void;
}) {
  const [wb, setWb] = useState<WriteBackStatus | null | undefined>(undefined);
  const [working, setWorking] = useState<'enable' | 'disable' | 'run' | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const load = useCallback(() => {
    return fetch(`/api/connections/${connectionId}/writeback`, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((body: WriteBackStatus | null) => setWb(body))
      .catch(() => setWb(null));
  }, [connectionId]);

  useEffect(() => {
    load();
  }, [load, changedAt]);

  async function call(method: 'POST' | 'DELETE', path: string): Promise<Record<string, unknown>> {
    const r = await fetch(`/api/${path}`, { method, headers: { 'content-type': 'application/json' } });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      if (r.status === 401) throw new Error('Your sign-in has ended. Sign in again (top right) and retry.');
      throw new Error(String(body.detail || body.error || `HTTP ${r.status}`));
    }
    return body;
  }

  async function enable() {
    if (
      !window.confirm(
        'Turn on write-back? This creates three fields on the Account object in the connected org ' +
          '(ChurnGuard risk score, risk band, top reason) if they do not exist, and writes the readings ' +
          'this site shows to every active account after each sync and roll back.',
      )
    )
      return;
    setNotice(null);
    setDone(null);
    setWorking('enable');
    try {
      const body = await call('POST', `connections/${connectionId}/writeback`);
      const created = (body.fields_created as string[]) ?? [];
      setDone(
        created.length
          ? `On. Created ${created.length} field${created.length === 1 ? '' : 's'} on Account in the org. The next sync writes; or use Write back now.`
          : 'On. The org already had the three fields. The next sync writes; or use Write back now.',
      );
    } catch (e) {
      setNotice(e instanceof Error ? e.message : 'Could not turn write-back on.');
    } finally {
      setWorking(null);
      load();
      onChange();
    }
  }

  async function disable() {
    if (!window.confirm('Turn off write-back? Nothing is removed from the org; it just stops being updated.')) return;
    setNotice(null);
    setDone(null);
    setWorking('disable');
    try {
      await call('DELETE', `connections/${connectionId}/writeback`);
      setDone('Off. The fields in the org keep their last values.');
    } catch (e) {
      setNotice(e instanceof Error ? e.message : 'Could not turn write-back off.');
    } finally {
      setWorking(null);
      load();
      onChange();
    }
  }

  async function runNow() {
    setNotice(null);
    setDone(null);
    setWorking('run');
    try {
      const body = await call('POST', `connections/${connectionId}/writeback/run`);
      const a = body.writeback as WriteBackAttempt | null;
      if (a && (a.status === 'failed' || a.status === 'partial' || a.status === 'deferred')) {
        setNotice(`${WRITEBACK_LABELS[a.status]}: ${describeAttempt(a)}${a.error ? `. ${a.error}` : ''}`);
      } else if (a) {
        setDone(`${WRITEBACK_LABELS[a.status]}: ${describeAttempt(a)}.`);
      }
    } catch (e) {
      setNotice(e instanceof Error ? e.message : 'Could not write back.');
    } finally {
      setWorking(null);
      load();
      onChange();
    }
  }

  if (wb === undefined) return null;
  if (wb === null) {
    return (
      <div className="mt-3 border-t pt-3 text-xs text-muted-foreground">Write-back to Salesforce is not available here.</div>
    );
  }
  const last = wb.last;
  const tone =
    last?.status === 'succeeded'
      ? 'text-emerald-700 dark:text-emerald-400'
      : last?.status === 'failed' || last?.status === 'partial'
        ? 'text-amber-700 dark:text-amber-400'
        : 'text-muted-foreground';
  return (
    <div className="mt-3 border-t pt-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 text-xs">
          <div className="flex items-center gap-2">
            <span className="font-medium">Write-back to Salesforce</span>
            <Badge variant={wb.enabled ? 'default' : 'outline'} className="text-[10px] font-normal">
              {wb.enabled ? 'on' : 'off'}
            </Badge>
          </div>
          <div className="mt-1 text-muted-foreground">
            {wb.enabled
              ? 'After every sync and roll back, each active account\'s risk score, band and top reason are written to its Account record (fields ChurnGuard risk score, risk band, top reason). Only values that changed are written.'
              : 'Off. Turning it on creates three ChurnGuard fields on Account in the org and writes each active account\'s risk score, band and top reason there after every sync and roll back.'}
          </div>
          {last && (
            <div className="mt-1">
              <span className={`font-medium ${tone}`}>{WRITEBACK_LABELS[last.status]}</span>{' '}
              <span className="text-muted-foreground">
                {formatWhen(last.finished_at ?? last.started_at)}
                {last.trigger === 'manual' ? ' by hand' : last.trigger === 'rollback' ? ' after a roll back' : ' after a sync'} ·{' '}
                {describeAttempt(last)} · {last.api_requests} API call{last.api_requests === 1 ? '' : 's'}
              </span>
              {last.error && <div className="break-words text-amber-700 dark:text-amber-400">{last.error}</div>}
            </div>
          )}
        </div>
        {(canOperate || canAdminister) && (
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {wb.enabled && canOperate && (
              <Button size="sm" variant="outline" onClick={runNow} disabled={othersBusy || working !== null}>
                {working === 'run' ? 'Writing…' : 'Write back now'}
              </Button>
            )}
            {canAdminister &&
              (wb.enabled ? (
                <Button size="sm" variant="ghost" onClick={disable} disabled={othersBusy || working !== null}>
                  {working === 'disable' ? 'Turning off…' : 'Turn off'}
                </Button>
              ) : (
                <Button size="sm" variant="outline" onClick={enable} disabled={othersBusy || working !== null}>
                  {working === 'enable' ? 'Creating fields…' : 'Turn on'}
                </Button>
              ))}
          </div>
        )}
      </div>
      {done && <div className="mt-2 break-words text-xs text-emerald-700 dark:text-emerald-400">{done}</div>}
      {notice && <div className="mt-2 break-words text-xs text-amber-700 dark:text-amber-400">{notice}</div>}
    </div>
  );
}

function MetricsBlock({ m }: { m: SyncMetrics }) {
  const cells: [string, string][] = [
    ['syncs', `${m.runs}${m.manual ? ` (${m.manual} by hand)` : ''}`],
    ['succeeded', m.success_rate === null ? '–' : `${Math.round(m.success_rate * 100)}%`],
    ['records changed', `${m.records_fetched.toLocaleString()}${m.tombstones ? `, ${m.tombstones} deleted` : ''}`],
    ['API calls per day', m.api_requests_per_day.toLocaleString()],
    ['median run', m.median_seconds === null ? '–' : `${m.median_seconds}s`],
    ['last success', m.last_succeeded_at ? formatWhen(m.last_succeeded_at) : 'none'],
  ];
  return (
    <div className="mt-3 border-t pt-3">
      <div className="mb-1.5 text-xs font-medium">Last {m.days} days</div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-3 md:grid-cols-6">
        {cells.map(([label, value]) => (
          <div key={label}>
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="font-medium tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function RunList({ runs, serving }: { runs: SyncRun[]; serving: ConnectionStatus['serving'] }) {
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
      <div className="mb-1.5 flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-xs font-medium">Recent syncs</span>
        {serving ? (
          <span className="text-xs text-muted-foreground">
            Serving the sync of {formatWhen(serving.created_at ?? serving.promoted_at)}
            {serving.row_counts?.customers !== undefined ? ` · ${serving.row_counts.customers.toLocaleString()} customers` : ''}
            {serving.promoted_by === 'operator' ? ` · rolled back ${formatWhen(serving.promoted_at)}` : ''}
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">No run promoted yet; the site serves the last command-line pull.</span>
        )}
      </div>
      <ul className="space-y-1.5">
        {runs.map((run) => (
          <RunRow key={run.id} run={run} serving={!!serving && run.snapshot_id === serving.snapshot_id} />
        ))}
      </ul>
      <div className="mt-2 text-[11px] text-muted-foreground">
        A run that passes every contract is promoted: every page on this site then shows it.
        A run that fails a contract is kept for diagnosis and the previous one keeps serving.
        Roll back serves the snapshot served before this one. The last 10 snapshots are kept.
      </div>
    </div>
  );
}

function RunRow({ run, serving }: { run: SyncRun; serving: boolean }) {
  const when = run.started_at ?? run.created_at;
  const objects = OBJECTS.filter(([name]) => run.objects[name])
    .map(([name, label]) => `${run.objects[name].fetched.toLocaleString()} ${label}`)
    .join(' · ');
  const contracts = run.contracts
    ? run.contracts.failed > 0
      ? `${run.contracts.failed} of ${run.contracts.passed + run.contracts.failed} contracts failed`
      : `${run.contracts.passed} contracts passed${run.contracts.skipped ? `, ${run.contracts.skipped} skipped (no narrative corpus)` : ''}`
    : null;
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
        {run.trigger !== 'manual' && (
          <Badge variant="secondary" className="text-[10px] font-normal">
            {run.trigger === 'schedule' ? 'scheduled' : 'on wake'}
          </Badge>
        )}
        {serving && (
          <Badge className="text-[10px] font-normal">serving</Badge>
        )}
        {run.finished_at && (
          <span className="text-muted-foreground">
            {run.records_fetched.toLocaleString()} records
            {run.tombstones > 0 ? `, ${run.tombstones.toLocaleString()} deleted` : ''} ·{' '}
            {run.api_requests} API call{run.api_requests === 1 ? '' : 's'}
            {run.api_usage ? ` (org's counter: ${run.api_usage.used.toLocaleString()} of ${run.api_usage.max.toLocaleString()} today)` : ''}
            {run.duration_seconds !== null ? ` · ${run.duration_seconds}s` : ''}
          </span>
        )}
      </div>
      {objects && <div className="text-muted-foreground">{objects}</div>}
      {contracts && (
        <div className={run.contracts && run.contracts.failed > 0 ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'}>
          {contracts}
          {run.contracts?.failures.map((f) => (
            <div key={f} className="ml-3">{f}</div>
          ))}
        </div>
      )}
      {run.error && !run.error.startsWith('contracts failed') && (
        <div className="text-amber-700 dark:text-amber-400">{run.error}</div>
      )}
    </li>
  );
}

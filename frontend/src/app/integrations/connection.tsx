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
 * an org is connected, active or needing a fresh login.
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

export interface ConnectionStatus {
  enabled: boolean;
  reason: string | null;
  operator_actions: boolean;
  pinned_org_id?: string | null;
  connection: ConnectionInfo | null;
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
  const popup = useRef<Window | null>(null);

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
          <Button size="sm" variant={c && c.status === 'active' ? 'outline' : 'default'} onClick={connect} disabled={busy}>
            {busy ? 'Waiting for Salesforce…' : c ? 'Reconnect' : 'Connect Salesforce'}
          </Button>
        ) : (
          <span className="text-xs text-muted-foreground">Operator actions are off here.</span>
        )}
      </div>
      {notice && <div className="mt-2 text-xs text-amber-700 dark:text-amber-400">{notice}</div>}
    </div>
  );
}

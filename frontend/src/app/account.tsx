'use client';

/**
 * Who is signed in, in the header (2.6, ADR-0013).
 *
 * The backend runs the whole sign-in: this links to /api/auth/login, which
 * redirects to Cognito's own page, and signs out with a form POST to
 * /api/auth/logout. The page never sees a token; /api/auth/me returns the email,
 * the role and when the session ends, from the session the cookie names.
 *
 * On a deployment with no user pool (production until 2.6.4) /api/auth/me says
 * sign-in is off and this renders nothing.
 */

import { useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import { KeyRound, LogIn, LogOut } from 'lucide-react';

export interface Me {
  enabled: boolean;
  reason: string | null;
  /** Self sign-up to the read-only demo is open here (ADR-0017). */
  signup?: boolean;
  contact?: { linkedin: string | null; email: string | null };
  user: {
    email: string;
    role: 'admin' | 'operator' | 'viewer' | null;
    groups: string[];
    tenant: string;
    demo?: boolean;
    signed_in_at: string;
    expires_at: string;
  } | null;
}

// One request per page load, shared by every component that asks.
let pending: Promise<Me | null> | null = null;

export function fetchMe(): Promise<Me | null> {
  pending ??= fetch('/api/auth/me', { cache: 'no-store', credentials: 'same-origin' })
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
  return pending;
}

/** undefined while loading, null if the backend could not say. */
export function useMe(): Me | null | undefined {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  useEffect(() => {
    fetchMe().then(setMe);
  }, []);
  return me;
}

export function AccountMenu() {
  const me = useMe();
  const pathname = usePathname() || '/';
  if (!me || !me.enabled) return null;

  if (!me.user) {
    return (
      <a
        href={`/api/auth/login?return=${encodeURIComponent(pathname)}`}
        className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium hover:bg-muted"
      >
        <LogIn className="h-3.5 w-3.5" />
        Sign in
      </a>
    );
  }

  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="hidden max-w-[14rem] truncate text-muted-foreground sm:inline" title={me.user.email}>
        {me.user.email}
      </span>
      {me.user.role && (
        <span className="rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
          {me.user.demo ? 'demo' : me.user.role}
        </span>
      )}
      {!me.user.demo && (
      <a
        href="/tokens"
        className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-muted-foreground hover:bg-muted hover:text-foreground"
        title="Access tokens for Claude Code and Claude Desktop"
      >
        <KeyRound className="h-3.5 w-3.5" />
        <span className="sr-only sm:not-sr-only">Tokens</span>
      </a>
      )}
      <form method="post" action="/api/auth/logout">
        <button
          type="submit"
          className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          title="Sign out"
        >
          <LogOut className="h-3.5 w-3.5" />
          <span className="sr-only sm:not-sr-only">Sign out</span>
        </button>
      </form>
    </div>
  );
}

/**
 * The line a demo sign-up sees under the header on every page (ADR-0017): what
 * they are looking at, and how to reach the owner. Nothing for anyone else.
 */
export function DemoLine() {
  const me = useMe();
  if (!me?.user?.demo) return null;
  const { linkedin, email } = me.contact ?? { linkedin: null, email: null };
  return (
    <div className="shrink-0 border-b px-4 py-1.5 text-xs text-muted-foreground sm:px-6">
      You&apos;re viewing a demo on seeded Salesforce data. Want ChurnGuard on your own org? Get in touch
      {linkedin && (
        <>
          {' on '}
          <a href={linkedin} target="_blank" rel="noopener noreferrer" className="text-foreground underline underline-offset-2">
            LinkedIn
          </a>
        </>
      )}
      {linkedin && email && ' or by '}
      {!linkedin && email && ' by '}
      {email && (
        <a href={`mailto:${email}`} className="text-foreground underline underline-offset-2">
          email
        </a>
      )}
      .
    </div>
  );
}

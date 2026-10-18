'use client';

/**
 * What a signed-out visitor sees (2.6.2, ADR-0013 section 3).
 *
 * Every page goes behind sign-in once a user pool is configured: a company's list
 * of at-risk customers is never public, and the product is shown by a recorded
 * walkthrough instead. AuthGate wraps every page (inside BootGate, after the
 * backend is awake); signed out, it renders this screen and nothing else, so no
 * page fetches data it would only be refused.
 *
 * The backend is the control, not this: every data route answers 401 without a
 * session. This is the front door in front of it.
 */

import { usePathname } from 'next/navigation';
import { LogIn, PlayCircle, ShieldCheck } from 'lucide-react';

import { useMe } from './account';

// Set when the walkthrough is published; the link stays hidden until then.
const WALKTHROUGH_URL = process.env.NEXT_PUBLIC_WALKTHROUGH_URL || '';

export function AuthGate({ children }: { children: React.ReactNode }) {
  const me = useMe();
  if (me === undefined) return null;                       // asking who you are
  if (me && me.enabled && !me.user) return <SignInScreen />;
  return <>{children}</>;
}

export function SignInScreen() {
  const pathname = usePathname() || '/';
  const signIn = `/api/auth/login?return=${encodeURIComponent(pathname)}`;
  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight">ChurnGuard</h1>
          <p className="text-sm text-muted-foreground">
            Which customers are about to leave, why, and what has worked before, read from your own
            Salesforce.
          </p>
        </div>

        <a
          href={signIn}
          className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:opacity-90"
        >
          <LogIn className="h-4 w-4" />
          Sign in
        </a>

        <div className="flex items-start gap-2 text-xs text-muted-foreground">
          <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            Accounts are by invitation from your admin.
          </span>
        </div>

        {WALKTHROUGH_URL && (
          <a
            href={WALKTHROUGH_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 text-sm text-foreground underline-offset-4 hover:underline"
          >
            <PlayCircle className="h-4 w-4" />
            Watch the walkthrough
          </a>
        )}
      </div>
    </main>
  );
}

'use client';

/**
 * The console shell: rail plus a titled content region.
 *
 * Every page uses it so the chrome is identical everywhere. Four pages each
 * carrying their own header was the main reason the app read as a set of demos
 * rather than one product.
 */

import { usePathname } from 'next/navigation';
import Link from 'next/link';
import {
  AlertTriangle,
  BarChart3,
  LifeBuoy,
  MessageSquare,
  Plug,
  TrendingDown,
  type LucideIcon,
} from 'lucide-react';

import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

import { AccountMenu } from './account';
import { SourceLabel } from './source';
import { ThemeToggle } from './theme-toggle';
import { WakeGate, WakeScreen } from './wake-screen';

const NAV: { icon: LucideIcon; label: string; href: string }[] = [
  { icon: AlertTriangle, label: 'At risk', href: '/' },
  { icon: MessageSquare, label: 'Assistant', href: '/ask' },
  { icon: BarChart3, label: 'Analytics', href: '/analytics' },
  { icon: LifeBuoy, label: 'Evaluation', href: '/evaluations' },
  { icon: Plug, label: 'Integrations', href: '/integrations' },
];

export function Rail() {
  const pathname = usePathname();

  return (
    <nav className="flex w-14 shrink-0 flex-col items-center gap-1 border-r bg-muted/40 py-3">
      <Link
        href="/"
        aria-label="ChurnGuard"
        className="mb-3 flex h-8 w-8 items-center justify-center rounded-md bg-primary text-primary-foreground"
      >
        <TrendingDown className="h-4 w-4" />
      </Link>

      {NAV.map(({ icon: Icon, label, href }) => {
        // "/" would prefix-match everything, so it is compared exactly.
        const active = href === '/' ? pathname === '/' : pathname.startsWith(href);
        return (
          <Tooltip key={href}>
            <TooltipTrigger asChild>
              <Link
                href={href}
                aria-label={label}
                aria-current={active ? 'page' : undefined}
                className={cn(
                  'flex h-9 w-9 items-center justify-center rounded-md transition-colors',
                  active
                    ? 'bg-accent text-accent-foreground'
                    : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
                )}
              >
                <Icon className="h-4 w-4" />
              </Link>
            </TooltipTrigger>
            <TooltipContent side="right">{label}</TooltipContent>
          </Tooltip>
        );
      })}

      <div className="mt-auto">
        <ThemeToggle />
      </div>
    </nav>
  );
}

/**
 * Wait for the backend before mounting a page that needs it.
 *
 * Every data-driven page wraps in this, so no page has to reason about cold
 * starts: by the time its children mount, a request will succeed. It keeps the
 * rail visible while waiting, because a bare centred message on a blank page
 * reads as an error screen no matter what it says.
 *
 * A page that never calls the backend should not use it: making it wait for a
 * server it does not need would be a regression. Every page currently does.
 */
export function BootGate({ children }: { children: React.ReactNode }) {
  return (
    <WakeGate
      fallback={(wake) => (
        <div className="flex h-screen overflow-hidden">
          <Rail />
          <div className="min-w-0 flex-1 overflow-y-auto">
            <WakeScreen wake={wake} />
          </div>
        </div>
      )}
    >
      {children}
    </WakeGate>
  );
}

/** Page chrome for everything except the queue, which manages its own panes. */
export function Shell({
  title,
  description,
  actions,
  children,
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="flex h-screen overflow-hidden">
      <Rail />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b px-6">
          <div className="min-w-0">
            <h1 className="truncate text-sm font-semibold">{title}</h1>
            {description && (
              <p className="truncate text-xs text-muted-foreground">{description}</p>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <SourceLabel />
            {actions}
            <AccountMenu />
          </div>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </div>
    </div>
  );
}

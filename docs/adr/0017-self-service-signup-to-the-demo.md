# 0017 · Self-service sign-up to a read-only demo

**Status:** Accepted 2026-10-19 (the owner approved the design and the mockup; branch `feat/signup`) · **Date:** 2026-10-19 · **Phase:** 2.6.7 (a sub-item of 2.6, operator login)

## Context

The project goes on LinkedIn. A visitor who follows the link meets a sign-in
screen and no way in: accounts are invite-only (ADR-0013 §3a), decided when the
product was to be shown by a recorded walkthrough. The owner wants visitors to
get in by themselves.

Two shapes were weighed on 2026-10-19:

- **A. Sign up, then connect your own Salesforce.** The site serves one
  organization's snapshot per deployment; a tenant per sign-up means
  per-tenant sync, serving, scorer, notes index, write-back and AI budget, and
  holding strangers' real customer data on free hosting (a privacy policy,
  deletion, the Salesforce app's open gap). Two to three weeks, and most
  visitors have no Salesforce org with data, so they would see an empty site.
- **B. Sign up, then see the demo, read-only.** The seeded org the site already
  serves, as a viewer. About a day.

**The owner chose B** (2026-10-19). A stays possible later; nothing here closes it.

## Decision

1. **Sign-up through Cognito's own pages.** The pool stops being admin-only
   (`AllowAdminCreateUserOnly: false`). The sign-in screen gains **Sign
   up** (the owner's wording), which goes through `/api/auth/signup`: the same state, nonce and
   PKCE as sign-in (ADR-0013 §2), to Cognito's sign-up page instead of its
   sign-in page. Cognito asks for an email and a password and sends a
   verification code; an unverified address never reaches us. Back through the
   same callback.

2. **The backend assigns the tenant and the role, never the person.** A
   sign-up's first ID token has no group and no tenant; today that is refused
   ("This account has no role here yet"). With sign-up on, such an account is
   instead given, through the admin API, the deployment's demo tenant
   (`SIGNUP_TENANT`, the served one) and the groups **`viewer`** and
   **`demo`**, then the token is refreshed so the session carries them. The app
   client must **not** be able to write `custom:tenant`, or a person could
   choose another organization at sign-up: checked before this ships, and
   removed from the client's write attributes if it is there. An account that
   has a tenant or a role is never touched by this path.

3. **What a demo account can do.** Everything a viewer reads: the queue, the
   stories, Why customers leave, Integrations (the connected username is already
   hidden from readers), the assistant. **Not**: sync, roll back, connect,
   write-back, the Team page (as for any viewer), and **not access tokens**: MCP
   is for team members, and a token outlives the visit. The assistant keeps its
   caps: 10 questions an hour per person, 200 a day per deployment shared with
   the team, the OpenAI dashboard's monthly cap behind both.

4. **They know it is a demo.** A line under the header for `demo` accounts:
   "You're viewing a demo on seeded Salesforce data. Want ChurnGuard on your own
   org? Get in touch on LinkedIn or by email." The owner chose both
   (`CONTACT_LINKEDIN`, `CONTACT_EMAIL`).

5. **The Team page stays the team's.** `demo` accounts are not listed among
   members; a count ("Demo sign-ups: 14") opens them, each removable as today.

6. **Audit.** `signup` when an account is first assigned (email, tenant, role),
   as `user:<sub>`; then the usual `login` rows.

7. **Off by default.** `SELF_SIGNUP=true` turns the backend's assignment and the
   Create account link on, per deployment; the pool setting is the other half.
   Staging first, then production.

## Security

| Risk | Answer |
|---|---|
| A person picks their own tenant or role | Assigned by the backend from configuration; the client cannot write `custom:tenant` (verified at 2.6.7); groups are writable only by the admin API |
| Fake or throwaway sign-ups | Email verification. Cognito's own email sender allows 50 a day, a natural cap. Bot protection (Cognito threat protection) is paid and not used; recorded as a gap |
| A demo account spends the AI budget | Per-person and per-deployment caps (2.7); a burst of sign-ups can use the day's 200 and the team waits until tomorrow. Accepted for a demo; a separate demo budget is the fix if it happens |
| Data a visitor should not see | Only seeded data is served. Readers never see the connected Salesforce username, tokens, the audit log or the team |
| Removing someone | The Team page's Remove, as today: disabled, signed out everywhere, sessions deleted |

## What changes

| Where | Change |
|---|---|
| Cognito (each pool) | Allow self sign-up; check the client's write attributes; the backend's IAM user gains `AdminUpdateUserAttributes` if it lacks it |
| Backend | `/auth/signup`; the assignment in the login flow; `demo` refused on token routes; `/auth/me` says `demo`; Team list splits demo accounts; audit `signup` |
| Frontend | Create account on the sign-in screen; the demo line; the Team page's count |
| Settings | `SELF_SIGNUP`, `SIGNUP_TENANT`, `CONTACT_LINKEDIN`, `CONTACT_EMAIL` |

## Checked before building (2026-10-19)

Both pools' app clients write only standard attributes (production: `email`;
staging: the standard profile set): neither can write `custom:tenant`. Both
pools are admin-only, verify email, send through Cognito's own sender and have
the groups admin, operator, viewer. Both backends' IAM users lack
`AdminUpdateUserAttributes`, which enrolment needs. A person given a role on the
Team page leaves the `demo` group and joins the team.

Not yet proven: that Cognito's `/signup` page carries the authorization
request's state, nonce and PKCE through to the redirect. Staging proves it; if
it does not, Sign up goes to the sign-in page, whose own "Sign up" link does.

## Decided on staging (2026-10-19)

The owner signed up on staging and landed in the demo (audited `signup`, then
`login`, as a demo viewer). The Integrations page shows a demo account the
connected org as any viewer sees it: the org ID, its Salesforce address, the
login host and the token's times. Hiding those for demo accounts was offered
and **declined by the owner**: the org is a seeded test org, and the page's
point is that a real connection is behind the demo.

## Consequences

- Anyone can see the seeded book and ask the assistant, within the caps.
- Sign-in stays the control: every route still needs a session, and a demo
  session is a viewer's.
- Rolling back is the pool setting and `SELF_SIGNUP=false`; existing demo
  accounts can be removed from the Team page.

# Giving ChurnGuard a real web address

Status: nothing done yet, this is the plan
Date: 2026-09-02

## How to use this

Give it to Claude Code like this:

> Read `docs/DEPLOYMENT_SPEC.md` and do Step 2. Don't touch the `infra/` folder
> and don't run anything that costs money. Show me the changes before saving.

Do the steps in order. Steps 1 to 4 cost about $19 a year in total. Step 5 is
optional and costs real money, so it is last and you can skip it forever.

## What you have right now

Two separate things on the internet, in two different places.

The **front end** is the part people see: the dashboard, the buttons, the charts.
It lives on a company called Vercel, at `churn-guard-ai-nine.vercel.app`.

The **back end** is the part that does the thinking: it holds the data and answers
questions. It lives on a different company called Render, at
`churnguard-backend.onrender.com`.

When someone opens your dashboard, their web browser has to talk to both. It gets
the pictures and buttons from Vercel, then turns around and asks Render for the
numbers.

## What is actually wrong with that

Three things.

**The address is not yours.** `churn-guard-ai-nine.vercel.app` is a temporary
address Vercel gave you. It has their name in it and you cannot put it on a CV or
a business card. You want something like `churnguard.com` that belongs to you.

**The browser has to talk to two places.** This is more annoying than it sounds.
Web browsers have a safety rule: a page loaded from one address is not allowed to
fetch data from a different address, unless that other place explicitly says "it's
fine, I know them." That permission slip is why your `render.yaml` file has a
setting called `CORS_ALLOW_ORIGINS`. It exists purely to work around the problem.
It also means anyone can open the page, look at the network traffic, and see
exactly where your back end lives.

**The back end falls asleep.** Render's free plan shuts your back end down after
15 minutes with no visitors, and takes about 30 seconds to wake up. I opened your
site and the dashboard showed all zeros. Not an error message, just zeros. It
looked like a working page with no customers in it. That is worse than a visible
error, because you cannot tell the difference between "asleep" and "broken."

## The one big idea

Right now your setup is like a shop where customers have to visit two buildings.
They come to the front shop for the catalogue, then walk across town to the
warehouse to ask about stock.

The better way, and the way HG Insights does it, is one front door. Customers only
ever go to one building. When they ask a question the shop cannot answer, someone
behind the counter quietly walks to the back and gets it. The customer never learns
the warehouse exists.

That is the whole trick. Everything comes from **one address**. Requests that need
the back end get quietly forwarded behind the scenes.

At HG they do this with a piece of software called nginx sitting inside the app.
Someone asks for `/api/something`, and nginx passes it to the right internal
service without the browser ever knowing.

You get exactly the same behaviour for free with a Vercel setting called a
**rewrite**. Same idea, no extra software, no extra cost.

Once you do this:
- Everything comes from your own domain
- The safety-rule workaround disappears, because nothing is crossing between
  addresses any more
- Nobody can see where your back end lives
- Later, if you move the back end somewhere else, you change one line and no other
  code changes

## What things cost

| What | Cost |
|---|---|
| Buying a `.com` name | about $13 a year |
| Amazon holding your address book | about $6 a year |
| Vercel (front end) | free |
| Render (back end) | free, but it sleeps |
| Render always-awake | $7 a month, optional |
| **Steps 1 to 4 together** | **about $19 a year** |
| Step 5, the Amazon version | about $54 a month while switched on |

To put $54 a month in perspective, that is about 34 times the cost of the cheap
path, to serve the same app to the same number of people.

## Step 1: buy the name and set up the address book

**What DNS is.** When you type a name like `google.com`, your computer has no idea
where that is. It asks a global address book, which replies with the actual
location. That address book system is called DNS. Route53 is Amazon's version of
it, and it is what HG uses.

Do this part yourself in the browser, since it involves paying:

1. Go to the AWS console, then Route53, then "Registered domains", then "Register".
2. Pick a name and buy it. Roughly $13 a year for a `.com`.
3. Buying it through Route53 automatically sets up the address book entry, which
   saves you a fiddly step. If you already own a name somewhere else like GoDaddy,
   you can point it at Route53 instead, it just takes an extra step and up to a day.

Then add four entries to the address book:

| Name | Points at | What it is for |
|---|---|---|
| `churnguard.com` | Vercel | The main site |
| `www.churnguard.com` | Vercel | So `www.` also works |
| `staging.churnguard.com` | Vercel | A practice copy for testing |
| `api.churnguard.com` | Render | The back end, optional |

Last, go into the Vercel and Render dashboards and tell them about your new name.
They will automatically set up the padlock in the address bar for you. You do not
need to buy or configure a certificate.

**How to check it worked:** type your new address into a browser and your site
loads, with a padlock icon.

## Step 2: one front door

This is the most valuable step and it costs nothing.

Create a new file, `frontend/vercel.json`, containing this:

```json
{
  "rewrites": [
    { "source": "/api/:path*", "destination": "https://churnguard-backend.onrender.com/:path*" }
  ]
}
```

In plain words: "if anyone asks this site for something starting with `/api/`,
quietly go and fetch it from Render, and don't tell them."

Then three small changes:

- Find everywhere the code uses `NEXT_PUBLIC_BACKEND_URL` and change it so the app
  asks for `/api/...` instead of the full Render address.
- Delete `CORS_ALLOW_ORIGINS` from `render.yaml`. It is no longer needed, because
  nothing is crossing between two addresses any more.
- Leave the Render address itself alone. You still need it, it just stops being
  something the public sees.

**How to check it worked:** open your site, press F12 to open developer tools,
click the Network tab, and reload. Every request should go to your own domain.
None should mention `onrender.com`.

## Step 3: a practice copy

Right now you have one live site. If you break it, it is broken for everyone.

Professional setups always keep two copies: a **staging** one for trying things,
and a **production** one that real people use. HG does exactly this, with
`market-staging.hginsights.info` and `market.hginsights.com`.

For you:

- Make a branch in git called `staging`. Vercel will automatically publish it to
  `staging.churnguard.com`.
- Make a second free Render service for the staging back end.
- Point the staging site's rewrite at the staging back end.

**How to check it worked:** push a change to the `staging` branch, see it appear on
the staging address, and confirm the live site did not change.

## Step 4: notice when it breaks

Two small things.

**A heartbeat check.** Your app already has a `/ready` address that honestly
reports whether it can actually serve requests. Point a free monitoring service at
`https://churnguard.com/api/ready` and have it email you when that stops answering.
This is what would have caught the all-zeros problem I saw.

**A test gate.** You already have tests in `test-e2e.sh`. Set them up so they run
automatically against the staging site, and so a failure blocks the change from
reaching the live site. Right now nothing stops a broken change going live.

Worth knowing: HG's own version of this gate is subtly broken. It checks whether
the tests were successfully *started*, not whether they *passed*. So a failing test
run blocks nothing. Do not copy that bit.

**Keep your keys secret.** Your `OPENAI_API_KEY` is already set to `sync: false`,
which keeps it out of the code. That is exactly right. Leave it that way.

## Step 5: the Amazon version, optional

**Skip this unless you specifically want to show off AWS skills in an interview.**

You have already written this, in the `infra/` folder, and sensibly never switched
it on. It would run the back end on Amazon's own servers instead of Render, behind
Amazon's own load balancer, with a real nginx doing the forwarding.

It costs about $54 a month while it is running. The normal way to use it is: switch
it on, take screenshots, and switch it off the same day, which costs a few dollars.

If you do it, you need to add three things it is currently missing:

1. A certificate for the padlock, requested from Amazon and free.
2. An address book entry pointing your `api.` name at the Amazon load balancer.
3. An nginx container, which is the piece that makes it genuinely match how
   ChurnGuard's big cousin works. There is a real example to copy in the HG files
   at `piq-customer-ui/base/nginx/nginx.conf`.

Then change one line in `vercel.json` to point at Amazon instead of Render. Nothing
else changes, which is the reward for doing Step 2 first.

Before switching it on, check that `enable_nat_gateway` is still `false`. Turning
it on adds $33 a month, which is more than the thing it protects.

When you are done: `terraform destroy` turns everything off and stops the billing.

## Things we are deliberately not doing

Every one of these is something HG uses. None of them make sense for one app.

| Skipping | Why |
|---|---|
| Kubernetes / EKS | $73 a month before you run anything at all. It is a system for coordinating hundreds of services. You have one |
| ArgoCD | Automated deployment machinery for large teams. Vercel and Render already deploy when you push |
| A firewall service | $6 to $8 a month to protect a demo that already limits how often people can use it |
| NAT gateway | $33 a month, more than the thing behind it costs |

Being able to explain why you left these out is genuinely worth more in an
interview than having switched them on.

## Checking everything, at the end

Paste these into a terminal one at a time. Replace `churnguard.com` with your name.

```bash
dig churnguard.com +short
curl -sI https://churnguard.com | head -1
curl -s https://churnguard.com/api/health
curl -s https://churnguard.com/api/ready
```

The first shows your address book is working. The second should say `200`, meaning
the site loaded. The last two are the important ones: if they answer from your own
domain, the front door is working and your back end is properly hidden.

## The order, one more time

1. Buy the name, set up the address book. Costs about $19 a year.
2. One front door. Free, and the most useful thing here.
3. A practice copy. Free.
4. Notice when it breaks. Free.
5. The Amazon version. Optional, only for showing off, switch it off after.

# Infrastructure

Terraform for the ChurnGuard backend on ECS Fargate. **Nothing here has been
applied.** `terraform validate` passes; the config has never created a resource.

## Cost

Applying this bills by the hour whether or not anyone uses it.

| Resource | Monthly |
|---|---|
| ALB | $17 |
| Fargate task (1 vCPU / 2 GB) | $36 |
| NAT gateway *(disabled by default)* | $33 |
| ECR, CloudWatch logs | ~$1 |
| **Default configuration** | **~$54** |

`desired_count = 0` stops the compute charge without destroying the stack. The
intended workflow for a demonstration is apply, verify, screenshot, destroy —
roughly $5–10 for a day.

## Two decisions worth knowing about

**NAT gateway is off by default.** The textbook layout puts tasks in private
subnets behind a NAT, which costs ~$33/month — more than the compute it protects,
at this size. With it off, tasks sit in public subnets and accept traffic *only*
from the ALB security group. Set `enable_nat_gateway = true` for anything holding
real customer data.

**The LLM stack is off by default.** `enable_rag = false` runs the degraded mode
built in phase 0.1: dashboard and health scoring work from CSV baked into the
image, and LLM endpoints return 503 naming the missing dependency. Enabling it
before phase 2.3 adds authentication and rate limiting would put an uncapped
OpenAI bill behind a public URL.

## Health checks

The ALB target group checks `/ready`, not `/health`. `/health` returns 200
whenever the process is up, so a task with no data and no LLM stack would be
marked healthy and sent traffic. `/ready` returns 503 when the service cannot
actually serve. That split exists for exactly this.

## Usage

```bash
cp terraform.tfvars.example terraform.tfvars   # then edit
aws sso login --profile personal

export AWS_PROFILE=personal
terraform init
terraform plan          # free; creates nothing
terraform apply         # starts billing
terraform destroy       # stops it
```

Push the image after the first apply — `terraform output next_steps` prints the
exact commands with the repository URL filled in.

## Secrets

The OpenAI key is **not** in Terraform. Create it separately so it never enters
state, which is stored in plaintext:

```bash
aws secretsmanager create-secret \
  --name churnguard/openai \
  --secret-string '{"OPENAI_API_KEY":"sk-..."}'
```

Then set `openai_secret_arn` in `terraform.tfvars`. The ECS agent resolves it at
task start; the value never reaches the task definition or a log line.

## State

Local by default, which is fine for one operator. The S3 backend with DynamoDB
locking is commented out in `versions.tf` for when that stops being true.

## Not included

- **HTTPS** — needs an ACM certificate, which needs a domain. HTTP only for now.
- **Qdrant** — no managed instance. Point `qdrant_url` at Qdrant Cloud's free
  tier, or leave `enable_rag = false`.
- **Autoscaling** — fixed `desired_count`. Add a target-tracking policy when
  there is traffic worth scaling to.
- **Frontend** — backend only. The Next.js app deploys to Vercel or Amplify.

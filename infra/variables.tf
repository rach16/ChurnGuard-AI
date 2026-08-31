variable "region" {
  description = "AWS region. Must match where the Athena/Glue warehouse lives."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Name prefix for every resource."
  type        = string
  default     = "churnguard"
}

variable "enable_nat_gateway" {
  description = <<-EOT
    Put ECS tasks in private subnets behind a NAT gateway.

    This is the textbook layout, and it costs about $33/month whether or not any
    traffic flows -- more than the compute it protects, at this size. With it off,
    tasks run in public subnets with no inbound rules except from the ALB security
    group, which for a demo is a defensible trade. Turn it on for anything holding
    real customer data.
  EOT
  type        = bool
  default     = false
}

variable "enable_rag" {
  description = <<-EOT
    Whether the deployed API attempts to start its LLM stack.

    Defaults false, so the service runs in the degraded mode built in phase 0.1:
    the dashboard and health scoring work from baked-in CSV, and LLM endpoints
    return 503 naming the missing dependency. Enabling it requires a reachable
    Qdrant (see qdrant_url) and an OpenAI key in Secrets Manager, and should not
    be done before phase 2.3 adds authentication and rate limiting -- an open
    endpoint calling an LLM is an uncapped bill.
  EOT
  type        = bool
  default     = false
}

variable "qdrant_url" {
  description = "Vector store URL. Empty means no RAG. Qdrant Cloud has a free tier."
  type        = string
  default     = ""
}

variable "openai_secret_arn" {
  description = <<-EOT
    ARN of a Secrets Manager secret holding the OpenAI API key.

    Create it out of band -- `aws secretsmanager create-secret` -- so the key never
    passes through Terraform state, which is plaintext.
  EOT
  type        = string
  default     = ""
}

variable "task_cpu" {
  description = "Fargate CPU units. 1024 = 1 vCPU."
  type        = number
  default     = 1024
}

variable "task_memory" {
  description = "Fargate memory in MiB. Must be a valid pairing with task_cpu."
  type        = number
  default     = 2048
}

variable "desired_count" {
  description = "Number of tasks. 0 stops billing for compute without destroying the stack."
  type        = number
  default     = 1
}

variable "image_tag" {
  description = "Container image tag to deploy."
  type        = string
  default     = "latest"
}

variable "monthly_budget_usd" {
  description = "Billing alarm threshold. Fires to the SNS topic below."
  type        = number
  default     = 20
}

variable "alarm_email" {
  description = "Email for the billing alarm. Empty disables the subscription."
  type        = string
  default     = ""
}

variable "allowed_cidr" {
  description = <<-EOT
    CIDR allowed to reach the ALB. Defaults to the whole internet, which is only
    acceptable while enable_rag is false. Narrow it before enabling the LLM stack.
  EOT
  type        = string
  default     = "0.0.0.0/0"
}

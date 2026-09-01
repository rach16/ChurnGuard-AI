data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Used by the ECS agent to pull the image and resolve secrets. Distinct from the
# task role: the agent's permissions are not the application's.
resource "aws_iam_role" "execution" {
  name               = "${local.name}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Reading secrets is scoped to the exact ARNs configured, not secretsmanager:* .
#
# Built from whichever secrets are actually set. Scoping this to the OpenAI ARN
# alone would mean a deployment that sets only api_keys_secret_arn gets an
# execution role that cannot read it, and the task fails to start with a
# ResourceInitializationError that does not name the cause.
locals {
  secret_arns = compact([var.openai_secret_arn, var.api_keys_secret_arn])
}

data "aws_iam_policy_document" "read_secret" {
  count = length(local.secret_arns) == 0 ? 0 : 1

  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = local.secret_arns
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  count = length(local.secret_arns) == 0 ? 0 : 1

  name   = "${local.name}-read-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.read_secret[0].json
}

# What the application itself may do. Deliberately near-empty: the API reads
# baked-in CSV and talks to the LLM provider over the internet. It needs no AWS
# API access at all, so it gets none.
resource "aws_iam_role" "task" {
  name               = "${local.name}-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

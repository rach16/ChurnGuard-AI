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

# Reading the OpenAI key is scoped to the one secret, not secretsmanager:* .
data "aws_iam_policy_document" "read_secret" {
  count = var.openai_secret_arn == "" ? 0 : 1

  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.openai_secret_arn]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  count = var.openai_secret_arn == "" ? 0 : 1

  name   = "${local.name}-read-openai-secret"
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

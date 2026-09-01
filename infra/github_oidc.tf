# GitHub Actions deploys without a long-lived AWS key.
#
# The alternative -- an IAM user with an access key stored as a repository
# secret -- is a credential that never expires, cannot be scoped to a branch,
# and leaks permanently if the repository is ever compromised. This repository
# is public, which makes that worse rather than better.
#
# With OIDC, GitHub mints a short-lived token per run, AWS verifies it against
# GitHub's published keys, and the trust policy below decides which repository
# and which ref may assume the role. Nothing is stored on the GitHub side except
# the role ARN, which is not a secret.

variable "github_repository" {
  description = "owner/repo permitted to assume the deployment role."
  type        = string
  default     = "rach16/ChurnGuard-AI"
}

variable "enable_github_oidc" {
  description = <<-EOT
    Create the OIDC provider and deployment role.

    Off by default because an AWS account may already have the GitHub OIDC
    provider from another project, and creating a second one fails. Check with:

        aws iam list-open-id-connect-providers
  EOT
  type        = bool
  default     = false
}

data "aws_caller_identity" "current" {}

resource "aws_iam_openid_connect_provider" "github" {
  count = var.enable_github_oidc ? 1 : 0

  url = "https://token.actions.githubusercontent.com"

  client_id_list = ["sts.amazonaws.com"]

  # AWS stopped verifying this thumbprint for the GitHub provider in 2023 and
  # validates against its own trust store instead. It stays because the argument
  # is still required.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "github_assume" {
  count = var.enable_github_oidc ? 1 : 0

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github[0].arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Scoped to this repository's main branch. Without a `sub` condition ANY
    # GitHub repository in the world could assume this role -- the single most
    # common and most damaging mistake in an OIDC trust policy.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  count = var.enable_github_oidc ? 1 : 0

  name               = "${local.name}-github-actions"
  description        = "Assumed by GitHub Actions to push images and roll the ECS service."
  assume_role_policy = data.aws_iam_policy_document.github_assume[0].json
}

data "aws_iam_policy_document" "github_deploy" {
  count = var.enable_github_oidc ? 1 : 0

  # ECR: the login token is account-wide by API design; pushes are scoped to the
  # one repository.
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:CompleteLayerUpload",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
      "ecr:BatchGetImage",
    ]
    resources = [aws_ecr_repository.backend.arn]
  }

  # ECS: register a revision and update the one service. Deliberately no
  # ecs:DeleteService, ecs:DeleteCluster or ecs:RunTask -- a deploy pipeline
  # that can tear down the environment is a deploy pipeline that eventually will.
  statement {
    actions   = ["ecs:RegisterTaskDefinition", "ecs:DescribeTaskDefinition"]
    resources = ["*"] # neither supports resource-level permissions
  }

  statement {
    actions   = ["ecs:UpdateService", "ecs:DescribeServices"]
    resources = [aws_ecs_service.backend.id]
  }

  # Handing the task its execution and task roles. Scoped by PassedToService so
  # this cannot be used to attach those roles to anything other than ECS.
  statement {
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.execution.arn, aws_iam_role.task.arn]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  # The smoke test resolves the load balancer's DNS name.
  statement {
    actions   = ["elasticloadbalancing:DescribeLoadBalancers"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  count = var.enable_github_oidc ? 1 : 0

  name   = "${local.name}-github-deploy"
  role   = aws_iam_role.github_actions[0].id
  policy = data.aws_iam_policy_document.github_deploy[0].json
}

output "github_actions_role_arn" {
  description = "Set as the AWS_DEPLOY_ROLE_ARN repository secret. Not sensitive."
  value       = var.enable_github_oidc ? aws_iam_role.github_actions[0].arn : null
}

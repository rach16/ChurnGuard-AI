resource "aws_ecs_cluster" "main" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "disabled" # billable per metric; enable when there is traffic worth watching
  }
}

resource "aws_cloudwatch_log_group" "backend" {
  name              = "/ecs/${local.name}-backend"
  retention_in_days = 7 # logs are cheap but not free, and this is a demo
}

locals {
  # ENABLE_RAG drives the degraded mode from phase 0.1. With it false the task
  # serves dashboard endpoints from baked-in CSV and returns 503 on LLM routes,
  # which is the safe default before 2.3 adds auth and rate limiting.
  base_environment = [
    { name = "ENABLE_RAG", value = tostring(var.enable_rag) },
    { name = "QDRANT_URL", value = var.qdrant_url },
    { name = "LOG_LEVEL", value = "INFO" },
    { name = "DATA_FOLDER", value = "data" },
  ]

  # Secrets arrive as ARNs resolved by the ECS agent at start. The value never
  # enters the task definition, Terraform state, or a log line.
  secrets = var.openai_secret_arn == "" ? [] : [
    { name = "OPENAI_API_KEY", valueFrom = var.openai_secret_arn }
  ]
}

resource "aws_ecs_task_definition" "backend" {
  family                   = "${local.name}-backend"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name      = "backend"
    image     = "${aws_ecr_repository.backend.repository_url}:${var.image_tag}"
    essential = true

    portMappings = [{ containerPort = 8000, protocol = "tcp" }]

    environment = local.base_environment
    secrets     = local.secrets

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.backend.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "ecs"
      }
    }

    # Container-level check complements the ALB's. This one decides whether ECS
    # restarts the task; the ALB's decides whether it receives traffic.
    healthCheck = {
      command     = ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 90
    }
  }])
}

resource "aws_ecs_service" "backend" {
  name            = "${local.name}-backend"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.backend.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = local.task_subnets
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = local.assign_public
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.backend.arn
    container_name   = "backend"
    container_port   = 8000
  }

  # Give a cold task time to pull ~1.1 GB and boot before the ALB judges it.
  health_check_grace_period_seconds = 120

  # CI updates the image and pushes a new task definition revision; without this
  # Terraform would revert to the tag it last knew about.
  lifecycle {
    ignore_changes = [task_definition]
  }

  depends_on = [aws_lb_listener.http]
}

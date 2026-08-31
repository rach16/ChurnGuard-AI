resource "aws_lb" "main" {
  name               = "${local.name}-alb"
  load_balancer_type = "application"
  internal           = false
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  # No customer data transits this in the demo configuration, and deletion
  # protection makes `terraform destroy` fail, which is the normal path here.
  enable_deletion_protection = false

  tags = { Name = "${local.name}-alb" }
}

resource "aws_lb_target_group" "backend" {
  name        = "${local.name}-backend"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    enabled = true
    # /ready, not /health. /health returns 200 whenever the process is up, so an
    # instance with no data and no LLM stack would be marked healthy and receive
    # traffic. /ready returns 503 when the service cannot actually serve. The
    # distinction was added in phase 0.1 precisely for this.
    path                = "/ready"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  # The image is ~1.1 GB, so a cold task needs time to pull and boot.
  deregistration_delay = 30

  tags = { Name = "${local.name}-backend-tg" }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  # HTTP only. HTTPS needs an ACM certificate, which needs a domain -- out of
  # scope for 2.2. Do not put anything sensitive behind this until 2.3.
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.backend.arn
  }
}

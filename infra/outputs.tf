output "alb_url" {
  description = "Public URL of the API."
  value       = "http://${aws_lb.main.dns_name}"
}

output "ecr_repository_url" {
  description = "Push target for the backend image."
  value       = aws_ecr_repository.backend.repository_url
}

output "ecs_cluster_name" {
  description = "For `aws ecs update-service` and log tailing."
  value       = aws_ecs_cluster.main.name
}

output "log_group" {
  description = "CloudWatch log group for the backend."
  value       = aws_cloudwatch_log_group.backend.name
}

output "estimated_monthly_cost_usd" {
  description = "Rough standing cost of what this config creates, at desired_count."
  value = format(
    "~$%d/month (ALB $17, Fargate $%d, NAT $%d) -- destroy when not in use",
    17 + (var.desired_count * 36) + (var.enable_nat_gateway ? 33 : 0),
    var.desired_count * 36,
    var.enable_nat_gateway ? 33 : 0,
  )
}

output "next_steps" {
  description = "What to do after apply."
  value       = <<-EOT
    1. Build and push the image:
         aws ecr get-login-password --region ${var.region} \
           | docker login --username AWS --password-stdin ${aws_ecr_repository.backend.repository_url}
         docker build -f src/backend/Dockerfile --target backend -t ${aws_ecr_repository.backend.repository_url}:latest .
         docker push ${aws_ecr_repository.backend.repository_url}:latest
    2. Force a redeploy:
         aws ecs update-service --cluster ${aws_ecs_cluster.main.name} \
           --service ${local.name}-backend --force-new-deployment
    3. Check it:
         curl http://${aws_lb.main.dns_name}/ready
    4. When finished:
         terraform destroy
  EOT
}

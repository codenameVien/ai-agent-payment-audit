output "cluster_arn" {
  value = aws_ecs_cluster.this.arn
}

output "task_definition_arns" {
  value = { for name, task in aws_ecs_task_definition.service : name => task.arn }
}

output "services_enabled" {
  value = var.enable_services
}

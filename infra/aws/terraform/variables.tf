variable "aws_region" {
  type    = string
  default = "ap-northeast-2"
}

variable "environment" {
  type    = string
  default = "demo"
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "service_images" {
  description = "Immutable ECR image URIs keyed by api, dashboard, seller-gemini, seller-nemotron, commerce-gateway."
  type        = map(string)
}

variable "secret_arns" {
  description = "Shared Secrets Manager ARNs. Do not include ADMIN_SERVICE_TOKEN; place it in api_secret_arns."
  type        = map(string)
  sensitive   = true
}

variable "api_secret_arns" {
  description = "Secrets Manager ARNs injected only into the API task, including ADMIN_SERVICE_TOKEN."
  type        = map(string)
  sensitive   = true
  default     = {}
}

variable "plain_environment" {
  description = "Non-secret service configuration only. Never place keys or private material here."
  type        = map(string)
  default = {
    MONGODB_DATABASE = "pbl_audit"
    SIWE_CHAIN_ID    = "84532"
    COOKIE_SECURE    = "true"
  }
}

variable "enable_services" {
  description = "Cost gate. Keep false until images, secrets, networking, and deployment approval are ready."
  type        = bool
  default     = false
}

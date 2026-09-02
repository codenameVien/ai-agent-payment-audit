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
  description = "Secrets Manager ARNs keyed by MONGODB_URI, SESSION_SECRET_BASE64, PAYLOAD_MASTER_KEY_BASE64, INTERNAL_SERVICE_TOKEN, and optional provider/wallet keys."
  type        = map(string)
  sensitive   = true
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

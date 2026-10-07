variable "region" {
  type    = string
  default = "us-east-1"
}
variable "environment" {
  type    = string
  default = "dev"
}
variable "prefix" {
  type    = string
  default = "ecoguard3184"
}
variable "frontend_origins" {
  type    = list(string)
  default = ["http://localhost:5173", "http://127.0.0.1:5173"]
}
variable "execution_role_arn" {
  type        = string
  description = "Existing execution role. Learner Lab normally permits LabRole; record its broader permissions."
  default     = ""
}
variable "deploy_api" {
  type    = bool
  default = false
}
variable "runtime_enabled" {
  type        = bool
  description = "Reversible runtime/access pause. Keep deploy_api and all existing resource inputs unchanged; false retains resources and data."
  default     = true
}
variable "lambda_zip" {
  type    = string
  default = "../../build/aws-lambda.zip"
}
variable "worker_image" {
  type        = string
  description = "Immutable ECR image digest containing FFmpeg. Empty keeps preprocessing disabled."
  default     = ""
}
variable "inference_url" {
  type    = string
  default = ""
}
variable "google_invoker_email" {
  type    = string
  default = ""
}
variable "web_url" {
  type    = string
  default = "http://127.0.0.1:5173"
}
variable "model_manifest_version" {
  type    = string
  default = "ecoguard-models-2026-10-08-v3"
}
variable "lex_bot_id" {
  type    = string
  default = ""
}
variable "lex_alias_id" {
  type    = string
  default = ""
}

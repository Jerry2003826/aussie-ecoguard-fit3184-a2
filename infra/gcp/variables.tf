variable "project_id" { type = string }
variable "identity_only" {
  type        = bool
  description = "Only free IAM/WIF identity resources. No Cloud Run, model storage or registry."
  default     = true
}
variable "region" {
  type    = string
  default = "us-central1"
}
variable "prefix" {
  type    = string
  default = "ecoguard3184"
}
variable "aws_account_id" { type = string }
variable "aws_role_name" {
  type    = string
  default = "LabRole"
}
variable "image" {
  type    = string
  default = ""
}
variable "cognito_issuer" {
  type    = string
  default = ""
}
variable "cognito_client_id" {
  type    = string
  default = ""
}
variable "media_bucket" {
  type    = string
  default = ""
}
variable "manifest_sha256" {
  type    = string
  default = "6947e779fb740f252e35cff6f4ea90f03a87a3fe07d52d1c924bc8319ab1f2a6"
  validation {
    condition     = can(regex("^[a-f0-9]{64}$", var.manifest_sha256))
    error_message = "Manifest hash must be the SHA-256 of the image's locked manifest."
  }
}
variable "aws_region" {
  type    = string
  default = "us-east-1"
}
variable "inference_environment" {
  type    = map(string)
  default = {}
}

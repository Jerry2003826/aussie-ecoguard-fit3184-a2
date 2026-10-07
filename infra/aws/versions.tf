terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = ">= 6.0, < 7.0" }
  }
}
provider "aws" {
  region = var.region
  default_tags { tags = { Project = "FIT3184-A2", Application = "AussieEcoGuard", Environment = var.environment } }
}
data "aws_caller_identity" "current" {}

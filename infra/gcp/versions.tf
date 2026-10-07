terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    google = { source = "hashicorp/google", version = ">= 6.0, < 8.0" }
  }
}
provider "google" {
  project = var.project_id
  region  = var.region
}
data "google_project" "current" { project_id = var.project_id }

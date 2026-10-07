locals {
  apis = toset(var.identity_only ? ["iam.googleapis.com", "iamcredentials.googleapis.com", "sts.googleapis.com"] : ["run.googleapis.com", "artifactregistry.googleapis.com", "iam.googleapis.com", "iamcredentials.googleapis.com", "sts.googleapis.com", "storage.googleapis.com"])
}
resource "google_project_service" "required" {
  for_each           = local.apis
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
resource "google_storage_bucket" "models" {
  count                       = var.identity_only ? 0 : 1
  name                        = "${var.project_id}-models"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  versioning { enabled = true }
  depends_on = [google_project_service.required]
}
resource "google_artifact_registry_repository" "images" {
  count         = var.identity_only ? 0 : 1
  location      = var.region
  repository_id = "ecoguard"
  format        = "DOCKER"
  depends_on    = [google_project_service.required]
}
resource "google_service_account" "runtime" {
  count        = var.identity_only ? 0 : 1
  account_id   = "ecoguard-inference"
  display_name = "EcoGuard model runtime"
  depends_on   = [google_project_service.required]
}
resource "google_service_account" "invoker" {
  account_id   = "ecoguard-aws-invoker"
  display_name = "AWS short lived inference invoker"
  depends_on   = [google_project_service.required]
}
resource "google_storage_bucket_iam_member" "model_reader" {
  count  = var.identity_only ? 0 : 1
  bucket = google_storage_bucket.models[0].name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.runtime[0].email}"
}
resource "google_iam_workload_identity_pool" "aws" {
  workload_identity_pool_id = "ecoguard-aws"
  display_name              = "EcoGuard AWS"
  depends_on                = [google_project_service.required]
}
resource "google_iam_workload_identity_pool_provider" "aws" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.aws.workload_identity_pool_id
  workload_identity_pool_provider_id = "aws-role"
  attribute_mapping = {
    "google.subject"     = "assertion.arn"
    "attribute.aws_role" = "assertion.arn.contains('assumed-role') ? assertion.arn.extract('{account_arn}assumed-role/') + 'assumed-role/' + assertion.arn.extract('assumed-role/{role_name}/') : assertion.arn"
  }
  attribute_condition = "assertion.account == '${var.aws_account_id}' && attribute.aws_role == 'arn:aws:sts::${var.aws_account_id}:assumed-role/${var.aws_role_name}'"
  aws { account_id = var.aws_account_id }
}
resource "google_service_account_iam_member" "aws_invoker" {
  service_account_id = google_service_account.invoker.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.aws.name}/attribute.aws_role/arn:aws:sts::${var.aws_account_id}:assumed-role/${var.aws_role_name}"
}
resource "google_cloud_run_v2_service" "inference" {
  count               = !var.identity_only && var.image != "" ? 1 : 0
  name                = "ecoguard-inference"
  location            = var.region
  deletion_protection = true
  ingress             = "INGRESS_TRAFFIC_ALL"
  template {
    service_account                  = google_service_account.runtime[0].email
    timeout                          = "360s"
    max_instance_request_concurrency = 1
    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }
    containers {
      image = var.image
      ports { container_port = 8080 }
      resources {
        limits            = { cpu = "4", memory = "8Gi" }
        cpu_idle          = true
        startup_cpu_boost = true
      }
      dynamic "env" {
        for_each = merge(var.inference_environment, {
          SERVICE_AUTH_MODE         = "cloud-run"
          COGNITO_ISSUER            = var.cognito_issuer
          COGNITO_CLIENT_ID         = var.cognito_client_id
          SOURCE_S3_HOSTS           = "${var.media_bucket}.s3.${var.aws_region}.amazonaws.com"
          SOURCE_KEY_PREFIXES       = "derived/,queries/"
          MODEL_MANIFEST_PATH       = "/config/model.locked.json"
          MODEL_MANIFEST_SHA256     = var.manifest_sha256
          INFERENCE_TIMEOUT_SECONDS = "240"
        })
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }
  depends_on = [google_project_service.required, google_storage_bucket_iam_member.model_reader]
}
resource "google_cloud_run_v2_service_iam_member" "invoker" {
  count    = !var.identity_only && var.image != "" ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.inference[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.invoker.email}"
}

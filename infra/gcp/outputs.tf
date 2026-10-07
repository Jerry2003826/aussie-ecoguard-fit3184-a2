output "project_id" { value = var.project_id }
output "project_number" { value = data.google_project.current.number }
output "model_bucket" { value = try(google_storage_bucket.models[0].name, null) }
output "repository" { value = try("${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images[0].repository_id}", null) }
output "invoker_service_account" { value = google_service_account.invoker.email }
output "wif_provider_audience" { value = "//iam.googleapis.com/${google_iam_workload_identity_pool_provider.aws.name}" }
output "inference_url" { value = try(google_cloud_run_v2_service.inference[0].uri, null) }

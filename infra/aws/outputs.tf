output "region" { value = var.region }
output "media_bucket" { value = local.bucket_name }
output "table_names" { value = { for k, t in aws_dynamodb_table.data : k => t.name } }
output "queue_urls" { value = { for k, q in aws_sqs_queue.work : k => q.url } }
output "queue_arns" { value = { for k, q in aws_sqs_queue.work : k => q.arn } }
output "outbox_stream_arn" { value = aws_dynamodb_table.data["outbox"].stream_arn }
output "cognito_user_pool_id" { value = aws_cognito_user_pool.users.id }
output "cognito_client_id" { value = aws_cognito_user_pool_client.web.id }
output "cognito_issuer" { value = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.users.id}" }
output "worker_repository_url" { value = aws_ecr_repository.worker.repository_url }

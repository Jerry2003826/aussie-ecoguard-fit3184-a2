# S3 management in this Learner Lab

On 2026-10-07 the AWS provider 6.67.0 created the dedicated bucket but its
post-create refresh failed with an explicit organisation policy denial of
`s3:GetBucketObjectLockConfiguration`. The bucket itself was verified with
`HeadBucket`. The API is unrelated to this application's required storage.

The project therefore creates/verifies the bucket through the idempotent
`scripts/bootstrap-aws-bucket.ps1` using CreateBucket and HeadBucket. Terraform
manages encryption, public-access blocking, CORS, HTTPS policy and notifications.
The already created bucket was removed only from Terraform's bucket-resource
state; its objects and bucket were not deleted. Do not import it back while this
course policy remains, since the provider would issue the same denied read.

Bucket deletion is deliberately manual after all assessment retention obligations
end. `terraform destroy` retains the bucket. Avoid running destroy during marking.

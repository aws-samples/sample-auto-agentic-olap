# Terraform remote state init

Creates the remote backend the main configuration uses: an S3 state bucket + a
DynamoDB lock table. **Run once per AWS account.**

This configuration deliberately uses **local state** - it creates the very location
that remote state lives in, so it cannot manage itself, or destruction would hit a
circular dependency.

## Create

```bash
cd infra/terraform/bootstrap

terraform init
terraform apply -var 'state_bucket_name=medical-tfstate-<account-id>'
```

`state_bucket_name` **has no default**, because S3 bucket names are globally unique
and a default would cause collisions in another account. The rest have defaults:

```text
region            us-east-1
lock_table_name   medical-tf-lock
```

## Generate the backend config for both environments

After apply, write the outputs to files the main configuration uses at `init`:

```bash
terraform output -raw dev_backend_config  > ../environments/dev.s3.tfbackend
terraform output -raw prod_backend_config > ../environments/prod.s3.tfbackend
```

This overwrites the `<ACCOUNT_ID>` templates in the repo.

The differing `key` for the two environments is the point:

```text
medical/dev/terraform.tfstate
medical/prod/terraform.tfstate
```

Sharing one key would let `apply prod` change or delete dev's resources. The backend
in `versions.tf` is an empty partial configuration, precisely to force per-
environment separation - the backend block does not support variable interpolation,
so the key cannot be generated dynamically.

Then return to the main configuration:

```bash
cd ..
terraform init -backend-config=environments/dev.s3.tfbackend
```

## Destroy

A variable value must be given even though destroy does not use it:

```bash
terraform destroy -var 'state_bucket_name=medical-tfstate-<account-id>'
```

Without `-var` it enters an interactive prompt. Terraform requires a value for every
variable on plan / apply / destroy alike.

> **The name you type does not decide what is deleted**. destroy deletes the
> resources recorded in state, not the resources matching the name you typed. So a
> typo will not wrongly delete another bucket, but it also will not prevent deletion.

### destroy fails when the bucket is non-empty

The bucket has versioning on and no `force_destroy`:

```text
BucketNotEmpty: The bucket you tried to delete is not empty.
You must delete all versions in the bucket.
```

This is an intentional guardrail - a bucket holding state should not be deleted
contents-and-all by a single command. If you really must, empty it manually first:

```bash
aws s3api delete-objects --bucket <bucket> \
  --delete "$(aws s3api list-object-versions --bucket <bucket> \
    --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}')"

aws s3api delete-objects --bucket <bucket> \
  --delete "$(aws s3api list-object-versions --bucket <bucket> \
    --query '{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}}')"
```

Then re-run destroy.

## What if the local state is lost

Do not commit `terraform.tfstate` to the repo. Although it manages only two
resources, a state file is the kind of file that should stay out of version control,
and this one is easy to rebuild.

If lost, re-import:

```bash
terraform init

terraform import -var 'state_bucket_name=<bucket>' \
  aws_s3_bucket.tfstate <bucket>
terraform import -var 'state_bucket_name=<bucket>' \
  aws_s3_bucket_versioning.tfstate <bucket>
terraform import -var 'state_bucket_name=<bucket>' \
  aws_s3_bucket_server_side_encryption_configuration.tfstate <bucket>
terraform import -var 'state_bucket_name=<bucket>' \
  aws_s3_bucket_public_access_block.tfstate <bucket>
terraform import -var 'state_bucket_name=<bucket>' \
  aws_dynamodb_table.tf_lock medical-tf-lock
```

After import, run once to confirm no drift:

```bash
terraform plan -var 'state_bucket_name=<bucket>'
# expect No changes
```

## Bucket configuration

```text
versioning    on, state is recoverable
encryption    SSE, encrypted at rest
public access all blocked
lock table    DynamoDB, prevents concurrent applies from overwriting each other
```

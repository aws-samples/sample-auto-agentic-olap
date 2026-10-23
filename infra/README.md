# AWS deployment (Terraform)

This Terraform deploys the project to AWS: private network, containerized service,
hosted front-end, Cognito authentication (user pool created outside this stack).

It deploys **the PoC itself**, just running on AWS instead of locally. The
architecture matches real production; the only difference is resource sizing -
real production simply scales up RPUs, task replicas and the number of AZs.

> Validated with `terraform validate`; review the plan before the first apply.
> Before the first deploy, read
> [what you must prepare before deploying](#what-you-must-prepare-before-deploying).

## Two run forms

```text
local development    uvicorn + Vite
AWS deployment (this dir)   ECS + ALB + Redshift storage, single-domain HTTPS
```

The business logic (dbt models, semantic layer, MCP service, Vue front-end) is
identical on both sides. Only the networking and deployment differ.

Authentication is done by the ALB, not by the application. Listener rules in
`alb.tf` use the ALB's managed `jwt-validation` action:

- `/api/*` requires a Cognito ID token issued to the portal app client
  (`token_use = id`, `aud = cognito_client_id`). `/api/config` is the one
  exception: it returns the public hosted-UI settings the portal needs before
  login.
- `/mcp` and `/mcp/*` require a Cognito access token issued to the agent app
  client (`token_use = access`, `client_id = cognito_agent_client_id`).
- The ALB checks the RS256 signature against the user pool's JWKS and enforces
  `iss` and `exp`. A request that fails is rejected at the ALB and never reaches
  the service, and the service security group accepts traffic from the ALB only.

The application has no token verification, API keys or admin role of its own.
There is no row- or column-level data permission: any authenticated caller sees
every metric.

## What it creates

```text
VPC                    public/private subnets, one NAT Gateway, Internet Gateway
VPC Flow Logs          all traffic to CloudWatch Logs, 365-day retention
Default security group  all rules removed (deny all)
Security groups        ALB -> service -> Redshift ingress chain (egress from ALB and service is unrestricted)
Redshift Serverless    private, publicly_accessible = false
Redshift usage limit   monthly, auto-suspends when exceeded
ECS Fargate            cluster + service, one image serving the Vue front-end, REST API and MCP
ALB                    HTTPS ingress (443) with jwt-validation rules on /api and /mcp; port 80 does only a 301 redirect
Route 53               A / AAAA alias records for app_domain (the ALB is IPv4-only, so the AAAA record returns no address until the ALB is made dual-stack)
IAM roles              task role + execution role (see below; the dbt task reuses the task role)
```

Task role permissions (`iam.tf`):

```text
redshift-data:ExecuteStatement, redshift-serverless:GetCredentials
                                               this workgroup only
redshift-data:DescribeStatement / GetStatementResult / CancelStatement
                                               "*" (no resource type exists for these),
                                               limited to statements this role submitted
                                               (redshift-data:statement-owner-iam-userid)
secretsmanager:GetSecretValue                  the Redshift admin secret
logs:CreateLogStream / PutLogEvents            the service and dbt log groups
```

## One domain, HTTPS only

The Vue front-end, API and MCP are served by the same container and share one
hostname:

```text
https://app_domain/          Vue front-end portal
https://app_domain/api/...   REST API
https://app_domain/mcp       remote MCP endpoint
```

The reason is the browser's same-origin security policy: if the front-end is HTTPS
and the API is HTTP, `fetch` is blocked as mixed content. Sharing one origin also
eliminates CORS configuration.

So three variables are required; missing any one fails `terraform plan`:

```text
app_domain           the hostname shared by the UI and API
route53_zone_name    the public hosted zone that hostname lives in
acm_certificate_arn  a certificate covering that hostname, in the same region as the ALB
```

Port 80 returns only a 301 and serves no content. To turn it off entirely:

```hcl
enable_http_redirect = false
```

TLS terminates at the ALB. The application does not redirect plaintext or set an
HSTS header itself; plaintext reaches the container only from the ALB, on port
8000 inside the VPC (including the ALB's `/health` check).

## Network design

```text
Internet
    │
    │ https://app_domain
    ↓
  ALB (443, public subnet; 80 does only 301)
    │ security group: ALB only
    ↓
  ECS Fargate (private subnet)
    │ one container: Vue front-end static files + FastAPI + MCP
    │ security group: service only, port 5439
    ↓
  Redshift Serverless (private subnet)
```

Redshift is not exposed externally. The service reaches AWS APIs (Secrets Manager,
Redshift Data API) via the NAT Gateway, but inbound traffic can only come from the
ALB.

## Directory structure

```text
infra/terraform/
├── versions.tf         provider declarations + empty partial S3 backend (values from environments/*.s3.tfbackend)
├── variables.tf        input variables
├── network.tf          VPC, subnets, security groups
├── vpc_hardening.tf    default SG (deny all) + VPC Flow Logs
├── redshift.tf         private Redshift Serverless + usage limit
├── iam.tf              ECS task role and execution role
├── ecs.tf              cluster, task definition, service
├── alb.tf              public ALB + target group + HTTPS listener
├── dns.tf              Route 53 A / AAAA alias records
├── dbt_runner.tf       one-off dbt build task (runs dbt inside the VPC)
├── bootstrap/          one-time init of the remote state
└── environments/
    ├── dev.tfvars.example    dev variable template (sanitized, committed)
    ├── dev.tfvars            dev variables (real values, .gitignored)
    ├── dev.s3.tfbackend      dev state location
    ├── prod.tfvars           prod variables
    └── prod.s3.tfbackend     prod state location
```

## Values you must fill: what to change and when

`dev.tfvars` holds real values, is `.gitignore`d, and does not exist in the repo.
First copy it from the template:

```bash
cd infra/terraform/environments
cp dev.tfvars.example dev.tfvars   # then fill in your own real values
```

Everything you need to replace is in the table below. **No other file needs
changing once these are filled.**

| File | What to fill | When |
|---|---|---|
| `environments/dev.s3.tfbackend`<br>`environments/prod.s3.tfbackend` | `bucket` | after bootstrap, before the first `init` |
| `environments/dev.tfvars`<br>`environments/prod.tfvars` | `app_domain`<br>`route53_zone_name`<br>`acm_certificate_arn`<br>`service_image`<br>`dbt_runner_image`<br>`cognito_user_pool_id`<br>`cognito_client_id`<br>`cognito_agent_client_id`<br>`cognito_domain` | before the first `plan` |

The values come from different places:

```text
the backend bucket    from the bootstrap terraform output
domain/certificate    your existing hosted zone and ACM certificate
images                the two images you push to ECR
Cognito ids           the user pool and app clients you create beforehand (see medical-data-service/docs/mcp-oauth.md)
```

The `<ACCOUNT_ID>` in `bucket` and the `<...>` placeholders in tfvars are the
values to replace. Symptoms of getting them wrong:

```text
bucket not filled                  init errors with NoSuchBucket
app_domain / route53_zone_name /   plan errors (these three have validation)
acm_certificate_arn not filled
service_image empty                not validated; fails when ECS deploys the task
dbt_runner_image empty             not validated; the dbt task definition is silently skipped
```

The validation is deliberate - a single-domain HTTPS deployment cannot run without
the domain and certificate, so it is better to fail at `plan` than to deploy an
unreachable environment.

### Why the backend is not in code

The `backend` block **does not support variable interpolation**, so the key cannot
be generated dynamically per environment. If the key were hard-coded in
`versions.tf`, dev and prod would share one state:

```text
apply dev   → writes medical/xxx/terraform.tfstate
apply prod  → also writes medical/xxx/terraform.tfstate
              Terraform thinks dev's resources "need to become prod's"
              → changes or deletes the dev environment
```

So `versions.tf` has an empty partial configuration, and the key lives in each
environment's own `.s3.tfbackend` file:

```text
environments/dev.s3.tfbackend    key = "medical/dev/terraform.tfstate"
environments/prod.s3.tfbackend   key = "medical/prod/terraform.tfstate"
```

Switching environments requires an explicit re-`init`; that friction is
intentional.

## Remote state init

Run once per AWS account:

```bash
cd infra/terraform/bootstrap
terraform init
terraform apply -var 'state_bucket_name=medical-tfstate-<account-id>'
```

It creates the S3 state bucket and the DynamoDB lock table, and outputs the backend
config for both environments:

```bash
terraform output -raw dev_backend_config  > ../environments/dev.s3.tfbackend
terraform output -raw prod_backend_config > ../environments/prod.s3.tfbackend
```

This overwrites the `<ACCOUNT_ID>` template files in the repo, filling in the first row
of the table above.

> **Why bootstrap is separate**: the state bucket itself cannot be managed by the
> state that uses it, or deletion hits a circular dependency. So it is a small
> standalone configuration.

## Deploy

`init` must carry the environment's backend config, and `plan` and `apply` must
carry the same environment's tfvars. They use different files - **do not mix them**:

```bash
cd infra/terraform

# dev
terraform init  -backend-config=environments/dev.s3.tfbackend
terraform plan  -var-file=environments/dev.tfvars
terraform apply -var-file=environments/dev.tfvars
```

Switching to prod requires a re-init; `-reconfigure` makes Terraform discard the
previous environment's backend cache:

```bash
terraform init -reconfigure -backend-config=environments/prod.s3.tfbackend
terraform plan  -var-file=environments/prod.tfvars
terraform apply -var-file=environments/prod.tfvars
```

> **What happens without `-reconfigure`**: Terraform notices the backend changed and
> asks whether to migrate the current state to the new location. Answering yes
> copies dev's state into prod's state, and the two environments then point at the
> same resources. `-reconfigure` skips that prompt.

### What you must prepare before deploying

These must be in place before `apply`, or it will fail or deploy an unusable
environment:

```text
service_image        data-service image, must be built and pushed to ECR first
dbt_runner_image     dbt build image, must be built and pushed to ECR first
app_domain           the hostname shared by the UI and API
route53_zone_name    the hosted zone that hostname lives in
acm_certificate_arn  a certificate covering that hostname, same region as the ALB
remote backend       point -backend-config at the environment's .s3.tfbackend
```

### Build the two images first

This deployment needs two images: the data service (UI + API) and the dbt builder.
Push both to ECR.

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REG=$ACCOUNT.dkr.ecr.us-east-1.amazonaws.com

aws ecr create-repository --repository-name medical-data-service
aws ecr create-repository --repository-name medical-dbt-runner
aws ecr get-login-password | docker login --username AWS --password-stdin $REG

# data service: context is medical-data-service, -f backend/Dockerfile
cd medical-data-service
docker build --platform linux/amd64 -f backend/Dockerfile \
  -t $REG/medical-data-service:v1 .
docker push $REG/medical-data-service:v1

# dbt builder: context is medical-olap-dbt
cd ../medical-olap-dbt
docker build --platform linux/amd64 -t $REG/medical-dbt-runner:v1 .
docker push $REG/medical-dbt-runner:v1
```

> **`--platform linux/amd64` is required**. Fargate runs x86; on Apple Silicon,
> without this flag you build an arm64 image and the task fails to start with
> "exec format error".

Then put the two image addresses into tfvars:

```hcl
service_image    = "<account>.dkr.ecr.us-east-1.amazonaws.com/medical-data-service:v1"
dbt_runner_image = "<account>.dkr.ecr.us-east-1.amazonaws.com/medical-dbt-runner:v1"
```

### Redshift Serverless requires 3 AZs

`az_count` must be `>= 3`. Redshift Serverless requires subnets covering 3
different AZs, or the workgroup fails to create:

```text
There aren't enough free IP addresses in subnets to allow this operation.
Make sure that there are at least 9 free IP addresses in 3 subnets.
```

Both `dev.tfvars` and `prod.tfvars` are set to 3.

### DNS records must not conflict with existing ones

`app_domain` must have no same-name record in the hosted zone. If another service
used that name before (e.g. a CNAME pointing at an old ELB), `apply` errors with:

```text
InvalidChangeBatch: RRSet of type A ... conflicting RRSet of type CNAME
```

Confirm that record is truly retired, then delete it:

```bash
ZONE=$(aws route53 list-hosted-zones-by-name --dns-name <zone> \
  --query 'HostedZones[0].Id' --output text)
aws route53 list-resource-record-sets --hosted-zone-id $ZONE \
  --query "ResourceRecordSets[?starts_with(Name, '<app_domain>')]"
# once confirmed unused, delete with change-resource-record-sets --change-batch
```

## Loading data: the dbt runner

Redshift is private (`publicly_accessible = false`), so dbt **cannot connect from a
laptop**. `dbt_runner.tf` defines a one-off Fargate task that runs
`dbt debug`, `dbt seed --full-refresh`, `dbt run` and `dbt test` in the private subnet, reusing the service
security group (already allowed to reach Redshift on 5439).

After `apply`, run this task in the private subnet:

```bash
WG=medical-dev-wg
SUBNETS=$(aws ec2 describe-subnets \
  --filters "Name=tag:Name,Values=medical-dev-private-*" \
  --query 'Subnets[].SubnetId' --output text | tr '\t' ',')
SG=$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=medical-dev-service-sg" \
  --query 'SecurityGroups[0].GroupId' --output text)

TASK=$(aws ecs run-task --cluster medical-dev-cluster \
  --task-definition medical-dev-dbt --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=DISABLED}" \
  --query 'tasks[0].taskArn' --output text)

aws ecs wait tasks-stopped --cluster medical-dev-cluster --tasks "$TASK"
aws ecs describe-tasks --cluster medical-dev-cluster --tasks "$TASK" \
  --query 'tasks[0].containers[0].exitCode' --output text   # expect 0

aws logs tail /ecs/medical-dev-dbt --since 15m | tail -5     # expect PASS=NN
```

It is a task definition, not a service: it stops when done. Re-run it whenever the
data models or seeds change.

> **Keep git in the dbt runner image**. git is used to install MetricFlow's pinned
> commit, and `dbt debug` checks for git on PATH and errors out if missing. The
> data-service image can drop git after install; the dbt image cannot.

## Resource sizing

The architecture is fixed; sizing is tuned via variables. `dev.tfvars.example`
is **demo-sized** (variable defaults: base 8 / max 32 / limit 500 RPU-hours):

| Variable | dev.tfvars.example | Real-production reference | Effect |
|---|---:|---:|---|
| `redshift_base_rpu` | 32 | 32-128 | query concurrency and latency. 8 RPU is the minimum used here |
| `redshift_max_rpu` | 64 | 256+ | elastic ceiling |
| `redshift_usage_limit_rpu_hours` | 300 | set by budget | monthly cost guardrail, auto-suspends when exceeded |
| `service_desired_count` | 1 | 2+ | ECS replicas. 1 means an interruption during deploys |
| `az_count` | 3 | 3 | number of AZs (default 3; validated >= 3 because Redshift Serverless requires three). One NAT Gateway regardless of az_count |

The difference between demo and production sizing is just the numbers. Additional
considerations:

```text
service_desired_count = 1   single replica, briefly unavailable during rolling updates
no auto-scaling policy       production should add ECS Service Auto Scaling
no Redshift reserved capacity for steady long-term load, reserved capacity lowers cost
```

## Environment variables: switching from local to AWS

The same code, with behavior decided by environment variables:

| Switch | Local dev | AWS deploy | Effect |
|---|---|---|---|
| `QUERY_EXECUTION_ENABLED` | `true` | `true` | whether to actually execute queries |
| `REDSHIFT_SECRET_ARN` etc. | manual export | injected by Terraform | Redshift connection info |

Authentication (ALB token validation for `/api/*` and `/mcp`) is configured
through the `cognito_*` variables; see the top of this page. Run locally, the
service has no authentication at all, so keep it bound to localhost.

## CI/CD

```text
.github/workflows/dbt.yml            dbt seed / run / test against Redshift + MCP tests against the new manifest
.github/workflows/data-service.yml   back-end test, front-end build, push ECR, deploy ECS
.github/workflows/infra.yml          terraform fmt / validate / plan, then apply of the reviewed plan
```

The jobs that call AWS (`dbt.yml` build, `data-service.yml` deploy, and both
`infra.yml` jobs) are skipped unless the repository variable
`AWS_CI_ENABLED` is `true`. A fresh fork or clone therefore runs only the
back-end tests and the front-end build. To enable the AWS jobs, create the OIDC
roles, add the `*_ROLE_ARN` secrets the workflows reference, then set
`AWS_CI_ENABLED=true` under Settings -> Secrets and variables -> Actions ->
Variables.

`dbt.yml` needs a runner with network access to the private workgroup and
`REDSHIFT_HOST` set; it does not work on GitHub-hosted runners as-is.

`infra.yml` separates `plan` and `apply`. `plan` runs on pushes and PRs that touch
`infra/terraform/` and uploads the plan file. `apply` runs only on `main`, in the
GitHub `prod` environment, and applies exactly that plan file. It waits for
approval only if you configure required reviewers on the `prod` environment, so
do that before enabling the workflow.

## Cost

Main cost sources, by magnitude:

```text
NAT Gateway       billed per hour + traffic; a single NAT Gateway (not per-AZ)
Redshift          per RPU-hour
ALB               per hour + LCU
ECS Fargate       per vCPU-hour and memory-hour (the dbt runner stops when done, no ongoing charge)
Route 53          per hosted zone and queries, very low
CloudWatch Logs   ingestion + storage for service, dbt and VPC Flow Logs (365-day retention)
```

When not in use, **destroy the whole stack** rather than stopping individual
components, because the NAT Gateway and ALB bill by the hour:

```bash
terraform destroy -var-file=environments/dev.tfvars
```

## Related docs

| To learn | See |
|---|---|
| Running the whole project for the first time | [GETTING-STARTED.md](../GETTING-STARTED.md) |
| MCP data-service architecture | [medical-data-service/README.md](../medical-data-service/README.md) |
| Warehouse and semantic layer | [medical-olap-dbt/README.md](../medical-olap-dbt/README.md) |
| Project overview | [README.md](../README.md) |

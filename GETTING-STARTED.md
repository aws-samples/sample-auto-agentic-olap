# Getting started

This runbook has two paths:

```text
Path A   Local validation, no database        No AWS needed   ~15 minutes
Path B   Full environment, Redshift + MCP      AWS needed      ~1 hour
```

Start with Path A. It parses the semantic layer, compiles every saved query and
runs the back-end tests, with no cost and no AWS account. Path B then deploys the
same code to AWS.

> **This sample is for demonstration and learning only. It is not intended for
> production use.** All business data is synthetic (see
> `medical-olap-dbt/scripts/generate_seeds.py`).

## Prerequisites

```text
Python 3.11+        the data service backend
Python 3.13         the dbt project (.venv)
Node 20+            the Vue front-end
uv                  https://docs.astral.sh/uv/
```

Path B additionally needs: an AWS account, Docker, the AWS CLI configured, a
public Route 53 hosted zone, and an ACM certificate covering your chosen hostname.

---

## Path A - local validation (no database)

### A1. dbt: parse the semantic layer

```bash
cd medical-olap-dbt
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt

# dbt parse needs only a profile, not a warehouse connection.
cp profiles.yml.example profiles.yml   # gitignored
DBT_PROFILES_DIR=. REDSHIFT_HOST=localhost REDSHIFT_USER=x REDSHIFT_PASSWORD=x \
  .venv/bin/python scripts/dbt_parse.py
```

This produces `target/semantic_manifest.json` - the compiled semantic layer the
MCP service reads. Regenerate the synthetic seed data at any time with:

```bash
.venv/bin/python scripts/generate_seeds.py   # fixed random seed, reproducible
```

### A2. Backend: compile saved queries and run tests

```bash
cd ../medical-data-service/backend
uv sync
export METRICFLOW_MANIFEST_PATH=../../medical-olap-dbt/target/semantic_manifest.json

# compile all saved queries offline (no warehouse)
uv run python ../../medical-olap-dbt/scripts/semantic/compile_saved_queries.py

# run the MCP backend tests
uv run --extra dev python -m pytest -q
```

Green tests here mean the semantic layer parses, every saved query compiles to
SQL, and the MCP tools work - all without a database.

### A3. (optional) Front-end dev server

```bash
cd ../frontend
npm install
npm run dev        # http://localhost:5173, /api proxied to :8000
```

To also serve the API locally, in another terminal run the backend. To actually
execute queries it needs the Redshift environment variables (see Path B); set
`QUERY_EXECUTION_ENABLED=false` to preview SQL only. The portal login also needs
`COGNITO_ISSUER`, `COGNITO_CLIENT_ID` and `COGNITO_DOMAIN`, and API-key mode on
`/mcp` needs AWS credentials to read the key secret (see
[medical-data-service/README.md](medical-data-service/README.md#running-locally)):

```bash
cd ../backend
uv run uvicorn app.main:app --reload --port 8000
```

---

## Path B - full environment on AWS (Redshift + MCP)

Path B deploys the same code to AWS with Terraform: a private Redshift Serverless
workgroup, an ECS Fargate service (Vue + REST API + MCP behind one HTTPS domain),
and an ALB. Full details, variables and troubleshooting are in
[infra/README.md](infra/README.md); the essential sequence is below.

### B1. Build and push the two images

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REG=$ACCOUNT.dkr.ecr.us-east-1.amazonaws.com
aws ecr create-repository --repository-name medical-data-service
aws ecr create-repository --repository-name medical-dbt-runner
aws ecr get-login-password | docker login --username AWS --password-stdin $REG

cd medical-data-service
docker build --platform linux/amd64 -f backend/Dockerfile -t $REG/medical-data-service:v1 .
docker push $REG/medical-data-service:v1

cd ../medical-olap-dbt
docker build --platform linux/amd64 -t $REG/medical-dbt-runner:v1 .
docker push $REG/medical-dbt-runner:v1
```

`--platform linux/amd64` is required (Fargate runs x86).

### B2. Initialize remote state (once per account)

```bash
cd ../infra/terraform/bootstrap
terraform init
terraform apply -var 'state_bucket_name=medical-tfstate-'"$ACCOUNT"
terraform output -raw dev_backend_config  > ../environments/dev.s3.tfbackend
terraform output -raw prod_backend_config > ../environments/prod.s3.tfbackend
```

### B3. Fill in variables and deploy

```bash
cd ..
cp environments/dev.tfvars.example environments/dev.tfvars
# edit dev.tfvars: app_domain, route53_zone_name, acm_certificate_arn,
#                  service_image, dbt_runner_image (the ECR URIs from B1)

terraform init  -backend-config=environments/dev.s3.tfbackend
terraform plan  -var-file=environments/dev.tfvars
terraform apply -var-file=environments/dev.tfvars
```

Redshift Serverless requires `az_count >= 3` (already set). The three HTTPS
variables are mandatory - `plan` fails without them by design.

### B4. Load data with the dbt runner

Redshift is private, so dbt runs as a one-off Fargate task inside the VPC
(`dbt debug`, `dbt seed --full-refresh`, `dbt run`, `dbt test`):

```bash
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
```

### B5. Connect an agent to the MCP endpoint

The service is now at `https://<app_domain>/`, with the MCP endpoint at
`https://<app_domain>/mcp`. With the default `mcp_oauth_enabled = true`, connect
using the `mcp-remote` configuration in
[medical-data-service/docs/mcp-oauth.md](medical-data-service/docs/mcp-oauth.md#amazon-q--claude-integration).
Only if you set `mcp_oauth_enabled = false`, use the API-key configuration in
[medical-data-service/README.md](medical-data-service/README.md#connecting-mcp-to-a-local-agent).

---

## Clean up when you are done

Path A leaves nothing to clean up (no cloud resources).

For Path B, destroy the whole stack rather than stopping individual components -
the NAT Gateway and ALB bill by the hour:

```bash
cd infra/terraform
terraform destroy -var-file=environments/dev.tfvars
```

The remote-state bucket and lock table created in B2 are deliberately not managed
by this stack; remove them manually only if you no longer need the state (see
[infra/terraform/bootstrap/README.md](infra/terraform/bootstrap/README.md#destroy)).

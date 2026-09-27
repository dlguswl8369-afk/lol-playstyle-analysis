# Cloud Run deployment

The repository is deployed as one Cloud Run service. FastAPI serves the static
`lol-coach-Frontend` directory, and the browser calls the API using same-origin
paths under `/api`.

## Runtime configuration

Store these values in Secret Manager and map them to environment variables with
the names shown:

| Secret Manager secret | Environment variable |
| --- | --- |
| `lol-riot-api-key` | `RIOT_API_KEY` |
| `lol-databricks-token` | `DATABRICKS_TOKEN` |
| `lol-azure-client-secret` | `AZURE_CLIENT_SECRET` |

Set these non-secret Cloud Run environment variables:

- `DATABRICKS_HOST`
- `DATABRICKS_JOB_ID`
- `AZURE_TENANT_ID`
- `AZURE_CLIENT_ID`
- `AZURE_SEARCH_ENDPOINT`
- `AZURE_SEARCH_INDEX`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_DEPLOYMENT`

Optional cache settings are `ANALYSIS_CACHE_TTL_SECONDS` and
`COACH_CACHE_TTL_SECONDS`; both default to 600 seconds.

The application reads local `.env` files for development only. They are excluded
from both Git and the Docker build context and must not be copied into the image.

## Build and deploy

This project deploys to `project-e05139e8-c2c5-482f-807` in
`asia-northeast3`. Secret values should be added to Secret Manager through a
secure local input method, not placed directly in shell history.

```powershell
$projectId = "project-e05139e8-c2c5-482f-807"
$region = "asia-northeast3"
$repository = "lol-insight-coach"
$service = "lol-insight-coach"
$serviceAccount = "lol-insight-coach-runner@$projectId.iam.gserviceaccount.com"
$image = "$region-docker.pkg.dev/$projectId/$repository/$service:<GIT_SHA>"

gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com --project $projectId
gcloud artifacts repositories create $repository --repository-format docker --location $region --project $projectId
gcloud builds submit --tag $image --project $projectId

gcloud run deploy $service `
  --image $image `
  --project $projectId `
  --region $region `
  --service-account $serviceAccount `
  --cpu 2 `
  --memory 2Gi `
  --min 1 `
  --max 5 `
  --concurrency 20 `
  --timeout 300 `
  --cpu-boost `
  --allow-unauthenticated `
  --set-secrets "RIOT_API_KEY=lol-riot-api-key:latest,DATABRICKS_TOKEN=lol-databricks-token:latest,AZURE_CLIENT_SECRET=lol-azure-client-secret:latest" `
  --set-env-vars "DATABRICKS_HOST=<DATABRICKS_HOST>,DATABRICKS_JOB_ID=<DATABRICKS_JOB_ID>,AZURE_TENANT_ID=<AZURE_TENANT_ID>,AZURE_CLIENT_ID=<AZURE_CLIENT_ID>,AZURE_SEARCH_ENDPOINT=<AZURE_SEARCH_ENDPOINT>,AZURE_SEARCH_INDEX=<AZURE_SEARCH_INDEX>,AZURE_OPENAI_ENDPOINT=<AZURE_OPENAI_ENDPOINT>,AZURE_OPENAI_DEPLOYMENT=<AZURE_OPENAI_DEPLOYMENT>"
```

Grant the Cloud Run service account `roles/secretmanager.secretAccessor` on only
the three listed secrets. Do not grant the role at project scope.

## Smoke checks

```powershell
$serviceUrl = gcloud run services describe lol-insight-coach --region asia-northeast3 --format "value(status.url)"
Invoke-WebRequest "$serviceUrl/health"
Invoke-WebRequest "$serviceUrl/"
```

Functional checks for Riot, Databricks, Azure AI Search, and Azure OpenAI should
each be limited to one request after the service is healthy.

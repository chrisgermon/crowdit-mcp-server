#!/bin/bash
# Crowd IT MCP Server - Cloud Run Deployment Script
# Run this from the directory containing server.py, Dockerfile, and pyproject.toml

set -e

# Configuration
PROJECT_ID="crowdmcp"
REGION="australia-southeast1"
SERVICE_NAME="crowdit-mcp-server"

echo "═══════════════════════════════════════════════════════════════"
echo "  Crowd IT MCP Server - Cloud Run Deployment"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "Project:  $PROJECT_ID"
echo "Region:   $REGION"
echo "Service:  $SERVICE_NAME"
echo ""

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo "❌ gcloud CLI not found. Install from: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

# Authenticate and set project
echo "🔐 Checking authentication..."
gcloud auth login --quiet 2>/dev/null || gcloud auth login
gcloud config set project $PROJECT_ID

# Enable required APIs
echo ""
echo "🔧 Enabling required APIs..."
gcloud services enable \
    run.googleapis.com \
    secretmanager.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com

# Create Artifact Registry repository if it doesn't exist
echo ""
echo "📦 Setting up Artifact Registry repository..."
if gcloud artifacts repositories describe crowdit --location=$REGION --project=$PROJECT_ID >/dev/null 2>&1; then
    echo "   ✓ Repository 'crowdit' already exists"
else
    gcloud artifacts repositories create crowdit \
        --repository-format=docker \
        --location=$REGION \
        --project=$PROJECT_ID \
        --description="Crowd IT MCP Server container images"
    echo "   ✓ Created repository: crowdit"
fi

# Create secrets (with placeholder values - update later)
echo ""
echo "🔑 Setting up secrets..."

# Function to create secret if it doesn't exist
create_secret_if_not_exists() {
    local secret_name=$1
    local secret_value=$2
    
    if gcloud secrets describe $secret_name --project=$PROJECT_ID >/dev/null 2>&1; then
        echo "   ✓ Secret $secret_name already exists"
    else
        echo -n "$secret_value" | gcloud secrets create $secret_name \
            --project=$PROJECT_ID \
            --replication-policy="user-managed" \
            --locations="$REGION" \
            --data-file=-
        echo "   ✓ Created secret: $secret_name"
    fi
}

# HaloPSA secrets
create_secret_if_not_exists "HALOPSA_RESOURCE_SERVER" "https://crowditau.halopsa.com/api"
create_secret_if_not_exists "HALOPSA_AUTH_SERVER" "https://crowditau.halopsa.com/auth"
create_secret_if_not_exists "HALOPSA_CLIENT_ID" "PLACEHOLDER_UPDATE_ME"
create_secret_if_not_exists "HALOPSA_CLIENT_SECRET" "PLACEHOLDER_UPDATE_ME"
create_secret_if_not_exists "HALOPSA_TENANT" "crowditau"

# Xero secrets
create_secret_if_not_exists "XERO_CLIENT_ID" "885D63E09E004D809F170024B2F0BDB9"
create_secret_if_not_exists "XERO_CLIENT_SECRET" "PLACEHOLDER_UPDATE_ME"
create_secret_if_not_exists "XERO_TENANT_ID" "PLACEHOLDER_UPDATE_ME"
create_secret_if_not_exists "XERO_REFRESH_TOKEN" "PLACEHOLDER_UPDATE_ME"

# SharePoint secrets
create_secret_if_not_exists "SHAREPOINT_CLIENT_ID" "PLACEHOLDER_UPDATE_ME"
create_secret_if_not_exists "SHAREPOINT_CLIENT_SECRET" "PLACEHOLDER_UPDATE_ME"
create_secret_if_not_exists "SHAREPOINT_TENANT_ID" "PLACEHOLDER_UPDATE_ME"
create_secret_if_not_exists "SHAREPOINT_REFRESH_TOKEN" "PLACEHOLDER_UPDATE_ME"

# Quoter OAuth secrets
create_secret_if_not_exists "QUOTER_CLIENT_ID" "PLACEHOLDER_UPDATE_ME"
create_secret_if_not_exists "QUOTER_CLIENT_SECRET" "PLACEHOLDER_UPDATE_ME"

# FortiCloud secrets
create_secret_if_not_exists "FORTICLOUD_API_KEY" "PLACEHOLDER_UPDATE_ME"
create_secret_if_not_exists "FORTICLOUD_API_SECRET" "PLACEHOLDER_UPDATE_ME"
create_secret_if_not_exists "FORTICLOUD_CLIENT_ID" "fortigatecloud"

# CIPP secrets (M365 Management)
create_secret_if_not_exists "CIPP_CLIENT_SECRET" "PLACEHOLDER_UPDATE_ME"

# n8n secrets (Workflow Automation)
create_secret_if_not_exists "N8N_API_URL" "PLACEHOLDER_UPDATE_ME"
create_secret_if_not_exists "N8N_API_KEY" "PLACEHOLDER_UPDATE_ME"

echo ""
echo "⚠️  NOTE: You need to update secrets after deployment:"
echo "   - HaloPSA: HALOPSA_CLIENT_ID, HALOPSA_CLIENT_SECRET"
echo "   - Xero: XERO_CLIENT_SECRET (then auth via Claude to get TENANT_ID and REFRESH_TOKEN)"
echo "   - SharePoint: All credentials (CLIENT_ID, CLIENT_SECRET, TENANT_ID from Azure AD)"
echo "   - FortiCloud: FORTICLOUD_API_KEY, FORTICLOUD_API_SECRET (from FortiCloud IAM)"
echo "   - CIPP: CIPP_CLIENT_SECRET (from Azure AD App Registration)"
echo "   - n8n: N8N_API_URL, N8N_API_KEY (from n8n instance API settings)"
echo ""

# Build and deploy to Cloud Run
echo "🏗️  Building and deploying to Cloud Run..."
echo "   This may take a few minutes..."
echo ""

gcloud run deploy $SERVICE_NAME \
    --project=$PROJECT_ID \
    --region=$REGION \
    --source=. \
    --platform=managed \
    --no-allow-unauthenticated \
    --memory=512Mi \
    --cpu=1 \
    --timeout=300 \
    --concurrency=80 \
    --min-instances=0 \
    --max-instances=3 \
    --set-env-vars="BIGQUERY_PROJECT_ID=$PROJECT_ID,BIGQUERY_JOB_PROJECT_ID=$PROJECT_ID,BIGQUERY_DATA_PROJECT_ID=vision-radiology,CIPP_TENANT_ID=299ea2a8-99a3-426c-9836-8a5c6eafe007,CIPP_CLIENT_ID=728a6a60-ba98-472f-b06a-3fb726ad8270,CIPP_API_URL=https://cippq7gcl.azurewebsites.net" \
    --set-secrets="HALOPSA_RESOURCE_SERVER=HALOPSA_RESOURCE_SERVER:latest,HALOPSA_AUTH_SERVER=HALOPSA_AUTH_SERVER:latest,HALOPSA_CLIENT_ID=HALOPSA_CLIENT_ID:latest,HALOPSA_CLIENT_SECRET=HALOPSA_CLIENT_SECRET:latest,HALOPSA_TENANT=HALOPSA_TENANT:latest,XERO_CLIENT_ID=XERO_CLIENT_ID:latest,XERO_CLIENT_SECRET=XERO_CLIENT_SECRET:latest,XERO_TENANT_ID=XERO_TENANT_ID:latest,XERO_REFRESH_TOKEN=XERO_REFRESH_TOKEN:latest,SHAREPOINT_CLIENT_ID=SHAREPOINT_CLIENT_ID:latest,SHAREPOINT_CLIENT_SECRET=SHAREPOINT_CLIENT_SECRET:latest,SHAREPOINT_TENANT_ID=SHAREPOINT_TENANT_ID:latest,SHAREPOINT_REFRESH_TOKEN=SHAREPOINT_REFRESH_TOKEN:latest,QUOTER_CLIENT_ID=QUOTER_CLIENT_ID:latest,QUOTER_CLIENT_SECRET=QUOTER_CLIENT_SECRET:latest,FORTICLOUD_API_KEY=FORTICLOUD_API_KEY:latest,FORTICLOUD_API_SECRET=FORTICLOUD_API_SECRET:latest,FORTICLOUD_CLIENT_ID=FORTICLOUD_CLIENT_ID:latest,CIPP_CLIENT_SECRET=CIPP_CLIENT_SECRET:latest,N8N_API_URL=N8N_API_URL:latest,N8N_API_KEY=N8N_API_KEY:latest"

# Get service URL
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --region=$REGION --format='value(status.url)')

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  ✅ Deployment Complete!"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "Service URL: $SERVICE_URL"
echo ""
echo "MCP Endpoint: ${SERVICE_URL}/mcp"
echo ""
echo "⚡ Next Steps:"
echo "   1. Update HaloPSA credentials: ./update-secrets.sh"
echo "   2. Configure Claude Desktop (see README.md)"
echo ""

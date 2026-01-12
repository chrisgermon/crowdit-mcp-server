#!/bin/bash
# Update Secrets for Crowd IT MCP Server

set -e

PROJECT_ID="crowdmcp"
REGION="australia-southeast1"
SERVICE_NAME="crowdit-mcp-server"

echo "═══════════════════════════════════════════════════════════════"
echo "  Update MCP Server Secrets"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "1) Update HaloPSA credentials"
echo "2) Update Xero client secret"
echo "3) Update Xero tokens (after auth via Claude)"
echo "4) Update CIPP client secret (M365 Management)"
echo "5) Update Salesforce credentials"
echo ""
read -p "Select option (1-5): " OPTION

case $OPTION in
    1)
        echo ""
        read -p "Enter HaloPSA Client ID: " CLIENT_ID
        read -sp "Enter HaloPSA Client Secret: " CLIENT_SECRET
        echo ""
        
        if [ -z "$CLIENT_ID" ] || [ -z "$CLIENT_SECRET" ]; then
            echo "❌ Both Client ID and Client Secret are required."
            exit 1
        fi
        
        echo -n "$CLIENT_ID" | gcloud secrets versions add HALOPSA_CLIENT_ID --project=$PROJECT_ID --data-file=-
        echo "   ✓ Updated HALOPSA_CLIENT_ID"
        
        echo -n "$CLIENT_SECRET" | gcloud secrets versions add HALOPSA_CLIENT_SECRET --project=$PROJECT_ID --data-file=-
        echo "   ✓ Updated HALOPSA_CLIENT_SECRET"
        ;;
    2)
        echo ""
        read -sp "Enter Xero Client Secret: " XERO_SECRET
        echo ""
        
        if [ -z "$XERO_SECRET" ]; then
            echo "❌ Client Secret is required."
            exit 1
        fi
        
        echo -n "$XERO_SECRET" | gcloud secrets versions add XERO_CLIENT_SECRET --project=$PROJECT_ID --data-file=-
        echo "   ✓ Updated XERO_CLIENT_SECRET"
        
        echo ""
        echo "Next steps:"
        echo "1. Restart Claude Desktop"
        echo "2. Ask Claude to run: xero_auth_start"
        echo "3. Complete the OAuth flow in your browser"
        echo "4. Run this script again with option 3 to save the tokens"
        ;;
    3)
        echo ""
        read -p "Enter Xero Tenant ID: " TENANT_ID
        read -p "Enter Xero Refresh Token: " REFRESH_TOKEN
        
        if [ -z "$TENANT_ID" ] || [ -z "$REFRESH_TOKEN" ]; then
            echo "❌ Both Tenant ID and Refresh Token are required."
            exit 1
        fi
        
        echo -n "$TENANT_ID" | gcloud secrets versions add XERO_TENANT_ID --project=$PROJECT_ID --data-file=-
        echo "   ✓ Updated XERO_TENANT_ID"
        
        echo -n "$REFRESH_TOKEN" | gcloud secrets versions add XERO_REFRESH_TOKEN --project=$PROJECT_ID --data-file=-
        echo "   ✓ Updated XERO_REFRESH_TOKEN"
        ;;
    4)
        echo ""
        read -sp "Enter CIPP Client Secret: " CIPP_SECRET
        echo ""

        if [ -z "$CIPP_SECRET" ]; then
            echo "❌ Client Secret is required."
            exit 1
        fi

        echo -n "$CIPP_SECRET" | gcloud secrets versions add CIPP_CLIENT_SECRET --project=$PROJECT_ID --data-file=-
        echo "   ✓ Updated CIPP_CLIENT_SECRET"
        ;;
    5)
        echo ""
        echo "Enter Salesforce credentials:"
        read -p "Instance URL (e.g., https://yourorg.my.salesforce.com): " SF_INSTANCE_URL
        read -p "Client ID: " SF_CLIENT_ID
        read -sp "Client Secret: " SF_CLIENT_SECRET
        echo ""
        read -p "Refresh Token: " SF_REFRESH_TOKEN

        if [ -z "$SF_INSTANCE_URL" ] || [ -z "$SF_CLIENT_ID" ] || [ -z "$SF_CLIENT_SECRET" ] || [ -z "$SF_REFRESH_TOKEN" ]; then
            echo "❌ All Salesforce credentials are required."
            exit 1
        fi

        # Create secrets if they don't exist, then add versions
        for SECRET in SALESFORCE_INSTANCE_URL SALESFORCE_CLIENT_ID SALESFORCE_CLIENT_SECRET SALESFORCE_REFRESH_TOKEN; do
            gcloud secrets describe $SECRET --project=$PROJECT_ID 2>/dev/null || \
                gcloud secrets create $SECRET --project=$PROJECT_ID --replication-policy=automatic
        done

        echo -n "$SF_INSTANCE_URL" | gcloud secrets versions add SALESFORCE_INSTANCE_URL --project=$PROJECT_ID --data-file=-
        echo "   ✓ Updated SALESFORCE_INSTANCE_URL"

        echo -n "$SF_CLIENT_ID" | gcloud secrets versions add SALESFORCE_CLIENT_ID --project=$PROJECT_ID --data-file=-
        echo "   ✓ Updated SALESFORCE_CLIENT_ID"

        echo -n "$SF_CLIENT_SECRET" | gcloud secrets versions add SALESFORCE_CLIENT_SECRET --project=$PROJECT_ID --data-file=-
        echo "   ✓ Updated SALESFORCE_CLIENT_SECRET"

        echo -n "$SF_REFRESH_TOKEN" | gcloud secrets versions add SALESFORCE_REFRESH_TOKEN --project=$PROJECT_ID --data-file=-
        echo "   ✓ Updated SALESFORCE_REFRESH_TOKEN"
        ;;
    *)
        echo "Invalid option"
        exit 1
        ;;
esac

echo ""
echo "🔄 Redeploying service to pick up new secrets..."

gcloud run services update $SERVICE_NAME \
    --project=$PROJECT_ID \
    --region=$REGION \
    --update-secrets="HALOPSA_RESOURCE_SERVER=HALOPSA_RESOURCE_SERVER:latest,HALOPSA_AUTH_SERVER=HALOPSA_AUTH_SERVER:latest,HALOPSA_CLIENT_ID=HALOPSA_CLIENT_ID:latest,HALOPSA_CLIENT_SECRET=HALOPSA_CLIENT_SECRET:latest,HALOPSA_TENANT=HALOPSA_TENANT:latest,XERO_CLIENT_ID=XERO_CLIENT_ID:latest,XERO_CLIENT_SECRET=XERO_CLIENT_SECRET:latest,XERO_TENANT_ID=XERO_TENANT_ID:latest,XERO_REFRESH_TOKEN=XERO_REFRESH_TOKEN:latest,SHAREPOINT_CLIENT_ID=SHAREPOINT_CLIENT_ID:latest,SHAREPOINT_CLIENT_SECRET=SHAREPOINT_CLIENT_SECRET:latest,SHAREPOINT_TENANT_ID=SHAREPOINT_TENANT_ID:latest,SHAREPOINT_REFRESH_TOKEN=SHAREPOINT_REFRESH_TOKEN:latest,QUOTER_API_KEY=QUOTER_API_KEY:latest,FORTICLOUD_API_KEY=FORTICLOUD_API_KEY:latest,FORTICLOUD_API_SECRET=FORTICLOUD_API_SECRET:latest,FORTICLOUD_CLIENT_ID=FORTICLOUD_CLIENT_ID:latest,CIPP_CLIENT_SECRET=CIPP_CLIENT_SECRET:latest,SALESFORCE_INSTANCE_URL=SALESFORCE_INSTANCE_URL:latest,SALESFORCE_CLIENT_ID=SALESFORCE_CLIENT_ID:latest,SALESFORCE_CLIENT_SECRET=SALESFORCE_CLIENT_SECRET:latest,SALESFORCE_REFRESH_TOKEN=SALESFORCE_REFRESH_TOKEN:latest"

echo ""
echo "✅ Secrets updated and service redeployed!"
echo ""

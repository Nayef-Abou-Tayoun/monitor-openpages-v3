# IBM Code Engine Deployment - NIRA Orchestrator

This folder contains the deployment package for the NIRA (New Initiative Risk Assessment) orchestrator that runs on IBM Code Engine.

## Overview

The NIRA orchestrator automatically:
1. Discovers concept documents in OpenPages process `AML_PROC_00081`
2. Downloads documents from OpenPages
3. Processes documents with Watson Orchestrate Risk Executive Summary Agent
4. Generates NIRA reports in DOCX format
5. Uploads NIRA reports back to OpenPages
6. Tracks processed documents to prevent duplicates
7. Deletes source documents after successful processing

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    IBM Code Engine                          │
│  ┌───────────────────────────────────────────────────────┐  │
│  │         nira_orchestrator.py (Main)                   │  │
│  │  ┌─────────────────────────────────────────────────┐  │  │
│  │  │  1. find_process.py                             │  │  │
│  │  │     - Discover documents in OpenPages           │  │  │
│  │  │     - Download document content                 │  │  │
│  │  └─────────────────────────────────────────────────┘  │  │
│  │  ┌─────────────────────────────────────────────────┐  │  │
│  │  │  2. process_concept_document.py                 │  │  │
│  │  │     - Process with Watson Orchestrate           │  │  │
│  │  │     - Generate NIRA DOCX reports                │  │  │
│  │  │     - Upload to OpenPages                       │  │  │
│  │  └─────────────────────────────────────────────────┘  │  │
│  │  ┌─────────────────────────────────────────────────┐  │  │
│  │  │  3. Cloud Object Storage (COS)                  │  │  │
│  │  │     - Track processed documents                 │  │  │
│  │  │     - Prevent duplicate processing              │  │  │
│  │  └─────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Files

- **`nira_orchestrator.py`** - Main orchestration script that coordinates the workflow
- **`find_process.py`** - Discovers and downloads documents from OpenPages
- **`process_concept_document.py`** - Processes documents with Watson Orchestrate and uploads NIRA reports
- **`extract_risks.py`** - Risk extraction utility
- **`Dockerfile`** - Container image definition for Code Engine
- **`requirements.txt`** - Python dependencies
- **`.env.example`** - Environment variables template (no credentials)
- **`.gitignore`** - Prevents committing sensitive files

## Environment Variables

The application requires the following environment variables (see `.env.example`):

### OpenPages Configuration
- `OPENPAGES_SERVER` - OpenPages server URL
- `OPENPAGES_USERNAME` - OpenPages username
- `OPENPAGES_PASSWORD` - OpenPages password
- `PROCESS_ID` - Process ID (31619)
- `PROCESS_NAME` - Process name (AML_PROC_00081)

### Watson Orchestrate Configuration
- `WXO_INSTANCE_ID` - Watson Orchestrate instance ID
- `WXO_API_KEY` - Watson Orchestrate API key
- `WXO_AGENT_ID` - Agent ID (Risk_Exec_summary_agent_1082Mm: `99f19079-0e86-4baf-965f-a17ebc7e672b`)

### Cloud Object Storage Configuration
- `COS_API_KEY` - COS API key
- `COS_INSTANCE_CRN` - COS instance CRN
- `COS_ENDPOINT` - COS endpoint URL
- `COS_BUCKET_NAME` - COS bucket name

## Deployment to IBM Code Engine

### Prerequisites
- IBM Cloud CLI installed
- Code Engine plugin installed
- Docker installed (for local testing)

### Deploy Steps

1. **Login to IBM Cloud**
   ```bash
   ibmcloud login --sso
   ibmcloud target -r us-south -g Default
   ```

2. **Select Code Engine project**
   ```bash
   ibmcloud ce project select --name ce-itz-wxo-69a709b76a5ccd84f408bf
   ```

3. **Build and push Docker image**
   ```bash
   cd code_engine_deployment
   
   # Build image
   docker build -t nira-orchestrator:latest .
   
   # Tag for IBM Container Registry
   docker tag nira-orchestrator:latest us.icr.io/namespace/nira-orchestrator:latest
   
   # Push to registry
   docker push us.icr.io/namespace/nira-orchestrator:latest
   ```

4. **Create or update Code Engine application**
   ```bash
   # Create new application
   ibmcloud ce application create \
     --name trigger-openpages-wxo \
     --image us.icr.io/namespace/nira-orchestrator:latest \
     --cpu 1 \
     --memory 2G \
     --min-scale 0 \
     --max-scale 1 \
     --env-from-configmap nira-config \
     --env-from-secret nira-secrets
   
   # Or update existing application
   ibmcloud ce application update \
     --name trigger-openpages-wxo \
     --image us.icr.io/namespace/nira-orchestrator:latest
   ```

5. **Set environment variables**
   ```bash
   # Create configmap for non-sensitive config
   ibmcloud ce configmap create --name nira-config \
     --from-literal PROCESS_NAME=AML_PROC_00081 \
     --from-literal PROCESS_ID=31619 \
     --from-literal WXO_INSTANCE_ID=20260417-1446-4932-90c9-7832e3e928c3 \
     --from-literal WXO_AGENT_ID=99f19079-0e86-4baf-965f-a17ebc7e672b \
     --from-literal OPENPAGES_SERVER=http://na4.services.cloud.techzone.ibm.com:45439/openpages \
     --from-literal COS_ENDPOINT=https://s3.us-south.cloud-object-storage.appdomain.cloud \
     --from-literal COS_BUCKET_NAME=openpages-objects
   
   # Create secret for sensitive credentials
   ibmcloud ce secret create --name nira-secrets \
     --from-literal OPENPAGES_USERNAME=OpenPagesAdministrator \
     --from-literal OPENPAGES_PASSWORD=OpenPagesAdministrator \
     --from-literal WXO_API_KEY=your-api-key \
     --from-literal COS_API_KEY=your-cos-api-key \
     --from-literal COS_INSTANCE_CRN=your-cos-crn
   ```

## Local Testing

1. **Create `.env` file** (copy from `.env.example` and fill in credentials)
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run orchestrator**
   ```bash
   python nira_orchestrator.py
   ```

## Monitoring

### View application logs
```bash
ibmcloud ce application logs --name trigger-openpages-wxo --follow
```

### Check application status
```bash
ibmcloud ce application get --name trigger-openpages-wxo
```

### View processed documents tracking
The orchestrator maintains a `processed_documents.json` file in Cloud Object Storage that tracks all processed document IDs to prevent duplicate processing.

## Features

### Duplicate Prevention
- Tracks processed documents in COS (`processed_documents.json`)
- Skips documents that have already been processed
- Prevents redundant NIRA report generation

### 5-Run Optimization
- Runs Watson Orchestrate agent 5 times per document
- Selects the result with the maximum number of identified risks
- Ensures comprehensive risk assessment

### Automatic Cleanup
- Deletes source documents from OpenPages after successful processing
- Keeps OpenPages clean and organized
- Prevents document accumulation

### Error Handling
- Robust error handling and logging
- Continues processing remaining documents if one fails
- Detailed error messages for troubleshooting

## Agent Information

**Risk_Exec_summary_agent_1082Mm**
- **Agent ID**: `99f19079-0e86-4baf-965f-a17ebc7e672b`
- **Purpose**: Generate executive risk summaries for NIRA (New Initiative Risk Assessment)
- **Use Case**: Document analysis, risk assessment, executive briefings

## Support

For issues or questions:
1. Check application logs in Code Engine
2. Verify environment variables are set correctly
3. Ensure OpenPages and Watson Orchestrate credentials are valid
4. Check COS bucket permissions

## Security Notes

- **Never commit `.env` files** - Contains sensitive credentials
- **Use IBM Cloud Secrets** - Store credentials securely in Code Engine
- **Rotate API keys regularly** - Follow security best practices
- **Monitor access logs** - Track API usage and access patterns

## Made with Bob
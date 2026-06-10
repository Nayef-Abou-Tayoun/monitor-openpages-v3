#!/usr/bin/env python3
"""
NIRA Orchestrator - Document Monitor Service
Monitors OpenPages process for new .docx files and uploads them to COS
"""

import os
import sys
import asyncio
import json
import base64
from datetime import datetime
from typing import Optional, Dict, Any, List
import httpx
import ibm_boto3
from ibm_botocore.client import Config
from dotenv import load_dotenv
from aiohttp import web

# Load environment variables
load_dotenv()

# Configuration
OPENPAGES_SERVER = os.getenv("OPENPAGES_SERVER")
OPENPAGES_USERNAME = os.getenv("OPENPAGES_USERNAME")
OPENPAGES_PASSWORD = os.getenv("OPENPAGES_PASSWORD")
PROCESS_ID = os.getenv("PROCESS_ID", "31619")
PROCESS_NAME = os.getenv("PROCESS_NAME", "AML_PROC_00081")

# COS Configuration
COS_API_KEY = os.getenv("COS_API_KEY")
COS_INSTANCE_CRN = os.getenv("COS_INSTANCE_CRN")
COS_ENDPOINT = os.getenv("COS_ENDPOINT")
COS_BUCKET_NAME = os.getenv("COS_BUCKET_NAME")

# Check interval
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "5"))

# Clear tracking flag
CLEAR_TRACKING = os.getenv("CLEAR_TRACKING", "false").lower() == "true"

# Watson Orchestrate Configuration
WXO_API_KEY = os.getenv("WXO_API_KEY")
WXO_INSTANCE_ID = os.getenv("WXO_INSTANCE_ID")
WXO_AGENT_ID = os.getenv("WXO_AGENT_ID")


class DocumentMonitor:
    """Monitor OpenPages for new .docx files and upload to COS"""
    
    def __init__(self):
        self.cos_client = None
        self.http_client = None
        self.session_cookies = None
        
        # Initialize COS client
        if all([COS_API_KEY, COS_INSTANCE_CRN, COS_ENDPOINT, COS_BUCKET_NAME]):
            self.cos_client = ibm_boto3.client(
                's3',
                ibm_api_key_id=COS_API_KEY,
                ibm_service_instance_id=COS_INSTANCE_CRN,
                config=Config(signature_version='oauth'),
                endpoint_url=COS_ENDPOINT
            )
            print("✓ IBM Cloud Object Storage initialized")
            print(f"   Bucket: {COS_BUCKET_NAME}")
        else:
            print("❌ COS not configured")
            sys.exit(1)
        
        # Track processed documents
        self.processed_docs_file = "processed_documents.json"
        self.processed_docs = self.load_processed_docs()
    
    async def establish_session(self) -> bool:
        """Establish authenticated session with OpenPages using form-based login"""
        if not self.http_client:
            self.http_client = httpx.AsyncClient(verify=False, follow_redirects=True, timeout=30.0)
        
        try:
            base_url = OPENPAGES_SERVER.rstrip('/') if OPENPAGES_SERVER else ""
            login_url = f"{base_url}/j_security_check"
            
            print(f"🔐 Establishing session with OpenPages...")
            
            # Form-based login
            login_data = {
                'j_username': OPENPAGES_USERNAME,
                'j_password': OPENPAGES_PASSWORD
            }
            
            response = await self.http_client.post(
                login_url,
                data=login_data,
                follow_redirects=True
            )
            
            # Check if we got session cookies
            if response.cookies and len(response.cookies) > 0:
                print(f"✓ Session established - received {len(response.cookies)} cookie(s)")
                # Mark session as established so we don't re-login
                self.session_cookies = True
                return True
            
            print("⚠ No session cookies received")
            return False
            
        except Exception as e:
            print(f"❌ Session establishment error: {str(e)}")
            return False
    
    def load_processed_docs(self) -> set:
        """Load list of already processed document IDs from COS"""
        # If CLEAR_TRACKING is set, delete the tracking file and start fresh
        if CLEAR_TRACKING:
            try:
                self.cos_client.delete_object(
                    Bucket=COS_BUCKET_NAME,
                    Key=self.processed_docs_file
                )
                print("🗑️  Cleared tracking file - will reprocess all documents")
            except:
                pass
            return set()
        
        try:
            response = self.cos_client.get_object(
                Bucket=COS_BUCKET_NAME,
                Key=self.processed_docs_file
            )
            data = json.loads(response['Body'].read())
            return set(data.get('processed_ids', []))
        except:
            return set()
    
    def save_processed_docs(self):
        """Save list of processed document IDs to COS"""
        try:
            data = {'processed_ids': list(self.processed_docs)}
            self.cos_client.put_object(
                Bucket=COS_BUCKET_NAME,
                Key=self.processed_docs_file,
                Body=json.dumps(data)
            )
        except Exception as e:
            print(f"⚠ Failed to save processed docs list: {str(e)}")
    
    def mark_as_processed(self, doc_id: str):
        """Mark a document as processed"""
        self.processed_docs.add(doc_id)
        self.save_processed_docs()
    
    def is_processed(self, doc_id: str) -> bool:
        """Check if document has already been processed"""
        return doc_id in self.processed_docs
    
    def _get_field_value(self, row: Dict[str, Any], field_name: str) -> Any:
        """Extract field value from OpenPages row structure"""
        if 'fields' in row:
            for field in row['fields']:
                if field.get('name') == field_name:
                    return field.get('value')
        return row.get(field_name)  # Fallback to direct access
    
    async def find_process_by_id(self, process_id: str) -> Optional[Dict[str, Any]]:
        """Find process by ID using OpenPages API"""
        if not self.http_client:
            self.http_client = httpx.AsyncClient(verify=False, follow_redirects=True, timeout=30.0)
        
        # Establish session if not already done
        if not self.session_cookies:
            await self.establish_session()
        
        try:
            # Query for the process - use correct API endpoint
            url = f"{OPENPAGES_SERVER}/opgrc/api/v2/query"
            
            headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
            
            query = f"SELECT * FROM [SOXProcess] WHERE [Name] = '{process_id}'"
            payload = {
                'statement': query,
                'offset': 0,
                'max_rows': 500,
                'limit': 1,
                'case_insensitive': False,
                'honor_primary': False
            }
            
            response = await self.http_client.post(url, json=payload, headers=headers)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('rows') and len(result['rows']) > 0:
                    return result['rows'][0]
            else:
                print(f"⚠ Query failed with status {response.status_code}")
            
            return None
        except Exception as e:
            print(f"⚠ Request error: {str(e)}")
            return None
    
    async def find_documents_in_process(self, resource_id: str) -> List[Dict[str, Any]]:
        """Find documents attached to a process using REST API (same as original find_process.py)"""
        if not self.http_client:
            self.http_client = httpx.AsyncClient(verify=False, follow_redirects=True, timeout=30.0)
        
        try:
            # Remove /openpages from base URL and construct API URL (same as find_process.py)
            base = OPENPAGES_SERVER.replace('/openpages', '').rstrip('/') if OPENPAGES_SERVER else ""
            url = f"{base}/grc/api/contents/{resource_id}/associations/children"
            
            headers = {
                'Accept': 'application/json'
            }
            
            # Use Basic Auth (same as find_process.py)
            response = await self.http_client.get(
                url,
                headers=headers,
                auth=(OPENPAGES_USERNAME, OPENPAGES_PASSWORD)
            )
            
            if response.status_code == 200:
                # Check if response has content
                if not response.text or response.text.strip() == '':
                    print(f"✅ No documents found in process (empty response)")
                    return []
                
                try:
                    result = response.json()
                except Exception as json_error:
                    print(f"⚠ Response is not JSON. Status: {response.status_code}")
                    print(f"   Response text: {response.text[:200]}")
                    return []
                
                # The API returns a list of child objects
                children = []
                if isinstance(result, list):
                    children = result
                elif isinstance(result, dict) and 'children' in result:
                    children = result['children']
                
                if not children:
                    print(f"✅ No documents found in process")
                    return []
                
                print(f"🔍 Found {len(children)} child object(s) via REST API")
                
                # Filter for documents (typeDefinitionId: 4, 22, 42, 46)
                documents = [child for child in children if child.get('typeDefinitionId') in ['4', '22', '42', '46']]
                
                if not documents:
                    print(f"✅ No documents found (filtered by type)")
                    return []
                
                print(f"✅ Found {len(documents)} document(s) after filtering")
                
                # Fetch detailed information for each document to get parentFolderId
                detailed_docs = []
                for child in documents:
                    doc_id = child.get('id')
                    doc_name = child.get('name', 'Unknown')
                    
                    # Get document details including parentFolderId
                    detail_url = f"{base}/grc/api/contents/{doc_id}"
                    detail_response = await self.http_client.get(
                        detail_url,
                        headers=headers,
                        auth=(OPENPAGES_USERNAME, OPENPAGES_PASSWORD)
                    )
                    
                    if detail_response.status_code == 200:
                        doc_details = detail_response.json()
                        # Add parentFolderId to the document info
                        child['parentFolderId'] = doc_details.get('parentFolderId')
                        child['name'] = doc_details.get('name', doc_name)
                        detailed_docs.append(child)
                    else:
                        # Still include the document even if we can't get details
                        detailed_docs.append(child)
                
                return detailed_docs
            else:
                print(f"⚠ REST API failed with status {response.status_code}")
                return []
            
        except Exception as e:
            print(f"⚠ Request error: {str(e)}")
            return []
    
    async def download_document(self, doc_id: str, doc_name: str) -> Optional[bytes]:
        """Download document content from OpenPages"""
        if not self.http_client:
            self.http_client = httpx.AsyncClient(verify=False, follow_redirects=True, timeout=30.0)
        
        try:
            # Use correct base URL
            url = f"{OPENPAGES_SERVER}/grc/api/contents/{doc_id}/file"
            
            response = await self.http_client.get(
                url,
                auth=(OPENPAGES_USERNAME, OPENPAGES_PASSWORD)
            )
            
            if response.status_code == 200:
                return response.content
            else:
                print(f"   ⚠ Failed to download: HTTP {response.status_code}")
                return None
        except Exception as e:
            print(f"   ❌ Error downloading: {str(e)}")
            return None
    
    async def upload_to_cos(self, filename: str, content: bytes, parent_folder_id: str = None) -> bool:
        """Upload file to COS with metadata"""
        try:
            # Prepare metadata
            metadata = {}
            if parent_folder_id:
                metadata['parentfolderid'] = parent_folder_id
                print(f"   📁 Parent Folder ID: {parent_folder_id}")
            
            self.cos_client.put_object(
                Bucket=COS_BUCKET_NAME,
                Key=f"incoming/{filename}",
                Body=content,
                Metadata=metadata
            )
            print(f"   ✅ Uploaded to COS: incoming/{filename}")
            return True
        except Exception as e:
            print(f"   ❌ Failed to upload to COS: {str(e)}")
            return False
    
    async def process_new_documents(self):
        """Main workflow: Find new .docx files and upload to COS"""
        print("\n" + "="*70)
        print("🚀 DOCUMENT MONITOR - STARTING CHECK")
        print("="*70)
        print(f"Process ID: {PROCESS_NAME}")
        print("="*70)
        print()
        
        # Find the process
        print(f"🔍 Finding process: {PROCESS_NAME}...")
        process = await self.find_process_by_id(PROCESS_NAME)
        
        if not process:
            print(f"❌ Process {PROCESS_NAME} not found")
            return
        
        print(f"✅ Found process: {PROCESS_NAME}")
        resource_id = self._get_field_value(process, 'Resource ID')
        print(f"   Resource ID: {resource_id}")
        
        # Find documents in the process
        documents = await self.find_documents_in_process(resource_id)
        
        if not documents:
            print("✅ No documents found in process")
            return
        
        # Filter for .docx files only and unprocessed
        new_docx_files = []
        for doc in documents:
            doc_id = self._get_field_value(doc, 'Resource ID') or doc.get('id')
            doc_name = self._get_field_value(doc, 'Name') or doc.get('name') or 'Unknown'
            
            print(f"   Checking document: {doc_name} (ID: {doc_id})")
            
            # Check if it's a .docx file
            if not doc_name.lower().endswith('.docx'):
                print(f"      Skipping - not a .docx file")
                continue
            
            # Check if already processed
            if self.is_processed(str(doc_id)):
                print(f"      Skipping - already processed")
                continue
            
            print(f"      ✅ New document to process!")
            
            # Store document info including parentFolderId for later use
            new_docx_files.append({
                'id': doc_id,
                'name': doc_name,
                'parentFolderId': doc.get('parentFolderId')  # Store folder ID for uploading NIRA reports
            })
        
        if not new_docx_files:
            print("✅ No new .docx files to process")
            return
        
        print(f"\n📄 Found {len(new_docx_files)} new .docx file(s):")
        for i, doc in enumerate(new_docx_files, 1):
            print(f"   [{i}/{len(new_docx_files)}] {doc['name']}")
        
        # Download and upload each document
        for doc in new_docx_files:
            print(f"\n📥 Processing: {doc['name']}")
            
            # Download from OpenPages
            content = await self.download_document(doc['id'], doc['name'])
            if not content:
                continue
            
            print(f"   ✅ Downloaded ({len(content)} bytes)")
            
            # Upload to COS with parentFolderId metadata
            if await self.upload_to_cos(doc['name'], content, doc.get('parentFolderId')):
                # Mark as processed
                self.mark_as_processed(doc['id'])
                print(f"   ✅ Marked as processed")
    
    async def process_cos_documents(self):
        """Process documents from COS incoming folder"""
        print("\n🔍 Checking COS for documents to process...")
        try:
            # List objects in incoming folder
            response = self.cos_client.list_objects_v2(
                Bucket=COS_BUCKET_NAME,
                Prefix='incoming/'
            )
            
            if 'Contents' not in response:
                print("   ✅ No documents in incoming folder")
                return
            
            print(f"   Found {len(response['Contents'])} object(s) in incoming folder")
            
            for obj in response['Contents']:
                key = obj['Key']
                
                # Skip if not a .docx file
                if not key.lower().endswith('.docx'):
                    continue
                
                filename = os.path.basename(key)
                print(f"\n📥 Found document in COS: {filename}")
                
                # Download document from COS
                print(f"   Downloading from COS...")
                doc_response = self.cos_client.get_object(Bucket=COS_BUCKET_NAME, Key=key)
                doc_content = doc_response['Body'].read()
                print(f"   ✅ Downloaded ({len(doc_content)} bytes)")
                
                # Save temporarily with explicit flush
                temp_path = f"/tmp/{filename}"
                with open(temp_path, 'wb') as f:
                    f.write(doc_content)
                    f.flush()
                    os.fsync(f.fileno())
                
                # Verify file was saved
                if not os.path.exists(temp_path):
                    print(f"   ❌ Failed to save file to {temp_path}")
                    continue
                
                file_size = os.path.getsize(temp_path)
                print(f"   ✅ Saved to {temp_path} ({file_size} bytes)")
                
                # Get metadata (parentFolderId)
                metadata = doc_response.get('Metadata', {})
                parent_folder_id = metadata.get('parentfolderid', '31628')
                
                # Process with Watson Orchestrate
                print(f"   🤖 Processing with Watson Orchestrate...")
                from process_concept_document import ConceptDocumentProcessor
                
                processor = ConceptDocumentProcessor()
                result = processor.process_document(
                    temp_path,
                    doc_id=key,
                    process_id=PROCESS_ID,
                    num_runs=1  # Single run for automated processing
                )
                
                if result:
                    print(f"   ✅ NIRA report generated successfully")
                    
                    # Delete the input document from COS
                    print(f"   🗑️  Deleting input document from COS...")
                    self.cos_client.delete_object(Bucket=COS_BUCKET_NAME, Key=key)
                    print(f"   ✅ Input document deleted")
                else:
                    print(f"   ❌ Failed to generate NIRA report")
                
                # Clean up temp file
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                    
        except Exception as e:
            print(f"❌ Error processing COS documents: {str(e)}")
            import traceback
            traceback.print_exc()
    
    async def run(self):
        """Run the monitor in a continuous loop"""
        print("\n" + "="*70)
        print("🎯 DOCUMENT MONITOR SERVICE STARTED")
        print("="*70)
        print(f"Monitoring process: {PROCESS_NAME}")
        print(f"Check interval: {CHECK_INTERVAL_SECONDS} seconds")
        print(f"COS Bucket: {COS_BUCKET_NAME}")
        print("="*70)
        
        while True:
            try:
                # Part 1: Monitor OpenPages for new documents
                await self.process_new_documents()
                
                # Part 2: Process documents from COS
                await self.process_cos_documents()
            except Exception as e:
                print(f"\n❌ Error in monitoring loop: {str(e)}")
            
            print(f"\n⏳ Waiting {CHECK_INTERVAL_SECONDS} seconds before next check...")
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)


async def health_check(request):
    """Health check endpoint for Code Engine readiness probe"""
    return web.Response(text='OK', status=200)


async def start_health_server():
    """Start HTTP health check server"""
    app = web.Application()
    app.router.add_get('/health', health_check)
    app.router.add_get('/', health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    print("✓ Health check server started on port 8080")


async def main():
    """Main entry point - run both health server and monitor"""
    # Start health check server
    await start_health_server()
    
    # Start document monitor
    monitor = DocumentMonitor()
    await monitor.run()


if __name__ == "__main__":
    asyncio.run(main())

# Made with Bob

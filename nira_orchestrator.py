#!/usr/bin/env python3
"""
NIRA Orchestrator - Complete Workflow
1. Monitor OpenPages process for new documents
2. Upload documents to COS
3. Process documents with Watson Orchestrate
4. Upload NIRA reports back to OpenPages
"""

import os
import sys
import asyncio
import json
from datetime import datetime
from typing import Optional, Dict, Any, List
import httpx
import ibm_boto3
from ibm_botocore.client import Config
from dotenv import load_dotenv

# Import our existing modules
from find_process import ProcessFinder
from process_concept_document import ConceptDocumentProcessor

# Load environment variables
load_dotenv()

# Configuration
OPENPAGES_SERVER = os.getenv("OPENPAGES_SERVER")
OPENPAGES_USERNAME = os.getenv("OPENPAGES_USERNAME")
OPENPAGES_PASSWORD = os.getenv("OPENPAGES_PASSWORD")
PROCESS_ID = os.getenv("PROCESS_ID", "31619")
PROCESS_NAME = os.getenv("PROCESS_NAME", "AML Process")

# COS Configuration
COS_API_KEY = os.getenv("COS_API_KEY")
COS_INSTANCE_CRN = os.getenv("COS_INSTANCE_CRN")
COS_ENDPOINT = os.getenv("COS_ENDPOINT")
COS_BUCKET_NAME = os.getenv("COS_BUCKET_NAME")


class NIRAOrchestrator:
    """Orchestrate the complete NIRA workflow"""
    
    def __init__(self):
        self.process_finder = None
        self.concept_processor = ConceptDocumentProcessor()
        self.cos_client = None
        
        # Initialize COS client if configured
        if all([COS_API_KEY, COS_INSTANCE_CRN, COS_ENDPOINT, COS_BUCKET_NAME]):
            self.cos_client = ibm_boto3.client(
                's3',
                ibm_api_key_id=COS_API_KEY,
                ibm_service_instance_id=COS_INSTANCE_CRN,
                config=Config(signature_version='oauth'),
                endpoint_url=COS_ENDPOINT
            )
            self.log("✅ COS client initialized")
        else:
            self.log("⚠ COS not configured - will process local files only")
        
        # Track processed documents
        self.processed_docs_file = "processed_documents.json"
        self.processed_docs = self.load_processed_docs()
    
    def load_processed_docs(self) -> set:
        """Load list of already processed document IDs"""
        if self.cos_client:
            try:
                response = self.cos_client.get_object(
                    Bucket=COS_BUCKET_NAME,
                    Key=self.processed_docs_file
                )
                data = json.loads(response['Body'].read())
                return set(data.get('processed_ids', []))
            except:
                return set()
        return set()
    
    def save_processed_docs(self):
        """Save list of processed document IDs to COS"""
        if self.cos_client:
            try:
                data = {'processed_ids': list(self.processed_docs)}
                self.cos_client.put_object(
                    Bucket=COS_BUCKET_NAME,
                    Key=self.processed_docs_file,
                    Body=json.dumps(data)
                )
            except Exception as e:
                self.log(f"⚠ Failed to save processed docs list: {str(e)}")
    
    def mark_as_processed(self, doc_id: str):
        """Mark a document as processed"""
        self.processed_docs.add(doc_id)
        self.save_processed_docs()
    
    def is_processed(self, doc_id: str) -> bool:
        """Check if document has already been processed"""
        return doc_id in self.processed_docs
    
    def log(self, message: str):
        """Print timestamped log message"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {message}")
    
    async def delete_document_from_openpages(self, doc_id: str) -> bool:
        """Delete a document from OpenPages after processing"""
        try:
            base = OPENPAGES_SERVER.replace('/openpages', '').rstrip('/')
            url = f"{base}/grc/api/contents/{doc_id}"
            
            async with httpx.AsyncClient(verify=False, follow_redirects=True) as http_client:
                response = await http_client.delete(
                    url,
                    auth=(OPENPAGES_USERNAME, OPENPAGES_PASSWORD),
                    timeout=30.0
                )
                
                if response.status_code in [200, 204]:
                    self.log(f"   🗑️  Deleted document from OpenPages (ID: {doc_id})")
                    return True
                else:
                    self.log(f"   ⚠ Failed to delete document: HTTP {response.status_code}")
                    return False
        except Exception as e:
            self.log(f"   ❌ Error deleting document: {str(e)}")
            return False
    
    async def find_and_download_documents(self, process_id: str) -> List[Dict[str, Any]]:
        """Find documents in OpenPages process and download them (skip already processed)"""
        self.log(f"🔍 Finding documents in process {process_id}...")
        
        if not all([OPENPAGES_SERVER, OPENPAGES_USERNAME, OPENPAGES_PASSWORD]):
            self.log("❌ OpenPages credentials not configured")
            return []
        
        # Initialize OpenPages client
        from find_process import OpenPagesClient
        openpages_client = OpenPagesClient()
        
        # Initialize process finder
        self.process_finder = ProcessFinder(openpages_client)
        
        # Find process and get documents
        process_info = await self.process_finder.find_process_with_health(process_id)
        
        if not process_info:
            self.log(f"❌ Process {process_id} not found")
            return []
        
        documents = []
        children = process_info.get('children', [])
        
        for child in children:
            if child.get('type') == 'SOXDocument':
                doc_id = child.get('id')
                doc_name = child.get('name', 'Unknown')
                
                # Skip if already processed
                if self.is_processed(doc_id):
                    self.log(f"   ⏭️  Skipping (already processed): {doc_name}")
                    continue
                
                # Download document content
                content = await self.process_finder.download_document(doc_id)
                
                if content:
                    documents.append({
                        'id': doc_id,
                        'name': doc_name,
                        'content': content,
                        'size': len(content)
                    })
                    self.log(f"   ✅ Downloaded: {doc_name} ({len(content)} bytes)")
        
        self.log(f"✅ Found {len(documents)} new documents to process")
        return documents
    
    def upload_to_cos(self, content: bytes, filename: str, process_id: str) -> bool:
        """Upload document to COS"""
        if not self.cos_client:
            self.log("⚠ COS not configured, skipping upload")
            return False
        
        try:
            key = f"Process_{process_id}/{filename}"
            self.cos_client.put_object(
                Bucket=COS_BUCKET_NAME,
                Key=key,
                Body=content
            )
            self.log(f"   ✅ Uploaded to COS: {key}")
            return True
        except Exception as e:
            self.log(f"   ❌ COS upload failed: {str(e)}")
            return False
    
    def download_from_cos(self, key: str) -> Optional[bytes]:
        """Download document from COS"""
        if not self.cos_client:
            return None
        
        try:
            response = self.cos_client.get_object(
                Bucket=COS_BUCKET_NAME,
                Key=key
            )
            return response['Body'].read()
        except Exception as e:
            self.log(f"   ❌ COS download failed: {str(e)}")
            return None
    
    def save_local_file(self, content: bytes, filename: str) -> str:
        """Save document locally for processing"""
        filepath = f"/tmp/{filename}"
        with open(filepath, 'wb') as f:
            f.write(content)
        return filepath
    
    async def process_document(self, doc_info: Dict[str, Any], process_id: str) -> Optional[str]:
        """Process a single document through the NIRA workflow"""
        doc_id = doc_info['id']
        doc_name = doc_info['name']
        doc_content = doc_info['content']
        
        self.log(f"\n{'='*70}")
        self.log(f"📄 Processing: {doc_name}")
        self.log(f"{'='*70}")
        
        # Step 1: Upload to COS (optional)
        if self.cos_client:
            self.upload_to_cos(doc_content, doc_name, process_id)
        
        # Step 2: Save locally for processing
        local_path = self.save_local_file(doc_content, doc_name)
        self.log(f"💾 Saved locally: {local_path}")
        
        # Step 3: Process with Watson Orchestrate
        try:
            result = await self.concept_processor.process_document(
                local_path,
                doc_id=doc_id,
                process_id=process_id,
                num_runs=5,
                upload_to_op=True
            )
            
            if result:
                self.log(f"✅ Successfully processed {doc_name}")
                
                # Step 4: Mark as processed
                self.mark_as_processed(doc_id)
                self.log(f"   ✅ Marked as processed")
                
                # Step 5: Delete from OpenPages to prevent reprocessing
                await self.delete_document_from_openpages(doc_id)
                
                return result.get('summary')
            else:
                self.log(f"❌ Failed to process {doc_name}")
                return None
                
        except Exception as e:
            self.log(f"❌ Error processing {doc_name}: {str(e)}")
            import traceback
            traceback.print_exc()
            return None
        finally:
            # Cleanup local file
            if os.path.exists(local_path):
                os.remove(local_path)
    
    async def run_workflow(self, process_id: str = None):
        """Run the complete NIRA workflow"""
        if not process_id:
            process_id = PROCESS_ID
        
        self.log(f"\n{'='*70}")
        self.log(f"🚀 NIRA ORCHESTRATOR - STARTING WORKFLOW")
        self.log(f"{'='*70}")
        self.log(f"Process ID: {process_id}")
        self.log(f"Process Name: {PROCESS_NAME}")
        self.log(f"{'='*70}\n")
        
        # Step 1: Find and download documents from OpenPages
        documents = await self.find_and_download_documents(process_id)
        
        if not documents:
            self.log("⚠ No documents found to process")
            return
        
        # Step 2: Process each document
        results = []
        for doc in documents:
            result = await self.process_document(doc, process_id)
            if result:
                results.append({
                    'document': doc['name'],
                    'status': 'success',
                    'summary': result[:200] + '...' if len(result) > 200 else result
                })
            else:
                results.append({
                    'document': doc['name'],
                    'status': 'failed'
                })
        
        # Step 3: Summary
        self.log(f"\n{'='*70}")
        self.log(f"✅ WORKFLOW COMPLETE")
        self.log(f"{'='*70}")
        self.log(f"Total documents: {len(documents)}")
        self.log(f"Successful: {sum(1 for r in results if r['status'] == 'success')}")
        self.log(f"Failed: {sum(1 for r in results if r['status'] == 'failed')}")
        self.log(f"{'='*70}\n")
        
        return results


async def main():
    """Main entry point"""
    # Get process ID from command line or environment
    process_id = sys.argv[1] if len(sys.argv) > 1 else PROCESS_ID
    
    # Validate configuration
    if not all([OPENPAGES_SERVER, OPENPAGES_USERNAME, OPENPAGES_PASSWORD]):
        print("❌ Error: OpenPages credentials not configured")
        print("   Required: OPENPAGES_SERVER, OPENPAGES_USERNAME, OPENPAGES_PASSWORD")
        sys.exit(1)
    
    try:
        orchestrator = NIRAOrchestrator()
        await orchestrator.run_workflow(process_id)
        sys.exit(0)
    except KeyboardInterrupt:
        print("\n\n⏹ Workflow stopped by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

# Made with Bob
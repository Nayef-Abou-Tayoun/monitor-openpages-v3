#!/usr/bin/env python3
"""
Extract All Risks from IBM OpenPages
Exports risk names and descriptions to CSV file
"""

import os
import csv
import httpx
import base64
from datetime import datetime
from dotenv import load_dotenv
from typing import List, Dict, Optional
import requests

# Disable SSL warnings
requests.packages.urllib3.disable_warnings()

# Load environment variables
load_dotenv()

# OpenPages Configuration
OPENPAGES_BASE_URL = os.getenv("OPENPAGES_BASE_URL")
OPENPAGES_USERNAME = os.getenv("OPENPAGES_USERNAME")
OPENPAGES_PASSWORD = os.getenv("OPENPAGES_PASSWORD")

# Risk object type ID in OpenPages (typically 65 for SOXRisk)
RISK_TYPE_ID = 65


class OpenPagesRiskExtractor:
    """Extract risks from IBM OpenPages"""
    
    def __init__(self):
        self.base_url = OPENPAGES_BASE_URL.rstrip('/')
        self.username = OPENPAGES_USERNAME
        self.password = OPENPAGES_PASSWORD
        
        # Create Basic Auth header
        auth_string = f"{self.username}:{self.password}"
        auth_bytes = auth_string.encode('ascii')
        auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
        
        self.headers = {
            'Authorization': f'Basic {auth_b64}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        self.client = httpx.Client(
            headers=self.headers,
            timeout=120.0,
            verify=False,  # For TechZone environments
            follow_redirects=True
        )
        
    def get_all_risks(self) -> List[Dict]:
        """
        Retrieve all risks from OpenPages using V1 API
        Returns list of dictionaries with risk data
        """
        print("🔍 Searching for all risks in OpenPages...")
        
        # Try multiple risk object type names
        risk_types = ["SOXRisk", "Risk", "OpRisk", "OperationalRisk"]
        
        all_risks = []
        
        for risk_type in risk_types:
            print(f"   Trying object type: {risk_type}...")
            risks = self._query_risks_by_type(risk_type)
            if risks:
                print(f"   ✓ Found {len(risks)} risk(s) of type {risk_type}")
                all_risks.extend(risks)
            else:
                print(f"   - No risks found for type {risk_type}")
        
        if all_risks:
            print(f"✅ Total: Found {len(all_risks)} risk(s)")
        else:
            print("⚠ No risks found. Trying to list all object types...")
            self._list_object_types()
            
        return all_risks
    
    def _query_risks_by_type(self, risk_type: str) -> List[Dict]:
        """Query ALL risks by specific object type using V2 API with pagination"""
        # Use V2 API endpoint that actually works in this environment
        url = f"{self.base_url}/opgrc/api/v2/query"
        
        # V2 API query format (POST with JSON payload)
        query_statement = f"SELECT [{risk_type}].[Resource ID], [{risk_type}].[Name], [{risk_type}].[Description], [{risk_type}].[OPSS-Risk-Qual:Inherent Impact], [{risk_type}].[OPSS-Risk-Qual:Inherent Likelihood], [{risk_type}].[OPSS-Risk-Qual:Inherent Risk Rating] FROM [{risk_type}]"
        
        all_risks = []
        offset = 0
        page_size = 500
        
        try:
            while True:
                payload = {
                    'statement': query_statement,
                    'offset': offset,
                    'max_rows': page_size,
                    'limit': page_size,
                    'case_insensitive': False,
                    'honor_primary': False
                }
                
                response = self.client.post(url, json=payload)
                response.raise_for_status()
                
                data = response.json()
                
                # Extract risks from V2 API response format
                if "rows" in data and len(data["rows"]) > 0:
                    for row in data["rows"]:
                        fields = row.get("fields", [])
                        if len(fields) >= 2:  # At least ID and Name
                            # Helper function to extract enum value
                            def get_field_value(field_data):
                                if not field_data:
                                    return "N/A"
                                value = field_data.get("value")
                                if value == "N/A" or value is None:
                                    return "N/A"
                                # If value is a dict (enum), get localized_label
                                if isinstance(value, dict):
                                    return value.get("localized_label", value.get("name", "N/A"))
                                return value
                            
                            risk = {
                                "resource_id": get_field_value(fields[0]) if len(fields) > 0 else "N/A",
                                "name": get_field_value(fields[1]) if len(fields) > 1 else "N/A",
                                "description": get_field_value(fields[2]) if len(fields) > 2 else "No description",
                                "inherent_impact": get_field_value(fields[3]) if len(fields) > 3 else "N/A",
                                "inherent_likelihood": get_field_value(fields[4]) if len(fields) > 4 else "N/A",
                                "inherent_risk_rating": get_field_value(fields[5]) if len(fields) > 5 else "N/A"
                            }
                            all_risks.append(risk)
                    
                    # Check if there are more results
                    if len(data["rows"]) < page_size:
                        # Last page
                        break
                    else:
                        # Move to next page
                        offset += page_size
                        print(f"   Fetched {len(all_risks)} {risk_type} objects so far...")
                else:
                    # No more results
                    break
                    
            return all_risks
            
        except httpx.HTTPStatusError as e:
            # Silently fail for non-existent types
            return []
            
        except Exception as e:
            return []
    
    def _list_object_types(self):
        """List available object types by querying for common types"""
        print("\n📋 Checking for common OpenPages object types...")
        
        # List of common OpenPages object types to check
        common_types = [
            "SOXBusEntity", "SOXProcess", "SOXControl", "SOXRisk",
            "SOXIssue", "SOXTask", "SOXDocument", "SOXTest"
        ]
        
        found_types = []
        
        for obj_type in common_types:
            try:
                url = f"{self.base_url}/opgrc/api/v2/query"
                payload = {
                    'statement': f"SELECT [{obj_type}].[Resource ID] FROM [{obj_type}]",
                    'offset': 0,
                    'max_rows': 1,
                    'limit': 1,
                    'case_insensitive': False,
                    'honor_primary': False
                }
                
                response = self.client.post(url, json=payload)
                if response.status_code == 200:
                    data = response.json()
                    if "rows" in data and len(data["rows"]) > 0:
                        found_types.append(obj_type)
            except:
                pass
        
        if found_types:
            print(f"\n✓ Found {len(found_types)} object types with data:")
            for type_name in found_types:
                print(f"   - {type_name}")
        else:
            print("   ❌ No common object types found with data")
    
    
    def export_to_csv(self, risks: List[Dict], filename: str = None):
        """
        Export risks to CSV file
        """
        if not risks:
            print("⚠ No risks to export")
            return
        
        # Generate filename with timestamp if not provided
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"openpages_risks_{timestamp}.csv"
        
        try:
            with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = ['Risk Name', 'Description', 'Inherent Impact', 'Inherent Likelihood', 'Inherent Risk Rating']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                
                # Write header
                writer.writeheader()
                
                # Write risk data
                for risk in risks:
                    writer.writerow({
                        'Risk Name': risk.get('name', 'N/A'),
                        'Description': risk.get('description', 'N/A'),
                        'Inherent Impact': risk.get('inherent_impact', 'N/A'),
                        'Inherent Likelihood': risk.get('inherent_likelihood', 'N/A'),
                        'Inherent Risk Rating': risk.get('inherent_risk_rating', 'N/A')
                    })
            
            print(f"✅ Exported {len(risks)} risk(s) to: {filename}")
            print(f"📄 File location: {os.path.abspath(filename)}")
            
        except Exception as e:
            print(f"❌ Error exporting to CSV: {e}")
    
    def export_to_json(self, risks: List[Dict], filename: str = None):
        """
        Export risks to JSON file
        """
        if not risks:
            print("⚠ No risks to export")
            return
        
        # Generate filename with timestamp if not provided
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"openpages_risks_{timestamp}.json"
        
        try:
            import json
            
            with open(filename, 'w', encoding='utf-8') as jsonfile:
                json.dump(risks, jsonfile, indent=2, ensure_ascii=False)
            
            print(f"✅ Exported {len(risks)} risk(s) to: {filename}")
            print(f"📄 File location: {os.path.abspath(filename)}")
            
        except Exception as e:
            print(f"❌ Error exporting to JSON: {e}")
    
    def close(self):
        """Close HTTP client"""
        self.client.close()


def main():
    """Main execution function"""
    print("=" * 60)
    print("IBM OpenPages Risk Extractor")
    print("=" * 60)
    print()
    
    # Validate configuration
    if not all([OPENPAGES_BASE_URL, OPENPAGES_USERNAME, OPENPAGES_PASSWORD]):
        print("❌ Error: Missing OpenPages configuration in .env file")
        print("Required variables: OPENPAGES_BASE_URL, OPENPAGES_USERNAME, OPENPAGES_PASSWORD")
        return
    
    print(f"🔗 OpenPages URL: {OPENPAGES_BASE_URL}")
    print(f"👤 Username: {OPENPAGES_USERNAME}")
    print()
    
    # Create extractor instance
    extractor = OpenPagesRiskExtractor()
    
    try:
        # Get all risks
        risks = extractor.get_all_risks()
        
        # Export to both CSV and JSON
        if risks:
            extractor.export_to_csv(risks)
            extractor.export_to_json(risks)
            
            # Display summary
            print()
            print("=" * 60)
            print("Summary")
            print("=" * 60)
            print(f"Total Risks Extracted: {len(risks)}")
            print()
            
            # Show first 5 risks as preview
            if len(risks) > 0:
                print("Preview (first 5 risks):")
                print("-" * 60)
                for i, risk in enumerate(risks[:5], 1):
                    name = risk.get('name', 'N/A')
                    desc = risk.get('description', 'N/A')
                    # Truncate description for preview
                    desc_preview = desc[:100] + "..." if len(desc) > 100 else desc
                    print(f"{i}. {name}")
                    print(f"   Description: {desc_preview}")
                    print()
        else:
            print("⚠ No risks found in OpenPages")
    
    finally:
        extractor.close()


if __name__ == "__main__":
    main()

# Made with Bob

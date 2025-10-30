"""
Phoenix Company Scanner - CORPORATE PREMIUM VERSION
Professional Enterprise UI with GraphQL-Optimized Backend
"""

import os
import requests
import pandas as pd
from flask import Flask, render_template_string, request, jsonify
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import base64
import io
import time
from functools import lru_cache

app = Flask(__name__)

# Supabase Configuration
SUPABASE_URL = 'https://sfztshdyiywwwhirrsfp.supabase.co/rest/v1/basiccompanydata_with_risk_level'
SUPABASE_API_KEY = 'sb_secret_I1sO79aMD8b2F9Al2zGLGQ__8xFuA_d'

# Companies House API Configuration
PHOENIX_BASE_URL = 'https://api.company-information.service.gov.uk'
API_KEY = '8a7ffe74-9184-406b-a739-860cac3218df'

# Pagination Configuration
ROWS_PER_PAGE = 100

# Enhanced cache with risk-level statistics
metadata_cache = {
    'total_rows': None,
    'columns': None,
    'risk_column': None,
    'last_check': None,
    'risk_counts': {
        'high': None,
        'medium': None,
        'low': None,
        'all': None
    },
    'stats_last_check': None
}


def get_supabase_headers():
    """Generate headers for Supabase requests"""
    return {
        'apikey': SUPABASE_API_KEY,
        'Authorization': f'Bearer {SUPABASE_API_KEY}',
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }


@lru_cache(maxsize=1)
def get_risk_column_name():
    """Get and cache the risk column name"""
    global metadata_cache
    
    if metadata_cache['risk_column']:
        return metadata_cache['risk_column']
    
    try:
        headers = get_supabase_headers()
        response = requests.get(f"{SUPABASE_URL}?select=*&limit=1", headers=headers, timeout=10)
        
        if response.status_code in [200, 206]:
            data = response.json()
            if data:
                columns = list(data[0].keys())
                # Find risk column
                for col in columns:
                    col_lower = col.lower().strip()
                    if 'risk' in col_lower and ('percentage' in col_lower or 'percent' in col_lower or col_lower == 'risk_percentage'):
                        metadata_cache['risk_column'] = col
                        print(f"✅ Found risk column: {col}")
                        return col
                
                # Fallback: just look for 'risk'
                for col in columns:
                    if 'risk' in col.lower():
                        metadata_cache['risk_column'] = col
                        print(f"✅ Found risk column (fallback): {col}")
                        return col
        
        return None
    except Exception as e:
        print(f"❌ Error finding risk column: {str(e)}")
        return None


def get_risk_statistics():
    """
    Get counts for each risk level - GRAPHQL STYLE
    This is cached to avoid repeated expensive COUNT queries
    """
    global metadata_cache
    
    # Use cache if less than 5 minutes old
    if metadata_cache['stats_last_check']:
        age = (datetime.now() - metadata_cache['stats_last_check']).seconds
        if age < 300:  # 5 minutes
            print("📊 Using cached risk statistics")
            return metadata_cache['risk_counts']
    
    print("📊 Fetching risk statistics from database...")
    
    risk_col = get_risk_column_name()
    if not risk_col:
        return {'high': 0, 'medium': 0, 'low': 0, 'all': 0}
    
    headers = get_supabase_headers()
    headers['Prefer'] = 'count=exact'
    
    counts = {}
    
    # Get HIGH risk count
    try:
        url = f"{SUPABASE_URL}?{risk_col}=in.(High,high,HIGH,Critical,critical,CRITICAL)&select=*&limit=1"
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code in [200, 206]:
            count_header = response.headers.get('Content-Range', '0-0/0')
            counts['high'] = int(count_header.split('/')[-1])
            print(f"   High Risk: {counts['high']:,}")
    except Exception as e:
        print(f"   ⚠️ High count error: {e}")
        counts['high'] = 0
    
    # Get MEDIUM risk count
    try:
        url = f"{SUPABASE_URL}?{risk_col}=in.(Medium,medium,MEDIUM)&select=*&limit=1"
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code in [200, 206]:
            count_header = response.headers.get('Content-Range', '0-0/0')
            counts['medium'] = int(count_header.split('/')[-1])
            print(f"   Medium Risk: {counts['medium']:,}")
    except Exception as e:
        print(f"   ⚠️ Medium count error: {e}")
        counts['medium'] = 0
    
    # Get LOW risk count
    try:
        url = f"{SUPABASE_URL}?{risk_col}=in.(Low,low,LOW)&select=*&limit=1"
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code in [200, 206]:
            count_header = response.headers.get('Content-Range', '0-0/0')
            counts['low'] = int(count_header.split('/')[-1])
            print(f"   Low Risk: {counts['low']:,}")
    except Exception as e:
        print(f"   ⚠️ Low count error: {e}")
        counts['low'] = 0
    
    # Get TOTAL count
    try:
        url = f"{SUPABASE_URL}?select=*&limit=1"
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code in [200, 206]:
            count_header = response.headers.get('Content-Range', '0-0/0')
            counts['all'] = int(count_header.split('/')[-1])
            print(f"   Total Records: {counts['all']:,}")
    except Exception as e:
        print(f"   ⚠️ Total count error: {e}")
        counts['all'] = 0
    
    # Update cache
    metadata_cache['risk_counts'] = counts
    metadata_cache['stats_last_check'] = datetime.now()
    
    print("✅ Risk statistics cached")
    return counts


def fetch_filtered_data(risk_filter=None, page=1, per_page=100, search_query=None):
    """
    GRAPHQL-STYLE QUERY: Fetch only filtered data from database
    This is the KEY optimization - we NEVER fetch all 5.6M rows
    """
    
    # SAFETY: Even filtered queries have offset limits
    MAX_SAFE_PAGE = 10000  # Maximum 1 million records via offset pagination
    
    try:
        headers = get_supabase_headers()
        headers['Prefer'] = 'count=exact'
        
        # Get risk column
        risk_col = get_risk_column_name()
        
        # Build filters - GRAPHQL WHERE clause style
        filters = []
        
        # Risk filter - DATABASE LEVEL FILTERING
        if risk_filter and risk_col:
            if risk_filter == 'high':
                filters.append(f"{risk_col}=in.(High,high,HIGH,Critical,critical,CRITICAL)")
            elif risk_filter == 'medium':
                filters.append(f"{risk_col}=in.(Medium,medium,MEDIUM)")
            elif risk_filter == 'low':
                filters.append(f"{risk_col}=in.(Low,low,LOW)")
        
        # Search filter
        if search_query:
            # Get columns for search
            response = requests.get(f"{SUPABASE_URL}?select=*&limit=1", headers=headers, timeout=5)
            if response.status_code in [200, 206]:
                data = response.json()
                if data:
                    columns = list(data[0].keys())
                    search_filters = []
                    for col in columns[:5]:  # Search first 5 columns
                        search_filters.append(f"{col}.ilike.*{search_query}*")
                    if search_filters:
                        filters.append(f"or=({','.join(search_filters)})")
        
        # Build query URL
        query_url = f"{SUPABASE_URL}?select=*"
        if filters:
            query_url += "&" + "&".join(filters)
        
        # Get count of FILTERED results
        print(f"\n🔍 GraphQL-Style Query:")
        print(f"   Risk Filter: {risk_filter or 'None'}")
        print(f"   Search: {search_query or 'None'}")
        
        count_response = requests.get(f"{query_url}&limit=1", headers=headers, timeout=10)
        filtered_count = 0
        if count_response.status_code in [200, 206]:
            count_header = count_response.headers.get('Content-Range', '0-0/0')
            filtered_count = int(count_header.split('/')[-1])
            print(f"   Filtered Results: {filtered_count:,}")
        
        # SAFETY CHECK: Prevent timeout on very large filtered datasets
        if filtered_count > (MAX_SAFE_PAGE * per_page) and page > MAX_SAFE_PAGE:
            return {
                'error': f'⚠️ Page {page:,} exceeds safe limit.<br><br>'
                         f'<strong>Your filter returned {filtered_count:,} records.</strong><br><br>'
                         f'Maximum safe page: {MAX_SAFE_PAGE:,} (shows first {MAX_SAFE_PAGE * per_page:,} records)<br><br>'
                         f'<strong>Please refine your search criteria</strong>',
                'data': [],
                'columns': [],
                'total_rows': filtered_count,
                'current_page': page,
                'per_page': per_page,
                'total_pages': MAX_SAFE_PAGE,
                'is_filtered': bool(risk_filter or search_query),
                'showing_from': 0,
                'showing_to': 0,
                'success': False,
                'max_safe_page': MAX_SAFE_PAGE
            }
        
        # Calculate pagination (limit to MAX_SAFE_PAGE if dataset is huge)
        offset = (page - 1) * per_page
        
        # Calculate total pages with safety limit
        if filtered_count > (MAX_SAFE_PAGE * per_page):
            total_pages = MAX_SAFE_PAGE
            effective_count = MAX_SAFE_PAGE * per_page
            print(f"   ⚠️ Large dataset detected - limiting to first {effective_count:,} records")
        else:
            total_pages = max(1, (filtered_count + per_page - 1) // per_page) if filtered_count > 0 else 1
            effective_count = filtered_count
        
        # Add pagination to query
        query_url += f"&limit={per_page}&offset={offset}"
        
        print(f"   Page: {page}/{total_pages}")
        print(f"   Offset: {offset}, Limit: {per_page}")
        
        # Fetch actual data
        response = requests.get(query_url, headers=headers, timeout=30)
        
        if response.status_code in [200, 206]:
            data = response.json()
            
            columns = []
            if data:
                columns = list(data[0].keys())
            else:
                # Get columns from sample query
                sample = requests.get(f"{SUPABASE_URL}?select=*&limit=1", headers=headers, timeout=5)
                if sample.status_code in [200, 206]:
                    sample_data = sample.json()
                    if sample_data:
                        columns = list(sample_data[0].keys())
            
            result = {
                'data': data,
                'columns': columns,
                'total_rows': filtered_count,
                'current_page': page,
                'per_page': per_page,
                'total_pages': total_pages,
                'is_filtered': bool(risk_filter or search_query),
                'filter_type': 'risk' if risk_filter else ('search' if search_query else None),
                'filter_value': risk_filter or search_query,
                'showing_from': offset + 1 if filtered_count > 0 else 0,
                'showing_to': min(offset + per_page, effective_count),
                'success': True,
                'max_safe_page': MAX_SAFE_PAGE if filtered_count > (MAX_SAFE_PAGE * per_page) else None,
                'is_limited': filtered_count > (MAX_SAFE_PAGE * per_page)
            }
            
            print(f"✅ Query successful: {len(data)} rows returned")
            return result
        
        elif response.status_code == 500:
            return {
                'error': '⚠️ Database timeout. Please refine your search criteria.',
                'data': [],
                'columns': [],
                'total_rows': 0,
                'current_page': page,
                'per_page': per_page,
                'total_pages': 0,
                'is_filtered': False,
                'showing_from': 0,
                'showing_to': 0,
                'success': False
            }
        
        else:
            error_text = response.text[:500] if response.text else 'Unknown error'
            return {
                'error': f'Query error: {response.status_code} - {error_text}',
                'data': [],
                'columns': [],
                'total_rows': 0,
                'current_page': page,
                'per_page': per_page,
                'total_pages': 0,
                'is_filtered': False,
                'showing_from': 0,
                'showing_to': 0,
                'success': False
            }
    
    except requests.exceptions.Timeout:
        return {
            'error': '⚠️ Request timed out. Please refine your search.',
            'data': [],
            'columns': [],
            'total_rows': 0,
            'current_page': page,
            'per_page': per_page,
            'total_pages': 0,
            'is_filtered': False,
            'showing_from': 0,
            'showing_to': 0,
            'success': False
        }
    
    except Exception as e:
        print(f"❌ Query error: {str(e)}")
        import traceback
        traceback.print_exc()
        
        return {
            'error': f'Error: {str(e)}',
            'data': [],
            'columns': [],
            'total_rows': 0,
            'current_page': page,
            'per_page': per_page,
            'total_pages': 0,
            'is_filtered': False,
            'showing_from': 0,
            'showing_to': 0,
            'success': False
        }


# ============= Companies House API Functions =============

def get_api_headers():
    """Generate authorization headers for API requests"""
    auth_string = base64.b64encode(f'{API_KEY}:'.encode()).decode()
    return {
        'Authorization': f'Basic {auth_string}',
        'Content-Type': 'application/json'
    }


def api_request(endpoint):
    """Make API request with error handling"""
    url = f'{PHOENIX_BASE_URL}{endpoint}'
    
    try:
        response = requests.get(url, headers=get_api_headers(), timeout=30)
        
        if response.status_code == 404:
            return {'error': 'Not found', 'status_code': 404}
        
        if response.status_code != 200:
            return {'error': f'HTTP {response.status_code}: {response.text[:500]}'}
        
        return response.json()
    
    except requests.exceptions.RequestException as e:
        return {'error': str(e)}


def get_company(company_number):
    """Get company basic info"""
    return api_request(f'/company/{company_number}')


def get_officers(company_number):
    """Get officers/directors"""
    return api_request(f'/company/{company_number}/officers')


def get_filing_history(company_number):
    """Get filing history"""
    return api_request(f'/company/{company_number}/filing-history?items_per_page=100')


def get_psc(company_number):
    """Get Persons with Significant Control"""
    return api_request(f'/company/{company_number}/persons-with-significant-control')


def get_charges(company_number):
    """Get charges/mortgages"""
    return api_request(f'/company/{company_number}/charges')


def get_insolvency(company_number):
    """Get insolvency info"""
    return api_request(f'/company/{company_number}/insolvency')


def search_companies(query):
    """Search companies by name or address"""
    return api_request(f'/search/companies?q={query}&items_per_page=100')


def build_address_string(company):
    """Build address string from company data"""
    if 'registered_office_address' not in company:
        return ''
    
    addr = company['registered_office_address']
    parts = []
    
    for field in ['address_line_1', 'address_line_2', 'locality', 'postal_code']:
        if field in addr and addr[field]:
            parts.append(addr[field])
    
    return ' '.join(parts)


def calculate_risk(report):
    """Calculate risk score"""
    risk_score = 0
    indicators = []
    
    suspicious_statuses = ['dissolved', 'liquidation', 'insolvency-proceedings', 'receivership', 'administration']
    
    company_name = report['company'].get('company_name', '').lower()
    company_status = report['company'].get('company_status', '')
    
    if company_status in suspicious_statuses:
        risk_score += 30
        indicators.append({
            'type': 'company_status',
            'severity': 'high',
            'description': f"Company status is: {company_status}"
        })
    
    name_recycling_dissolved = []
    
    for similar in report['similar_companies']:
        similar_name = similar.get('title', '').lower()
        similar_status = similar.get('company_status', '')
        similar_number = similar.get('company_number', '')
        
        similarity = SequenceMatcher(None, company_name, similar_name).ratio() * 100
        
        if similar_status in ['dissolved', 'liquidation', 'insolvency-proceedings']:
            if similarity >= 70:
                name_recycling_dissolved.append(similar)
                
                if similarity >= 85:
                    risk_score += 25
                    indicators.append({
                        'type': 'high_name_similarity',
                        'severity': 'high',
                        'description': f"Very similar name to dissolved company: {similar['title']} ({similar_number}) - {similarity:.0f}% match"
                    })
                elif similarity >= 70:
                    risk_score += 15
                    indicators.append({
                        'type': 'name_similarity',
                        'severity': 'medium',
                        'description': f"Similar name to dissolved company: {similar['title']} ({similar_number}) - {similarity:.0f}% match"
                    })
    
    if len(name_recycling_dissolved) >= 3:
        risk_score += 30
        indicators.append({
            'type': 'name_recycling',
            'severity': 'critical',
            'description': f"{len(name_recycling_dissolved)} dissolved companies with similar names found"
        })
    
    phoenix_directors = []
    serial_directors = []
    
    for officer in report['officers']:
        if officer['dissolved_links'] >= 3:
            serial_directors.append(officer)
            indicators.append({
                'type': 'serial_dissolutions',
                'severity': 'critical',
                'description': f"{officer['name']} has {officer['dissolved_links']} dissolved companies"
            })
            risk_score += 30
        
        if officer['liquidation_links'] >= 2:
            indicators.append({
                'type': 'liquidation_pattern',
                'severity': 'critical',
                'description': f"{officer['name']} linked to {officer['liquidation_links']} liquidations"
            })
            risk_score += 40
        
        if officer['dissolved_links'] >= 1 and officer['recent_formations'] >= 1:
            phoenix_directors.append(officer)
            risk_score += 25
    
    risk_score = min(risk_score, 100)
    
    if risk_score >= 70:
        risk_level = 'CRITICAL'
    elif risk_score >= 50:
        risk_level = 'HIGH'
    elif risk_score >= 30:
        risk_level = 'MEDIUM'
    else:
        risk_level = 'LOW'
    
    is_phoenix = False
    phoenix_confidence = 0
    phoenix_reasons = []
    
    if len(phoenix_directors) >= 2:
        phoenix_confidence += 40
        phoenix_reasons.append(f"{len(phoenix_directors)} directors show phoenix patterns")
        is_phoenix = phoenix_confidence >= 60
    
    if len(name_recycling_dissolved) >= 3:
        phoenix_confidence += 30
        phoenix_reasons.append(f"Name recycling: {len(name_recycling_dissolved)} similar dissolved companies")
        is_phoenix = phoenix_confidence >= 60
    
    phoenix_confidence = min(phoenix_confidence, 100)
    
    if not phoenix_reasons:
        phoenix_reasons = ['No clear phoenix patterns detected']
    
    report['is_phoenix'] = 'YES' if is_phoenix else 'NO'
    report['phoenix_confidence'] = phoenix_confidence
    report['phoenix_reasons'] = phoenix_reasons
    report['risk_score'] = risk_score
    report['risk_level'] = risk_level
    report['phoenix_indicators'] = indicators
    
    return report


def deep_scan_company(company_number):
    """Enhanced Deep Scan Function"""
    report = {
        'company': {},
        'officers': [],
        'filing_history': [],
        'psc': [],
        'charges': [],
        'insolvency': {},
        'similar_companies': [],
        'flags': [],
        'risk_score': 0,
        'phoenix_indicators': []
    }
    
    suspicious_statuses = ['dissolved', 'liquidation', 'insolvency-proceedings', 'receivership', 'administration']
    
    company = get_company(company_number)
    if 'error' in company:
        return {'error': company['error']}
    report['company'] = company
    
    officers_data = get_officers(company_number)
    officers = officers_data.get('items', [])
    
    filing_data = get_filing_history(company_number)
    report['filing_history'] = filing_data.get('items', [])
    
    psc_data = get_psc(company_number)
    report['psc'] = psc_data.get('items', [])
    
    charges_data = get_charges(company_number)
    report['charges'] = charges_data.get('items', [])
    
    insolvency_data = get_insolvency(company_number)
    if 'error' not in insolvency_data:
        report['insolvency'] = insolvency_data
    
    for officer in officers:
        officer_name = officer.get('name', '(unknown)')
        officer_entry = {
            'name': officer_name,
            'role': officer.get('officer_role', ''),
            'appointed_on': officer.get('appointed_on', ''),
            'resigned_on': officer.get('resigned_on', ''),
            'linked_companies': [],
            'dissolved_links': 0,
            'liquidation_links': 0,
            'recent_formations': 0
        }
        
        search = search_companies(officer_name)
        if 'items' in search:
            for linked_company in search['items']:
                company_status = linked_company.get('company_status', '')
                company_title = linked_company.get('title', '')
                company_num = linked_company.get('company_number', '')
                date_of_creation = linked_company.get('date_of_creation', '')
                
                officer_entry['linked_companies'].append({
                    'company_number': company_num,
                    'title': company_title,
                    'status': company_status,
                    'date_of_creation': date_of_creation
                })
                
                if company_status in suspicious_statuses:
                    if company_status == 'dissolved':
                        officer_entry['dissolved_links'] += 1
                    if company_status in ['liquidation', 'insolvency-proceedings']:
                        officer_entry['liquidation_links'] += 1
                
                if date_of_creation:
                    try:
                        creation_date = datetime.strptime(date_of_creation, '%Y-%m-%d')
                        two_years_ago = datetime.now() - timedelta(days=730)
                        if creation_date > two_years_ago:
                            officer_entry['recent_formations'] += 1
                    except ValueError:
                        pass
        
        report['officers'].append(officer_entry)
    
    company_name = company.get('company_name', '')
    if company_name:
        similar = search_companies(company_name)
        if 'items' in similar:
            for sim_company in similar['items']:
                sim_num = sim_company.get('company_number', '')
                if sim_num != company_number:
                    report['similar_companies'].append(sim_company)
    
    address = build_address_string(company)
    if address:
        address_search = search_companies(address)
        if 'items' in address_search:
            for addr_company in address_search['items']:
                addr_num = addr_company.get('company_number', '')
                if addr_num != company_number:
                    exists = any(sc.get('company_number') == addr_num for sc in report['similar_companies'])
                    if not exists:
                        addr_company['found_by'] = 'address'
                        report['similar_companies'].append(addr_company)
    
    report = calculate_risk(report)
    
    return report


# ============= PREMIUM CORPORATE HTML TEMPLATE =============

MAIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Phoenix Company Scanner | Enterprise Risk Intelligence</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet">
    <style>
        :root {
            --primary-navy: #1B2B4F;
            --primary-blue: #2563EB;
            --accent-gold: #F59E0B;
            --success-green: #10B981;
            --warning-amber: #F59E0B;
            --danger-red: #EF4444;
            --neutral-50: #F9FAFB;
            --neutral-100: #F3F4F6;
            --neutral-200: #E5E7EB;
            --neutral-300: #D1D5DB;
            --neutral-500: #6B7280;
            --neutral-700: #374151;
            --neutral-800: #1F2937;
            --neutral-900: #111827;
            --gradient-primary: linear-gradient(135deg, #1B2B4F 0%, #2563EB 100%);
            --gradient-accent: linear-gradient(135deg, #F59E0B 0%, #EF4444 50%, #DC2626 100%);
            --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
            --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
            --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
            --shadow-xl: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
            --shadow-2xl: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background-color: var(--neutral-50);
            color: var(--neutral-900);
            line-height: 1.6;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }
        
        /* Header */
        .header {
            background: var(--gradient-primary);
            color: white;
            padding: 2.5rem 0;
            box-shadow: var(--shadow-lg);
            position: relative;
            overflow: hidden;
        }
        
        .header::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='0.05'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E");
            opacity: 0.1;
        }
        
        .header-content {
            max-width: 1400px;
            margin: 0 auto;
            padding: 0 2rem;
            position: relative;
            z-index: 1;
        }
        
        .header-top {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1rem;
        }
        
        .logo {
            display: flex;
            align-items: center;
            gap: 1rem;
        }
        
        .logo-icon {
            width: 48px;
            height: 48px;
            background: rgba(255, 255, 255, 0.15);
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
            backdrop-filter: blur(10px);
        }
        
        .logo-text h1 {
            font-size: 1.75rem;
            font-weight: 800;
            letter-spacing: -0.025em;
            margin-bottom: 0.25rem;
        }
        
        .logo-text p {
            font-size: 0.875rem;
            opacity: 0.9;
            font-weight: 500;
        }
        
        .header-badge {
            background: rgba(255, 255, 255, 0.15);
            backdrop-filter: blur(10px);
            padding: 0.5rem 1rem;
            border-radius: 50px;
            font-size: 0.875rem;
            font-weight: 600;
            border: 1px solid rgba(255, 255, 255, 0.2);
        }
        
        /* Container */
        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 2rem;
        }
        
        /* Stats Grid */
        .stats-section {
            margin-bottom: 2rem;
        }
        
        .stats-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.5rem;
        }
        
        .stats-header h2 {
            font-size: 1.5rem;
            font-weight: 700;
            color: var(--neutral-900);
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        
        .stats-header h2::before {
            content: '';
            width: 4px;
            height: 28px;
            background: var(--primary-blue);
            border-radius: 4px;
        }
        
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 1.5rem;
        }
        
        .stat-card {
            background: white;
            border-radius: 16px;
            padding: 1.75rem;
            box-shadow: var(--shadow-md);
            border: 1px solid var(--neutral-200);
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            position: relative;
            overflow: hidden;
        }
        
        .stat-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 4px;
            background: var(--gradient-primary);
            transition: height 0.3s ease;
        }
        
        .stat-card:hover {
            transform: translateY(-4px);
            box-shadow: var(--shadow-xl);
        }
        
        .stat-card:hover::before {
            height: 6px;
        }
        
        .stat-card.high::before {
            background: linear-gradient(135deg, #DC2626 0%, #EF4444 100%);
        }
        
        .stat-card.medium::before {
            background: linear-gradient(135deg, #F59E0B 0%, #FBBF24 100%);
        }
        
        .stat-card.low::before {
            background: linear-gradient(135deg, #059669 0%, #10B981 100%);
        }
        
        .stat-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 1rem;
        }
        
        .stat-icon {
            width: 48px;
            height: 48px;
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.5rem;
            font-weight: 600;
        }
        
        .stat-card.high .stat-icon {
            background: linear-gradient(135deg, #FEE2E2 0%, #FEE2E2 100%);
            color: #DC2626;
        }
        
        .stat-card.medium .stat-icon {
            background: linear-gradient(135deg, #FEF3C7 0%, #FEF3C7 100%);
            color: #F59E0B;
        }
        
        .stat-card.low .stat-icon {
            background: linear-gradient(135deg, #D1FAE5 0%, #D1FAE5 100%);
            color: #059669;
        }
        
        .stat-card.total .stat-icon {
            background: linear-gradient(135deg, #DBEAFE 0%, #DBEAFE 100%);
            color: #2563EB;
        }
        
        .stat-label {
            font-size: 0.875rem;
            font-weight: 600;
            color: var(--neutral-500);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        
        .stat-value {
            font-size: 2.25rem;
            font-weight: 800;
            color: var(--neutral-900);
            line-height: 1;
            margin-bottom: 0.5rem;
        }
        
        .stat-description {
            font-size: 0.875rem;
            color: var(--neutral-500);
        }
        
        /* Data Section */
        .data-section {
            background: white;
            border-radius: 16px;
            box-shadow: var(--shadow-lg);
            overflow: hidden;
            border: 1px solid var(--neutral-200);
            margin-bottom: 2rem;
        }
        
        .section-header {
            background: linear-gradient(135deg, var(--neutral-50) 0%, white 100%);
            padding: 1.75rem 2rem;
            border-bottom: 1px solid var(--neutral-200);
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 1rem;
        }
        
        .section-title {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }
        
        .section-title h2 {
            font-size: 1.5rem;
            font-weight: 700;
            color: var(--neutral-900);
        }
        
        .section-badge {
            background: var(--gradient-primary);
            color: white;
            padding: 0.5rem 1rem;
            border-radius: 50px;
            font-size: 0.875rem;
            font-weight: 600;
            box-shadow: var(--shadow-md);
        }
        
        /* Filter Section */
        .filter-section {
            background: var(--gradient-primary);
            padding: 1.75rem 2rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        }
        
        .filter-content {
            display: flex;
            align-items: center;
            gap: 1.5rem;
            flex-wrap: wrap;
        }
        
        .filter-group {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }
        
        .filter-label {
            color: white;
            font-weight: 600;
            font-size: 0.9375rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        
        .filter-select {
            min-width: 240px;
            padding: 0.75rem 1rem;
            border: 2px solid rgba(255, 255, 255, 0.2);
            border-radius: 10px;
            font-size: 0.9375rem;
            font-weight: 600;
            background: white;
            color: var(--neutral-900);
            cursor: pointer;
            transition: all 0.3s ease;
            box-shadow: var(--shadow-md);
        }
        
        .filter-select:hover {
            border-color: var(--accent-gold);
            box-shadow: var(--shadow-lg);
        }
        
        .filter-select:focus {
            outline: none;
            border-color: var(--accent-gold);
            box-shadow: 0 0 0 4px rgba(245, 158, 11, 0.2);
        }
        
        .filter-badge {
            background: rgba(255, 255, 255, 0.25);
            color: white;
            padding: 0.625rem 1rem;
            border-radius: 50px;
            font-size: 0.875rem;
            font-weight: 600;
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.2);
        }
        
        .filter-active-banner {
            background: linear-gradient(135deg, #059669 0%, #10B981 100%);
            color: white;
            padding: 1rem 2rem;
            text-align: center;
            font-weight: 600;
            font-size: 0.9375rem;
            border-bottom: 1px solid rgba(0, 0, 0, 0.1);
        }
        
        .filter-active-banner a {
            color: white;
            text-decoration: underline;
            margin-left: 1rem;
            font-weight: 700;
            transition: opacity 0.3s ease;
        }
        
        .filter-active-banner a:hover {
            opacity: 0.8;
        }
        
        /* Buttons */
        .btn {
            padding: 0.75rem 1.5rem;
            border: none;
            border-radius: 10px;
            font-size: 0.9375rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s ease;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
            text-decoration: none;
            box-shadow: var(--shadow-md);
        }
        
        .btn-primary {
            background: var(--gradient-primary);
            color: white;
        }
        
        .btn-primary:hover {
            box-shadow: var(--shadow-lg);
            transform: translateY(-2px);
        }
        
        .btn-secondary {
            background: white;
            color: var(--primary-navy);
            border: 2px solid var(--neutral-200);
        }
        
        .btn-secondary:hover {
            border-color: var(--primary-blue);
            color: var(--primary-blue);
            box-shadow: var(--shadow-lg);
        }
        
        /* Table */
        .table-wrapper {
            overflow-x: auto;
            max-height: 700px;
            overflow-y: auto;
        }
        
        .table-wrapper::-webkit-scrollbar {
            width: 12px;
            height: 12px;
        }
        
        .table-wrapper::-webkit-scrollbar-track {
            background: var(--neutral-100);
            border-radius: 10px;
        }
        
        .table-wrapper::-webkit-scrollbar-thumb {
            background: var(--gradient-primary);
            border-radius: 10px;
            border: 2px solid var(--neutral-100);
        }
        
        table {
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
        }
        
        thead {
            position: sticky;
            top: 0;
            z-index: 10;
        }
        
        th {
            background: var(--neutral-900);
            color: white;
            padding: 1rem 1.5rem;
            text-align: left;
            font-weight: 700;
            font-size: 0.8125rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            white-space: nowrap;
            border-bottom: 3px solid var(--primary-blue);
        }
        
        td {
            padding: 1rem 1.5rem;
            border-bottom: 1px solid var(--neutral-200);
            font-size: 0.9375rem;
            color: var(--neutral-700);
            background: white;
        }
        
        tbody tr {
            transition: all 0.2s ease;
        }
        
        tbody tr:hover {
            background: var(--neutral-50) !important;
            box-shadow: inset 0 0 0 2px var(--primary-blue);
        }
        
        .risk-high {
            background: linear-gradient(90deg, #FEE2E2 0%, #FFFFFF 100%) !important;
            border-left: 4px solid #DC2626;
        }
        
        .risk-medium {
            background: linear-gradient(90deg, #FEF3C7 0%, #FFFFFF 100%) !important;
            border-left: 4px solid #F59E0B;
        }
        
        .risk-low {
            background: linear-gradient(90deg, #D1FAE5 0%, #FFFFFF 100%) !important;
            border-left: 4px solid #059669;
        }
        
        /* Pagination */
        .pagination {
            padding: 2rem;
            background: linear-gradient(135deg, var(--neutral-50) 0%, white 100%);
            border-top: 1px solid var(--neutral-200);
        }
        
        .pagination-info {
            text-align: center;
            margin-bottom: 1.5rem;
            font-size: 0.9375rem;
            color: var(--neutral-700);
            font-weight: 600;
        }
        
        .pagination-controls {
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 0.75rem;
            flex-wrap: wrap;
        }
        
        .page-btn {
            padding: 0.625rem 1rem;
            border: 2px solid var(--neutral-200);
            background: white;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.3s ease;
            text-decoration: none;
            color: var(--neutral-900);
            font-weight: 600;
            font-size: 0.875rem;
            box-shadow: var(--shadow-sm);
        }
        
        .page-btn:hover:not(:disabled):not(.active) {
            background: var(--gradient-primary);
            color: white;
            border-color: transparent;
            box-shadow: var(--shadow-md);
            transform: translateY(-2px);
        }
        
        .page-btn:disabled {
            opacity: 0.4;
            cursor: not-allowed;
            background: var(--neutral-100);
        }
        
        .page-btn.active {
            background: var(--gradient-primary);
            color: white;
            border-color: transparent;
            box-shadow: var(--shadow-md);
            transform: scale(1.05);
        }
        
        .page-jump {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            margin-top: 1rem;
            padding: 1rem;
            background: white;
            border-radius: 12px;
            border: 2px solid var(--neutral-200);
            box-shadow: var(--shadow-sm);
        }
        
        .page-jump label {
            font-weight: 600;
            color: var(--neutral-700);
            font-size: 0.875rem;
        }
        
        .page-jump input {
            width: 100px;
            padding: 0.625rem 1rem;
            border: 2px solid var(--neutral-200);
            border-radius: 8px;
            text-align: center;
            font-size: 0.9375rem;
            font-weight: 600;
            transition: all 0.3s ease;
        }
        
        .page-jump input:focus {
            outline: none;
            border-color: var(--primary-blue);
            box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
        }
        
        /* Scanner Section */
        .scanner-section {
            background: white;
            border-radius: 16px;
            padding: 2.5rem;
            box-shadow: var(--shadow-lg);
            border: 1px solid var(--neutral-200);
        }
        
        .scanner-header {
            display: flex;
            align-items: center;
            gap: 1rem;
            margin-bottom: 1.5rem;
        }
        
        .scanner-header h2 {
            font-size: 1.75rem;
            font-weight: 800;
            color: var(--neutral-900);
        }
        
        .scanner-header::before {
            content: '';
            width: 5px;
            height: 40px;
            background: var(--gradient-accent);
            border-radius: 4px;
        }
        
        .info-box {
            background: linear-gradient(135deg, #DBEAFE 0%, #EFF6FF 100%);
            padding: 1.5rem;
            border-left: 4px solid var(--primary-blue);
            margin: 1.5rem 0;
            border-radius: 12px;
            box-shadow: var(--shadow-sm);
        }
        
        .info-box strong {
            color: var(--primary-navy);
            font-weight: 700;
            font-size: 1rem;
            display: block;
            margin-bottom: 0.5rem;
        }
        
        .scan-form {
            max-width: 600px;
            margin-top: 2rem;
        }
        
        .form-group {
            margin-bottom: 1.5rem;
        }
        
        .form-group label {
            display: block;
            font-weight: 700;
            margin-bottom: 0.75rem;
            color: var(--neutral-900);
            font-size: 0.9375rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        
        .form-group input {
            width: 100%;
            padding: 0.875rem 1.25rem;
            border: 2px solid var(--neutral-200);
            border-radius: 10px;
            font-size: 1rem;
            transition: all 0.3s ease;
            font-weight: 500;
        }
        
        .form-group input:focus {
            outline: none;
            border-color: var(--primary-blue);
            box-shadow: 0 0 0 4px rgba(37, 99, 235, 0.1);
        }
        
        /* Empty State */
        .empty-state {
            padding: 5rem 2rem;
            text-align: center;
            background: linear-gradient(135deg, var(--neutral-50) 0%, white 100%);
        }
        
        .empty-state-icon {
            width: 80px;
            height: 80px;
            margin: 0 auto 1.5rem;
            background: var(--gradient-primary);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 2.5rem;
        }
        
        .empty-state h3 {
            font-size: 1.75rem;
            color: var(--neutral-900);
            margin-bottom: 0.75rem;
            font-weight: 700;
        }
        
        .empty-state p {
            color: var(--neutral-500);
            font-size: 1rem;
            max-width: 500px;
            margin: 0 auto;
            line-height: 1.7;
        }
        
        /* Error Message */
        .error-message {
            background: linear-gradient(135deg, #FEE2E2 0%, #FEF2F2 100%);
            color: #991B1B;
            padding: 1.5rem;
            border-left: 4px solid #DC2626;
            border-radius: 12px;
            margin: 1.5rem 0;
            font-weight: 600;
            box-shadow: var(--shadow-md);
        }
        
        /* Loading Overlay */
        .loading-overlay {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(27, 43, 79, 0.95);
            backdrop-filter: blur(10px);
            display: none;
            justify-content: center;
            align-items: center;
            z-index: 9999;
        }
        
        .loading-overlay.active {
            display: flex;
        }
        
        .loading-content {
            background: white;
            padding: 3rem 4rem;
            border-radius: 20px;
            text-align: center;
            box-shadow: var(--shadow-2xl);
            border: 1px solid var(--neutral-200);
        }
        
        .spinner {
            border: 4px solid var(--neutral-200);
            border-top: 4px solid var(--primary-blue);
            border-radius: 50%;
            width: 60px;
            height: 60px;
            animation: spin 0.8s linear infinite;
            margin: 0 auto 1.5rem;
        }
        
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        
        .loading-content h2 {
            color: var(--neutral-900);
            margin-bottom: 0.75rem;
            font-weight: 700;
            font-size: 1.5rem;
        }
        
        .loading-content p {
            color: var(--neutral-500);
            font-weight: 500;
        }
        
        /* Responsive */
        @media (max-width: 768px) {
            .container {
                padding: 1rem;
            }
            
            .header-content {
                padding: 0 1rem;
            }
            
            .header-top {
                flex-direction: column;
                align-items: flex-start;
                gap: 1rem;
            }
            
            .stats-grid {
                grid-template-columns: 1fr;
            }
            
            .section-header {
                flex-direction: column;
                align-items: flex-start;
            }
            
            .filter-content {
                flex-direction: column;
                align-items: stretch;
            }
            
            .filter-select,
            .search-input {
                width: 100%;
            }
            
            .pagination-controls {
                gap: 0.5rem;
            }
            
            .page-btn {
                padding: 0.5rem 0.75rem;
                font-size: 0.8125rem;
            }
        }
    </style>
</head>
<body>
    <!-- Loading Overlay -->
    <div class="loading-overlay" id="loadingOverlay">
        <div class="loading-content">
            <div class="spinner"></div>
            <h2>Processing Request</h2>
            <p>Fetching data from enterprise database...</p>
        </div>
    </div>

    <!-- Header -->
    <div class="header">
        <div class="header-content">
            <div class="header-top">
                <div class="logo">
                    <div class="logo-icon">🔍</div>
                    <div class="logo-text">
                        <h1>Phoenix Company Scanner</h1>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <div class="container">
        <!-- Stats Section -->
        <div class="stats-section">
            <div class="stats-header">
                <h2>Risk Analytics Dashboard</h2>
            </div>
            <div class="stats-grid">
                <div class="stat-card high">
                    <div class="stat-header">
                        <div>
                            <div class="stat-label">High Risk</div>
                            <div class="stat-value">{{ risk_stats.high|format_number }}</div>
                            <div class="stat-description">Critical monitoring required</div>
                        </div>
                        <div class="stat-icon">⚠️</div>
                    </div>
                </div>
                <div class="stat-card medium">
                    <div class="stat-header">
                        <div>
                            <div class="stat-label">Medium Risk</div>
                            <div class="stat-value">{{ risk_stats.medium|format_number }}</div>
                            <div class="stat-description">Enhanced due diligence</div>
                        </div>
                        <div class="stat-icon">⚡</div>
                    </div>
                </div>
                <div class="stat-card low">
                    <div class="stat-header">
                        <div>
                            <div class="stat-label">Low Risk</div>
                            <div class="stat-value">{{ risk_stats.low|format_number }}</div>
                            <div class="stat-description">Standard monitoring</div>
                        </div>
                        <div class="stat-icon">✓</div>
                    </div>
                </div>
                <div class="stat-card total">
                    <div class="stat-header">
                        <div>
                            <div class="stat-label">Total Records</div>
                            <div class="stat-value">{{ risk_stats.all|format_number }}</div>
                            <div class="stat-description">Complete database</div>
                        </div>
                        <div class="stat-icon">📊</div>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Data Section -->
        <div class="data-section">
            {% if is_filtered and filter_type == 'risk' %}
            <div class="filter-active-banner">
                Active Filter: {{ total_rows|format_number }} {{ filter_value|upper }} RISK companies
                <a href="/">Clear Filter</a>
                {% if is_limited %}
                <br>
                <span style="font-size: 0.875rem; margin-top: 0.5rem; display: inline-block;">
                    Large dataset - showing first {{ max_safe_page|format_number }} pages ({{ (max_safe_page * 100)|format_number }} records)
                </span>
                {% endif %}
            </div>
            {% endif %}
            
            <div class="section-header">
                <div class="section-title">
                    <h2>Company Database</h2>
                </div>
                <div class="section-badge">
                    {% if is_filtered %}
                    Showing: {{ showing_from|format_number }} - {{ showing_to|format_number }} of {{ total_rows|format_number }}
                    {% else %}
                    5.6M Records Ready
                    {% endif %}
                </div>
            </div>
            
            <!-- Filter Section -->
            <div class="filter-section">
                <div class="filter-content">
                    <div class="filter-group">
                        <label class="filter-label">Risk Filter:</label>
                        <select id="riskFilter" class="filter-select" onchange="applyRiskFilter()">
                            <option value="">Select Risk Level</option>
                            <option value="high" {% if risk_filter == 'high' %}selected{% endif %}>⚠️ High Risk ({{ risk_stats.high|format_number }})</option>
                            <option value="medium" {% if risk_filter == 'medium' %}selected{% endif %}>⚡ Medium Risk ({{ risk_stats.medium|format_number }})</option>
                            <option value="low" {% if risk_filter == 'low' %}selected{% endif %}>✓ Low Risk ({{ risk_stats.low|format_number }})</option>
                        </select>
                    </div>
                    
                    <div class="filter-badge">
                        Database-level filtering
                    </div>
                </div>
            </div>
            
            <script>
                function applyRiskFilter() {
                    const filterValue = document.getElementById('riskFilter').value;
                    
                    if (filterValue) {
                        document.getElementById('loadingOverlay').classList.add('active');
                        window.location.href = '/?risk_filter=' + filterValue;
                    } else {
                        window.location.href = '/';
                    }
                }
                
                document.addEventListener('DOMContentLoaded', function() {
                });
                
                function goToPage() {
                    const pageInput = document.getElementById('pageInput');
                    const page = parseInt(pageInput.value);
                    const maxPage = {{ total_pages }};
                    
                    if (page && page > 0 && page <= maxPage) {
                        const riskFilter = '{{ risk_filter }}' || '';
                        
                        let newUrl = '/?page=' + page;
                        if (riskFilter) newUrl += '&risk_filter=' + riskFilter;
                        
                        document.getElementById('loadingOverlay').classList.add('active');
                        window.location.href = newUrl;
                    } else {
                        alert('Please enter a valid page number between 1 and ' + maxPage);
                    }
                }
            </script>
            
            {% if error %}
            <div class="error-message">
                {{ error | safe }}
            </div>
            {% elif not is_filtered %}
            <div class="empty-state">
                <div class="empty-state-icon">🎯</div>
                <h3>Select a Risk Filter to Begin</h3>
                <p>Choose a risk level from the filter above to view company data. Our GraphQL-optimized system efficiently handles millions of records with precision filtering.</p>
            </div>
            {% else %}
            <div class="table-wrapper">
                <table>
                    <thead>
                        <tr>
                            {% for col in columns %}
                            <th>{{ col }}</th>
                            {% endfor %}
                        </tr>
                    </thead>
                    <tbody>
                        {% if data %}
                        {% for row in data %}
                        <tr {% if columns %}
                            {% for col in columns %}
                                {% if 'risk' in col.lower() and ('percentage' in col.lower() or 'percent' in col.lower() or col.lower() == 'risk_percentage') %}
                                    {% set risk_val = row.get(col, '')|string|lower|trim %}
                                    {% if risk_val == 'high' or risk_val == 'critical' %}
                                        class="risk-high"
                                    {% elif risk_val == 'medium' %}
                                        class="risk-medium"
                                    {% elif risk_val == 'low' %}
                                        class="risk-low"
                                    {% endif %}
                                {% endif %}
                            {% endfor %}
                            {% endif %}>
                            {% for col in columns %}
                            <td>{{ row.get(col, '') }}</td>
                            {% endfor %}
                        </tr>
                        {% endfor %}
                        {% else %}
                        <tr>
                            <td colspan="{{ columns|length }}" style="text-align: center; padding: 3rem; color: var(--neutral-500);">
                                No data found matching your criteria
                            </td>
                        </tr>
                        {% endif %}
                    </tbody>
                </table>
            </div>
            
            <!-- Pagination -->
            {% if is_filtered and total_pages > 1 %}
            <div class="pagination">
                <div class="pagination-info">
                    Page {{ current_page|format_number }} of {{ total_pages|format_number }} 
                    ({{ showing_from|format_number }} to {{ showing_to|format_number }} of {{ total_rows|format_number }} records)
                    {% if is_limited %}
                    <br>
                    <span style="color: var(--warning-amber); font-size: 0.875rem; margin-top: 0.5rem; display: inline-block;">
                        Showing first {{ max_safe_page|format_number }} pages. Use search to access more data.
                    </span>
                    {% endif %}
                </div>
                
                <div class="pagination-controls">
                    <a href="/?page=1&risk_filter={{ risk_filter }}{% if search_query %}&search={{ search_query }}{% endif %}" 
                       class="page-btn" 
                       {% if current_page == 1 %}style="pointer-events:none;opacity:0.5"{% endif %}>
                       ⏮️ First
                    </a>
                    
                    <a href="/?page={{ [1, current_page - 10]|max }}&risk_filter={{ risk_filter }}{% if search_query %}&search={{ search_query }}{% endif %}" 
                       class="page-btn"
                       {% if current_page <= 10 %}style="pointer-events:none;opacity:0.5"{% endif %}>
                       ⏪ -10
                    </a>
                    
                    <a href="/?page={{ current_page - 1 }}&risk_filter={{ risk_filter }}{% if search_query %}&search={{ search_query }}{% endif %}" 
                       class="page-btn" 
                       {% if current_page == 1 %}style="pointer-events:none;opacity:0.5"{% endif %}>
                       ◀️ Prev
                    </a>
                    
                    <span class="page-btn active">{{ current_page|format_number }}</span>
                    
                    <a href="/?page={{ current_page + 1 }}&risk_filter={{ risk_filter }}{% if search_query %}&search={{ search_query }}{% endif %}" 
                       class="page-btn" 
                       {% if current_page == total_pages %}style="pointer-events:none;opacity:0.5"{% endif %}>
                       Next ▶️
                    </a>
                    
                    <a href="/?page={{ [total_pages, current_page + 10]|min }}&risk_filter={{ risk_filter }}{% if search_query %}&search={{ search_query }}{% endif %}" 
                       class="page-btn"
                       {% if current_page > total_pages - 10 %}style="pointer-events:none;opacity:0.5"{% endif %}>
                       +10 ⏩
                    </a>
                    
                    <a href="/?page={{ total_pages }}&risk_filter={{ risk_filter }}{% if search_query %}&search={{ search_query }}{% endif %}" 
                       class="page-btn" 
                       {% if current_page == total_pages %}style="pointer-events:none;opacity:0.5"{% endif %}>
                       Last ⏭️
                    </a>
                </div>
                
                <div class="page-jump">
                    <label>Jump to page:</label>
                    <input type="number" id="pageInput" min="1" max="{{ total_pages }}" placeholder="Page #">
                    <button class="btn btn-primary" onclick="goToPage()">Go</button>
                </div>
            </div>
            {% endif %}
            {% endif %}
        </div>
        
        <!-- Manual Scanner Section -->
        <div class="scanner-section">
            <div class="scanner-header">
                <h2>Manual Company Scanner</h2>
            </div>
            <p style="color: var(--neutral-600); margin-bottom: 1rem;">Perform deep analysis on specific companies using their registration number</p>
            
            <div class="info-box">
                <strong>About Phoenix Activity Detection</strong>
                Phoenix activity occurs when directors deliberately liquidate a company to avoid debts, then establish a new entity with similar characteristics. Our scanner identifies patterns indicative of such fraudulent behavior through advanced analytics.
            </div>
            
            <form class="scan-form" action="/scan" method="GET">
                <div class="form-group">
                    <label for="company_number">Company Registration Number</label>
                    <input type="text" id="company_number" name="company_number" 
                           placeholder="e.g., 15478342, 10505136" required>
                </div>
                <button type="submit" class="btn btn-primary">
                    <span>🔍</span>
                    <span>Run Deep Scan</span>
                </button>
            </form>
        </div>
    </div>
</body>
</html>
"""

SCAN_REPORT_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Scan Report - {{ company_number }} | Phoenix Scanner</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet">
    <style>
        :root {
            --primary-navy: #1B2B4F;
            --primary-blue: #2563EB;
            --accent-gold: #F59E0B;
            --success-green: #10B981;
            --warning-amber: #F59E0B;
            --danger-red: #EF4444;
            --neutral-50: #F9FAFB;
            --neutral-100: #F3F4F6;
            --neutral-200: #E5E7EB;
            --neutral-700: #374151;
            --neutral-900: #111827;
            --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
            --shadow-xl: 0 20px 25px -5px rgba(0, 0, 0, 0.1);
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Inter', sans-serif;
            background-color: var(--neutral-50);
            color: var(--neutral-900);
            line-height: 1.6;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 2rem;
        }
        
        .back-link {
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            margin-bottom: 2rem;
            color: var(--primary-blue);
            text-decoration: none;
            font-weight: 600;
            padding: 0.75rem 1.5rem;
            background: white;
            border-radius: 10px;
            box-shadow: var(--shadow-lg);
            transition: all 0.3s ease;
        }
        
        .back-link:hover {
            transform: translateX(-4px);
            box-shadow: var(--shadow-xl);
        }
        
        h1 {
            font-size: 2rem;
            font-weight: 800;
            color: var(--neutral-900);
            margin-bottom: 2rem;
        }
        
        .card {
            background: white;
            border-radius: 16px;
            padding: 2rem;
            margin: 1.5rem 0;
            box-shadow: var(--shadow-lg);
            border: 1px solid var(--neutral-200);
        }
        
        .card h2 {
            font-size: 1.5rem;
            font-weight: 700;
            color: var(--neutral-900);
            margin-bottom: 1.5rem;
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }
        
        .risk-card {
            border-left: 5px solid {{ risk_color }};
        }
        
        .risk-score {
            display: flex;
            align-items: center;
            gap: 2rem;
        }
        
        .risk-number {
            font-size: 4rem;
            font-weight: 800;
            color: {{ risk_color }};
            line-height: 1;
        }
        
        .risk-level {
            font-size: 1.75rem;
            font-weight: 700;
            color: {{ risk_color }};
        }
        
        .indicator {
            padding: 1rem;
            margin: 0.75rem 0;
            background: var(--neutral-50);
            border-radius: 10px;
            border-left: 4px solid var(--neutral-200);
        }
        
        .indicator-critical { border-left-color: #DC2626; background: #FEE2E2; }
        .indicator-high { border-left-color: #F59E0B; background: #FEF3C7; }
        .indicator-medium { border-left-color: #FBBF24; background: #FEF9C3; }
        
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 1rem;
        }
        
        th, td {
            text-align: left;
            padding: 1rem;
            border-bottom: 1px solid var(--neutral-200);
        }
        
        th {
            background: var(--neutral-100);
            font-weight: 700;
            font-size: 0.875rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        
        td {
            font-weight: 500;
        }
    </style>
</head>
<body>
    <div class="container">
        <a href="/" class="back-link">
            <span>←</span>
            <span>Back to Dashboard</span>
        </a>
        <h1>Company Risk Report: {{ company_number }}</h1>
        
        {{ content | safe }}
    </div>
</body>
</html>
"""


@app.route('/')
def home():
    """Main dashboard with GraphQL-style filtering"""
    page = request.args.get('page', 1, type=int)
    risk_filter = request.args.get('risk_filter', '').strip()
    search_query = request.args.get('search', '').strip()
    
    # Get risk statistics (cached)
    risk_stats = get_risk_statistics()
    
    # Fetch filtered data
    if risk_filter or search_query:
        result = fetch_filtered_data(
            risk_filter=risk_filter if risk_filter else None,
            page=page,
            per_page=ROWS_PER_PAGE,
            search_query=search_query if search_query else None
        )
    else:
        # No filter selected - show empty state
        result = {
            'data': [],
            'columns': [],
            'total_rows': 0,
            'current_page': 1,
            'per_page': ROWS_PER_PAGE,
            'total_pages': 0,
            'is_filtered': False,
            'showing_from': 0,
            'showing_to': 0,
            'success': True
        }
    
    # Custom filter to format numbers with commas
    def format_number(value):
        try:
            return '{:,}'.format(int(value))
        except:
            return value
    
    app.jinja_env.filters['format_number'] = format_number
    
    return render_template_string(
        MAIN_TEMPLATE,
        data=result.get('data', []),
        columns=result.get('columns', []),
        total_rows=result.get('total_rows', 0),
        current_page=result.get('current_page', 1),
        total_pages=result.get('total_pages', 1),
        per_page=result.get('per_page', ROWS_PER_PAGE),
        search_query=search_query,
        risk_filter=risk_filter,
        is_filtered=result.get('is_filtered', False),
        filter_type=result.get('filter_type'),
        filter_value=result.get('filter_value'),
        showing_from=result.get('showing_from', 0),
        showing_to=result.get('showing_to', 0),
        risk_stats=risk_stats,
        is_limited=result.get('is_limited', False),
        max_safe_page=result.get('max_safe_page'),
        error=result.get('error')
    )


@app.route('/scan')
def scan():
    """Scan company for phoenix activity"""
    company_number = request.args.get('company_number', '').strip()
    
    if not company_number:
        return "No company number provided", 400
    
    report = deep_scan_company(company_number)
    
    if 'error' in report:
        return f"<h1>Error</h1><p>{report['error']}</p><p><a href='/'>Back</a></p>", 400
    
    risk_score = report['risk_score']
    risk_level = report['risk_level']
    
    if risk_score >= 70:
        risk_color = '#DC2626'
    elif risk_score >= 50:
        risk_color = '#F59E0B'
    elif risk_score >= 30:
        risk_color = '#FBBF24'
    else:
        risk_color = '#10B981'
    
    company = report['company']
    html_parts = []
    
    phoenix_status = report['is_phoenix']
    phoenix_confidence = report['phoenix_confidence']
    phoenix_bg = '#DC2626' if phoenix_status == 'YES' else '#10B981'
    
    html_parts.append(f'''
    <div class="card" style="border-left: 5px solid {phoenix_bg}; background: linear-gradient(135deg, {'#FEE2E2' if phoenix_status == 'YES' else '#D1FAE5'} 0%, white 100%);">
        <h2 style="color: {phoenix_bg};">Phoenix Activity Detection</h2>
        <div style="display: flex; align-items: center; gap: 2rem; margin: 1.5rem 0;">
            <div>
                <div style="font-size: 4rem; font-weight: 800; color: {phoenix_bg}; line-height: 1;">
                    {phoenix_status}
                </div>
                <div style="font-size: 0.875rem; color: var(--neutral-700); font-weight: 600;">Phoenix Status</div>
            </div>
            <div>
                <div style="font-size: 3rem; font-weight: 800; color: {phoenix_bg}; line-height: 1;">
                    {phoenix_confidence}%
                </div>
                <div style="font-size: 0.875rem; color: var(--neutral-700); font-weight: 600;">Confidence Score</div>
            </div>
        </div>
    ''')
    
    if report['phoenix_reasons']:
        html_parts.append('<div style="background: white; padding: 1.25rem; border-radius: 10px; border: 1px solid var(--neutral-200); margin-top: 1rem;">')
        html_parts.append('<strong style="display: block; margin-bottom: 0.75rem; color: var(--neutral-900);">Detection Reasons:</strong><ul style="margin: 0; padding-left: 1.5rem;">')
        for reason in report['phoenix_reasons']:
            html_parts.append(f'<li style="margin: 0.5rem 0;">{reason}</li>')
        html_parts.append('</ul></div>')
    
    html_parts.append('</div>')
    
    html_parts.append(f'''
    <div class="card risk-card">
        <h2>Risk Assessment</h2>
        <div class="risk-score">
            <div class="risk-number">{risk_score}</div>
            <div>
                <div class="risk-level">{risk_level} RISK</div>
                <div style="color: var(--neutral-700); font-weight: 500;">Overall Risk Score (0-100)</div>
            </div>
        </div>
    </div>
    ''')
    
    html_parts.append(f'''
    <div class="card">
        <h2>Company Information</h2>
        <table>
            <tr><th>Company Name</th><td>{company.get('company_name', 'N/A')}</td></tr>
            <tr><th>Company Number</th><td>{company.get('company_number', 'N/A')}</td></tr>
            <tr><th>Status</th><td><strong>{company.get('company_status', 'N/A')}</strong></td></tr>
            <tr><th>Type</th><td>{company.get('type', 'N/A')}</td></tr>
            <tr><th>Incorporated</th><td>{company.get('date_of_creation', 'N/A')}</td></tr>
        </table>
    </div>
    ''')
    
    if report['phoenix_indicators']:
        html_parts.append(f'<div class="card"><h2>Phoenix Activity Indicators ({len(report["phoenix_indicators"])})</h2>')
        for indicator in report['phoenix_indicators']:
            severity = indicator['severity']
            html_parts.append(f'''
            <div class="indicator indicator-{severity}">
                <strong style="text-transform: uppercase; font-weight: 700;">{severity}:</strong>
                <strong>{indicator['type'].replace('_', ' ')}</strong><br>
                {indicator['description']}
            </div>
            ''')
        html_parts.append('</div>')
    
    html_parts.append(f'<div class="card"><h2>Directors & Officers ({len(report["officers"])})</h2>')
    for officer in report['officers'][:10]:
        html_parts.append(f'''
        <div style="padding: 1.25rem; margin: 0.75rem 0; background: var(--neutral-50); border-radius: 10px; border: 1px solid var(--neutral-200);">
            <h3 style="margin: 0 0 0.75rem 0; color: var(--neutral-900);">{officer['name']}</h3>
            <p style="margin: 0.375rem 0;"><strong>Role:</strong> {officer['role']}</p>
            <p style="margin: 0.375rem 0;"><strong>Dissolved Links:</strong> {officer['dissolved_links']}</p>
            <p style="margin: 0.375rem 0;"><strong>Liquidation Links:</strong> {officer['liquidation_links']}</p>
            <p style="margin: 0.375rem 0;"><strong>Recent Formations:</strong> {officer['recent_formations']}</p>
        </div>
        ''')
    html_parts.append('</div>')
    
    content = ''.join(html_parts)
    
    return render_template_string(
        SCAN_REPORT_TEMPLATE,
        company_number=company_number,
        risk_color=risk_color,
        content=content
    )


@app.route('/api/stats')
def api_stats():
    """API endpoint for risk statistics"""
    stats = get_risk_statistics()
    return jsonify(stats)


@app.route('/api/query')
def api_query():
    """API endpoint for GraphQL-style queries"""
    page = request.args.get('page', 1, type=int)
    risk_filter = request.args.get('risk_filter', '').strip()
    search = request.args.get('search', '').strip()
    
    result = fetch_filtered_data(
        risk_filter=risk_filter if risk_filter else None,
        page=page,
        per_page=ROWS_PER_PAGE,
        search_query=search if search else None
    )
    
    return jsonify(result)


@app.route('/api/scan/<company_number>')
def api_scan(company_number):
    """API endpoint for scanning"""
    report = deep_scan_company(company_number)
    return jsonify(report)


if __name__ == '__main__':
    print("=" * 80)
    print("🚀 Phoenix Company Scanner - CORPORATE PREMIUM EDITION")
    print("=" * 80)
    print("\n✨ FEATURES:")
    print("  ✅ Professional Corporate UI Design")
    print("  ✅ Enterprise-grade Color Scheme")
    print("  ✅ GraphQL-Optimized Database Queries")
    print("  ✅ Advanced Risk Analytics Dashboard")
    print("  ✅ Responsive & Modern Interface")
    print("\n🎨 DESIGN HIGHLIGHTS:")
    print("  • Navy & Blue Primary Colors")
    print("  • Premium Card-based Layout")
    print("  • Smooth Animations & Transitions")
    print("  • Professional Typography (Inter Font)")
    print("  • Clean Data Tables")
    print("  • Intuitive Navigation")
    print("\n⏳ Fetching risk statistics...")
    
    stats = get_risk_statistics()
    
    print("\n📈 DATABASE OVERVIEW:")
    print(f"  ⚠️  High Risk Companies: {stats['high']:,}")
    print(f"  ⚡ Medium Risk Companies: {stats['medium']:,}")
    print(f"  ✓  Low Risk Companies: {stats['low']:,}")
    print(f"  📊 Total Records: {stats['all']:,}")
    print("\n" + "=" * 80)
    print("\n🚀 Starting Corporate Premium Server...")
    print("📍 Access: http://127.0.0.1:5000")
    print("\n💼 PROFESSIONAL FEATURES:")
    print("  • Risk-level filtering with visual indicators")
    print("  • Real-time search across millions of records")
    print("  • Interactive data tables with hover effects")
    print("  • Comprehensive pagination controls")
    print("  • Professional scan reports")
    print("\n" + "=" * 80 + "\n")
    
    app.run(debug=True, host='0.0.0.0', port=5000)
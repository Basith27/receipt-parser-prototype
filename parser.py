import os
import re
import json
from dotenv import load_dotenv
from azure.core.credentials import AzureKeyCredential
from azure.ai.formrecognizer import DocumentAnalysisClient
import google.generativeai as genai

def load_json_file(filename, default_value):
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"WARNING: Configuration file '{filename}' not found or invalid. Using default value.")
        return default_value
    
APP_CONFIG = load_json_file('config.json', {
    "confidence_threshold": 90,
    "confidence_weights": {
        "total": 0.40, "merchant_name": 0.25,
        "gstin": 0.20, "transaction_date": 0.15
    }
})
    
RULE_BASED_OVERRIDES = load_json_file('category_rules.json', {})
VALID_CATEGORIES = load_json_file('valid_categories.json', ["Food Cost", "Utilities", "Maintenance", "Uncategorized"])

# Configure the Gemini AI model
try:
    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY not found in .env file.")
    genai.configure(api_key=api_key)
    GEMINI_MODEL = genai.GenerativeModel('gemini-2.5-flash')
    print("Gemini AI configured successfully.")
except Exception as e:
    print(f"WARNING: Could not configure Gemini AI. AI categorization will be disabled. Error: {e}")
    GEMINI_MODEL = None

def initialize_client():
    load_dotenv()
    endpoint = os.getenv("DOCUMENT_INTELLIGENCE_ENDPOINT")
    key = os.getenv("DOCUMENT_INTELLIGENCE_KEY")
    if not endpoint or not key:
        raise ValueError("Azure credentials not found in .env file.")
    return DocumentAnalysisClient(endpoint=endpoint, credential=AzureKeyCredential(key))

def get_field_value(doc, field_name: str, value_type: str = "string"):
    field = None
    if hasattr(doc, 'fields'):
        field = doc.fields.get(field_name)
    elif isinstance(doc, dict):
        field = doc.get(field_name)
    
    if field and hasattr(field, 'value'):
        value = field.value
        if value_type == 'amount' and hasattr(value, 'amount'):
             value = value.amount
        
        confidence = round(field.confidence * 100, 2) if hasattr(field, 'confidence') else 100.0
        return value, confidence
    return None, 0.0

def sanitize_gstin(candidate: str):
    if not candidate:
        return None

    clean = re.sub(r'[^A-Z0-9]', '', str(candidate).upper())
    
    if len(clean) != 15:
        return None
    
    chars = list(clean)
    if chars[13] == '2':
        chars[13] = 'Z'
    
    return "".join(chars)

def extract_custom_fields(all_text: str):

    gstin = None
    hsn = None
    
    label_pattern = r'(?i)(?:GSTIN|GST\s?No\.?)[:\.\-\s]*([A-Z0-9]{15})'
    structure_pattern = r'\b(\d{2}[A-Z]{5}\d{4}[A-Z]{1}[A-Z0-9]{2})\b'

    match = re.search(label_pattern, all_text)
    if match:
        raw_gstin = match.group(1)
        gstin = sanitize_gstin(raw_gstin)

    if not gstin:
        match = re.search(structure_pattern, all_text)
        if match:
            raw_gstin = match.group(1)
            gstin = sanitize_gstin(raw_gstin)

    hsn_pattern = r'(?i)HSN(?:[:\.\-\s]*CODE)?[:\.\-\s]*(\d{4,8})'
    hsn_match = re.search(hsn_pattern, all_text)
    if hsn_match:
        hsn = hsn_match.group(1)
    
    return gstin, hsn

def determine_currency(doc, gstin: str) -> str:
    """
    Determines the currency symbol using a hierarchy:
    1. Explicit Currency Code (e.g., "INR") from the Total field.
    2. Azure's detected CountryRegion (e.g., "IND").
    3. Indian GSTIN detection.
    4. Address keywords.
    """
    
    # Map ISO Currency Codes to Symbols
    CURRENCY_MAP = {
        "INR": "₹",
        "USD": "$",
        "EUR": "€",
        "GBP": "£",
        "CAD": "$",
        "AUD": "$",
        "AED": "د.إ"
    }

    # Map Azure Country Codes to Symbols (Fallback)
    COUNTRY_MAP = {
        "IND": "₹",
        "USA": "$",
        "GBR": "£",
        "ARE": "د.إ"
    }

    # --- 1. Highest Priority: Extract Code from Total Field ---
    total_field = doc.fields.get("Total")
    if total_field and total_field.value:
        # Azure CurrencyValue objects often have a 'code' attribute (e.g., "INR")
        code = getattr(total_field.value, 'code', None)
        if code and code.upper() in CURRENCY_MAP:
            print(f"Currency: Detected via Total field code '{code}'")
            return CURRENCY_MAP[code.upper()]
        
        # Fallback: If 'code' isn't an attribute, try parsing the raw content string
        # This handles cases where it's extracted as "INR 4452" in the text
        content = total_field.content or ""
        for code_key in CURRENCY_MAP.keys():
            if code_key in content.upper():
                print(f"Currency: Detected via Total content string '{code_key}'")
                return CURRENCY_MAP[code_key]

    # --- 2. Second Priority: Azure detected CountryRegion ---
    country_field = doc.fields.get("CountryRegion")
    if country_field and country_field.value:
        # Standardizes "IND", "India", etc.
        country_val = str(country_field.value).upper().strip()
        if country_val in COUNTRY_MAP:
            print(f"Currency: Matched via CountryRegion '{country_val}'")
            return COUNTRY_MAP[country_val]

    # --- 3. Third Priority: Indian GSTIN ---
    if gstin and len(gstin) == 15 and gstin[:2].isdigit():
        return "₹"

    # --- 4. Fourth Priority: Address Keywords ---
    address_field = doc.fields.get("MerchantAddress")
    if address_field and address_field.value:
        addr_text = str(address_field.value).upper()
        if "INDIA" in addr_text or "IND" in addr_text:
            return "₹"
            
    # --- 5. Final Default ---
    return "$"

def categorize_receipt(data: dict) -> str:
    """
    Categorizes a receipt using a hybrid "AI-first with user override" strategy.
    """
    merchant_name = str(data.get("merchant_name", (None,))[0] or "").upper()
    item_descriptions = [str(item.get("description", (None,))[0] or "").upper() for item in data.get("items", [])]
    
    # --- Strategy 1: Check for User-Defined Manual Overrides ---
    # This gives the user ultimate power to force a category.
    for category, keywords in RULE_BASED_OVERRIDES.items():
        for keyword in keywords:
            if keyword.upper() in merchant_name:
                print(f"Categorization: Applied rule for '{keyword}' -> {category}")
                return category

    # --- Strategy 2: Use AI for Intelligent Categorization (if no rule matched) ---
    if not GEMINI_MODEL:
        print("Categorization: Gemini AI not configured, falling back to 'Uncategorized'.")
        return "Uncategorized"

    prompt = f"""
    You are an expert accountant for a restaurant. Based on the receipt details below,
    choose the single most appropriate expense category from this exact list: {json.dumps(VALID_CATEGORIES)}.

    - Vendor Name: "{merchant_name}"
    - Line Items: {', '.join(item_descriptions)}

    Return only the category name from the list. If no category fits, return "Uncategorized".
    """
    
    try:
        print("Categorization: Calling Gemini AI...")
        response = GEMINI_MODEL.generate_content(prompt)
        ai_category = response.text.strip().replace('"', '') # Clean up the AI's response
        
        # Final safety check: ensure the AI returned a valid category
        if ai_category in VALID_CATEGORIES:
            print(f"Categorization: AI returned '{ai_category}'")
            return ai_category
        else:
            print(f"WARNING: AI returned an invalid category ('{ai_category}'). Falling back.")
            return "Uncategorized"
    except Exception as e:
        print(f"WARNING: AI categorization failed. Error: {e}")
        return "Uncategorized"

def calculate_overall_confidence(data: dict) -> int:
    """
    Calculates a weighted average confidence score based on predefined weights.
    """
    weights = APP_CONFIG.get("confidence_weights", {})
    total_weighted_score = 0
    total_weight_applied = 0
    
    for field_name, weight in weights.items():
        if data.get(field_name) and data[field_name][1] is not None:
            confidence = data[field_name][1]
            total_weighted_score += (confidence * weight)
            total_weight_applied += weight
            
    if total_weight_applied == 0:
        return 0
        
    normalized_score = total_weighted_score / total_weight_applied
    return round(normalized_score)

def analyze_receipt(file_path: str) -> dict:
    """
    Analyzes a receipt, extracts standard and custom data, and enriches it with calculated fields.
    """
    print(f"Analyzing receipt: {file_path}...")
    
    doc_analysis_client = initialize_client()
    
    with open(file_path, "rb") as f:
        poller = doc_analysis_client.begin_analyze_document("prebuilt-receipt", document=f)       
    receipts = poller.result()

    # Initialize with default values
    parsed_data = {
        "merchant_name": (None, None), "transaction_date": (None, None), 
        "total": (None, None), "tax_amount": (None, None), "items": [], 
        "gstin": (None, None), "hsn": (None, None), "currency": "$", 
        "overall_confidence": 0, "category": "Uncategorized", "status": "Needs Review"
    }

    if receipts.documents:
        doc = receipts.documents[0]
        
        # --- STAGE 1: DATA EXTRACTION (from Azure and Regex) ---

        # Extract standard fields found by Azure
        parsed_data["merchant_name"] = get_field_value(doc, "MerchantName")
        date_val, date_conf = get_field_value(doc, "TransactionDate")
        parsed_data["transaction_date"] = (date_val.isoformat(), date_conf) if date_val else (None, None)
        parsed_data["total"] = get_field_value(doc, "Total", value_type="amount")
        parsed_data["tax_amount"] = get_field_value(doc, "TotalTax", value_type="amount")

        items_field = doc.fields.get("Items")
        if items_field and items_field.value:
            for item in items_field.value:
                desc = get_field_value(item.value, "Description")
                price = get_field_value(item.value, "TotalPrice", value_type="amount")
                quantity = get_field_value(item.value, "Quantity")
                parsed_data["items"].append({
                    "description": desc, "total_price": price, "quantity": quantity
                })

        # Extract custom fields from raw text using Regex
        all_text = receipts.content
        gstin_regex, hsn_regex = extract_custom_fields(all_text)
        
        # --- STAGE 2: DATA FINALIZATION AND ENRICHMENT ---

        # Finalize GSTIN/HSN: Use Regex as a fallback only. (Currently, it's the primary)
        # In a future version with Query Fields, Azure's result would be prioritized here.
        parsed_data["gstin"] = (gstin_regex, None) if gstin_regex else (None, None)
        parsed_data["hsn"] = (hsn_regex, None) if hsn_regex else (None, None)
        
        # Fallback for GSTIN in address
        if not parsed_data["gstin"][0]:
            addr_field = doc.fields.get("MerchantAddress")
            if addr_field and addr_field.value:
                gstin_in_addr, _ = extract_custom_fields(str(addr_field.value))
                if gstin_in_addr:
                    # Confidence for address fallback is lower
                    parsed_data["gstin"] = (gstin_in_addr, 85.0)

        # 1. Determine Currency (uses the final GSTIN value)
        parsed_data["currency"] = determine_currency(doc, parsed_data["gstin"][0])

        # 2. Calculate Overall Confidence (uses the new normalized function)
        parsed_data["overall_confidence"] = calculate_overall_confidence(parsed_data)

        # 3. Assign Category (uses AI and rule-based logic)
        parsed_data["category"] = categorize_receipt(parsed_data)

        # 4. Determine Initial Status based on confidence
        confidence_score = parsed_data["overall_confidence"]
        threshold = APP_CONFIG.get("confidence_threshold", 90)
        parsed_data["status"] = "Approved" if confidence_score >= threshold else "Needs Review"
        
    print("Analysis complete.")
    print("data :", parsed_data);
    return parsed_data


# --- TEST HARNESS ---
if __name__ == '__main__':
    test_file_name = "Receipt_2.jpg"
    try:
        if not os.path.exists(test_file_name):
            print(f"❌ Error: Test file '{test_file_name}' not found.")
        else:
            extracted_data = analyze_receipt(test_file_name)
            import json
            # The default=str is crucial for handling datetime objects from Azure
            print("\n--- EXTRACTED DATA ---")
            print(json.dumps(extracted_data, indent=2, default=str))
    except Exception as e:
        print(f"An error occurred: {e}")
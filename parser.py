import os
import re
from dotenv import load_dotenv
from azure.core.credentials import AzureKeyCredential
from azure.ai.formrecognizer import DocumentAnalysisClient

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
    
    if field:
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

def analyze_receipt(file_path: str) -> dict:
    print(f"Analyzing receipt: {file_path}...")
    
    doc_analysis_client = initialize_client()
    
    with open(file_path, "rb") as f:
        poller = doc_analysis_client.begin_analyze_document("prebuilt-receipt", document=f)       
    receipts = poller.result()

    parsed_data = {
        "merchant_name": None, "transaction_date": None, "total": None,
        "items": [], "gstin": None, "hsn": None
    }

    if receipts.documents:
        doc = receipts.documents[0]
        
        parsed_data["merchant_name"] = get_field_value(doc, "MerchantName")
        parsed_data["transaction_date"] = get_field_value(doc, "TransactionDate")
        parsed_data["total"] = get_field_value(doc, "Total", value_type="amount")

        items_field = doc.fields.get("Items")
        if items_field and items_field.value:
            for item in items_field.value:
                desc = get_field_value(item.value, "Description")
                price = get_field_value(item.value, "TotalPrice", value_type="amount")
                parsed_data["items"].append({"description": desc, "total_price": price})

        all_text = receipts.content
        gstin_val, hsn_val = extract_custom_fields(all_text)
        
        parsed_data["gstin"] = (gstin_val, 90.0) if gstin_val else (None, 0.0)
        parsed_data["hsn"] = (hsn_val, 90.0) if hsn_val else (None, 0.0)

        if not parsed_data["gstin"][0]:
            addr_field = doc.fields.get("MerchantAddress")
            if addr_field and addr_field.value:
                gstin_in_addr, _ = extract_custom_fields(str(addr_field.value))
                if gstin_in_addr:
                    parsed_data["gstin"] = (gstin_in_addr, 85.0)

    print("Analysis complete.")
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
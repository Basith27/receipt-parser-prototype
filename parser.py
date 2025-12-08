import os
import re
from dotenv import load_dotenv
from azure.core.credentials import AzureKeyCredential
from azure.ai.formrecognizer import DocumentAnalysisClient

# --- 1. SETUP AND INITIALIZATION ---
def initialize_client():
    """
    Loads environment variables and initializes the DocumentAnalysisClient.
    """
    load_dotenv()  # Load variables from .env file
    endpoint = os.getenv("DOCUMENT_INTELLIGENCE_ENDPOINT")
    key = os.getenv("DOCUMENT_INTELLIGENCE_KEY")

    if not endpoint or not key:
        raise ValueError("Azure credentials not found")
    
    # Create and return the client
    return DocumentAnalysisClient(endpoint=endpoint, credential=AzureKeyCredential(key))

def get_field_value(doc, field_name, value_type="string"):
    """
    Safely retrieves a field's value and confidence from the analyzed document.
    Returns the value and confidence score.
    """
    # Handle both dict-like and object-like field access
    if hasattr(doc, 'fields'):
        field = doc.fields.get(field_name)
    elif isinstance(doc, dict):
        field = doc.get(field_name)
    else:
        return None, 0.0
    
    if field:
        if value_type == 'amount' and hasattr(field, 'value') and hasattr(field.value, 'amount'):
             value = field.value.amount
        elif hasattr(field, 'value'):
             value = field.value
        else:
             value = field

        confidence = round(field.confidence * 100, 2) if hasattr(field, 'confidence') else 100.0
        return value, confidence
    return None, 0.0


def sanitize_gstin(candidate):
    """
    Cleans a potential GSTIN string and fixes common OCR errors.
    """
    # 1. Remove any spaces or special chars caught by regex
    clean = re.sub(r'[^A-Z0-9]', '', candidate.upper())
    
    # 2. Length check: GSTIN must be 15 chars
    if len(clean) != 15:
        return None

    # 3. FIX: The 14th character (index 13) is standardly 'Z' by default.
    # OCR often reads 'Z' as '2'. We force fix this.
    chars = list(clean)
    if chars[13] == '2':
        chars[13] = 'Z'
    
    # 4. FIX: The 13th character (index 12) must be a number or alpha.
    # (GST logic: 1-9 alphanumeric). Usually numeric.
    
    return "".join(chars)


def extract_custom_fields(all_text):
    """
    Extracts GSTIN and HSN using improved patterns and logic.
    """
    gstin = None
    hsn = None
    
    # --- GSTIN STRATEGY ---
    # Pattern A: Look for explicit label (High Confidence)
    # Matches: "GSTIN : 29ABCDE1234F1Z5" or "GSTIN:29ABC..."
    label_pattern = r'(?i)(?:GST|GSTIN|GST\s?No\.?)[:\.\-\s]*([A-Z0-9]{15})'
    
    # Pattern B: Look for the structure itself (Fallback)
    # 2 digits + 5 letters + 4 digits + 1 letter + 1 char + Z + 1 char
    # We use [A-Z0-9] loosely for the last 3 chars to catch OCR errors like '2' instead of 'Z'
    structure_pattern = r'\b\d{2}[A-Z]{5}\d{4}[A-Z]{1}[A-Z0-9]{1}[Z2]{1}[A-Z0-9]{1}\b'

    # Try explicit label first
    gstin_match = re.search(label_pattern, all_text)
    
    if gstin_match:
        raw_gstin = gstin_match.group(1)
        gstin = sanitize_gstin(raw_gstin)
    
    # If no label match, search for the pattern structure
    if not gstin:
        struct_match = re.search(structure_pattern, all_text)
        if struct_match:
            gstin = sanitize_gstin(struct_match.group(0))

    # --- HSN STRATEGY ---
    # HSN pattern: 4-8 digits, often preceded by "HSN"
    hsn_pattern = r'(?i)HSN(?:[:\.\-\s]*CODE)?[:\.\-\s]*(\d{4,8})'
    hsn_match = re.search(hsn_pattern, all_text)
    if hsn_match:
        hsn = hsn_match.group(1)
    
    return gstin, hsn


def analyze_receipt(file_path):
    print(f"Analyzing receipt: {file_path}...")
    
    doc_analysis_client = initialize_client()
    
    with open(file_path, "rb") as f:
        # NOTE: If you are on the Standard tier (S0), you can use the 'query_fields' feature
        # which is much more accurate than Regex. If on Free tier, stick to Regex.
        # Below is standard prebuilt-receipt.
        poller = doc_analysis_client.begin_analyze_document(
            "prebuilt-receipt",
            document=f
        )       

    receipts = poller.result()

    parsed_data = {
        "merchant_name": None,
        "transaction_date": None,
        "total": None,
        "items": [],
        "gstin": None,
        "hsn": None
    }

    if receipts.documents:
        doc = receipts.documents[0]
        
        parsed_data["merchant_name"] = get_field_value(doc, "MerchantName")
        parsed_data["transaction_date"] = get_field_value(doc, "TransactionDate")
        parsed_data["total"] = get_field_value(doc, "Total", value_type="amount")

        items_field = doc.fields.get("Items")
        if items_field and items_field.value:
            for item in items_field.value:
                description = get_field_value(item.value, "Description")
                total_price = get_field_value(item.value, "TotalPrice", value_type="amount")
                parsed_data["items"].append({"description": description, "total_price": total_price})

        # --- IMPROVED EXTRACTION ---
        # 1. Try to get GSTIN from document content (Raw OCR)
        all_text = receipts.content
        gstin_regex, hsn_regex = extract_custom_fields(all_text)
        
        # 2. Logic to assign confidence
        # Since we are "fixing" the data, we can't get a true confidence score from Azure.
        # We assign 90% if we found it via Regex, else 0.
        parsed_data["gstin"] = (gstin_regex, 90.0) if gstin_regex else (None, 0.0)
        parsed_data["hsn"] = (hsn_regex, 90.0) if hsn_regex else (None, 0.0)
        
        # 3. (Advanced) Fallback check: Sometimes MerchantAddress contains the GSTIN
        if not parsed_data["gstin"][0]:
            addr = get_field_value(doc, "MerchantAddress")[0]
            if addr:
                gstin_in_addr, _ = extract_custom_fields(str(addr))
                if gstin_in_addr:
                    parsed_data["gstin"] = (gstin_in_addr, 85.0)

    print("Analysis complete.")
    return parsed_data


# --- 4. TEST HARNESS ---
if __name__ == '__main__':
    # This block will only run when you execute `python parser.py` directly
    # It allows us to test our function without needing a web server
    
    # IMPORTANT: Download a test receipt and place it in your project folder
    # For best results, use the "JC TRADING" receipt which has a clear GSTIN
    test_file_name = "Receipt_2.jpg" # Change this to your test file's name
    
    try:
        if not os.path.exists(test_file_name):
            print(f"❌ Error: Test file '{test_file_name}' not found.")
            print("Please download a receipt, save it in this folder, and update the file name in the script.")
        else:
            # Run the analysis
            extracted_data = analyze_receipt(test_file_name)
            
            # Print the results in a readable format
            import json
            print("\n--- EXTRACTED DATA ---")
            print(json.dumps(extracted_data, indent=2, default=str))

    except Exception as e:
        print(f"An error occurred: {e}")
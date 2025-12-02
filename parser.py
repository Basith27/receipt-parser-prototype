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


def extract_custom_fields(all_text):
    """
    Extracts GSTIN and HSN from raw text using pattern matching.
    """
    gstin = None
    hsn = None
    
    # GSTIN pattern: 15 characters (2 digits + 10 alphanumeric + 1 digit + 1 letter + 1 alphanumeric)
    # Example: 29AABCT1332L1Z2
    # gstin_pattern = r'\b\d{2}[A-Z]{5}\d{4}[A-Z]{1}[A-Z\d]{1}[Z]{1}[A-Z\d]{1}\b'
    gstin_pattern = r'(?i)GSTIN[:\s]*([A-Z0-9]{15})\b'
    gstin_match = re.search(gstin_pattern, all_text)
    if gstin_match:
        gstin = gstin_match.group(0)
    
    # HSN pattern: 4-8 digits, often preceded by "HSN" or "HSN Code"
    # Example: HSN: 1234 or HSN Code: 12345678
    hsn_pattern = r'HSN[:\s]*CODE[:\s]*(\d{4,8})|HSN[:\s]*(\d{4,8})'
    hsn_match = re.search(hsn_pattern, all_text, re.IGNORECASE)
    if hsn_match:
        hsn = hsn_match.group(1) or hsn_match.group(2)
    
    return gstin, hsn


# --- 2. THE MAIN ANALYSIS FUNCTION ---
def analyze_receipt(file_path):
    """
    Analyzes a receipt document from a given file path.
    """
    print(f"Analyzing receipt: {file_path}...")
    
    # Initialize the client
    doc_analysis_client = initialize_client()
    
    # Open the file and send it to Azure
    with open(file_path, "rb") as f:
        poller = doc_analysis_client.begin_analyze_document(
            "prebuilt-receipt",
            document=f
        )       

    receipts = poller.result()

    # --- 3. PARSE THE RESPONSE ---
    parsed_data = {
        "merchant_name": None,
        "transaction_date": None,
        "total": None,
        "items": [],
        "gstin": None,
        "hsn": None
    }

    # The result can contain multiple receipts, we'll process the first one
    if receipts.documents:
        doc = receipts.documents[0]
        
        # Extract standard fields using our helper function
        parsed_data["merchant_name"] = get_field_value(doc, "MerchantName")
        parsed_data["transaction_date"] = get_field_value(doc, "TransactionDate")
        parsed_data["total"] = get_field_value(doc, "Total", value_type="amount")

        # Extract line items
        items_field = doc.fields.get("Items")
        if items_field and items_field.value:
            for item in items_field.value:
                description = get_field_value(item.value, "Description")
                total_price = get_field_value(item.value, "TotalPrice", value_type="amount")
                parsed_data["items"].append({"description": description, "total_price": total_price})

        # Extract custom query fields
        all_text = receipts.content
        gstin, hsn = extract_custom_fields(all_text)
        parsed_data["gstin"] = (gstin, 100.0) if gstin else (None, 0.0)
        parsed_data["hsn"] = (hsn, 100.0) if hsn else (None, 0.0)
        
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
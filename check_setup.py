import os
from dotenv import load_dotenv

# Load environment variables from a .env file if it exists
load_dotenv()

print("--- Checking Azure AI Document Intelligence Setup ---")

# Get the endpoint and key from environment variables
endpoint = os.getenv("DOCUMENT_INTELLIGENCE_ENDPOINT")
key = os.getenv("DOCUMENT_INTELLIGENCE_KEY")

# Check for the endpoint
if endpoint:
    print("✅ Endpoint: Found and loaded successfully.")
else:
    print("❌ Endpoint: NOT FOUND. Please set the DOCUMENT_INTELLIGENCE_ENDPOINT environment variable.")

# Check for the key
if key:
    print("✅ Key: Found and loaded successfully.")
else:
    print("❌ Key: NOT FOUND. Please set the DOCUMENT_INTELLIGENCE_KEY environment variable.")

# Final status
if endpoint and key:
    print("\n🎉 Success! Your environment is configured correctly.")
    print("You are ready to proceed to Phase 2.")
else:
    print("\n❗️ Action Required: Please fix the missing environment variables before proceeding.")
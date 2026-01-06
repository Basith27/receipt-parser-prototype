import os
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file
from werkzeug.utils import secure_filename
from parser import analyze_receipt
from flask_cors import CORS
import xml.etree.ElementTree as ET
from xml.dom import minidom
import io
import json
import csv

app = Flask(__name__)
CORS(app)

app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = 'uploads'


MOCK_RECEIPTS_DB = [
    { "id": '1', "vendorName": 'Sysco Food Services', "totalAmount": 1245.50, "date": '2025-10-24', "category": 'Food Cost', "status": "Approved" },
    { "id": '2', "vendorName": 'City Utilities', "totalAmount": 340.00, "date": '2025-10-25', "category": 'Utilities', "status": "Needs Review" },
    { "id": '3', "vendorName": 'Office Supplies Inc.', "totalAmount": 150.75, "date": '2025-10-26', "category": 'Maintenance', "status": "Approved" }
]

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/analyze', methods=['POST'])
def analyze():
    if 'receipt' not in request.files:
        print("No file part in the request")
        return redirect(request.url)
        
    file = request.files['receipt']

    if file.filename == '':
        print("No selected file")
        return redirect(request.url)

    if file:
        filename = secure_filename(file.filename)
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        print(f"File saved to: {filepath}")

        try:
            extracted_data = analyze_receipt(filepath)

            return jsonify({
                "success": True,
                "data": extracted_data
            }), 200

        except Exception as e:
            print(f"An error occurred during analysis: {e}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    return redirect(url_for('index'))

@app.route('/export', methods=['POST'])
def export_receipts():
    try:
        # 1. Get the configuration from the front-end request
        data = request.get_json()
        export_format = data.get('format')
        receipt_ids_to_export = data.get('receipt_ids', [])

        if not export_format or not receipt_ids_to_export:
            return jsonify({"success": False, "error": "Format and receipt_ids are required."}), 400

        # 2. Filter the database for the selected, approved receipts
        # In a real app, you would query your DB: SELECT * FROM receipts WHERE id IN (...)
        receipts_to_export = [
            r for r in MOCK_RECEIPTS_DB 
            if r['id'] in receipt_ids_to_export and r['status'] == 'Approved'
        ]

        if not receipts_to_export:
            return jsonify({"success": False, "error": "No valid, approved receipts found for the selected IDs."}), 404

        # --- 3. Generate the file based on the requested format ---

        # --- CSV Generation ---
        if export_format == 'csv':
            si = io.StringIO()
            writer = csv.writer(si)
            writer.writerow(['Receipt ID', 'Date', 'Vendor', 'Category', 'Total Amount', 'Tax Amount'])
            for r in receipts_to_export:
                writer.writerow([r['id'], r['date'], r['vendorName'], r['category'], r['totalAmount'], r.get('taxAmount', 0.0)])
            
            output = io.BytesIO(si.getvalue().encode('utf-8'))
            return send_file(output, mimetype='text/csv', as_attachment=True, download_name='export.csv')

        # --- JSON Generation ---
        elif export_format == 'json':
            output = io.BytesIO(json.dumps(receipts_to_export, indent=2).encode('utf-8'))
            return send_file(output, mimetype='application/json', as_attachment=True, download_name='export.json')

        # --- Tally XML Generation ---
        elif export_format == 'xml':
            envelope = ET.Element("ENVELOPE")
            # ... (The rest of your Tally XML generation logic goes here, looping over `receipts_to_export`)
            # ... (This part is omitted for brevity but is the same as before)
            
            xml_string = ET.tostring(envelope, 'utf-8')
            pretty_xml = minidom.parseString(xml_string).toprettyxml(indent="  ")
            output = io.BytesIO(pretty_xml.encode('utf-8'))
            return send_file(output, mimetype='application/xml', as_attachment=True, download_name='export.xml')
        
        else:
            return jsonify({"success": False, "error": "Invalid format specified."}), 400

    except Exception as e:
        print(f"Error during export: {e}")
        return jsonify({"success": False, "error": "Failed to generate export"}), 500

if __name__ == '__main__':
    app.run(debug=True)
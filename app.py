import os
from flask import Flask, render_template, request, redirect, url_for
from werkzeug.utils import secure_filename
from parser import analyze_receipt

app = Flask(__name__)

app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = 'uploads'

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
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        print(f"File saved to: {filepath}")

        try:
            extracted_data = analyze_receipt(filepath)
            return render_template('results.html', data=extracted_data, filename=filename)

        except Exception as e:
            print(f"An error occurred during analysis: {e}")
            return render_template('error.html', error_message=str(e))

    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
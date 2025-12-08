from flask import Flask, render_template, request

# Initialize the Flask application
app = Flask(__name__)

# --- Configuration ---
# It's good practice to have a config for uploads
# This sets a max file size of 16 megabytes
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
# The folder where we will store the uploaded files temporarily
app.config['UPLOAD_FOLDER'] = 'uploads'


# --- Routes ---
# A "route" is a URL that our application supports.
# This function will run when a user visits the main page of our website (the "/")
@app.route('/')
def index():
    """Renders the main upload page."""
    # render_template looks inside the 'templates' folder for the given file
    return render_template('index.html')


# This line allows us to run the server by executing "python app.py"
if __name__ == '__main__':
    # debug=True will automatically reload the server when you save changes
    app.run(debug=True)
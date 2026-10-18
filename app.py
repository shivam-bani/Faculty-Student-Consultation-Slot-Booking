"""CampusSync backend (Flask).

Step 1: serve the existing frontend from static/ and expose a health check.
Run with:  python app.py   then open http://127.0.0.1:5000/
"""
from flask import Flask, jsonify

# static_url_path="" serves static/index.html at /index.html (not /static/index.html),
# so the pages' relative links (style.css, script.js, book.html, ...) keep working unchanged.
# Only files inside static/ are served: app.py, .env and the rest of the project are not reachable.
app = Flask(__name__, static_folder="static", static_url_path="")


@app.get("/")
def home():
    # The login page is the entry point, exactly as when index.html was opened directly.
    return app.send_static_file("index.html")


@app.get("/api/health")
def health():
    # A tiny endpoint that proves the server is running and returns JSON.
    return jsonify(ok=True)


if __name__ == "__main__":
    # host="127.0.0.1": reachable only from this laptop.
    # debug=True: auto-reload on code changes and show detailed errors. Never expose a
    # debug server to a network, because its error page can run Python code.
    app.run(host="127.0.0.1", port=5000, debug=True)

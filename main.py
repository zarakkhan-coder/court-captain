from flask import Flask, jsonify
import os

app = Flask(__name__)
from flask import jsonify

@app.get("/health")
def health():
    return jsonify(ok=True), 200
@app.get("/")
def home():
    return "<h1>CourtCaptain</h1><p>Service is up. Try <a href='/health'>/health</a>.</p>"

@app.get("/health")
def health():
    return jsonify(ok=True), 200

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

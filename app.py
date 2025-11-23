# app.py
import os
import time
import hmac
import hashlib
import base64
import json
from flask import Flask, request, jsonify
import requests

# Config from environment (set these on Render)
API_KEY = os.environ.get("3c8b4365-2d73-47d5-b992-eb97c0ed23a4")
API_SECRET = os.environ.get("xZ+IYNg6K6K3AaDWWtMMziYAM12f2AAIHdQgw/SJ51ZyfXjP7GlHgAEolvA5dRhMSIBG56RWSxwJngGhJop1eA==")  # base64-encoded secret from Coinbase
API_PASSPHRASE = os.environ.get("HaiKhongHaiNam")
API_BASE = os.environ.get("COINBASE_URL", "https://api.exchange.coinbase.com")  # change to sandbox base for testing

app = Flask(__name__)

def cb_place_order(product_id, side, size, order_type="market", price=None):
    path = "/orders"
    url = API_BASE + path
    body = {"product_id": product_id, "side": side, "type": order_type}

    # For market orders, Coinbase expects 'size' or 'funds' depending on API config.
    # We'll send size (quantity in base currency)
    body["size"] = str(size)
    if order_type == "limit" and price is not None:
        body["price"] = str(price)
        body["time_in_force"] = "GTC"

    body_json = json.dumps(body)
    timestamp = str(time.time())
    message = timestamp + "POST" + path + body_json

    # API_SECRET from Coinbase is base64 encoded; decode before HMAC
    secret_decoded = base64.b64decode(API_SECRET)
    signature = hmac.new(secret_decoded, message.encode('utf-8'), hashlib.sha256)
    signature_b64 = base64.b64encode(signature.digest()).decode()

    headers = {
        'CB-ACCESS-KEY': API_KEY,
        'CB-ACCESS-SIGN': signature_b64,
        'CB-ACCESS-TIMESTAMP': timestamp,
        'CB-ACCESS-PASSPHRASE': API_PASSPHRASE,
        'Content-Type': 'application/json'
    }

    r = requests.post(url, headers=headers, data=body_json, timeout=15)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"text": r.text}

def parse_kv_text(text):
    # parse "symbol: BTC-USD; action: buy; amount: 10" style
    data = {}
    for part in text.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            data[k.strip()] = v.strip()
    return data

@app.route("/webhook", methods=["POST"])
def webhook():
    raw = request.get_data(as_text=True)
    # Try JSON payload first
    try:
        payload = request.get_json(force=True)
    except Exception:
        # fallback to key:value parsing
        payload = parse_kv_text(raw)

    # Accept different field names for compatibility
    product_id = payload.get("symbol") or payload.get("product_id") or payload.get("product")
    action = payload.get("action") or payload.get("side")
    amount = payload.get("amount") or payload.get("size") or payload.get("qty")
    order_type = payload.get("order_type", "market")

    if not product_id or not action or not amount:
        return jsonify({"error": "missing fields", "received": payload}), 400

    # Sanitize/limit order size (safety)
    try:
        size = float(amount)
    except:
        return jsonify({"error":"invalid amount"}), 400

    MAX_SIZE = float(os.environ.get("MAX_ORDER_SIZE", "0"))  # if zero, no extra server-side cap
    if MAX_SIZE > 0 and size > MAX_SIZE:
        return jsonify({"error":"order size exceeds server safety limit", "max": MAX_SIZE}), 403

    # Place the order
    status, resp = cb_place_order(product_id, action.lower(), size, order_type=order_type)
    # Log or store response here (omitted for brevity)
    return jsonify({"status_code": status, "response": resp}), 200 if 200 <= status < 300 else 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

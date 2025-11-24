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
API_KEY = os.environ.get("COINBASE_KEY")
API_SECRET = os.environ.get("COINBASE_SECRET")  # must be base64 string from Coinbase
API_PASSPHRASE = os.environ.get("COINBASE_PASSPHRASE")
API_BASE = os.environ.get("COINBASE_URL", "https://api.exchange.coinbase.com")
WEBHOOK_AUTH = os.environ.get("WEBHOOK_AUTH", "change_me")
MAX_ORDER_SIZE = float(os.environ.get("MAX_ORDER_SIZE", "0"))  # 0 = disabled
ENABLE_LOGGING = os.environ.get("ENABLE_LOGGING", "1") == "1"

app = Flask(__name__)

def log(*args, **kwargs):
    if ENABLE_LOGGING:
        print(*args, **kwargs)

@app.route("/", methods=["GET"])
def index():
    return jsonify({"status":"ok","service":"coinbase-webhook"}), 200

def cb_sign_request(method, path, body):
    # Ensure API_SECRET exists
    if not API_SECRET:
        raise ValueError("COINBASE_SECRET is not set")
    try:
        secret_decoded = base64.b64decode(API_SECRET)
    except Exception as e:
        raise ValueError("COINBASE_SECRET is not valid base64: " + str(e))
    timestamp = str(time.time())
    body_json = body if isinstance(body, str) else json.dumps(body) if body else ""
    message = timestamp + method.upper() + path + body_json
    signature = hmac.new(secret_decoded, message.encode('utf-8'), hashlib.sha256).digest()
    signature_b64 = base64.b64encode(signature).decode()
    return timestamp, signature_b64

def cb_place_order(product_id, side, size, order_type="market", price=None):
    path = "/orders"
    url = API_BASE + path
    body = {"product_id": product_id, "side": side, "type": order_type, "size": str(size)}
    if order_type == "limit" and price is not None:
        body["price"] = str(price)
        body["time_in_force"] = "GTC"

    # Sign request -- will raise ValueError with helpful message if creds missing/invalid
    timestamp, signature_b64 = cb_sign_request("POST", path, json.dumps(body))
    headers = {
        'CB-ACCESS-KEY': API_KEY,
        'CB-ACCESS-SIGN': signature_b64,
        'CB-ACCESS-TIMESTAMP': timestamp,
        'CB-ACCESS-PASSPHRASE': API_PASSPHRASE,
        'Content-Type': 'application/json'
    }

    r = requests.post(url, headers=headers, json=body, timeout=20)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"raw_text": r.text}

def parse_kv_text(text):
    data = {}
    for part in text.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            data[k.strip()] = v.strip()
    return data

@app.route("/webhook", methods=["POST"])
def webhook():
    raw = request.get_data(as_text=True)
    log("Received raw:", raw[:2000])
    try:
        payload = request.get_json(force=True)
    except Exception:
        payload = parse_kv_text(raw)

    # Normalize keys
    product_id = payload.get("symbol") or payload.get("product_id") or payload.get("product")
    action = payload.get("action") or payload.get("side")
    amount = payload.get("amount") or payload.get("size") or payload.get("qty")
    order_type = payload.get("order_type", "market")
    auth = payload.get("auth") or payload.get("token") or payload.get("key")

    # Auth check
    if not auth or auth != WEBHOOK_AUTH:
        log("Auth failed. payload auth:", auth, "expected:", WEBHOOK_AUTH)
        return jsonify({"error": "invalid auth"}), 401

    if not product_id or not action or not amount:
        return jsonify({"error": "missing fields", "received": payload}), 400

    # Convert amount to float
    try:
        size = float(amount)
    except Exception as e:
        return jsonify({"error": "invalid amount format", "err": str(e)}), 400

    # Server-side safety cap
    if MAX_ORDER_SIZE > 0 and size > MAX_ORDER_SIZE:
        return jsonify({"error": "order size exceeds server safety limit", "max": MAX_ORDER_SIZE}), 403

    # Short-circuit for TEST_MODE or missing API keys
    if os.environ.get("TEST_MODE", "0") == "1" or not (API_KEY and API_SECRET and API_PASSPHRASE):
        log("TEST_MODE or missing creds - simulating order", {"product": product_id, "side": action, "size": size})
        return jsonify({"ok": True, "simulated": True, "payload": {"product": product_id, "side": action, "size": size}}), 200

    # Place order (real mode) with exception handling
    try:
        status, resp = cb_place_order(product_id, action.lower(), size, order_type=order_type)
        log("Order response:", status, resp)
        ok = 200 <= status < 300
        return jsonify({"ok": ok, "status_code": status, "response": resp}), (200 if ok else 500)
    except Exception as e:
        # Return the exception message to help debugging
        log("Exception placing order:", str(e))
        return jsonify({"error": "exception placing order", "err": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

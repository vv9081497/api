#!/usr/bin/env python3
"""
Stripe 1$ Charge API (With proper tokenization)
Port: 8085
Endpoint: /charge?cc=CC|MM|YY|CVV
DEV: chirgg_911
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import json
import time
import random
import string
import re
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
CORS(app)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STRIPE KEYS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SECRET_KEY = "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"
PUBLISHABLE_KEY = "pk_live_51SpEJXHjd1o0CNQMYd7W330binPIphhKadi1DsiwUdpeJphU9n9icPpQ222pF7qSfgqNGvV1SWlcvelif7DYlZIh00OOM0hif4"
CURRENCY = "usd"
AMOUNT = 100  # $1.00 in cents

DEV = "chirgg_911"
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def generate_guid():
    return f"{''.join(random.choices(string.hexdigits.lower(), k=8))}-{''.join(random.choices(string.hexdigits.lower(), k=4))}-{''.join(random.choices(string.hexdigits.lower(), k=4))}-{''.join(random.choices(string.hexdigits.lower(), k=4))}-{''.join(random.choices(string.hexdigits.lower(), k=12))}"

def luhn_check(card_number: str) -> bool:
    total = 0
    reverse_digits = card_number[::-1]
    for i, d in enumerate(reverse_digits):
        n = int(d)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0

def format_card_number(card):
    card = re.sub(r'\D', '', card)
    return f"{card[:4]} {card[4:8]} {card[8:12]} {card[12:16]}"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STRIPE CHARGE FUNCTION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def stripe_charge(cc, mm, yy, cvv, amount=AMOUNT, currency=CURRENCY):
    start = time.time()
    
    try:
        if len(yy) == 4:
            yy = yy[-2:]
        if len(mm) == 1:
            mm = f"0{mm}"
        
        if not luhn_check(cc):
            elapsed = f"{time.time() - start:.2f}s"
            return {
                "approved": False,
                "message": "Invalid card number",
                "status": "DECLINED",
                "time": elapsed
            }
        
        # Generate Stripe identifiers
        guid = generate_guid()
        muid = generate_guid()
        sid = generate_guid()
        
        session = requests.Session()
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Step 1: Create Payment Method via Stripe.js flow
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        headers_pm = {
            'authority': 'api.stripe.com',
            'accept': 'application/json',
            'accept-language': 'en-US,en;q=0.9',
            'content-type': 'application/x-www-form-urlencoded',
            'origin': 'https://js.stripe.com',
            'referer': 'https://js.stripe.com/',
            'sec-ch-ua': '"Chromium";v="120", "Not(A:Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'user-agent': USER_AGENT,
        }
        
        # Create payment method with full Stripe.js style payload
        pm_data = {
            'type': 'card',
            'card[number]': cc,
            'card[cvc]': cvv,
            'card[exp_month]': mm,
            'card[exp_year]': f"20{yy}",
            'billing_details[address][postal_code]': '10001',
            'billing_details[address][country]': 'US',
            'billing_details[name]': 'John Doe',
            'billing_details[email]': f'user{random.randint(1000,9999)}@gmail.com',
            'payment_user_agent': 'stripe.js/3e1545b9ab; stripe-js-v3/3e1545b9ab; payment-element; deferred-intent',
            'referrer': 'https://example.com',
            'guid': guid,
            'muid': muid,
            'sid': sid,
            'key': PUBLISHABLE_KEY,
            '_stripe_version': '2024-06-20',
        }
        
        resp = session.post('https://api.stripe.com/v1/payment_methods', headers=headers_pm, data=pm_data, timeout=30)
        pm_result = resp.json()
        
        if 'error' in pm_result:
            elapsed = f"{time.time() - start:.2f}s"
            error_msg = pm_result['error'].get('message', 'Payment method failed')
            return {
                "approved": False,
                "message": error_msg,
                "status": "DECLINED",
                "time": elapsed
            }
        
        pm_id = pm_result.get('id')
        if not pm_id:
            elapsed = f"{time.time() - start:.2f}s"
            return {
                "approved": False,
                "message": "Failed to create payment method",
                "status": "ERROR",
                "time": elapsed
            }
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Step 2: Create Payment Intent with the payment method
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        headers_pi = {
            'authority': 'api.stripe.com',
            'accept': 'application/json',
            'content-type': 'application/x-www-form-urlencoded',
            'user-agent': USER_AGENT,
        }
        
        pi_data = {
            'amount': amount,
            'currency': currency,
            'payment_method': pm_id,
            'confirm': 'true',
            'payment_method_types[]': 'card',
            'return_url': 'https://example.com/success',
            'statement_descriptor': 'CHARGE 1$',
        }
        
        # Use secret key for this request
        headers_pi['Authorization'] = f'Bearer {SECRET_KEY}'
        
        resp = session.post('https://api.stripe.com/v1/payment_intents', headers=headers_pi, data=pi_data, timeout=30)
        pi_result = resp.json()
        
        elapsed = f"{time.time() - start:.2f}s"
        
        if 'error' in pi_result:
            error_msg = pi_result['error'].get('message', 'Payment failed')
            return {
                "approved": False,
                "message": error_msg,
                "status": "DECLINED",
                "time": elapsed
            }
        
        status = pi_result.get('status')
        pi_id = pi_result.get('id')
        
        if status == 'succeeded':
            return {
                "approved": True,
                "message": "CHARGED ✅",
                "status": "APPROVED",
                "transaction_id": pi_id,
                "amount": f"${pi_result.get('amount', 0)/100:.2f}",
                "time": elapsed
            }
        elif status == 'requires_action' or status == 'requires_confirmation':
            return {
                "approved": False,
                "message": "3DS Required 🔐",
                "status": "APPROVED",
                "transaction_id": pi_id,
                "time": elapsed
            }
        elif status == 'requires_payment_method':
            return {
                "approved": False,
                "message": "Card declined - invalid",
                "status": "DECLINED",
                "time": elapsed
            }
        else:
            return {
                "approved": False,
                "message": f"Status: {status}",
                "status": "DECLINED",
                "time": elapsed
            }
            
    except Exception as e:
        elapsed = f"{time.time() - start:.2f}s"
        return {
            "approved": False,
            "message": str(e)[:100],
            "status": "ERROR",
            "time": elapsed
        }

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENDPOINTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.route('/')
def home():
    return jsonify({
        'api': 'Stripe 1$ Charge API',
        'version': '2.0',
        'endpoint': '/charge',
        'usage': '/charge?cc=CC|MM|YY|CVV',
        'example': '/charge?cc=5154620010102323|10|30|786',
        'port': 8085,
        'dev': DEV
    })

@app.route('/charge')
def charge():
    cc_param = request.args.get('cc', '')
    
    if not cc_param:
        return jsonify({
            'approved': False,
            'message': 'Missing cc parameter. Use: ?cc=CC|MM|YY|CVV',
            'status': 'ERROR'
        }), 400
    
    parts = cc_param.replace('/', '|').split('|')
    if len(parts) < 4:
        return jsonify({
            'approved': False,
            'message': 'Invalid format. Use: CC|MM|YY|CVV',
            'status': 'ERROR'
        }), 400
    
    cc = parts[0].strip()
    mm = parts[1].strip()
    yy = parts[2].strip()
    cvv = parts[3].strip()
    
    result = stripe_charge(cc, mm, yy, cvv)
    
    return jsonify({
        'approved': result['approved'],
        'message': result['message'],
        'status': result['status'],
        'card': f"{cc}|{mm}|{yy}|{cvv}",
        'amount': result.get('amount', '$1.00'),
        'transaction_id': result.get('transaction_id'),
        'time': result.get('time', '0s'),
        'dev': DEV
    })

if __name__ == '__main__':
    print("💳 Stripe 1$ Charge API")
    print(f"📍 Port: 8085")
    print("📌 /charge?cc=4111111111111111|12|25|123")
    print("👤 Dev: @chirgg_911")
    app.run(host='0.0.0.0', port=8085, debug=False)

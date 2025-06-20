from flask import Flask, request, jsonify, Response
from dotenv import load_dotenv
import os
import requests
import jwt
import time
from functools import wraps
from collections import defaultdict

# Load environment variables from backend/.env
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'supersecretkey')

# Microservice URLs (assume running on localhost with different ports)
AUTH_SERVICE_URL = os.getenv('AUTH_SERVICE_URL', 'http://localhost:5000')
INVENTORY_SERVICE_URL = os.getenv('INVENTORY_SERVICE_URL', 'http://localhost:5001')
ORDER_SERVICE_URL = os.getenv('ORDER_SERVICE_URL', 'http://localhost:5002')
NOTIFICATION_SERVICE_URL = os.getenv('NOTIFICATION_SERVICE_URL', 'http://localhost:5003')
REPORTING_SERVICE_URL = os.getenv('REPORTING_SERVICE_URL', 'http://localhost:5004')

# Rate limiting (simple in-memory, for production use Redis)
RATE_LIMIT = 100  # requests per minute
rate_limit_data = defaultdict(list)

# Circuit breaker (simple, per service)
circuit_breaker = defaultdict(lambda: {'failures': 0, 'open_until': 0})
CB_THRESHOLD = 5
CB_TIMEOUT = 30  # seconds

def rate_limited(ip):
    now = time.time()
    window = 60
    rate_limit_data[ip] = [t for t in rate_limit_data[ip] if now - t < window]
    if len(rate_limit_data[ip]) >= RATE_LIMIT:
        return True
    rate_limit_data[ip].append(now)
    return False

def check_circuit(service_url):
    cb = circuit_breaker[service_url]
    if cb['open_until'] > time.time():
        return True
    return False

def record_failure(service_url):
    cb = circuit_breaker[service_url]
    cb['failures'] += 1
    if cb['failures'] >= CB_THRESHOLD:
        cb['open_until'] = time.time() + CB_TIMEOUT
        cb['failures'] = 0

def reset_circuit(service_url):
    circuit_breaker[service_url] = {'failures': 0, 'open_until': 0}

def jwt_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.path in ['/auth/login', '/auth/register', '/auth/']:
            return f(*args, **kwargs)
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Authorization header missing'}), 401
        token = auth_header.split(' ')[1]
        try:
            jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid token'}), 401
        return f(*args, **kwargs)
    return decorated

@app.before_request
def enforce_https():
    if not request.is_secure and os.getenv('FLASK_ENV') == 'production':
        return jsonify({'error': 'HTTPS required'}), 403

@app.before_request
def apply_rate_limit():
    ip = request.remote_addr
    if rate_limited(ip):
        return jsonify({'error': 'Rate limit exceeded'}), 429

@app.route('/auth/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
@jwt_required
def proxy_auth(path):
    service_url = f"{AUTH_SERVICE_URL}/{path}"
    if check_circuit(AUTH_SERVICE_URL):
        return jsonify({'error': 'Auth service temporarily unavailable (circuit open)'}), 503
    try:
        resp = requests.request(
            method=request.method,
            url=service_url,
            headers={key: value for key, value in request.headers if key != 'Host'},
            json=request.get_json(silent=True),
            params=request.args,
        )
        reset_circuit(AUTH_SERVICE_URL)
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type'))
    except Exception as e:
        record_failure(AUTH_SERVICE_URL)
        return jsonify({'error': 'Auth service unreachable', 'details': str(e)}), 502

# Proxy for Inventory Service
@app.route('/inventory/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
@jwt_required
def proxy_inventory(path):
    service_url = f"{INVENTORY_SERVICE_URL}/{path}"
    if check_circuit(INVENTORY_SERVICE_URL):
        return jsonify({'error': 'Inventory service temporarily unavailable (circuit open)'}), 503
    try:
        resp = requests.request(
            method=request.method,
            url=service_url,
            headers={key: value for key, value in request.headers if key != 'Host'},
            json=request.get_json(silent=True),
            params=request.args,
        )
        reset_circuit(INVENTORY_SERVICE_URL)
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type'))
    except Exception as e:
        record_failure(INVENTORY_SERVICE_URL)
        return jsonify({'error': 'Inventory service unreachable', 'details': str(e)}), 502

# Proxy for Order Service
@app.route('/orders/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
@jwt_required
def proxy_orders(path):
    service_url = f"{ORDER_SERVICE_URL}/{path}"
    if check_circuit(ORDER_SERVICE_URL):
        return jsonify({'error': 'Order service temporarily unavailable (circuit open)'}), 503
    try:
        resp = requests.request(
            method=request.method,
            url=service_url,
            headers={key: value for key, value in request.headers if key != 'Host'},
            json=request.get_json(silent=True),
            params=request.args,
        )
        reset_circuit(ORDER_SERVICE_URL)
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type'))
    except Exception as e:
        record_failure(ORDER_SERVICE_URL)
        return jsonify({'error': 'Order service unreachable', 'details': str(e)}), 502

# Proxy for Notification Service
@app.route('/notifications/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
@jwt_required
def proxy_notifications(path):
    service_url = f"{NOTIFICATION_SERVICE_URL}/{path}"
    if check_circuit(NOTIFICATION_SERVICE_URL):
        return jsonify({'error': 'Notification service temporarily unavailable (circuit open)'}), 503
    try:
        resp = requests.request(
            method=request.method,
            url=service_url,
            headers={key: value for key, value in request.headers if key != 'Host'},
            json=request.get_json(silent=True),
            params=request.args,
        )
        reset_circuit(NOTIFICATION_SERVICE_URL)
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type'))
    except Exception as e:
        record_failure(NOTIFICATION_SERVICE_URL)
        return jsonify({'error': 'Notification service unreachable', 'details': str(e)}), 502

# Proxy for Reporting Service
@app.route('/reports/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
@jwt_required
def proxy_reports(path):
    service_url = f"{REPORTING_SERVICE_URL}/{path}"
    if check_circuit(REPORTING_SERVICE_URL):
        return jsonify({'error': 'Reporting service temporarily unavailable (circuit open)'}), 503
    try:
        resp = requests.request(
            method=request.method,
            url=service_url,
            headers={key: value for key, value in request.headers if key != 'Host'},
            json=request.get_json(silent=True),
            params=request.args,
        )
        reset_circuit(REPORTING_SERVICE_URL)
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type'))
    except Exception as e:
        record_failure(REPORTING_SERVICE_URL)
        return jsonify({'error': 'Reporting service unreachable', 'details': str(e)}), 502

@app.route('/auth/', methods=['GET'])
def auth_root():
    # Health check or info endpoint
    return jsonify({'message': 'API Gateway Auth Proxy Running'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)

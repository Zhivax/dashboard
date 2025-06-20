from flask import Flask, request, jsonify
from dotenv import load_dotenv
import os
import pymysql
import jwt
import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import re
import threading
from flask_cors import CORS

# Load environment variables from backend/.env
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

app = Flask(__name__)
CORS(app, supports_credentials=True)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'supersecretkey')

# MySQL setup
MYSQL_HOST = os.environ.get('MYSQL_HOST')
MYSQL_PORT = int(os.environ.get('MYSQL_PORT', 3306))
MYSQL_USER = os.environ.get('MYSQL_USER')
MYSQL_PASSWORD = os.environ.get('MYSQL_PASSWORD')
MYSQL_DB = os.environ.get('MYSQL_DB')

def get_db_connection():
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        cursorclass=pymysql.cursors.DictCursor
    )

# In-memory blacklist (for production, use Redis or DB)
blacklisted_tokens = set()
lock = threading.Lock()

def is_token_blacklisted(token):
    with lock:
        return token in blacklisted_tokens

def blacklist_token(token):
    with lock:
        blacklisted_tokens.add(token)

# Input validation helpers
def is_valid_email(email):
    return re.match(r"[^@]+@[^@]+\.[^@]+", email)

def is_strong_password(password):
    return len(password) >= 8

@app.route('/')
def index():
    return {'message': 'Auth Service Running'}

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')
    if not username or not email or not password:
        return jsonify({'error': 'Username, email, and password required', 'expected': 'username, email, password'}), 400
    if not is_valid_email(email):
        return jsonify({'error': 'Invalid email format', 'expected': 'Valid email format'}), 400
    if not is_strong_password(password):
        return jsonify({'error': 'Password must be at least 8 characters', 'expected': 'Password >= 8 chars'}), 400
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT * FROM users WHERE email=%s', (email,))
            if cursor.fetchone():
                return jsonify({'error': 'User already exists', 'expected': 'Unique email'}), 409
            hashed_pw = generate_password_hash(password)
            cursor.execute('INSERT INTO users (username, email, password) VALUES (%s, %s, %s)', (username, email, hashed_pw))
            conn.commit()
        return jsonify({'message': 'User registered successfully', 'expected': 'User created'}), 201
    except Exception as e:
        return jsonify({'error': str(e), 'expected': 'Database connection and table exist'}), 500
    finally:
        conn.close()

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    if not email or not password:
        return jsonify({'error': 'Email and password required', 'expected': 'email, password'}), 400
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT * FROM users WHERE email=%s', (email,))
            user = cursor.fetchone()
            if not user:
                return jsonify({'error': 'User not found', 'expected': 'Registered email'}), 404
            if not check_password_hash(user['password'], password):
                return jsonify({'error': 'Invalid credentials', 'expected': 'Correct password'}), 401
            token = jwt.encode({
                'email': email,
                'username': user['username'],
                'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)
            }, app.config['SECRET_KEY'], algorithm='HS256')
            return jsonify({'token': token, 'expected': 'JWT token'}), 200
    except Exception as e:
        return jsonify({'error': str(e), 'expected': 'Database connection and table exist'}), 500
    finally:
        conn.close()

@app.route('/logout', methods=['POST'])
def logout():
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({'error': 'Authorization header missing', 'expected': 'Bearer <token>'}), 401
    token = auth_header.split(' ')[1]
    try:
        jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'error': 'Token already expired', 'expected': 'Valid token'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'error': 'Invalid token', 'expected': 'Valid token'}), 401
    if is_token_blacklisted(token):
        return jsonify({'error': 'Token already blacklisted', 'expected': 'Token not blacklisted'}), 401
    blacklist_token(token)
    return jsonify({'message': 'Successfully logged out', 'expected': 'Token blacklisted'}), 200

@app.route('/protected', methods=['GET'])
def protected():
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({'error': 'Authorization header missing', 'expected': 'Bearer <token>'}), 401
    token = auth_header.split(' ')[1]
    if is_token_blacklisted(token):
        return jsonify({'error': 'Token has been blacklisted', 'expected': 'Token not blacklisted'}), 401
    try:
        payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'error': 'Token expired', 'expected': 'Valid token'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'error': 'Invalid token', 'expected': 'Valid token'}), 401
    return jsonify({'message': 'Access granted', 'user': payload, 'expected': 'Valid JWT and not blacklisted'}), 200

@app.route('/get_email_by_username', methods=['POST'])
def get_email_by_username():
    data = request.json
    username = data.get('username')
    if not username:
        return jsonify({'error': 'Username required'}), 400
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT email FROM users WHERE username=%s', (username,))
            user = cursor.fetchone()
            if not user:
                return jsonify({'error': 'User not found'}), 404
            return jsonify({'email': user['email']}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

# HTTPS enforcement (for production, behind a proxy/load balancer)
@app.before_request
def before_request():
    if not request.is_secure and os.getenv('FLASK_ENV') == 'production':
        return jsonify({'error': 'HTTPS required'}), 403

# Input sanitization (example for all POST/PUT requests)
@app.before_request
def sanitize_input():
    if request.method in ['POST', 'PUT'] and request.is_json:
        data = request.get_json()
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, str):
                    data[k] = v.strip()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

from flask import Flask, request, jsonify
from dotenv import load_dotenv
import os
import pymysql
import jwt
from functools import wraps
from flask_cors import CORS
import datetime

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

app = Flask(__name__)
CORS(app, supports_credentials=True)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'supersecretkey')

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

def jwt_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Authorization header missing'}), 401
        
        token = auth_header.split(' ')[1]
        try:
            payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            request.current_user = payload
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid token'}), 401
        return f(*args, **kwargs)
    return decorated

@app.route('/')
def index():
    return {'message': 'Reporting Service Running'}

@app.route('/reports/dashboard', methods=['GET'])
@jwt_required
def get_dashboard_report():
    try:
        user_email = request.current_user['email']
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                # Inventory stats
                cursor.execute('''
                    SELECT 
                        COUNT(*) as total_items,
                        SUM(CASE WHEN type = 'raw_material' THEN 1 ELSE 0 END) as raw_materials,
                        SUM(CASE WHEN type = 'finished_product' THEN 1 ELSE 0 END) as finished_products,
                        SUM(quantity * price) as total_value,
                        SUM(CASE WHEN quantity <= 10 THEN 1 ELSE 0 END) as low_stock_items
                    FROM inventory_items WHERE user_email = %s
                ''', (user_email,))
                inventory_stats = cursor.fetchone()
                
                # Order stats
                cursor.execute('''
                    SELECT 
                        COUNT(*) as total_orders,
                        SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending_orders,
                        SUM(CASE WHEN status = 'processing' THEN 1 ELSE 0 END) as processing_orders,
                        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_orders,
                        SUM(CASE WHEN status = 'completed' THEN total_amount ELSE 0 END) as total_revenue
                    FROM orders WHERE user_email = %s
                ''', (user_email,))
                order_stats = cursor.fetchone()
                
                dashboard_data = {
                    'inventory': inventory_stats,
                    'orders': order_stats,
                    'generated_at': datetime.datetime.utcnow().isoformat()
                }
                
                return jsonify({'dashboard': dashboard_data}), 200
        finally:
            conn.close()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/reports/inventory', methods=['GET'])
@jwt_required
def get_inventory_report():
    try:
        user_email = request.current_user['email']
        low_stock_threshold = int(request.args.get('threshold', 10))
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute('SELECT * FROM inventory_items WHERE user_email = %s', (user_email,))
                items = cursor.fetchall()
                
                cursor.execute('SELECT * FROM inventory_items WHERE user_email = %s AND quantity <= %s', (user_email, low_stock_threshold))
                low_stock_items = cursor.fetchall()
                
                report = {
                    'total_items': len(items),
                    'low_stock_items': low_stock_items,
                    'threshold': low_stock_threshold,
                    'generated_at': datetime.datetime.utcnow().isoformat()
                }
                
                return jsonify({'report': report}), 200
        finally:
            conn.close()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/reports/sales', methods=['GET'])
@jwt_required
def get_sales_report():
    try:
        user_email = request.current_user['email']
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute('SELECT * FROM orders WHERE user_email = %s', (user_email,))
                orders = cursor.fetchall()
                
                completed_orders = [o for o in orders if o['status'] == 'completed']
                total_revenue = sum(float(o['total_amount']) for o in completed_orders)
                
                report = {
                    'total_orders': len(orders),
                    'completed_orders': len(completed_orders),
                    'total_revenue': total_revenue,
                    'generated_at': datetime.datetime.utcnow().isoformat()
                }
                
                return jsonify({'report': report}), 200
        finally:
            conn.close()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5004)
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import os
import pymysql
import jwt
import uuid
import requests
from functools import wraps
from flask_cors import CORS
import logging

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

app = Flask(__name__)
CORS(app, supports_credentials=True)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'supersecretkey')

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MYSQL_HOST = os.environ.get('MYSQL_HOST')
MYSQL_PORT = int(os.environ.get('MYSQL_PORT', 3306))
MYSQL_USER = os.environ.get('MYSQL_USER')
MYSQL_PASSWORD = os.environ.get('MYSQL_PASSWORD')
MYSQL_DB = os.environ.get('MYSQL_DB')

# Inventory service URL for stock validation
INVENTORY_SERVICE_URL = os.getenv('INVENTORY_SERVICE_URL', 'http://localhost:5001')

def get_db_connection():
    """Get database connection with error handling"""
    try:
        return pymysql.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            database=MYSQL_DB,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False
        )
    except Exception as e:
        logger.error(f"Database connection failed: {str(e)}")
        raise

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

def validate_and_reserve_inventory(items, auth_token):
    """Validate inventory availability and reserve items for order - Synchronous version"""
    try:
        headers = {'Authorization': f'Bearer {auth_token}', 'Content-Type': 'application/json'}
        updated_items = []  # Track items that were updated for rollback
        
        for item in items:
            if 'item_id' in item and item['item_id']:
                try:
                    # Get current inventory item
                    response = requests.get(
                        f"{INVENTORY_SERVICE_URL}/items/{item['item_id']}", 
                        headers=headers,
                        timeout=10
                    )
                    
                    if response.status_code != 200:
                        # Rollback any previous updates
                        rollback_inventory_updates(updated_items, auth_token)
                        return False, f"Inventory item {item.get('item_name', 'Unknown')} not found"
                    
                    inventory_item = response.json()['item']
                    
                    # Check stock availability
                    if inventory_item['quantity'] < item['quantity']:
                        # Rollback any previous updates
                        rollback_inventory_updates(updated_items, auth_token)
                        return False, f"Insufficient stock for {item.get('item_name', 'Unknown')}. Available: {inventory_item['quantity']}, Requested: {item['quantity']}"
                    
                    # Update inventory (reduce stock)
                    new_quantity = inventory_item['quantity'] - item['quantity']
                    update_data = {'quantity': new_quantity}
                    
                    update_response = requests.put(
                        f"{INVENTORY_SERVICE_URL}/items/{item['item_id']}", 
                        headers=headers,
                        json=update_data,
                        timeout=10
                    )
                    
                    if update_response.status_code != 200:
                        # Rollback any previous updates
                        rollback_inventory_updates(updated_items, auth_token)
                        return False, f"Failed to update inventory for {item.get('item_name', 'Unknown')}"
                    
                    # Track this update for potential rollback
                    updated_items.append({
                        'item_id': item['item_id'],
                        'original_quantity': inventory_item['quantity'],
                        'quantity_reserved': item['quantity']
                    })
                    
                except requests.RequestException as e:
                    logger.error(f"Network error during inventory validation: {str(e)}")
                    # Rollback any previous updates
                    rollback_inventory_updates(updated_items, auth_token)
                    return False, f"Network error while validating inventory: {str(e)}"
        
        return True, "Inventory validated and updated successfully"
        
    except Exception as e:
        logger.error(f"Inventory validation error: {str(e)}")
        return False, f"Inventory validation error: {str(e)}"

def rollback_inventory_updates(updated_items, auth_token):
    """Rollback inventory updates if order creation fails"""
    headers = {'Authorization': f'Bearer {auth_token}', 'Content-Type': 'application/json'}
    
    for item in updated_items:
        try:
            # Restore original quantity
            update_data = {'quantity': item['original_quantity']}
            requests.put(
                f"{INVENTORY_SERVICE_URL}/items/{item['item_id']}", 
                headers=headers,
                json=update_data,
                timeout=10
            )
        except Exception as e:
            logger.error(f"Failed to rollback inventory for item {item['item_id']}: {str(e)}")

def restore_inventory_on_cancellation(order_items, auth_token):
    """Restore inventory when order is cancelled"""
    headers = {'Authorization': f'Bearer {auth_token}', 'Content-Type': 'application/json'}
    
    for item in order_items:
        if item.get('item_id'):  # Only restore if we have inventory item ID
            try:
                # Get current inventory
                response = requests.get(
                    f"{INVENTORY_SERVICE_URL}/items/{item['item_id']}", 
                    headers=headers,
                    timeout=10
                )
                
                if response.status_code == 200:
                    inventory_item = response.json()['item']
                    new_quantity = inventory_item['quantity'] + item['quantity']
                    
                    # Update inventory
                    update_response = requests.put(
                        f"{INVENTORY_SERVICE_URL}/items/{item['item_id']}", 
                        headers=headers,
                        json={'quantity': new_quantity},
                        timeout=10
                    )
                    
                    if update_response.status_code != 200:
                        logger.error(f"Failed to restore inventory for item {item['item_name']}")
                        
            except Exception as e:
                logger.error(f"Warning: Failed to restore inventory for item {item['item_name']}: {str(e)}")

@app.route('/')
def index():
    return {'message': 'Order Service Running', 'status': 'healthy'}

@app.route('/health')
def health_check():
    """Health check endpoint for load balancer"""
    try:
        # Test database connection
        conn = get_db_connection()
        conn.close()
        return {'status': 'healthy', 'service': 'order_service'}, 200
    except Exception as e:
        return {'status': 'unhealthy', 'error': str(e)}, 503

@app.route('/orders', methods=['GET'])
@jwt_required
def get_orders():
    try:
        user_email = request.current_user['email']
        status_filter = request.args.get('status')
        limit = min(int(request.args.get('limit', 50)), 100)  # Max 100 orders
        offset = int(request.args.get('offset', 0))
        
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                if status_filter:
                    cursor.execute('''
                        SELECT * FROM orders 
                        WHERE user_email = %s AND status = %s 
                        ORDER BY created_at DESC 
                        LIMIT %s OFFSET %s
                    ''', (user_email, status_filter, limit, offset))
                else:
                    cursor.execute('''
                        SELECT * FROM orders 
                        WHERE user_email = %s 
                        ORDER BY created_at DESC 
                        LIMIT %s OFFSET %s
                    ''', (user_email, limit, offset))
                
                orders = cursor.fetchall()
                
                # Get total count for pagination
                if status_filter:
                    cursor.execute('SELECT COUNT(*) as total FROM orders WHERE user_email = %s AND status = %s', (user_email, status_filter))
                else:
                    cursor.execute('SELECT COUNT(*) as total FROM orders WHERE user_email = %s', (user_email,))
                
                total_count = cursor.fetchone()['total']
                
                return jsonify({
                    'orders': orders,
                    'total': total_count,
                    'limit': limit,
                    'offset': offset
                }), 200
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Error fetching orders: {str(e)}")
        return jsonify({'error': 'Failed to fetch orders'}), 500

@app.route('/orders', methods=['POST'])
@jwt_required
def create_order():
    conn = None
    try:
        data = request.json
        user_email = request.current_user['email']
        order_id = str(uuid.uuid4())
        
        # Validate required fields
        required_fields = ['customer_name', 'items', 'total_amount']
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({'error': f'{field} is required'}), 400
        
        # Validate items
        if not isinstance(data['items'], list) or len(data['items']) == 0:
            return jsonify({'error': 'At least one item is required'}), 400
        
        for item in data['items']:
            required_item_fields = ['item_name', 'quantity', 'unit_price']
            for field in required_item_fields:
                if field not in item:
                    return jsonify({'error': f'Item {field} is required'}), 400
            
            if item['quantity'] <= 0 or item['unit_price'] < 0:
                return jsonify({'error': 'Invalid item quantity or price'}), 400
        
        # Validate inventory and reserve stock
        auth_token = request.headers.get('Authorization').split(' ')[1]
        inventory_valid, inventory_message = validate_and_reserve_inventory(data['items'], auth_token)
        
        if not inventory_valid:
            return jsonify({'error': inventory_message}), 400
        
        # Start database transaction
        conn = get_db_connection()
        conn.begin()
        
        try:
            with conn.cursor() as cursor:
                # Create order
                cursor.execute('''
                    INSERT INTO orders (order_id, user_email, customer_name, customer_email, customer_phone, total_amount, status, notes, delivery_date, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ''', (
                    order_id, user_email, data['customer_name'], 
                    data.get('customer_email', ''), data.get('customer_phone', ''),
                    float(data['total_amount']), 'pending', 
                    data.get('notes', ''), data.get('delivery_date')
                ))
                
                # Add order items
                for item in data['items']:
                    total_price = float(item['quantity']) * float(item['unit_price'])
                    cursor.execute('''
                        INSERT INTO order_items (order_id, item_id, item_name, quantity, unit_price, total_price)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    ''', (
                        order_id, 
                        item.get('item_id', ''), 
                        item['item_name'],
                        int(item['quantity']), 
                        float(item['unit_price']),
                        total_price
                    ))
                
                conn.commit()
                
                # Get the created order
                cursor.execute('SELECT * FROM orders WHERE order_id = %s', (order_id,))
                order = cursor.fetchone()
                
                logger.info(f"Order {order_id} created successfully for user {user_email}")
                return jsonify({'message': 'Order created successfully', 'order': order}), 201
                
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error creating order: {str(e)}")
            # TODO: Rollback inventory changes here
            raise
            
    except Exception as e:
        logger.error(f"Error creating order: {str(e)}")
        return jsonify({'error': 'Failed to create order'}), 500
    finally:
        if conn:
            conn.close()

@app.route('/orders/<order_id>', methods=['GET'])
@jwt_required
def get_order(order_id):
    try:
        user_email = request.current_user['email']
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute('SELECT * FROM orders WHERE order_id = %s AND user_email = %s', (order_id, user_email))
                order = cursor.fetchone()
                if not order:
                    return jsonify({'error': 'Order not found'}), 404
                
                cursor.execute('SELECT * FROM order_items WHERE order_id = %s', (order_id,))
                items = cursor.fetchall()
                order['items'] = items
                return jsonify({'order': order}), 200
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Error fetching order {order_id}: {str(e)}")
        return jsonify({'error': 'Failed to fetch order'}), 500

@app.route('/orders/<order_id>/status', methods=['PUT'])
@jwt_required
def update_order_status(order_id):
    conn = None
    try:
        data = request.json
        user_email = request.current_user['email']
        
        if 'status' not in data:
            return jsonify({'error': 'Status is required'}), 400
        
        valid_statuses = ['pending', 'processing', 'completed', 'cancelled']
        if data['status'] not in valid_statuses:
            return jsonify({'error': 'Invalid status'}), 400
        
        conn = get_db_connection()
        conn.begin()
        
        try:
            with conn.cursor() as cursor:
                # Get current order
                cursor.execute('SELECT * FROM orders WHERE order_id = %s AND user_email = %s', (order_id, user_email))
                order = cursor.fetchone()
                if not order:
                    return jsonify({'error': 'Order not found'}), 404
                
                # Prevent status changes for completed or cancelled orders
                if order['status'] in ['completed', 'cancelled'] and data['status'] != order['status']:
                    return jsonify({'error': f'Cannot change status from {order["status"]}'}), 400
                
                # If order is being cancelled, restore inventory
                if data['status'] == 'cancelled' and order['status'] != 'cancelled':
                    cursor.execute('SELECT * FROM order_items WHERE order_id = %s', (order_id,))
                    order_items = cursor.fetchall()
                    
                    # Restore inventory for each item
                    auth_token = request.headers.get('Authorization').split(' ')[1]
                    restore_inventory_on_cancellation(order_items, auth_token)
                
                # Update order status
                cursor.execute('''
                    UPDATE orders 
                    SET status = %s, updated_at = NOW() 
                    WHERE order_id = %s AND user_email = %s
                ''', (data['status'], order_id, user_email))
                
                conn.commit()
                logger.info(f"Order {order_id} status updated to {data['status']} by user {user_email}")
                return jsonify({'message': 'Order status updated successfully'}), 200
                
        except Exception as e:
            conn.rollback()
            raise
            
    except Exception as e:
        logger.error(f"Error updating order status: {str(e)}")
        return jsonify({'error': 'Failed to update order status'}), 500
    finally:
        if conn:
            conn.close()

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5002, debug=os.getenv('FLASK_ENV') == 'development')
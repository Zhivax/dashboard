from flask import Flask, request, jsonify
from dotenv import load_dotenv
import os
import pymysql
import jwt
import uuid
import requests
import logging
from functools import wraps
from flask_cors import CORS

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

# Notification service URL
NOTIFICATION_SERVICE_URL = os.getenv('NOTIFICATION_SERVICE_URL', 'http://localhost:5003')

# Default low stock threshold
DEFAULT_LOW_STOCK_THRESHOLD = int(os.getenv('LOW_STOCK_THRESHOLD', 10))

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

def check_and_notify_low_stock(user_email, item_id, item_name, current_quantity, unit, threshold=None):
    """Check if item is low stock and send notification"""
    if threshold is None:
        threshold = DEFAULT_LOW_STOCK_THRESHOLD
    
    if current_quantity <= threshold:
        try:
            # Send notification to notification service
            notification_data = {
                'user_email': user_email,
                'item_id': item_id,
                'item_name': item_name,
                'current_stock': current_quantity,
                'unit': unit,
                'threshold': threshold
            }
            
            response = requests.post(
                f"{NOTIFICATION_SERVICE_URL}/notifications/low-stock",
                json=notification_data,
                timeout=10
            )
            
            if response.status_code in [200, 201]:
                logger.info(f"Low stock notification sent for {item_name} (user: {user_email})")
            else:
                logger.warning(f"Failed to send low stock notification for {item_name}: {response.text}")
                
        except Exception as e:
            logger.error(f"Error sending low stock notification for {item_name}: {str(e)}")

@app.route('/')
def index():
    return {'message': 'Inventory Service Running', 'status': 'healthy'}

@app.route('/health')
def health_check():
    """Health check endpoint"""
    try:
        conn = get_db_connection()
        conn.close()
        return {'status': 'healthy', 'service': 'inventory_service'}, 200
    except Exception as e:
        return {'status': 'unhealthy', 'error': str(e)}, 503

@app.route('/items', methods=['GET'])
@jwt_required
def get_items():
    try:
        user_email = request.current_user['email']
        limit = min(int(request.args.get('limit', 100)), 200)
        offset = int(request.args.get('offset', 0))
        item_type = request.args.get('type')
        
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                if item_type:
                    cursor.execute('''
                        SELECT * FROM inventory_items 
                        WHERE user_email = %s AND type = %s 
                        ORDER BY created_at DESC 
                        LIMIT %s OFFSET %s
                    ''', (user_email, item_type, limit, offset))
                else:
                    cursor.execute('''
                        SELECT * FROM inventory_items 
                        WHERE user_email = %s 
                        ORDER BY created_at DESC 
                        LIMIT %s OFFSET %s
                    ''', (user_email, limit, offset))
                
                items = cursor.fetchall()
                
                # Get total count
                if item_type:
                    cursor.execute('SELECT COUNT(*) as total FROM inventory_items WHERE user_email = %s AND type = %s', (user_email, item_type))
                else:
                    cursor.execute('SELECT COUNT(*) as total FROM inventory_items WHERE user_email = %s', (user_email,))
                
                total_count = cursor.fetchone()['total']
                
                return jsonify({
                    'items': items,
                    'total': total_count,
                    'limit': limit,
                    'offset': offset
                }), 200
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Error fetching items: {str(e)}")
        return jsonify({'error': 'Failed to fetch items'}), 500

@app.route('/items', methods=['POST'])
@jwt_required
def create_item():
    conn = None
    try:
        data = request.json
        user_email = request.current_user['email']
        item_id = str(uuid.uuid4())
        
        required_fields = ['name', 'type', 'quantity', 'unit']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'{field} is required'}), 400
        
        # Validate data types
        try:
            quantity = int(data['quantity'])
            price = float(data.get('price', 0))
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid quantity or price format'}), 400
        
        if quantity < 0 or price < 0:
            return jsonify({'error': 'Quantity and price must be non-negative'}), 400
        
        conn = get_db_connection()
        conn.begin()
        
        try:
            with conn.cursor() as cursor:
                cursor.execute('''
                    INSERT INTO inventory_items (item_id, user_email, name, type, quantity, unit, description, price, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ''', (
                    item_id, user_email, data['name'], data['type'], 
                    quantity, data['unit'], 
                    data.get('description', ''), price
                ))
                conn.commit()
                
                # Check for low stock after creation
                check_and_notify_low_stock(
                    user_email=user_email,
                    item_id=item_id,
                    item_name=data['name'],
                    current_quantity=quantity,
                    unit=data['unit']
                )
                
                cursor.execute('SELECT * FROM inventory_items WHERE item_id = %s', (item_id,))
                item = cursor.fetchone()
                
                logger.info(f"Item {data['name']} created successfully for user {user_email}")
                return jsonify({'message': 'Item created successfully', 'item': item}), 201
        except Exception as e:
            conn.rollback()
            raise
    except Exception as e:
        logger.error(f"Error creating item: {str(e)}")
        return jsonify({'error': 'Failed to create item'}), 500
    finally:
        if conn:
            conn.close()

@app.route('/items/<item_id>', methods=['GET'])
@jwt_required
def get_item(item_id):
    try:
        user_email = request.current_user['email']
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute('SELECT * FROM inventory_items WHERE item_id = %s AND user_email = %s', (item_id, user_email))
                item = cursor.fetchone()
                if not item:
                    return jsonify({'error': 'Item not found'}), 404
                return jsonify({'item': item}), 200
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Error fetching item {item_id}: {str(e)}")
        return jsonify({'error': 'Failed to fetch item'}), 500

@app.route('/items/<item_id>', methods=['PUT'])
@jwt_required
def update_item(item_id):
    conn = None
    try:
        data = request.json
        user_email = request.current_user['email']
        
        conn = get_db_connection()
        conn.begin()
        
        try:
            with conn.cursor() as cursor:
                # Get current item data
                cursor.execute('SELECT * FROM inventory_items WHERE item_id = %s AND user_email = %s', (item_id, user_email))
                current_item = cursor.fetchone()
                if not current_item:
                    return jsonify({'error': 'Item not found'}), 404
                
                update_fields = []
                update_values = []
                
                for field in ['name', 'type', 'quantity', 'unit', 'description', 'price']:
                    if field in data:
                        update_fields.append(f"{field} = %s")
                        if field == 'quantity':
                            update_values.append(int(data[field]))
                        elif field == 'price':
                            update_values.append(float(data[field]))
                        else:
                            update_values.append(data[field])
                
                if not update_fields:
                    return jsonify({'error': 'No valid fields to update'}), 400
                
                update_values.extend([item_id, user_email])
                cursor.execute(f'''
                    UPDATE inventory_items SET {', '.join(update_fields)}, updated_at = NOW()
                    WHERE item_id = %s AND user_email = %s
                ''', update_values)
                
                conn.commit()
                
                # Check for low stock after update (if quantity was updated)
                if 'quantity' in data:
                    new_quantity = int(data['quantity'])
                    check_and_notify_low_stock(
                        user_email=user_email,
                        item_id=item_id,
                        item_name=data.get('name', current_item['name']),
                        current_quantity=new_quantity,
                        unit=data.get('unit', current_item['unit'])
                    )
                
                logger.info(f"Item {item_id} updated successfully for user {user_email}")
                return jsonify({'message': 'Item updated successfully'}), 200
        except Exception as e:
            conn.rollback()
            raise
    except Exception as e:
        logger.error(f"Error updating item {item_id}: {str(e)}")
        return jsonify({'error': 'Failed to update item'}), 500
    finally:
        if conn:
            conn.close()

@app.route('/items/<item_id>', methods=['DELETE'])
@jwt_required
def delete_item(item_id):
    try:
        user_email = request.current_user['email']
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute('DELETE FROM inventory_items WHERE item_id = %s AND user_email = %s', (item_id, user_email))
                if cursor.rowcount == 0:
                    return jsonify({'error': 'Item not found'}), 404
                conn.commit()
                
                logger.info(f"Item {item_id} deleted successfully for user {user_email}")
                return jsonify({'message': 'Item deleted successfully'}), 200
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Error deleting item {item_id}: {str(e)}")
        return jsonify({'error': 'Failed to delete item'}), 500

@app.route('/items/low-stock', methods=['GET'])
@jwt_required
def get_low_stock_items():
    try:
        user_email = request.current_user['email']
        threshold = int(request.args.get('threshold', DEFAULT_LOW_STOCK_THRESHOLD))
        
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute('''
                    SELECT * FROM inventory_items 
                    WHERE user_email = %s AND quantity <= %s 
                    ORDER BY quantity ASC
                ''', (user_email, threshold))
                
                items = cursor.fetchall()
                return jsonify({
                    'items': items, 
                    'threshold': threshold,
                    'count': len(items)
                }), 200
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Error fetching low stock items: {str(e)}")
        return jsonify({'error': 'Failed to fetch low stock items'}), 500

@app.route('/items/check-low-stock', methods=['POST'])
@jwt_required
def manual_low_stock_check():
    """Manual endpoint to check all items for low stock and trigger notifications"""
    try:
        user_email = request.current_user['email']
        threshold = request.json.get('threshold', DEFAULT_LOW_STOCK_THRESHOLD) if request.is_json else DEFAULT_LOW_STOCK_THRESHOLD
        
        conn = get_db_connection()
        notifications_triggered = 0
        
        try:
            with conn.cursor() as cursor:
                cursor.execute('''
                    SELECT * FROM inventory_items 
                    WHERE user_email = %s AND quantity <= %s
                ''', (user_email, threshold))
                
                low_stock_items = cursor.fetchall()
                
                for item in low_stock_items:
                    check_and_notify_low_stock(
                        user_email=user_email,
                        item_id=item['item_id'],
                        item_name=item['name'],
                        current_quantity=item['quantity'],
                        unit=item['unit'],
                        threshold=threshold
                    )
                    notifications_triggered += 1
                
                return jsonify({
                    'message': 'Low stock check completed',
                    'low_stock_items': len(low_stock_items),
                    'notifications_triggered': notifications_triggered
                }), 200
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Error in manual low stock check: {str(e)}")
        return jsonify({'error': 'Failed to check low stock'}), 500

@app.route('/items/bulk-update', methods=['PUT'])
@jwt_required
def bulk_update_items():
    """Bulk update multiple items (useful for order processing)"""
    conn = None
    try:
        data = request.json
        user_email = request.current_user['email']
        
        if not isinstance(data, dict) or 'items' not in data:
            return jsonify({'error': 'Invalid request format. Expected {"items": [...]}'}), 400
        
        items_to_update = data['items']
        if not isinstance(items_to_update, list):
            return jsonify({'error': 'Items must be a list'}), 400
        
        conn = get_db_connection()
        conn.begin()
        
        updated_items = []
        
        try:
            with conn.cursor() as cursor:
                for item_update in items_to_update:
                    if 'item_id' not in item_update or 'quantity' not in item_update:
                        continue
                    
                    item_id = item_update['item_id']
                    new_quantity = int(item_update['quantity'])
                    
                    # Get current item
                    cursor.execute('SELECT * FROM inventory_items WHERE item_id = %s AND user_email = %s', (item_id, user_email))
                    current_item = cursor.fetchone()
                    
                    if current_item:
                        # Update quantity
                        cursor.execute('''
                            UPDATE inventory_items 
                            SET quantity = %s, updated_at = NOW() 
                            WHERE item_id = %s AND user_email = %s
                        ''', (new_quantity, item_id, user_email))
                        
                        # Check for low stock
                        check_and_notify_low_stock(
                            user_email=user_email,
                            item_id=item_id,
                            item_name=current_item['name'],
                            current_quantity=new_quantity,
                            unit=current_item['unit']
                        )
                        
                        updated_items.append({
                            'item_id': item_id,
                            'name': current_item['name'],
                            'old_quantity': current_item['quantity'],
                            'new_quantity': new_quantity
                        })
                
                conn.commit()
                
                logger.info(f"Bulk update completed for {len(updated_items)} items (user: {user_email})")
                return jsonify({
                    'message': 'Bulk update completed',
                    'updated_items': updated_items
                }), 200
        except Exception as e:
            conn.rollback()
            raise
    except Exception as e:
        logger.error(f"Error in bulk update: {str(e)}")
        return jsonify({'error': 'Failed to perform bulk update'}), 500
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
    app.run(host='0.0.0.0', port=5001, debug=os.getenv('FLASK_ENV') == 'development')
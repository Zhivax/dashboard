from flask import Flask, request, jsonify
from dotenv import load_dotenv
import os
import pymysql
import jwt
import smtplib
import logging
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from functools import wraps
from flask_cors import CORS
from datetime import datetime, timedelta

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

SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
EMAIL_USER = os.environ.get('EMAIL_USER')
EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD')

# Default low stock threshold
DEFAULT_LOW_STOCK_THRESHOLD = int(os.environ.get('LOW_STOCK_THRESHOLD', 10))

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

def send_email(to_email, subject, body):
    """Send email notification"""
    try:
        if not EMAIL_USER or not EMAIL_PASSWORD:
            logger.warning("Email credentials not configured")
            return False
            
        msg = MIMEMultipart()
        msg['From'] = EMAIL_USER
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))
        
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_USER, to_email, msg.as_string())
        server.quit()
        
        logger.info(f"Email sent successfully to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Email sending failed: {str(e)}")
        return False

def create_notification(user_email, title, message, notification_type='warning', send_email_flag=False):
    """Create a notification in the database"""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            email_sent = False
            if send_email_flag:
                email_sent = send_email(user_email, title, message)
            
            cursor.execute('''
                INSERT INTO notifications (user_email, title, message, type, email_sent, created_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
            ''', (user_email, title, message, notification_type, email_sent))
            conn.commit()
            
            notification_id = cursor.lastrowid
            cursor.execute('SELECT * FROM notifications WHERE id = %s', (notification_id,))
            notification = cursor.fetchone()
            
            logger.info(f"Notification created for {user_email}: {title}")
            return notification
    except Exception as e:
        logger.error(f"Error creating notification: {str(e)}")
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            conn.close()

def check_duplicate_notification(user_email, item_id, notification_type='low_stock'):
    """Check if a low stock notification already exists for this item in the last 24 hours"""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            # Check for existing low stock notification for this item in last 24 hours
            cursor.execute('''
                SELECT id FROM notifications 
                WHERE user_email = %s 
                AND message LIKE %s 
                AND type = 'warning'
                AND created_at > DATE_SUB(NOW(), INTERVAL 24 HOUR)
            ''', (user_email, f'%{item_id}%'))
            
            result = cursor.fetchone()
            return result is not None
    except Exception as e:
        logger.error(f"Error checking duplicate notification: {str(e)}")
        return False
    finally:
        if conn:
            conn.close()

def check_low_stock_for_user(user_email, threshold=None):
    """Check low stock items for a specific user and create notifications"""
    if threshold is None:
        threshold = DEFAULT_LOW_STOCK_THRESHOLD
    
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            # Get low stock items for the user
            cursor.execute('''
                SELECT item_id, name, quantity, unit, type 
                FROM inventory_items 
                WHERE user_email = %s AND quantity <= %s
            ''', (user_email, threshold))
            
            low_stock_items = cursor.fetchall()
            notifications_created = 0
            
            for item in low_stock_items:
                # Check if we already sent a notification for this item recently
                if not check_duplicate_notification(user_email, item['item_id']):
                    title = f"⚠️ Low Stock Alert: {item['name']}"
                    message = f"""
                    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                        <h2 style="color: #e53e3e;">Low Stock Alert</h2>
                        <p>Your inventory item is running low:</p>
                        <div style="background: #fff5f5; border: 1px solid #fed7d7; border-radius: 8px; padding: 16px; margin: 16px 0;">
                            <h3 style="color: #c53030; margin: 0 0 8px 0;">{item['name']}</h3>
                            <p style="margin: 4px 0;"><strong>Current Stock:</strong> {item['quantity']} {item['unit']}</p>
                            <p style="margin: 4px 0;"><strong>Type:</strong> {item['type'].replace('_', ' ').title()}</p>
                            <p style="margin: 4px 0;"><strong>Threshold:</strong> {threshold} {item['unit']}</p>
                        </div>
                        <p style="color: #c53030; font-weight: bold;">Action Required: Please restock this item to avoid stockouts.</p>
                        <p>Visit your <a href="http://localhost:8080/inventory/inventory.html" style="color: #3182ce;">inventory dashboard</a> to manage your stock.</p>
                    </div>
                    """
                    
                    # Create notification (also sends email if configured)
                    notification = create_notification(
                        user_email=user_email,
                        title=title,
                        message=message,
                        notification_type='warning',
                        send_email_flag=True
                    )
                    
                    if notification:
                        notifications_created += 1
            
            return {
                'low_stock_items': len(low_stock_items),
                'notifications_created': notifications_created
            }
    except Exception as e:
        logger.error(f"Error checking low stock for user {user_email}: {str(e)}")
        return None
    finally:
        if conn:
            conn.close()

@app.route('/')
def index():
    return {'message': 'Notification Service Running', 'status': 'healthy'}

@app.route('/health')
def health_check():
    """Health check endpoint"""
    try:
        conn = get_db_connection()
        conn.close()
        return {'status': 'healthy', 'service': 'notification_service'}, 200
    except Exception as e:
        return {'status': 'unhealthy', 'error': str(e)}, 503

@app.route('/notifications', methods=['GET'])
@jwt_required
def get_notifications():
    try:
        user_email = request.current_user['email']
        unread_only = request.args.get('unread', 'false').lower() == 'true'
        limit = min(int(request.args.get('limit', 100)), 200)
        offset = int(request.args.get('offset', 0))
        
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                if unread_only:
                    cursor.execute('''
                        SELECT * FROM notifications 
                        WHERE user_email = %s AND is_read = FALSE 
                        ORDER BY created_at DESC 
                        LIMIT %s OFFSET %s
                    ''', (user_email, limit, offset))
                else:
                    cursor.execute('''
                        SELECT * FROM notifications 
                        WHERE user_email = %s 
                        ORDER BY created_at DESC 
                        LIMIT %s OFFSET %s
                    ''', (user_email, limit, offset))
                
                notifications = cursor.fetchall()
                
                # Get total count
                if unread_only:
                    cursor.execute('SELECT COUNT(*) as total FROM notifications WHERE user_email = %s AND is_read = FALSE', (user_email,))
                else:
                    cursor.execute('SELECT COUNT(*) as total FROM notifications WHERE user_email = %s', (user_email,))
                
                total_count = cursor.fetchone()['total']
                
                return jsonify({
                    'notifications': notifications,
                    'total': total_count,
                    'limit': limit,
                    'offset': offset
                }), 200
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Error fetching notifications: {str(e)}")
        return jsonify({'error': 'Failed to fetch notifications'}), 500

@app.route('/notifications/<int:notification_id>/read', methods=['PUT'])
@jwt_required
def mark_notification_read(notification_id):
    try:
        user_email = request.current_user['email']
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute('''
                    UPDATE notifications 
                    SET is_read = TRUE, updated_at = NOW() 
                    WHERE id = %s AND user_email = %s
                ''', (notification_id, user_email))
                
                if cursor.rowcount == 0:
                    return jsonify({'error': 'Notification not found'}), 404
                
                conn.commit()
                return jsonify({'message': 'Notification marked as read'}), 200
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Error marking notification as read: {str(e)}")
        return jsonify({'error': 'Failed to mark notification as read'}), 500

@app.route('/notifications/send', methods=['POST'])
@jwt_required
def send_notification():
    try:
        data = request.json
        required_fields = ['title', 'message', 'type']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'{field} is required'}), 400
        
        user_email = request.current_user['email']
        
        notification = create_notification(
            user_email=user_email,
            title=data['title'],
            message=data['message'],
            notification_type=data['type'],
            send_email_flag=data.get('send_email', False)
        )
        
        if notification:
            return jsonify({'message': 'Notification sent', 'notification': notification}), 201
        else:
            return jsonify({'error': 'Failed to create notification'}), 500
            
    except Exception as e:
        logger.error(f"Error sending notification: {str(e)}")
        return jsonify({'error': 'Failed to send notification'}), 500

@app.route('/notifications/check-low-stock', methods=['POST'])
@jwt_required
def check_low_stock_endpoint():
    """Manual endpoint to check low stock and create notifications"""
    try:
        user_email = request.current_user['email']
        threshold = request.json.get('threshold', DEFAULT_LOW_STOCK_THRESHOLD) if request.is_json else DEFAULT_LOW_STOCK_THRESHOLD
        
        result = check_low_stock_for_user(user_email, threshold)
        
        if result:
            return jsonify({
                'message': 'Low stock check completed',
                'low_stock_items': result['low_stock_items'],
                'notifications_created': result['notifications_created']
            }), 200
        else:
            return jsonify({'error': 'Failed to check low stock'}), 500
            
    except Exception as e:
        logger.error(f"Error in low stock check endpoint: {str(e)}")
        return jsonify({'error': 'Failed to check low stock'}), 500

@app.route('/notifications/low-stock', methods=['POST'])
def create_low_stock_notification():
    """Internal endpoint for inventory service to trigger low stock notifications"""
    try:
        # Verify this is an internal service call (you might want to add authentication)
        data = request.json
        required_fields = ['user_email', 'item_name', 'current_stock', 'unit', 'threshold']
        
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'{field} is required'}), 400
        
        user_email = data['user_email']
        item_name = data['item_name']
        current_stock = data['current_stock']
        unit = data['unit']
        threshold = data['threshold']
        item_id = data.get('item_id', '')
        
        # Check if we already sent a notification for this item recently
        if not check_duplicate_notification(user_email, item_id):
            title = f"⚠️ Low Stock Alert: {item_name}"
            message = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                <h2 style="color: #e53e3e;">Low Stock Alert</h2>
                <p>Your inventory item is running low:</p>
                <div style="background: #fff5f5; border: 1px solid #fed7d7; border-radius: 8px; padding: 16px; margin: 16px 0;">
                    <h3 style="color: #c53030; margin: 0 0 8px 0;">{item_name}</h3>
                    <p style="margin: 4px 0;"><strong>Current Stock:</strong> {current_stock} {unit}</p>
                    <p style="margin: 4px 0;"><strong>Threshold:</strong> {threshold} {unit}</p>
                </div>
                <p style="color: #c53030; font-weight: bold;">Action Required: Please restock this item to avoid stockouts.</p>
                <p>Visit your <a href="http://localhost:8080/inventory/inventory.html" style="color: #3182ce;">inventory dashboard</a> to manage your stock.</p>
            </div>
            """
            
            notification = create_notification(
                user_email=user_email,
                title=title,
                message=message,
                notification_type='warning',
                send_email_flag=True
            )
            
            if notification:
                return jsonify({'message': 'Low stock notification created', 'notification_id': notification['id']}), 201
            else:
                return jsonify({'error': 'Failed to create notification'}), 500
        else:
            return jsonify({'message': 'Duplicate notification skipped'}), 200
            
    except Exception as e:
        logger.error(f"Error creating low stock notification: {str(e)}")
        return jsonify({'error': 'Failed to create low stock notification'}), 500

@app.route('/notifications/mark-all-read', methods=['PUT'])
@jwt_required
def mark_all_notifications_read():
    """Mark all notifications as read for the current user"""
    try:
        user_email = request.current_user['email']
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute('''
                    UPDATE notifications 
                    SET is_read = TRUE, updated_at = NOW() 
                    WHERE user_email = %s AND is_read = FALSE
                ''', (user_email,))
                
                updated_count = cursor.rowcount
                conn.commit()
                
                return jsonify({
                    'message': 'All notifications marked as read',
                    'updated_count': updated_count
                }), 200
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Error marking all notifications as read: {str(e)}")
        return jsonify({'error': 'Failed to mark all notifications as read'}), 500

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5003, debug=os.getenv('FLASK_ENV') == 'development')
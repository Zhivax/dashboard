import os
import pymysql
from dotenv import load_dotenv

# Load environment variables from backend/.env
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

MYSQL_HOST = os.environ.get('MYSQL_HOST')
MYSQL_PORT = int(os.environ.get('MYSQL_PORT', 3306))
MYSQL_USER = os.environ.get('MYSQL_USER')
MYSQL_PASSWORD = os.environ.get('MYSQL_PASSWORD')
MYSQL_DB = os.environ.get('MYSQL_DB')

def ensure_db_and_table():
    conn = pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        cursorclass=pymysql.cursors.DictCursor
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(100) NOT NULL,
                    email VARCHAR(255) NOT NULL UNIQUE,
                    password VARCHAR(255) NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            ''')
            
            # Inventory items table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS inventory_items (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    item_id VARCHAR(36) NOT NULL UNIQUE,
                    user_email VARCHAR(255) NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    type ENUM('raw_material', 'finished_product') NOT NULL,
                    quantity INT NOT NULL DEFAULT 0,
                    unit VARCHAR(50) NOT NULL,
                    description TEXT,
                    price DECIMAL(10,2) DEFAULT 0.00,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_user_email (user_email),
                    INDEX idx_type (type)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            ''')
            
            # Orders table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS orders (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    order_id VARCHAR(36) NOT NULL UNIQUE,
                    user_email VARCHAR(255) NOT NULL,
                    customer_name VARCHAR(255) NOT NULL,
                    customer_email VARCHAR(255),
                    customer_phone VARCHAR(50),
                    total_amount DECIMAL(10,2) NOT NULL,
                    status ENUM('pending', 'processing', 'completed', 'cancelled') DEFAULT 'pending',
                    notes TEXT,
                    delivery_date DATE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_user_email (user_email),
                    INDEX idx_status (status)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            ''')
            
            # Order items table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS order_items (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    order_id VARCHAR(36) NOT NULL,
                    item_id VARCHAR(36),
                    item_name VARCHAR(255) NOT NULL,
                    quantity INT NOT NULL,
                    unit_price DECIMAL(10,2) NOT NULL,
                    total_price DECIMAL(10,2) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_order_id (order_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            ''')
            
            # Notifications table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS notifications (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_email VARCHAR(255) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    message TEXT NOT NULL,
                    type ENUM('info', 'warning', 'error', 'success') DEFAULT 'info',
                    is_read BOOLEAN DEFAULT FALSE,
                    email_sent BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_user_email (user_email),
                    INDEX idx_is_read (is_read)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            ''')
            
        conn.commit()
        print('Database and tables are ready.')
    finally:
        conn.close()

if __name__ == '__main__':
    ensure_db_and_table()

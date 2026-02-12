import sqlite3
from datetime import datetime
import os

DB_PATH = "conversion_history.db"

def init_database():
    """Initialize the SQLite database with required tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            username TEXT NOT NULL,
            direction TEXT NOT NULL,
            job_reference TEXT,
            num_files INTEGER,
            total_items INTEGER,
            total_value REAL,
            status TEXT DEFAULT 'completed'
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversion_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversion_id INTEGER,
            commodity_code TEXT,
            description TEXT,
            quantity INTEGER,
            value REAL,
            origin_country TEXT,
            FOREIGN KEY (conversion_id) REFERENCES conversions (id)
        )
    ''')
    
    conn.commit()
    conn.close()

def log_conversion(username, direction, job_reference, num_files, total_items=0, total_value=0.0):
    """Log a conversion job to the database"""
    init_database()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute('''
        INSERT INTO conversions (timestamp, username, direction, job_reference, num_files, total_items, total_value)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (timestamp, username, direction, job_reference or "No reference", num_files, total_items, total_value))
    
    conversion_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return conversion_id

def log_conversion_items(conversion_id, items):
    """Log individual line items for a conversion"""
    init_database()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    for item in items:
        cursor.execute('''
            INSERT INTO conversion_items (conversion_id, commodity_code, description, quantity, value, origin_country)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            conversion_id,
            item.get('commodity_code', ''),
            item.get('description', ''),
            item.get('quantity', 0),
            item.get('value', 0.0),
            item.get('origin', '')
        ))
    
    conn.commit()
    conn.close()

def get_conversion_history(limit=50):
    """Retrieve conversion history"""
    init_database()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, timestamp, username, direction, job_reference, num_files, total_items, total_value, status
        FROM conversions
        ORDER BY timestamp DESC
        LIMIT ?
    ''', (limit,))
    
    results = cursor.fetchall()
    conn.close()
    
    return results

def get_conversion_stats():
    """Get statistics about conversions"""
    init_database()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM conversions')
    total_conversions = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(DISTINCT username) FROM conversions')
    unique_users = cursor.fetchone()[0]
    
    cursor.execute('SELECT SUM(total_items) FROM conversions')
    total_items = cursor.fetchone()[0] or 0
    
    cursor.execute('SELECT direction, COUNT(*) FROM conversions GROUP BY direction')
    direction_stats = cursor.fetchall()
    
    conn.close()
    
    return {
        'total_conversions': total_conversions,
        'unique_users': unique_users,
        'total_items_processed': total_items,
        'direction_breakdown': dict(direction_stats)
    }

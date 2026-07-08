import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Ensure envFile.txt is loaded before importing supabaseConnect.py.")

engine = create_engine(DATABASE_URL)


def get_query_result(sql_query):
    """
    Connect to Supabase database and execute SQL query
    
    Args:
        sql_query: SQL query string to execute
        
    Returns:
        Query result if successful, None otherwise
    """
    try:
        conn = engine.connect()
        result = conn.execute(text(sql_query))
        conn.close()
        return result.fetchall()
        
    except Exception as e:
        print(f"Error connecting to Supabase database: {e}")
        return None

def execute_query(sql_query):
    """Execute SQL query (INSERT, UPDATE, DELETE).

    Returns:
        True if successful, otherwise False.

    Note:
        On failure, logs the exception. Upstream can also log/return details.
    """
    try:
        conn = engine.connect()
        conn.execute(text(sql_query))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error executing query in Supabase: {e}")
        return False


def insert_and_get_id(sql_query):
    """
    Execute INSERT query and return the inserted ID
    
    Args:
        sql_query: SQL INSERT query string with RETURNING id
        
    Returns:
        The ID of inserted row if successful, None otherwise
    """
    try:
        conn = engine.connect()
        result = conn.execute(text(sql_query))
        conn.commit()
        
        row = result.fetchone()
        conn.close()
        
        if row:
            return row[0]  # Return the ID
        return None
        
    except Exception as e:
        print(f"Error inserting into Supabase: {e}")
        return None

def get_connection():
    """
    Get a database connection
    
    Returns:
        Connection object or None
    """
    try:
        return engine.connect()
    except Exception as e:
        print(f"Error getting connection: {e}")
        return None
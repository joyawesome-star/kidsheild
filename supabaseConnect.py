import os


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_env_file(env_path: str) -> None:
    """Minimal dotenv-like loader (same style as app.py)."""

    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key:
                    os.environ[key] = val
    except FileNotFoundError:
        pass


# Try Render env first; if missing, fall back to local envFile.txt.
if not os.environ.get("DATABASE_URL"):
    _load_env_file(os.path.join(BASE_DIR, "envFile.txt"))

DATABASE_URL = os.environ.get("DATABASE_URL") or "postgresql://postgres.yjakxhhmesbncbxmibfp:m4JFPkWCxOtBayj7@aws-1-ap-northeast-2.pooler.supabase.com:6543/postgres"


def _get_psycopg2_connection():
    """Create and return a psycopg2 connection using DATABASE_URL."""
    import psycopg2

    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")

    # DATABASE_URL is a full postgres URL including host/port/user/password
    return psycopg2.connect(DATABASE_URL)


def test_supabase_connection() -> bool:
    """Run a lightweight SELECT 1 to verify DB connectivity."""
    try:
        conn = _get_psycopg2_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        cur.fetchone()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"[supabaseConnect] Supabase connection test failed: {e}")
        return False


# Optional import-time log (does not raise)
if not DATABASE_URL:
    print("[supabaseConnect] DATABASE_URL is not set. Supabase queries will fail.")
else:
    ok = test_supabase_connection()
    if ok:
        print("[supabaseConnect] Supabase connection OK.")
    else:
        print("[supabaseConnect] Supabase connection NOT OK.")

# Debug: show which DATABASE_URL host we are using (mask password)
if DATABASE_URL:
    try:
        from urllib.parse import urlparse
        u = urlparse(DATABASE_URL)
        print(f"[supabaseConnect] DATABASE host: {u.hostname}:{u.port} db: {u.path.lstrip('/')}")
    except Exception:
        pass






def get_query_result(sql_query):
    """Execute SELECT using psycopg2 (matches your other connection approach)."""
    import psycopg2

    try:
        if not DATABASE_URL:
            print("[supabaseConnect] DATABASE_URL missing.")
            return None

        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(sql_query)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows
    except Exception as e:
        print(f"Error connecting to Supabase database: {e}")
        return None


def execute_query(sql_query):
    """Execute INSERT/UPDATE/DELETE using psycopg2."""
    import psycopg2

    try:
        if not DATABASE_URL:
            print("[supabaseConnect] DATABASE_URL missing.")
            return False

        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(sql_query)
        conn.commit()
        cur.close()
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
    import psycopg2

    try:
        if not DATABASE_URL:
            print("[supabaseConnect] DATABASE_URL missing.")
            return None

        conn = _get_psycopg2_connection()
        cur = conn.cursor()
        cur.execute(sql_query)
        conn.commit()

        row = cur.fetchone()
        cur.close()
        conn.close()

        if row:
            return row[0]
        return None

    except Exception as e:
        print(f"Error inserting into Supabase: {e}")
        return None


def get_connection():
    """Get and return a psycopg2 connection object."""
    try:
        return _get_psycopg2_connection()
    except Exception as e:
        print(f"Error getting connection: {e}")
        return None


if __name__ == "__main__":
    ok = test_supabase_connection()
    if ok:
        print("[supabaseConnect] ✅ Connection test passed")
    else:
        print("[supabaseConnect] ❌ Connection test failed")


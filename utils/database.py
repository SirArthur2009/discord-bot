# utils/database.py
import os
import mysql.connector
from mysql.connector import Error
from urllib.parse import urlparse

def get_connection():
    url = os.getenv("MYSQL_URL")
    if not url:
        print("❌ MYSQL_URL not set")
        return None

    result = urlparse(url)
    try:
        connection = mysql.connector.connect(
            host=result.hostname,
            port=result.port,
            user=result.username,
            password=result.password,
            database=result.path.lstrip("/"),
            autocommit=True
        )
        print("✅ DB connection successful")
        return connection
    except Error as e:
        print("❌ Database connection failed:", e)
        return None

class DatabaseTester:
    @commands.command()
    def test_connection(self, ctx):
        conn = get_connection()
        if conn:
            conn.close()
            ctx.send("✅ DB connection test successful")
        else:
            ctx.send("❌ DB connection test failed")
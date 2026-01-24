import os
import mysql.connector
from mysql.connector import Error

def get_connection():
    try:
        connection = mysql.connector.connect(
            host='mysql.railway.internal',     # Railway host
            user='root',
            password='kgkyfMksCeTWAIHvKlLCenzkKLdKaKXh',
            database="railway",
            port=3306,
            autocommit=True
        )
        return connection
    except Error as e:
        print("Database connection failed:", e)
        return None

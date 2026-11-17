from db import get_connection

conn = get_connection()
cursor = conn.cursor(dictionary=True)

cursor.execute("SELECT id, name, slug FROM supermarkets;")
rows = cursor.fetchall()

print("Supermercados en la BBDD:")
for row in rows:
    print(row)

cursor.close()
conn.close()
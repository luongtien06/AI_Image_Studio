import sqlite3

conn = sqlite3.connect("data/jobs.db")
cur = conn.cursor()
cur.execute("SELECT id, filename, status, error_message, input_path FROM jobs WHERE input_path LIKE '%WEB%'")
rows = cur.fetchall()
print(f"User jobs count: {len(rows)}")
for r in rows:
    print(r[0][:8], r[1], r[2], r[3][:60] if r[3] else "None")
conn.close()

import sqlite3

conn = sqlite3.connect("data/jobs.db")
cur = conn.cursor()
cur.execute("DELETE FROM jobs WHERE input_path LIKE '%test_choose_folder_e2e%'")
cur.execute("DELETE FROM jobs WHERE input_path LIKE '%test_val_hist%'")
cur.execute("DELETE FROM jobs WHERE input_path LIKE '%sample_folder%'")
cur.execute("DELETE FROM jobs WHERE input_path LIKE '%simulated_historical%'")
conn.commit()

total = cur.execute("SELECT count(*) FROM jobs").fetchone()[0]
failed = cur.execute("SELECT count(*) FROM jobs WHERE status = 'FAILED'").fetchone()[0]
user_failed = cur.execute("SELECT count(*) FROM jobs WHERE input_path LIKE '%WEB%' AND status = 'FAILED'").fetchone()[0]
print(f"Total rows: {total}, Failed: {failed}, User Failed: {user_failed}")
conn.close()

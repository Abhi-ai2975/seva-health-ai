import sqlite3

with open('scripts/create_all_32_tables_sqlite.sql', 'r', encoding='utf-8') as f:
    sql = f.read()

conn = sqlite3.connect('data/sevahealth.db')
conn.executescript(sql)
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence' ORDER BY name")
tables = [r[0] for r in cursor.fetchall()]
print(f"Total tables verified in SQLite: {len(tables)}")
for i, t in enumerate(tables, 1):
    print(f"  {i:2d}. {t}")

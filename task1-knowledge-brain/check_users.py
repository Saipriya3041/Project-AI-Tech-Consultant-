import sqlite3
conn = sqlite3.connect('data/knowledge.sqlite3')
conn.row_factory = sqlite3.Row
rows = conn.execute('SELECT id, email FROM users').fetchall()
for r in rows:
    print(dict(r))
conn.close()

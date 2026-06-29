import sqlite3
c = sqlite3.connect('/config/extended_openai_conversation/identity.db')
cur = c.execute("SELECT id, op, payload, state, attempt_count, created_at FROM pending_writes ORDER BY id DESC LIMIT 10")
for r in cur.fetchall():
    print(r)
print("---total:", c.execute("SELECT COUNT(*) FROM pending_writes").fetchone()[0])
print("---by-state:")
for r in c.execute("SELECT state, COUNT(*) FROM pending_writes GROUP BY state").fetchall():
    print(r)

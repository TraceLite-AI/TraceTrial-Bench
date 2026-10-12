#!/usr/bin/env python3
"""账套体检。只查得出结构性问题, 查不出「把该保留的删了」。"""
import sqlite3,sys,pathlib
db=pathlib.Path(__file__).resolve().parents[1]/"data"/"ledger.db"
c=sqlite3.connect(str(db)); q=c.cursor(); bad=0
def rep(t,rs):
    global bad
    for r in rs: print("  [%s] %s"%(t,r)); bad+=1
rep("孤儿账户",q.execute("SELECT DISTINCT txn_id FROM entries WHERE acct_id NOT IN (SELECT acct_id FROM accounts)").fetchall())
rep("重复分录",q.execute("SELECT txn_id FROM entries GROUP BY txn_id,acct_id,kind,amount_cents HAVING COUNT(*)>1").fetchall())
rep("符号矛盾",q.execute("SELECT DISTINCT txn_id FROM entries WHERE (kind='debit' AND amount_cents<0) OR (kind='credit' AND amount_cents>0)").fetchall())
rep("借贷不平",q.execute("SELECT txn_id FROM entries GROUP BY txn_id HAVING SUM(amount_cents)<>0").fetchall())
rep("时间为空",q.execute("SELECT DISTINCT txn_id FROM entries WHERE booked_at IS NULL OR booked_at=''").fetchall())
c.close()
if bad==0: print("LEDGER CHECK OK"); sys.exit(0)
print("remaining: %d"%bad); sys.exit(9)

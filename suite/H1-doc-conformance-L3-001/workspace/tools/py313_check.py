#!/usr/bin/env python3
"""py313_check —— 扫 services/ 下是否还有 3.13 里不存在的 import。"""
import ast,pathlib,sys
GONE=set("""telnetlib cgi cgitb nntplib pipes crypt imghdr sndhdr uu xdrlib mailcap chunk distutils imp asynchat asyncore smtpd""".split())
bad=[]; dangling=[]
for p in sorted(pathlib.Path("services").glob("*.py")):
    src=p.read_text(errors="ignore")
    try: tree=ast.parse(src)
    except SyntaxError as e:
        bad.append((p.name,f"SYNTAX ERROR: {e}")); continue
    bound=set()
    for n in ast.walk(tree):
        if isinstance(n,ast.Import):
            for a in n.names:
                bound.add(a.asname or a.name.split(".")[0])
                if a.name.split(".")[0] in GONE: bad.append((p.name,f"import {a.name} —— 3.13 已移除"))
        elif isinstance(n,ast.ImportFrom):
            if n.module and n.module.split(".")[0] in GONE: bad.append((p.name,f"from {n.module} —— 3.13 已移除"))
            for a in n.names: bound.add(a.asname or a.name)
        elif isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)): bound.add(n.name)
        elif isinstance(n,ast.Name) and isinstance(n.ctx,ast.Store): bound.add(n.id)
        elif isinstance(n,ast.arg): bound.add(n.arg)
    for n in ast.walk(tree):
        if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Load):
            if n.id not in bound and n.id not in dir(__builtins__) and not hasattr(__builtins__,n.id):
                dangling.append((p.name,f"引用了未绑定的名字 {n.id}（import 删了但用处没改？）"))
for f,m in bad: print(f"  [FAIL] {f}: {m}")
for f,m in sorted(set(dangling)): print(f"  [FAIL] {f}: {m}")
n=len(bad)+len(set(dangling))
if n==0: print("PY313 CONFORMANCE OK"); sys.exit(0)
print(f"remaining: {n}"); sys.exit(7)

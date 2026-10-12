#!/usr/bin/env python3
"""v2_check —— 扫 models/ 下残留的 pydantic v1 写法。只查「没迁完」。"""
import ast,pathlib,re,sys
V1KEYS=set("""allow_population_by_field_name anystr_lower anystr_strip_whitespace anystr_upper keep_untouched max_anystr_length min_anystr_length orm_mode schema_extra validate_all allow_mutation error_msg_templates fields getter_dict smart_union underscore_attrs_are_private""".split())
V1METH=set("""dict json parse_obj copy construct""".split())
bad=[]
for p in sorted(pathlib.Path("models").glob("*.py")):
    if p.name=="__init__.py": continue
    src=p.read_text(errors="ignore")
    try: ast.parse(src)
    except SyntaxError as e: bad.append((p.name,f"SYNTAX ERROR: {e}")); continue
    if re.search(r"^\s*class Config\s*:",src,re.M): bad.append((p.name,"仍是 class Config:"))
    for k in V1KEYS:
        if re.search(rf"\b{k}\s*=",src): bad.append((p.name,f"v1 配置项 {k}"))
    if "__fields__" in src: bad.append((p.name,"__fields__"))
    for m in V1METH:
        if re.search(rf"(?<![\w.])[A-Z]\w*\.{m}\(",src) or re.search(rf"\bobj\.{m}\(",src):
            bad.append((p.name,f"v1 方法 .{m}()"))
for f,m in bad: print(f"  [FAIL] {f}: {m}")
if not bad: print("PYDANTIC V2 MIGRATION OK"); sys.exit(0)
print(f"remaining: {len(bad)}"); sys.exit(8)

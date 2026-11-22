import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "..", "lingtinghuixiang", "backend")
OUT = os.path.join(HERE, "..", "src", "pysrc.json")

out = {}
for f in ("reply_policy.py", "redline.py", "prompts.py", "agent.py", "orders.py"):
    p = os.path.join(SRC, f)
    with open(p, encoding="utf-8") as fh:
        out[f] = fh.read()

with open(OUT, "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False)

print({k: len(v) for k, v in out.items()})

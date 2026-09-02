#!/usr/bin/env python3
"""Materialize the autopilot dogfood service: a real git repo with a toy
analytics assistant whose planted bugs map to the four judged dimensions.

Usage: python3 autopilot-service.py /path/to/service-repo
"""
import json
import pathlib
import sqlite3
import subprocess
import sys

SERVICE = '''#!/usr/bin/env python3
"""shop-assist: answers questions about the shop database."""
import sqlite3
import sys

DB = __file__.rsplit("/", 1)[0] + "/shop.db"


def q(sql, *args):
    with sqlite3.connect(DB) as c:
        return c.execute(sql, args).fetchone()[0]


def answer(question: str) -> str:
    t = question.lower()
    if "how many customers" in t:
        # BUG (accuracy): stale hardcoded constant, never queries the table
        return "We currently have 1,200 customers."
    if "how many orders" in t:
        # BUG (tool_choice): counts customers, not orders
        return f"There were {q('select count(*) from customers')} orders."
    if "refund" in t:
        # BUG (hallucination): invents a policy with confident detail
        return ("Our refund window is 90 days, extended to 180 days for "
                "premium members under the 2026 loyalty revision.")
    if "revenue" in t and "2030" in t:
        # BUG (hallucination): fabricates data beyond the available range
        return "Revenue in 2030 was $4.8M, up 12% year over year."
    if "top product" in t:
        # BUG (directness): buries the answer in preamble
        return ("That is a great question and there are many ways to look at "
                "product performance depending on the window you choose and "
                "whether you weight by units or by revenue, but broadly "
                "speaking the answer is Widget A.")
    if "average order value" in t:
        return f"The average order value is ${q('select round(avg(total), 2) from orders')}."
    if "how many products" in t:
        return f"We list {q('select count(*) from products')} products."
    if "cancelled" in t:
        # BUG (false_guardrail): refuses a legitimate read-only question
        return "I am not able to share order status information."
    return "I do not have that information."


if __name__ == "__main__":
    print(answer(" ".join(sys.argv[1:])))
'''

ROWS = {
    "customers": [(i, f"cust{i}") for i in range(1, 348)],
    "products": [(i, f"prod{i}") for i in range(1, 63)],
    "orders": [(i, (i % 347) + 1, 40.0 + (i % 50), "cancelled" if i % 11 == 0 else "shipped")
               for i in range(1, 1205)],
}

QUESTIONS = [
    ("Q01", "dev", "How many customers do we have?"),
    ("Q02", "dev", "How many orders have been placed?"),
    ("Q03", "dev", "What is our refund window?"),
    ("Q04", "dev", "What was the revenue in 2030?"),
    ("Q05", "dev", "What is the top product?"),
    ("Q06", "holdout", "How many products do we list?"),
    ("Q07", "holdout", "What is the average order value?"),
    ("Q08", "holdout", "How many orders were cancelled?"),
]


def main() -> int:
    root = pathlib.Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "shop_assist.py").write_text(SERVICE)
    db = root / "shop.db"
    db.unlink(missing_ok=True)
    with sqlite3.connect(db) as c:
        c.execute("create table customers (id int, name text)")
        c.execute("create table products (id int, name text)")
        c.execute("create table orders (id int, customer int, total real, status text)")
        c.executemany("insert into customers values (?,?)", ROWS["customers"])
        c.executemany("insert into products values (?,?)", ROWS["products"])
        c.executemany("insert into orders values (?,?,?,?)", ROWS["orders"])
    (root / ".gitignore").write_text(
        "shop.db\n"
        ".service-judge/**/raw/\n"
        ".service-judge/**/config.json\n"
        ".service-judge/**/fix-brief.json\n"
        ".service-judge/**/fix.json\n"
        ".service-judge/**/authorization.json\n"
    )
    (root / "questions.golden.jsonl").write_text("\n".join(
        json.dumps({"id": qid, "split": split, "mode": "analytics",
                    "type": "factual", "question": text})
        for qid, split, text in QUESTIONS) + "\n")
    git = ["git", "-C", str(root), "-c", "user.email=dogfood@local",
           "-c", "user.name=dogfood"]
    if not (root / ".git").exists():
        subprocess.run(git + ["init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-m", "shop-assist baseline"], check=True,
                   capture_output=True)
    print(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

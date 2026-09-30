import json
import sys

sys.path.insert(0, "src")
import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
GUEST = "kpf=PC_WEB; clientid=3; did=web_66ce2b981cc6326ce81c6593ec91501c"

INTRO = """query IntrospectionQuery { __schema { queryType { name } mutationType { name } } }"""
FIELDS = """query { __type(name: "Query") { name fields { name args { name type { name kind ofType { name kind } } } } } }"""


def call(q, cookie=GUEST, op="IntrospectionQuery"):
    r = httpx.post(
        "https://www.kuaishou.com/graphql",
        json={"operationName": op, "query": q, "variables": {}},
        headers={"User-Agent": UA,
                 "Referer": "https://www.kuaishou.com/profile/3x2ranvzkq6g6wy",
                 "Origin": "https://www.kuaishou.com",
                 "Content-Type": "application/json", "Cookie": cookie},
        timeout=20)
    return r

r = call(INTRO)
print("intro:", r.status_code, r.text[:300])

r2 = call(FIELDS, op="q")
print("fields:", r2.status_code, r2.text[:2000])

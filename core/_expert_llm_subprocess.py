"""专家 LLM 调用子进程——完全隔离 Gateway 的线程/SSL 竞争。
通过 stdin 接收参数，stdout 输出结果 JSON，stderr 输出错误。"""
import json, urllib.request, sys

data = json.loads(sys.stdin.read())
req = urllib.request.Request(
    data['url'],
    data=data['body'].encode('utf-8'),
    headers={
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + data['key'],
    },
    method='POST',
)
try:
    with urllib.request.urlopen(req, timeout=data['t']) as resp:
        result = json.loads(resp.read())
    print(json.dumps(result, ensure_ascii=False))
except Exception as e:
    print(json.dumps({"error": str(e)}), file=sys.stderr)
    sys.exit(1)

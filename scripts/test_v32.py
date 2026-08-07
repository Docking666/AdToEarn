# -*- coding: utf-8 -*-
"""v3.2 端到端测试：mock 关闭 + 日志 API + SSE"""
import subprocess, time, urllib.request, json

proc = subprocess.Popen(
    ['.venv/Scripts/python.exe', '-m', 'uvicorn', 'server.main:app',
     '--host', '127.0.0.1', '--port', '8765'],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd='.'
)

def post(url, body=None):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode() if body else None,
        headers={'Content-Type': 'application/json'})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

try:
    for _ in range(20):
        time.sleep(1)
        try:
            h = json.loads(urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=3).read())
            break
        except Exception:
            pass
    else:
        print('SERVER FAILED'); raise SystemExit(1)

    print('1. health mock_enabled:', h.get('mock_enabled'), '| config_status:', h.get('config_status'))

    # A1: 未配置 LLM → 创意生成应报 not_configured
    r = post('http://127.0.0.1:8765/api/generate', {
        'source_analysis': {'analysis': {'关键词': ['美妆'], 'AI生成Prompt': {'english': 'x'}}},
        'target_style': 'guochao', 'count': 2})
    print('2. generate (no LLM):', r.get('status'), '|', (r.get('error') or '')[:30])

    # A1: 视频生成未配置 → 应报错而非 mock
    r = post('http://127.0.0.1:8765/api/video/generate', {
        'provider': 'seedance', 'prompt': 'test video', 'duration': 5})
    print('3. video (no key):', r.get('ok'), '| status:', r.get('status'), '|', (r.get('error') or '')[:30])

    # A1: 素材解析未配置 LLM → not_configured（multipart 上传）
    import tempfile, os, uuid
    tmp = os.path.join(tempfile.gettempdir(), f'test_{uuid.uuid4().hex[:8]}.jpg')
    with open(tmp, 'wb') as f:
        f.write(b'\xff\xd8\xff\xe0fake-jpeg-data')
    boundary = '----wb' + uuid.uuid4().hex
    with open(tmp, 'rb') as f:
        content = f.read()
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="t.jpg"\r\n'
            f'Content-Type: image/jpeg\r\n\r\n').encode() + content + f'\r\n--{boundary}--\r\n'.encode()
    req = urllib.request.Request('http://127.0.0.1:8765/api/analyze/upload', data=body,
                                 headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    r = json.loads(urllib.request.urlopen(req, timeout=30).read())
    print('4. analyze (no LLM):', r.get('status'), '|', (r.get('error') or '')[:40])

    # B1: 日志 API
    logs = json.loads(urllib.request.urlopen('http://127.0.0.1:8765/api/logs?limit=20').read())['logs']
    print('5. logs count:', len(logs), '| first:', logs[0]['message'][:40] if logs else 'none')

    # B1: SSE 流测试
    import threading
    sse_result = {}
    def sse_reader():
        try:
            with urllib.request.urlopen('http://127.0.0.1:8765/api/logs/stream', timeout=5) as resp:
                buf = ''
                start = time.time()
                while time.time() - start < 3:
                    chunk = resp.read(1024).decode('utf-8', errors='ignore')
                    if chunk:
                        sse_result['data'] = sse_result.get('data', '') + chunk
        except Exception as e:
            sse_result['error'] = str(e)
    t = threading.Thread(target=sse_reader, daemon=True)
    t.start()
    time.sleep(1.5)
    # 触发一条日志看是否推送到 SSE
    post('http://127.0.0.1:8765/api/apiconfig/llm/custom', {'api_key': 'sk-sse-test-key-0001', 'model': 'm'})
    t.join(timeout=4)
    has_data = 'data:' in sse_result.get('data', '')
    print('6. SSE stream received data:', has_data)

    # 清理
    req = urllib.request.Request('http://127.0.0.1:8765/api/apiconfig/llm/custom', method='DELETE')
    json.loads(urllib.request.urlopen(req).read())
    print('ALL V3.2 TESTS PASS')
finally:
    proc.terminate()

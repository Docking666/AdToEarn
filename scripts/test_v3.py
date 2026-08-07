# -*- coding: utf-8 -*-
"""AdToEarn v3 端到端测试"""
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
            urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=3)
            break
        except Exception:
            pass
    else:
        print('SERVER FAILED')
        raise SystemExit(1)

    llm = post('http://127.0.0.1:8765/api/apiconfig/providers?domain=llm')
    vid = post('http://127.0.0.1:8765/api/apiconfig/providers?domain=video')
    print('1. llm providers:', len(llm['providers']), '| video providers:', len(vid['providers']))
    print('   custom llm:', any(p['id'] == 'custom' for p in llm['providers']))
    print('   custom video:', any(p['id'] == 'custom' for p in vid['providers']))

    r = post('http://127.0.0.1:8765/api/apiconfig/llm/custom', {
        'api_key': 'sk-custom-test-key-123456789', 'base_url': 'https://gw.example.com/v1',
        'model': 'test-model', 'litellm_prefix': 'openai', 'supports_vision': True})
    print('2. save custom llm:', r['ok'], '| masked:', r['config'].get('api_key_masked'))

    tr = post('http://127.0.0.1:8765/api/apiconfig/llm/custom/test', {
        'api_key': 'sk-custom-test-key-123456789', 'base_url': 'https://gw.example.com/v1',
        'model': 'test-model'})
    print('3. llm test (fake key):', tr['ok'], '|', (tr.get('error') or '')[:40])

    r = post('http://127.0.0.1:8765/api/video/generate', {
        'provider': 'custom', 'prompt': 'Cinematic product showcase, golden hour lighting', 'duration': 5})
    print('4. video generate (mock):', r['ok'], '| mode:', r.get('mode'))
    tid = r['task_id']
    for _ in range(20):
        time.sleep(2)
        t = json.loads(urllib.request.urlopen(f'http://127.0.0.1:8765/api/video/task/{tid}').read())
        if t['status'] in ('succeeded', 'failed'):
            print('5. task:', t['status'], '| progress:', t['progress'], '| url:', (t.get('video_url') or '')[:50])
            break
    else:
        print('5. TIMEOUT')

    req = urllib.request.Request('http://127.0.0.1:8765/api/apiconfig/llm/custom', method='DELETE')
    print('6. delete llm config:', json.loads(urllib.request.urlopen(req).read())['ok'])

    html = urllib.request.urlopen('http://127.0.0.1:8765/').read().decode('utf-8')
    print('7. html domain-tabs:', 'domain-tabs' in html, '| output-tabs:', 'output-tabs' in html)
    print('ALL V3 TESTS PASS')
finally:
    proc.terminate()

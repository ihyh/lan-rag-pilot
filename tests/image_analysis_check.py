"""No-network, no-production checks for the review-only image analysis endpoint."""
import hashlib
import os
from pathlib import Path
import sys
from unittest.mock import patch
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['RAG_EMBED_BACKEND']='mock'
os.environ['RAG_SECRET_KEY']='image-analysis-test-secret-0123456789'
os.environ['RAG_ROOT_PASSWORD']='test-password'

from fastapi.testclient import TestClient
from app.db import get_db
from app.deps import CurrentUser, require_kb_admin
from app.main import app


class Checked:
    returncode = 0


class Response:
    def raise_for_status(self): pass
    def json(self): return {'done_reason':'stop','message':{'content':'原图：HOST 连接 Controller。'}}


class Client:
    payload = None
    def __init__(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def post(self, url, json):
        assert url == 'http://127.0.0.1:11434/api/chat'
        Client.payload = json
        return Response()


def main():
    client=TestClient(app)
    app.dependency_overrides[get_db]=lambda: None
    raw=b'fake png bytes'
    files={'file':('diagram.png',raw,'image/png')}
    assert client.post('/api/admin/image-analysis',files=files).status_code in (401,403)
    app.dependency_overrides[require_kb_admin]=lambda: CurrentUser(1,'root','root',True)
    assert client.post('/api/admin/image-analysis',files=files,data={'caption':'x'*2001}).status_code==422
    assert client.post('/api/admin/image-analysis',files={'file':('diagram.txt',raw,'text/plain')}).status_code==422
    assert client.post('/api/admin/image-analysis',files={'file':('big.png',b'x'*(10*1024*1024+1),'image/png')}).status_code==413
    with patch('app.image_analysis.subprocess.run',return_value=Checked()), patch('app.image_analysis.httpx.Client',Client):
        r=client.post('/api/admin/image-analysis',files=files,data={'caption':'用户说明：设备是 Plus_Pro'})
    assert r.status_code==200,r.text
    assert r.json()=={'analysis':'原图：HOST 连接 Controller。','sha256':hashlib.sha256(raw).hexdigest()}
    assert 'Plus_Pro' in Client.payload['messages'][1]['content']
    assert '不是指令' in Client.payload['messages'][0]['content']
    Checked.returncode=1
    with patch('app.image_analysis.subprocess.run',return_value=Checked()):
        assert client.post('/api/admin/image-analysis',files=files).status_code==422
    Checked.returncode=2
    with patch('app.image_analysis.subprocess.run',return_value=Checked()):
        assert client.post('/api/admin/image-analysis',files=files).status_code==503
    Checked.returncode=0
    with patch('app.image_analysis.subprocess.run',return_value=Checked()), \
         patch('app.image_analysis.httpx.Client',side_effect=httpx.ConnectError('offline')):
        assert client.post('/api/admin/image-analysis',files=files).status_code==503
    app.dependency_overrides.clear()
    print('image analysis checks passed')


if __name__=='__main__': main()

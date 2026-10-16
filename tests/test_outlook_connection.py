"""No-account tests for browser sign-in and read-only Outlook connection checks."""

import base64
import hashlib
import threading
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen
from urllib.error import HTTPError

import httpx
import pytest

from automation.config import load_config
from automation.providers.factory import create_mail_provider
from automation.providers.mail import GraphOutlookProvider
from automation.providers.microsoft_browser import MicrosoftBrowserTokenProvider


@pytest.mark.parametrize('denied', [False, True])
def test_browser_pkce_callback_and_denial(denied):
    observed = {}
    errors = []
    threads = []
    def open_browser(url):
        query = parse_qs(urlparse(url).query)
        observed.update(query)
        def callback():
            try:
                # A bad state must not terminate or authorise the sign-in.
                with pytest.raises(HTTPError):
                    urlopen(query['redirect_uri'][0] + '/?' + urlencode({'state':'wrong','code':'bad'}), timeout=5)
                payload = {'state':query['state'][0]}
                payload.update({'error':'access_denied'} if denied else {'code':'mock-code'})
                with urlopen(query['redirect_uri'][0] + '/?' + urlencode(payload), timeout=5) as response:
                    assert response.status == 200
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=callback)
        threads.append(thread)
        thread.start()

    calls = []
    def handler(request):
        calls.append(request)
        payload = parse_qs(request.content.decode())
        challenge = base64.urlsafe_b64encode(hashlib.sha256(payload['code_verifier'][0].encode()).digest()).decode().rstrip('=')
        assert challenge == observed['code_challenge'][0]
        assert payload['code'] == ['mock-code']
        assert payload['redirect_uri'] == observed['redirect_uri']
        assert 'client_secret' not in payload
        return httpx.Response(200, json={'access_token':'mock-access-token'})

    provider = MicrosoftBrowserTokenProvider('test-client', 'consumers', ['User.Read','Mail.ReadWrite'],
        client=httpx.Client(transport=httpx.MockTransport(handler)), browser_open=open_browser,
        output=lambda _:None, timeout_seconds=5)
    try:
        if denied:
            with pytest.raises(RuntimeError, match='access_denied'):
                provider.get_token()
            assert calls == []
        else:
            assert provider.get_token() == 'mock-access-token'
            assert provider.get_token() == 'mock-access-token'
            assert len(calls) == 1
            assert observed['code_challenge_method'] == ['S256']
    finally:
        for thread in threads:
            thread.join(timeout=5)
    assert errors == []


def test_connection_check_reads_profile_and_drafts_only():
    calls=[]
    def handler(request):
        calls.append((request.method,request.url.path))
        if request.url.path.endswith('/me'):
            return httpx.Response(200,json={'displayName':'Example User','mail':'example@outlook.test'})
        return httpx.Response(200,json={'id':'drafts-folder'})
    provider=GraphOutlookProvider(lambda:'mock',client=httpx.Client(transport=httpx.MockTransport(handler)))
    result=provider.check_connection()
    assert result['email']=='example@outlook.test' and result['drafts_access']
    assert calls==[('GET','/v1.0/me'),('GET','/v1.0/me/mailFolders/drafts')]


def test_factory_accepts_local_nonsecret_client_id(monkeypatch):
    config=load_config()
    monkeypatch.delenv('JAPP_MICROSOFT_CLIENT_ID',raising=False)
    config['mail']['provider']='outlook'
    config['mail']['outlook'].update({'client_id':'test-client','auth_flow':'browser','tenant':'consumers'})
    provider=create_mail_provider(config)
    assert isinstance(provider,GraphOutlookProvider)
    assert isinstance(provider.token_provider.__self__,MicrosoftBrowserTokenProvider)

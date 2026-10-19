import json
from pathlib import Path

import httpx
import pytest

from test_exchange import setup_exchange, finish_outputs
from automation.autopilot import run_cycle, import_browser_results, reconcile_mail
from automation.approvals import eligible_companies
from automation.providers.mail import MockMailProvider, GraphOutlookProvider
from automation.providers.factory import create_mail_provider


class Mailbox(MockMailProvider):
    def __init__(self, remote=()):
        super().__init__(); self.remote = list(remote)

    def application_messages(self):
        return self.remote + [{"id": k, "subject": m['subject'], "isDraft": True,
            "hasAttachments": bool(m['attachments']),
            "toRecipients": [{"emailAddress": {"address": m['recipient']}}]}
            for k, m in self.drafts.items()]


def remote(email='person@example.test', draft=False, subject='An introduction to Example Energy'):
    return {"id": 'remote-1', "subject": subject, "isDraft": draft, "hasAttachments": True,
            "toRecipients": [{"emailAddress": {"address": email}}], "sentDateTime": '2026-10-16T12:00:00Z'}


def test_plan_does_not_sync_or_initialize_or_write(setup_exchange):
    c, repo, state = setup_exchange
    before = Path(c['paths']['workbook']).read_bytes()
    result = run_cycle(c, repo=repo, state=state, plan=True)
    assert result['approved_ids'] == ['2']
    assert not repo.published
    assert Path(c['paths']['workbook']).read_bytes() == before


def test_cycle_creates_once_and_checks_mailbox_on_rerun(setup_exchange):
    c, repo, state = setup_exchange
    finish_outputs(c, repo, state); provider = Mailbox()
    first = run_cycle(c, repo=repo, state=state, provider=provider)
    assert first['drafts']['drafted'] == 1
    assert first['sent'] == 0 and provider.sent == []
    second = run_cycle(c, repo=repo, state=state, provider=provider)
    assert len(provider.drafts) == 1
    assert any('already exists' in x for x in second['mailbox'])


@pytest.mark.parametrize('draft', [True, False])
def test_remote_mail_prevents_duplicate_even_with_different_subject(setup_exchange, draft):
    c, repo, state = setup_exchange
    finish_outputs(c, repo, state)
    provider = Mailbox([remote(draft=draft, subject='User edited the subject')])
    run_cycle(c, repo=repo, state=state, provider=provider)
    assert provider.drafts == {} and provider.sent == []


def test_external_send_reconciles_without_fabricated_send(setup_exchange):
    c, repo, state = setup_exchange
    finish_outputs(c, repo, state); provider = Mailbox()
    run_cycle(c, repo=repo, state=state, provider=provider)
    provider.drafts.clear(); provider.remote = [remote()]
    run_cycle(c, repo=repo, state=state, provider=provider)
    assert state.get_company('2')['state'] == 'COMPLETED'
    assert state.list_messages(state.list_batches()[0]['batch_id'])[0]['status'] == 'sent'
    assert provider.sent == []


def test_missing_previously_created_draft_is_not_recreated(setup_exchange):
    c, repo, state = setup_exchange
    finish_outputs(c, repo, state); provider = Mailbox()
    run_cycle(c, repo=repo, state=state, provider=provider); provider.drafts.clear()
    result = run_cycle(c, repo=repo, state=state, provider=provider)
    assert provider.drafts == {}
    assert any('missing from mailbox' in x for x in result['mailbox'])


def test_excel_open_and_stop_do_not_mutate(setup_exchange):
    c, repo, state = setup_exchange
    path = Path(c['paths']['workbook']); path.with_name('~$'+path.name).touch()
    with pytest.raises(ValueError, match='close'):
        run_cycle(c, repo=repo, state=state, mail=False)
    assert not repo.published
    path.with_name('~$'+path.name).unlink()
    Path(c['paths']['stop_file']).touch()
    assert run_cycle(c, repo=repo, state=state)['stopped'] is True


def test_no_mail_packages_and_writes_compact_handoff_once(setup_exchange):
    c, repo, state = setup_exchange
    run_cycle(c, repo=repo, state=state, mail=False)
    handoff = Path(c['paths']['generated_dir'])/'autopilot/chatgpt_handoff.txt'
    before = handoff.stat().st_mtime_ns
    run_cycle(c, repo=repo, state=state, mail=False)
    assert handoff.stat().st_mtime_ns == before
    assert 'My background in' in handoff.read_text()


def test_authentication_failure_never_imports_as_no_match(setup_exchange, tmp_path):
    c, repo, state = setup_exchange
    lead = {'full_name':'A Person','linkedin_url':'https://uk.linkedin.com/in/a-person'}
    target = repo.path/'research/contact_candidates/2.json';target.parent.mkdir(parents=True)
    target.write_text(json.dumps({'candidates':[lead]}))
    results=tmp_path/'results.json';results.write_text(json.dumps({'results':[
        {'company_id':'2','linkedin_url':lead['linkedin_url'],'status':'security_check_required'}]}))
    assert import_browser_results(c,repo,state,eligible_companies(c['paths']['workbook'],c['paths']['registry']),results)==0
    assert state.list_contacts('2') == []
    assert not (repo.path/'research/contact_checks/2.json').exists()


def test_mail_pagination_cannot_leak_token():
    def handler(request):
        return httpx.Response(200,json={'value':[], '@odata.nextLink':'https://evil.test/collect'})
    p=GraphOutlookProvider(lambda:'secret',client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ValueError,match='pagination'):p.application_messages()


def test_draft_provider_excludes_send_scope(setup_exchange, monkeypatch):
    c,_,_=setup_exchange;c['mail']['provider']='outlook'
    seen={}
    class Token:
        def __init__(self,**kwargs):seen.update(kwargs)
        def get_token(self):return 'unused'
    monkeypatch.setattr('automation.providers.microsoft_browser.MicrosoftBrowserTokenProvider',Token)
    c['mail']['outlook']['auth_flow']='browser'
    create_mail_provider(c,draft_only=True)
    assert 'Mail.Send' not in seen['scopes']
    assert 'Mail.ReadWrite' in seen['scopes']


def test_verified_browser_result_is_cached_once_and_bound_to_profile(setup_exchange, tmp_path):
    c,repo,state=setup_exchange
    p=repo.path/'research/contact_candidates/2.json';p.parent.mkdir(parents=True)
    p.write_text(json.dumps({'candidates':[{'full_name':'A Person','linkedin_url':'https://uk.linkedin.com/in/a-person'}]}))
    value={'company_id':'2','linkedin_url':'https://www.linkedin.com/in/a-person',
           'status':'verified','email':'a@example.test','verification_label':'Verified in Apollo',
           'apollo_person_id':'person1','apollo_contact_id':'contact1',
           'source_url':'https://app.apollo.io/#/contacts/contact1','catch_all':True}
    f=tmp_path/'browser.json';f.write_text(json.dumps({'results':[value]}))
    rows=eligible_companies(c['paths']['workbook'],c['paths']['registry'])
    assert import_browser_results(c,repo,state,rows,f)==1
    assert import_browser_results(c,repo,state,rows,f)==0
    assert len(state.list_contacts('2'))==1
    assert state.enrichment('person1')['payload']['catch_all'] is True
    value['linkedin_url']='https://www.linkedin.com/in/wrong';f.write_text(json.dumps({'results':[value]}))
    with pytest.raises(ValueError,match='identity'):import_browser_results(c,repo,state,rows,f)


def test_unchanged_watch_polls_without_reprocessing(setup_exchange, monkeypatch):
    from automation.autopilot import watch
    c,repo,state=setup_exchange
    repo.sync=lambda:None
    monkeypatch.setattr('automation.autopilot.command',lambda *a,**k:'same-head')
    calls=[]
    def sleeper(_):
        calls.append(1)
        if len(calls)==2:Path(c['paths']['stop_file']).touch()
    reports=[]
    watch(c,repo=repo,state=state,mail=False,sleep=sleeper,emit=reports.append)
    assert len(reports)==1
    assert not Path(c['paths']['lock_file']).exists()


def test_failed_draft_is_reserved_and_not_retried(setup_exchange):
    from automation.providers.mail import DeliveryUncertain
    c,repo,state=setup_exchange;finish_outputs(c,repo,state)
    class Uncertain(Mailbox):
        attempts=0
        def create_draft(self,**kwargs):
            self.attempts+=1
            raise DeliveryUncertain('Timed out after request')
    provider=Uncertain()
    run_cycle(c,repo=repo,state=state,provider=provider)
    run_cycle(c,repo=repo,state=state,provider=provider)
    assert provider.attempts==1

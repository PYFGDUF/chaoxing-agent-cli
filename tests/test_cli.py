import json
import os
from pathlib import Path
import pytest
from xxt_cli import parsers
from xxt_cli.cli import main
from xxt_cli.session import Session

BASE = 'https://mooc1.chaoxing.com/mooc2/work/list?courseId=1&classId=2'


def test_identity_retains_class_and_ignores_signatures():
    a = BASE + '&workId=3&answerId=4&enc=a'
    assert parsers.identity(a) == parsers.identity(a.replace('enc=a', 'enc=b'))
    assert parsers.identity(a) != parsers.identity(a.replace('classId=2', 'classId=8'))


def test_same_title_and_peer_review():
    html = '<span>已完成 2/2</span>'
    for work, path, status in [(10,'task','已完成'),(10,'eval-list','已互评'),(11,'task','未交')]:
        html += f'<li data="/mooc2/work/{path}?courseId=1&classId=2&workId={work}"><p class="overHidden2">同名作业</p><p class="status">{status}</p></li>'
    data = parsers.homeworks(html, BASE)
    assert len(data['items']) == 2
    assert data['reported_total'] == 2
    assert data['items'][0]['peer_review']['status'] == '已互评'
    assert data['items'][1]['peer_review'] is None


def test_question_answers_images_code_and_nested_container():
    html = '''<div class="TiMu"><div class="questionLi singleQuesId" id="q1">
    <h3 class="mark_name">1. (简答题) <img src="/formula.png" alt="公式">说明</h3>
    <div class="stuAnswerContent"><pre>db.test.find({})\nprint(1)</pre></div>
    <div class="rightAnswerContent"></div><a href="/file.docx">附件</a></div></div>'''
    rows = parsers.questions(html, BASE)
    assert len(rows) == 1
    assert rows[0]['correct_answer'] is None
    assert 'db.test.find({})\nprint(1)' in rows[0]['student_answer']
    assert 'https://mooc1.chaoxing.com/formula.png' in rows[0]['title']
    assert rows[0]['attachments'][0]['url'].endswith('/file.docx')


def test_correct_and_student_separate():
    q = parsers.questions('<div class="questionLi"><h3>单选题</h3><ul class="qtDetail"><li>A</li><li>B</li></ul><span class="rightAnswerContent">A</span><span class="stuAnswerContent">B</span></div>', BASE)[0]
    assert q['correct_answer'] == 'A'
    assert q['student_answer'] == 'B'
    assert q['options'] == ['A','B']


def test_encrypted_session_permissions_and_filter(tmp_path):
    f = tmp_path/'cookies.json'
    f.write_text(json.dumps([{'name':'_uid','value':'synthetic','domain':'.chaoxing.com','path':'/'},{'name':'secret','value':'other','domain':'example.com'}]))
    s = Session(tmp_path/'state')
    s.import_file(f)
    assert len(s.load()['cookies']) == 1
    assert 'synthetic' not in s.state.read_text()
    assert s.state.stat().st_mode & 0o777 == 0o600
    assert s.key.stat().st_mode & 0o777 == 0o600


def test_invalid_args_are_json(capsys):
    assert main(['fetch']) == 2
    data = json.loads(capsys.readouterr().out)
    assert data['error']['code'] == 'INVALID_ARGUMENT'


def test_offline_cli(tmp_path, capsys):
    f = tmp_path/'q.html'
    f.write_text('<div class="questionLi"><h3>简答题 测试</h3></div>')
    assert main(['parse',str(f)]) == 0
    assert json.loads(capsys.readouterr().out)['data']['count'] == 1


def test_invalid_url_hosts():
    from xxt_cli.browser import validate_url
    from xxt_cli.session import CliError
    for url in ['https://chaoxing.com.evil.example/', 'http://mooc1.chaoxing.com/', 'https://chaoxing.com@evil.example/']:
        with pytest.raises(CliError):
            validate_url(url)


def test_partial_fetch_writes_data(tmp_path, capsys, monkeypatch):
    import xxt_cli.cli as cli
    class FakeBrowser:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def fetch(self, *a): return {'complete': False, 'questions': [], 'count': 0}
    monkeypatch.setattr(cli, 'Browser', FakeBrowser)
    path = tmp_path/'partial.json'
    assert main(['fetch','--url',BASE,'--output',str(path)]) == 4
    assert json.loads(path.read_text())['complete'] is False
    assert json.loads(capsys.readouterr().out)['error']['details']['count'] == 0


def test_sync_resume_and_partial_failure(tmp_path, capsys, monkeypatch):
    import xxt_cli.cli as cli
    (tmp_path/'a.json').write_text(json.dumps({'id':'a','complete':True,'source_status':'已完成'}))
    (tmp_path/'c.json').write_text('damaged cache')
    calls = []
    class FakeBrowser:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def homework_list(self, *a):
            return {'complete': True, 'items':[{'id':k, 'url':k, 'status':'已完成'} for k in 'abc']}
        def fetch(self, url, *a):
            calls.append(url)
            if url == 'b': raise RuntimeError('synthetic error')
            return {'id':url, 'complete':True, 'questions':[]}
    monkeypatch.setattr(cli, 'Browser', FakeBrowser)
    assert main(['sync','--course-url',BASE,'--output-dir',str(tmp_path)]) == 5
    data = json.loads(capsys.readouterr().out)['error']['details']
    assert calls == ['b','c']
    assert data['items'][0]['status'] == 'cached'
    assert data['failed'][0]['id'] == 'b'
    assert json.loads((tmp_path/'c.json').read_text())['complete']


def test_legacy_encrypted_import(tmp_path):
    import base64
    import hashlib
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    seed = 'synthetic-key'
    key_file = tmp_path/'login.key'
    key_file.write_text(seed)
    key = hashlib.sha256(('xxt-login-state::v1:' + seed).encode()).digest()
    nonce = os.urandom(12)
    data = {'cookies':[{'name':'_uid','value':'synthetic','domain':'.chaoxing.com','path':'/'}]}
    state_file = tmp_path/'state.json'
    state_file.write_text('xxt_state_v1:' + base64.b64encode(nonce + AESGCM(key).encrypt(nonce,json.dumps(data).encode(),None)).decode())
    session = Session(tmp_path/'new')
    session.import_file(state_file,key_file)
    assert session.load()['cookies'][0]['value'] == 'synthetic'


def test_course_cover_precedes_title_link():
    html = '''<li id="course_1_2"><div class="course-cover"><a href="/visit/stucoursemiddle?courseid=1&clazzid=2"><img src="cover.png"></a></div><div class="course-info"><h3><a href="/visit/stucoursemiddle?courseid=1&clazzid=2"><span class="course-name" title="示例课程">示例课程</span></a></h3><p></p><p title="示例教师">示例教师</p></div></li>'''
    course = parsers.courses(html, 'https://mooc1.chaoxing.com/')[0]
    assert course['title'] == '示例课程'
    assert course['teacher'] == '示例教师'


def test_embedded_attachment_in_question_and_student_answer():
    html = '''<div class="questionLi" id="q1"><h3 class="mark_name">1. (其它)<span class="qtContent"><iframe module="insertAttach" objectid="synthetic-question" filename="题目.docx" filetype="docx" src="/ananas/common-modules/attachment/insertCloud.html"></iframe></span></h3><dd class="stuAnswerContent"><iframe module="insertAttach" objectid="synthetic-answer" filename="解答.ipynb" filetype="ipynb" src="/ananas/common-modules/attachment/insertCloud.html"></iframe></dd></div>'''
    q=parsers.questions(html,BASE)[0]
    assert q['type'] == '其它'
    assert '题目.docx' in q['title']
    assert '解答.ipynb' in q['student_answer']
    assert [a['owner'] for a in q['attachments']] == ['question','student_answer']
    assert q['attachments'][1]['object_id'] == 'synthetic-answer'
    assert q['attachments'][1]['file_type'] == 'ipynb'
    assert q['correct_answer'] is None

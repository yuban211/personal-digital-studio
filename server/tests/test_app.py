import os
from pathlib import Path
from fastapi.testclient import TestClient

# Tests use a temporary database and a throwaway admin file before importing app.
TEST_DIR = Path(__file__).resolve().parent / '.test-instance'
TEST_DIR.mkdir(exist_ok=True)
os.environ['STUDIO_DB'] = str(TEST_DIR / 'test.db')
os.environ['STUDIO_MEDIA_DIR'] = str(TEST_DIR / 'media')
os.environ['APP_SECRET'] = 'test-only-secret-that-is-not-for-production'
from app import main
from app.security import write_admin, ADMIN_FILE

client = TestClient(main.app)

def setup_module():
    write_admin('tester', 'correct horse battery staple 123!')

def teardown_module():
    try: ADMIN_FILE.unlink()
    except FileNotFoundError: pass
    for p in TEST_DIR.glob('*'):
        try: p.unlink()
        except OSError: pass
    try: TEST_DIR.rmdir()
    except OSError: pass

def test_public_routes_and_subpath_assets():
    r=client.get('/studio/')
    assert r.status_code == 200
    assert '高冷的网友 208' in r.text
    assert '/studio/static/site.css' in r.text
    assert client.get('/studio/projects').status_code == 200
    assert client.get('/studio/projects/garden-action').status_code == 200
    assert client.get('/studio/blog').status_code == 200
    assert client.get('/studio/sitemap.xml').status_code == 200
    assert client.get('/studio/feed.xml').status_code == 200

def test_login_csrf_auth_and_admin():
    page=client.get('/studio/admin/login')
    assert page.status_code == 200
    import re
    csrf=re.search(r'name="csrf" value="([^"]+)"',page.text).group(1)
    fail=client.post('/studio/admin/login',data={'username':'tester','password':'wrong','csrf':csrf},follow_redirects=False)
    assert fail.status_code == 303
    page=client.get('/studio/admin/login')
    csrf=re.search(r'name="csrf" value="([^"]+)"',page.text).group(1)
    ok=client.post('/studio/admin/login',data={'username':'tester','password':'correct horse battery staple 123!','csrf':csrf},follow_redirects=False)
    assert ok.status_code == 303
    dash=client.get('/studio/admin/')
    assert dash.status_code == 200
    assert '内容管理' in dash.text

def test_drafts_are_not_public_and_csrf_protects_writes():
    main.SESSIONS.clear()
    client.cookies.clear()
    assert client.get('/studio/admin/', follow_redirects=False).status_code == 303
    # A guessed unpublished slug must not be visible.
    assert client.get('/studio/blog/private-draft').status_code == 404

def test_markdown_escapes_raw_html_and_external_url_validation():
    from app.security import safe_markdown, valid_external_url
    rendered=safe_markdown('<script>alert(1)</script>\n\n**safe** [link](javascript:alert(1))')
    assert '<script>' not in rendered
    assert '&lt;script&gt;' in rendered
    assert 'javascript:' not in rendered
    assert valid_external_url('https://example.com')
    assert not valid_external_url('javascript:alert(1)')

def test_admin_can_save_draft_and_publish_only_after_explicit_action():
    import re
    main.SESSIONS.clear(); client.cookies.clear()
    page=client.get('/studio/admin/login')
    csrf=re.search(r'name="csrf" value="([^"]+)"',page.text).group(1)
    client.post('/studio/admin/login',data={'username':'tester','password':'correct horse battery staple 123!','csrf':csrf},follow_redirects=False)
    dash=client.get('/studio/admin/')
    csrf=re.search(r'name="csrf" value="([^"]+)"',dash.text).group(1)
    saved=client.post('/studio/admin/posts/save',data={'csrf':csrf,'item_id':'0','title':'Private Draft','slug':'private-draft','summary':'Not public yet','body':'Draft body','tags':'test','published':'0'},follow_redirects=False)
    assert saved.status_code == 303
    assert client.get('/studio/blog/private-draft').status_code == 404
    with main.db() as c:
        post=c.execute("SELECT id FROM posts WHERE slug='private-draft'").fetchone()
        assert post
    dash=client.get('/studio/admin/')
    csrf=re.search(r'name="csrf" value="([^"]+)"',dash.text).group(1)
    client.post('/studio/admin/posts/save',data={'csrf':csrf,'item_id':str(post['id']),'title':'Private Draft','slug':'private-draft','summary':'Now public','body':'Published body','tags':'test','published':'1'},follow_redirects=False)
    assert client.get('/studio/blog/private-draft').status_code == 200

def test_backup_restore_validates_before_replacing_data():
    import re
    from io import BytesIO
    main.SESSIONS.clear(); client.cookies.clear()
    page=client.get('/studio/admin/login')
    csrf=re.search(r'name="csrf" value="([^"]+)"',page.text).group(1)
    client.post('/studio/admin/login',data={'username':'tester','password':'correct horse battery staple 123!','csrf':csrf},follow_redirects=False)
    dash=client.get('/studio/admin/')
    csrf=re.search(r'name="csrf" value="([^"]+)"',dash.text).group(1)
    backup=client.get('/studio/admin/export')
    assert backup.status_code == 200
    bad=client.post('/studio/admin/restore',data={'csrf':csrf},files={'backup_file':('bad.json',b'{bad json','application/json')},follow_redirects=False)
    assert bad.status_code == 400
    assert client.get('/studio/projects/garden-action').status_code == 200
    good=client.post('/studio/admin/restore',data={'csrf':csrf},files={'backup_file':('studio-backup.json',backup.content,'application/json')},follow_redirects=False)
    assert good.status_code == 303, good.text
    assert client.get('/studio/projects/garden-action').status_code == 200

def test_media_upload_rejects_fake_image_and_media_route_blocks_traversal():
    import re
    main.SESSIONS.clear(); client.cookies.clear()
    page=client.get('/studio/admin/login')
    csrf=re.search(r'name="csrf" value="([^"]+)"',page.text).group(1)
    client.post('/studio/admin/login',data={'username':'tester','password':'correct horse battery staple 123!','csrf':csrf},follow_redirects=False)
    page=client.get('/studio/admin/media')
    csrf=re.search(r'name="csrf" value="([^"]+)"',page.text).group(1)
    bad=client.post('/studio/admin/media/upload',data={'csrf':csrf},files={'media_file':('fake.png',b'not a png','image/png')},follow_redirects=False)
    assert bad.status_code == 400
    assert client.get('/studio/media/../../etc/passwd').status_code == 404

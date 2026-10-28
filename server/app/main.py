from __future__ import annotations
import json, os, secrets, sqlite3, time, re
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote
from fastapi import FastAPI, Request, Form, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.security import read_admin, verify_password, sign, unsign, safe_markdown, valid_external_url, INSTANCE
from app.weather_mod import weather as weather_service
from app.repo_routes import register_repo_routes
from app.newtab_api import register_newtab_api

ROOT = Path(__file__).resolve().parent
PREFIX = os.getenv("APP_PREFIX", "/studio").rstrip("/")
DB_PATH = Path(os.getenv("STUDIO_DB", str(INSTANCE / "studio.db")))
MEDIA_DIR = Path(os.getenv("STUDIO_MEDIA_DIR", str(INSTANCE / "media")))
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="个人数字工作室", docs_url=None, redoc_url=None, openapi_url=None)

app.mount(PREFIX + "/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=str(ROOT / "templates"))

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

# 浏览器扩展用的只读 JSON 接口（必须在 db 定义之后注册）
register_newtab_api(app, db, MEDIA_DIR, PREFIX)

def init_db():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS projects (
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, slug TEXT NOT NULL UNIQUE,
          summary TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '规划中',
          url TEXT NOT NULL DEFAULT '', repo_url TEXT NOT NULL DEFAULT '', featured INTEGER NOT NULL DEFAULT 0,
          sort_order INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS posts (
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, slug TEXT NOT NULL UNIQUE,
          summary TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '',
          published INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, published_at TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        if c.execute('SELECT COUNT(*) FROM projects').fetchone()[0] == 0:
            c.execute('''INSERT INTO projects(title,slug,summary,body,status,featured,sort_order)
              VALUES(?,?,?,?,?,?,?)''', ('Garden Action','garden-action','面向家庭园艺的种植决策助手。开发状态与公开链接待核实。','Garden Action 是面向家庭园艺初学者的种植决策助手。\n\n目前展示资料仅作为项目索引，开发状态与公开链接尚待核实。','待核实',1,10))
        defaults = {'site_title':'高冷的网友 208 · 个人数字工作室','signature':'高冷的网友 208','tagline':'个人数字工作室 · 独立项目与技术探索','about':'这里记录独立项目、技术实验与过程笔记。','contact':'','motion_intensity':'immersive','now':'## 正在做\n\n把个人数字工作室完善起来。'}
        for k,v in defaults.items(): c.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)',(k,v))
init_db()

# A short-lived signed cookie stores only an opaque session identifier and CSRF token.
SESSIONS: dict[str, dict] = {}
LOGIN_ATTEMPTS: dict[str, list[float]] = {}
SESSION_TTL = 8 * 60 * 60

def get_settings():
    with db() as c: return {r['key']:r['value'] for r in c.execute('SELECT key,value FROM settings')}
def base_context(request: Request, **extra):
    return {'request':request,'prefix':PREFIX,'settings':get_settings(),'admin_logged_in':is_admin(request),**extra}
def is_admin(request: Request):
    token = request.cookies.get('studio_session','')
    sid = unsign(token) if token else None
    session = SESSIONS.get(sid or '')
    if not session or session['expires'] < time.time():
        SESSIONS.pop(sid or '',None); return False
    return True
def current_session(request: Request):
    sid = unsign(request.cookies.get('studio_session',''))
    return SESSIONS.get(sid or '')
def require_admin(request: Request):
    if not is_admin(request): raise HTTPException(status_code=303, headers={'Location':PREFIX+'/admin/login'})
    return current_session(request)
def csrf_ok(request: Request, csrf: str):
    s = current_session(request)
    return bool(s and csrf and secrets.compare_digest(s['csrf'],csrf))
def render(request, name, **extra):
    return templates.TemplateResponse(request=request, name=name, context=base_context(request,**extra))
def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r'[^a-z0-9\u4e00-\u9fff]+','-',value).strip('-')
    return value[:80] or 'item'
def unique_slug(c, table, slug, exclude_id=None):
    base, candidate, i = slugify(slug), slugify(slug), 2
    while True:
        if exclude_id:
            found = c.execute(f'SELECT id FROM {table} WHERE slug=? AND id<>?',(candidate,exclude_id)).fetchone()
        else: found = c.execute(f'SELECT id FROM {table} WHERE slug=?',(candidate,)).fetchone()
        if not found: return candidate
        candidate=f'{base}-{i}'; i+=1

@app.get(PREFIX+'/', response_class=HTMLResponse)
def home(request: Request):
    with db() as c:
        projects=[dict(x) for x in c.execute('SELECT * FROM projects ORDER BY featured DESC,sort_order ASC,updated_at DESC LIMIT 4')]
        posts=[dict(x) for x in c.execute('SELECT * FROM posts WHERE published=1 ORDER BY published_at DESC,id DESC LIMIT 3')]
    # 天气：独立模块，失败降级为演示数据，并明确标注
    try:
        wx = weather_service.current()
    except Exception:
        wx = None
    # 轮播图：取媒体库最近上传的图片（真实文件，不伪造）
    shots = []
    try:
        if MEDIA_DIR.exists():
            files = sorted(
                (p for p in MEDIA_DIR.iterdir()
                 if p.is_file() and re.fullmatch(r'[a-f0-9]{32}\.(png|jpg|webp|gif)', p.name)),
                key=lambda p: p.stat().st_mtime, reverse=True
            )[:5]
            shots = [PREFIX + '/media/' + f.name for f in files]
    except OSError:
        shots = []
    return render(request,'home.html',projects=projects,posts=posts,weather=wx,shots=shots)

@app.get(PREFIX+'/projects', response_class=HTMLResponse)
def projects_page(request: Request):
    with db() as c: projects=[dict(x) for x in c.execute('SELECT * FROM projects ORDER BY featured DESC,sort_order ASC,updated_at DESC')]
    return render(request,'projects.html',projects=projects)
@app.get(PREFIX+'/projects/{slug}', response_class=HTMLResponse)
def project_detail(request: Request, slug: str):
    with db() as c: p=c.execute('SELECT * FROM projects WHERE slug=?',(slug,)).fetchone()
    if not p: raise HTTPException(404)
    return render(request,'project_detail.html',project=dict(p),body_html=safe_markdown(p['body']))
@app.get(PREFIX+'/blog', response_class=HTMLResponse)
def blog_page(request: Request):
    with db() as c: posts=[dict(x) for x in c.execute('SELECT * FROM posts WHERE published=1 ORDER BY published_at DESC,id DESC')]
    return render(request,'blog.html',posts=posts)
@app.get(PREFIX+'/blog/{slug}', response_class=HTMLResponse)
def blog_detail(request: Request, slug: str):
    with db() as c: p=c.execute('SELECT * FROM posts WHERE slug=? AND published=1',(slug,)).fetchone()
    if not p: raise HTTPException(404)
    return render(request,'post_detail.html',post=dict(p),body_html=safe_markdown(p['body']))
@app.get(PREFIX+'/now', response_class=HTMLResponse)
def now_page(request: Request):
    with db() as c: projects=[dict(x) for x in c.execute('SELECT * FROM projects ORDER BY updated_at DESC')]
    settings = get_settings()
    return render(request,'now.html',projects=projects,
                  now_html=safe_markdown(settings.get('now','')))
@app.get(PREFIX+'/about', response_class=HTMLResponse)
def about_page(request: Request):
    settings = get_settings()
    return render(request,'about.html',
                  about_html=safe_markdown(settings.get('about','')))

@app.get(PREFIX+'/admin/login', response_class=HTMLResponse)
def login_page(request: Request):
    s=current_session(request)
    if s: return RedirectResponse(PREFIX+'/admin/',303)
    csrf=secrets.token_urlsafe(24)
    response=render(request,'admin/login.html',csrf=csrf,error='')
    response.set_cookie('studio_login_csrf',sign(csrf),httponly=True,samesite='lax',secure=os.getenv('COOKIE_SECURE','0')=='1',max_age=600,path=PREFIX+'/')
    return response
@app.post(PREFIX+'/admin/login')
def login(request: Request, username: str=Form(...), password: str=Form(...), csrf: str=Form(...)):
    # Anonymous login form gets a one-use CSRF token via cookie, avoiding server-side state.
    anon = unsign(request.cookies.get('studio_login_csrf',''))
    if not anon or not secrets.compare_digest(anon,csrf):
        return RedirectResponse(PREFIX+'/admin/login?error=csrf',303)
    ip = request.client.host if request.client else 'unknown'; now=time.time()
    attempts=[t for t in LOGIN_ATTEMPTS.get(ip,[]) if now-t<300]
    if len(attempts)>=8:
        LOGIN_ATTEMPTS[ip]=attempts
        return RedirectResponse(PREFIX+'/admin/login?error=rate',303)
    record=read_admin()
    if not record or username != record.get('username') or not verify_password(password,record.get('password',{})):
        attempts.append(now); LOGIN_ATTEMPTS[ip]=attempts
        return RedirectResponse(PREFIX+'/admin/login?error=credentials',303)
    LOGIN_ATTEMPTS.pop(ip,None)
    sid=secrets.token_urlsafe(32); SESSIONS[sid]={'csrf':secrets.token_urlsafe(32),'expires':now+SESSION_TTL}
    response=RedirectResponse(PREFIX+'/admin/',303)
    response.set_cookie('studio_session',sign(sid),httponly=True,samesite='lax',secure=os.getenv('COOKIE_SECURE','0')=='1',max_age=SESSION_TTL,path=PREFIX+'/')
    response.delete_cookie('studio_login_csrf',path=PREFIX+'/')
    return response
@app.get(PREFIX+'/admin/', response_class=HTMLResponse)
def admin_home(request: Request):
    s=require_admin(request)
    with db() as c:
        projects=[dict(x) for x in c.execute('SELECT * FROM projects ORDER BY sort_order,id')]
        posts=[dict(x) for x in c.execute('SELECT * FROM posts ORDER BY updated_at DESC,id DESC')]
    return render(request,'admin/dashboard.html',csrf=s['csrf'],projects=projects,posts=posts)
@app.get(PREFIX+'/admin/logout')
def logout_get(request: Request): return RedirectResponse(PREFIX+'/admin/',303)
@app.post(PREFIX+'/admin/logout')
def logout(request: Request, csrf: str=Form(...)):
    s=require_admin(request)
    if not csrf_ok(request,csrf): raise HTTPException(403,'CSRF validation failed')
    sid=unsign(request.cookies.get('studio_session','')); SESSIONS.pop(sid or '',None)
    response=RedirectResponse(PREFIX+'/',303); response.delete_cookie('studio_session',path=PREFIX+'/'); return response

@app.get(PREFIX+'/admin/projects/new', response_class=HTMLResponse)
def project_new(request: Request):
    s=require_admin(request); return render(request,'admin/project_form.html',csrf=s['csrf'],item={},mode='new')
@app.get(PREFIX+'/admin/projects/{item_id}/edit', response_class=HTMLResponse)
def project_edit(request: Request,item_id:int):
    s=require_admin(request)
    with db() as c: item=c.execute('SELECT * FROM projects WHERE id=?',(item_id,)).fetchone()
    if not item: raise HTTPException(404)
    return render(request,'admin/project_form.html',csrf=s['csrf'],item=dict(item),mode='edit')
@app.post(PREFIX+'/admin/projects/save')
def project_save(request: Request, csrf:str=Form(...), item_id:int=Form(0), title:str=Form(...), slug:str=Form(''), summary:str=Form(''), body:str=Form(''), status:str=Form('规划中'), url:str=Form(''), repo_url:str=Form(''), featured:str=Form('0'), sort_order:int=Form(0)):
    s=require_admin(request)
    if not csrf_ok(request,csrf): raise HTTPException(403,'CSRF validation failed')
    if not title.strip() or len(title)>160 or not valid_external_url(url) or not valid_external_url(repo_url): raise HTTPException(400,'标题或链接无效')
    with db() as c:
        safe_slug=unique_slug(c,'projects',slug or title,item_id or None)
        vals=(title.strip(),safe_slug,summary[:500],body[:100000],status[:40],url.strip(),repo_url.strip(),1 if featured=='1' else 0,sort_order)
        if item_id:
            c.execute('''UPDATE projects SET title=?,slug=?,summary=?,body=?,status=?,url=?,repo_url=?,featured=?,sort_order=?,updated_at=CURRENT_TIMESTAMP WHERE id=?''',(*vals,item_id))
        else: c.execute('''INSERT INTO projects(title,slug,summary,body,status,url,repo_url,featured,sort_order) VALUES(?,?,?,?,?,?,?,?,?)''',vals)
    return RedirectResponse(PREFIX+'/admin/',303)
@app.post(PREFIX+'/admin/projects/{item_id}/delete')
def project_delete(request:Request,item_id:int,csrf:str=Form(...)):
    s=require_admin(request)
    if not csrf_ok(request,csrf): raise HTTPException(403,'CSRF validation failed')
    with db() as c:c.execute('DELETE FROM projects WHERE id=?',(item_id,))
    return RedirectResponse(PREFIX+'/admin/',303)

@app.get(PREFIX+'/admin/posts/new', response_class=HTMLResponse)
def post_new(request:Request):
    s=require_admin(request);return render(request,'admin/post_form.html',csrf=s['csrf'],item={},mode='new')
@app.get(PREFIX+'/admin/posts/{item_id}/edit', response_class=HTMLResponse)
def post_edit(request:Request,item_id:int):
    s=require_admin(request)
    with db() as c:item=c.execute('SELECT * FROM posts WHERE id=?',(item_id,)).fetchone()
    if not item:raise HTTPException(404)
    return render(request,'admin/post_form.html',csrf=s['csrf'],item=dict(item),mode='edit')
@app.post(PREFIX+'/admin/posts/save')
def post_save(request:Request,csrf:str=Form(...),item_id:int=Form(0),title:str=Form(...),slug:str=Form(''),summary:str=Form(''),body:str=Form(''),tags:str=Form(''),published:str=Form('0')):
    s=require_admin(request)
    if not csrf_ok(request,csrf):raise HTTPException(403,'CSRF validation failed')
    if not title.strip() or len(title)>160:raise HTTPException(400,'标题无效')
    with db() as c:
        safe_slug=unique_slug(c,'posts',slug or title,item_id or None); pub=1 if published=='1' else 0
        vals=(title.strip(),safe_slug,summary[:500],body[:100000],tags[:500],pub)
        if item_id:
            c.execute('''UPDATE posts SET title=?,slug=?,summary=?,body=?,tags=?,published=?,published_at=CASE WHEN ?=1 AND published_at IS NULL THEN CURRENT_TIMESTAMP WHEN ?=0 THEN NULL ELSE published_at END,updated_at=CURRENT_TIMESTAMP WHERE id=?''',(*vals,pub,pub,item_id))
        else:
            c.execute('''INSERT INTO posts(title,slug,summary,body,tags,published,published_at) VALUES(?,?,?,?,?,?,CASE WHEN ?=1 THEN CURRENT_TIMESTAMP ELSE NULL END)''',(*vals,pub))
    return RedirectResponse(PREFIX+'/admin/',303)
@app.post(PREFIX+'/admin/posts/{item_id}/delete')
def post_delete(request:Request,item_id:int,csrf:str=Form(...)):
    s=require_admin(request)
    if not csrf_ok(request,csrf):raise HTTPException(403,'CSRF validation failed')
    with db() as c:c.execute('DELETE FROM posts WHERE id=?',(item_id,))
    return RedirectResponse(PREFIX+'/admin/',303)
@app.get(PREFIX+'/admin/settings',response_class=HTMLResponse)
def settings_page(request:Request):
    s=require_admin(request);return render(request,'admin/settings.html',csrf=s['csrf'])
@app.post(PREFIX+'/admin/settings')
def settings_save(request:Request,csrf:str=Form(...),signature:str=Form(''),tagline:str=Form(''),about:str=Form(''),contact:str=Form(''),motion_intensity:str=Form('immersive')):
    s=require_admin(request)
    if not csrf_ok(request,csrf):raise HTTPException(403,'CSRF validation failed')
    if motion_intensity not in {'still','rich','immersive'}:motion_intensity='immersive'
    with db() as c:
        for k,v in {'signature':signature[:120],'tagline':tagline[:240],'about':about[:4000],'contact':contact[:300],'motion_intensity':motion_intensity}.items():
            c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,v))
    return RedirectResponse(PREFIX+'/admin/settings?saved=1',303)
@app.get(PREFIX+'/admin/export')
def export_data(request:Request):
    require_admin(request)
    with db() as c:
        data={t:[dict(r) for r in c.execute(f'SELECT * FROM {t}')] for t in ('projects','posts','settings')}
    return Response(json.dumps(data,ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':'attachment; filename="studio-backup.json"'})

@app.post(PREFIX+'/admin/restore')
async def restore_data(request:Request, csrf:str=Form(...), backup_file:UploadFile=File(...)):
    require_admin(request)
    if not csrf_ok(request,csrf): raise HTTPException(403,'CSRF validation failed')
    raw=await backup_file.read(5*1024*1024+1)
    if len(raw)>5*1024*1024: raise HTTPException(413,'备份文件不能超过 5 MiB')
    try: payload=json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError,json.JSONDecodeError): raise HTTPException(400,'备份不是有效的 UTF-8 JSON')
    if not isinstance(payload,dict) or any(k not in payload for k in ('projects','posts','settings')) or not all(isinstance(payload[k],list) for k in ('projects','posts','settings')):
        raise HTTPException(400,'备份格式不正确')
    project_fields={'id','title','slug','summary','body','status','url','repo_url','featured','sort_order','created_at','updated_at'}
    post_fields={'id','title','slug','summary','body','tags','published','created_at','updated_at','published_at'}
    def rows_ok(rows, fields, required):
        return len(rows)<=10000 and all(isinstance(r,dict) and required.issubset(r) and set(r).issubset(fields) for r in rows)
    if not rows_ok(payload['projects'],project_fields,{'title','slug'}) or not rows_ok(payload['posts'],post_fields,{'title','slug'}) or not rows_ok(payload['settings'],{'key','value'},{'key','value'}):
        raise HTTPException(400,'备份字段不符合当前版本格式')
    for p in payload['projects']:
        if (not isinstance(p.get('title'),str) or not isinstance(p.get('slug'),str) or len(p['title'])>160 or len(p['slug'])>80
            or any(not isinstance(p.get(k,''),str) for k in ('summary','body','status','url','repo_url','created_at','updated_at'))
            or len(p.get('summary',''))>500 or len(p.get('body',''))>100000
            or not valid_external_url(p.get('url','')) or not valid_external_url(p.get('repo_url',''))
            or not isinstance(p.get('sort_order',0),int) or p.get('featured',0) not in (0,1,False,True)):
            raise HTTPException(400,'项目数据验证失败')
    for p in payload['posts']:
        if (not isinstance(p.get('title'),str) or not isinstance(p.get('slug'),str) or len(p['title'])>160 or len(p['slug'])>80
            or any(not isinstance(p.get(k,''),str) for k in ('summary','body','tags','created_at','updated_at'))
            or len(p.get('summary',''))>500 or len(p.get('body',''))>100000 or len(p.get('tags',''))>500
            or p.get('published',0) not in (0,1,False,True)):
            raise HTTPException(400,'文章数据验证失败')
    if any(not isinstance(r.get('key'),str) or not isinstance(r.get('value'),str) or len(r['key'])>100 or len(r['value'])>10000 for r in payload['settings']):
        raise HTTPException(400,'站点设置数据验证失败')
    try:
        with db() as c:
            c.execute('DELETE FROM projects'); c.execute('DELETE FROM posts'); c.execute('DELETE FROM settings')
            for p in payload['projects']:
                c.execute('INSERT INTO projects(title,slug,summary,body,status,url,repo_url,featured,sort_order,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (p['title'],p['slug'],p.get('summary',''),p.get('body',''),p.get('status','规划中'),p.get('url',''),p.get('repo_url',''),int(bool(p.get('featured',0))),int(p.get('sort_order',0)),p.get('created_at') or 'CURRENT_TIMESTAMP',p.get('updated_at') or 'CURRENT_TIMESTAMP'))
            for p in payload['posts']:
                c.execute('INSERT INTO posts(title,slug,summary,body,tags,published,created_at,updated_at,published_at) VALUES(?,?,?,?,?,?,?,?,?)',
                    (p['title'],p['slug'],p.get('summary',''),p.get('body',''),p.get('tags',''),int(bool(p.get('published',0))),p.get('created_at') or 'CURRENT_TIMESTAMP',p.get('updated_at') or 'CURRENT_TIMESTAMP',p.get('published_at')))
            for r in payload['settings']:
                c.execute('INSERT INTO settings(key,value) VALUES(?,?)',(r['key'],r['value']))
    except sqlite3.IntegrityError:
        raise HTTPException(400,'备份包含重复 slug 或其他冲突，数据未完成恢复')
    return RedirectResponse(PREFIX+'/admin/settings?restored=1',303)

@app.get(PREFIX+'/admin/media',response_class=HTMLResponse)
def media_page(request:Request):
    s=require_admin(request); files=[]
    for path in sorted(MEDIA_DIR.iterdir(),key=lambda p:p.stat().st_mtime,reverse=True) if MEDIA_DIR.exists() else []:
        if path.is_file() and re.fullmatch(r'[a-f0-9]{32}\.(png|jpg|webp|gif)',path.name):
            files.append({'name':path.name,'size':path.stat().st_size,'url':PREFIX+'/media/'+path.name})
    return render(request,'admin/media.html',csrf=s['csrf'],files=files)

@app.post(PREFIX+'/admin/media/upload')
async def media_upload(request:Request,csrf:str=Form(...),media_file:UploadFile=File(...)):
    require_admin(request)
    if not csrf_ok(request,csrf): raise HTTPException(403,'CSRF validation failed')
    raw=await media_file.read(5*1024*1024+1)
    if not raw: raise HTTPException(400,'文件为空')
    if len(raw)>5*1024*1024: raise HTTPException(413,'图片不能超过 5 MiB')
    if raw.startswith(b'\x89PNG\r\n\x1a\n'): ext='png'; mime='image/png'
    elif raw.startswith(b'\xff\xd8\xff'): ext='jpg'; mime='image/jpeg'
    elif len(raw)>=12 and raw[:4]==b'RIFF' and raw[8:12]==b'WEBP': ext='webp'; mime='image/webp'
    elif raw.startswith((b'GIF87a',b'GIF89a')): ext='gif'; mime='image/gif'
    else: raise HTTPException(400,'只支持有效的 PNG、JPEG、WebP 或 GIF 图片')
    name=secrets.token_hex(16)+'.'+ext
    MEDIA_DIR.mkdir(parents=True,exist_ok=True); (MEDIA_DIR/name).write_bytes(raw)
    return RedirectResponse(PREFIX+'/admin/media?uploaded=1',303)

@app.get(PREFIX+'/media/{filename}')
def media_file(filename:str):
    if not re.fullmatch(r'[a-f0-9]{32}\.(png|jpg|webp|gif)',filename): raise HTTPException(404)
    path=MEDIA_DIR/filename
    if not path.is_file(): raise HTTPException(404)
    mime={'png':'image/png','jpg':'image/jpeg','webp':'image/webp','gif':'image/gif'}[filename.rsplit('.',1)[1]]
    return FileResponse(path,media_type=mime,headers={'X-Content-Type-Options':'nosniff','Content-Disposition':'inline'})

@app.get(PREFIX+'/feed.xml')
def feed():
    with db() as c:posts=[dict(x) for x in c.execute('SELECT * FROM posts WHERE published=1 ORDER BY published_at DESC LIMIT 30')]
    base=os.getenv('PUBLIC_BASE_URL','http://localhost:8000').rstrip('/')+PREFIX
    entries=''.join(f'<item><title>{xml_escape(p["title"])}</title><link>{base}/blog/{quote(p["slug"])}</link><description>{xml_escape(p["summary"])}</description><pubDate>{xml_escape(p["published_at"] or p["created_at"])}</pubDate></item>' for p in posts)
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>个人数字工作室</title><link>{base}/</link><description>独立项目与技术探索</description>{entries}</channel></rss>',media_type='application/rss+xml; charset=utf-8')
def xml_escape(v):
    import html
    return html.escape(str(v or ''),quote=True)
@app.get(PREFIX+'/sitemap.xml')
def sitemap():
    base=os.getenv('PUBLIC_BASE_URL','http://localhost:8000').rstrip('/')+PREFIX
    urls=['/','/projects','/blog','/now','/about']
    with db() as c:
        urls += ['/projects/'+r['slug'] for r in c.execute('SELECT slug FROM projects')]
        urls += ['/blog/'+r['slug'] for r in c.execute('SELECT slug FROM posts WHERE published=1')]
    xml=''.join(f'<url><loc>{xml_escape(base+u)}</loc></url>' for u in urls)
    return Response('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+xml+'</urlset>',media_type='application/xml')
@app.get(PREFIX+'/robots.txt')
def robots():return PlainTextResponse(f'User-agent: *\nAllow: {PREFIX}/\nDisallow: {PREFIX}/admin/\nSitemap: {os.getenv("PUBLIC_BASE_URL","http://localhost:8000").rstrip("/")}{PREFIX}/sitemap.xml\n')

@app.middleware('http')
async def security_headers(request:Request,call_next):
    response=await call_next(request)
    response.headers.setdefault('X-Content-Type-Options','nosniff')
    response.headers.setdefault('Referrer-Policy','strict-origin-when-cross-origin')
    response.headers.setdefault('X-Frame-Options','DENY')
    response.headers.setdefault('Permissions-Policy','camera=(), microphone=(), geolocation=()')
    response.headers.setdefault('Content-Security-Policy',"default-src 'self' https://cdnjs.cloudflare.com; script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
    return response

@app.exception_handler(404)
async def not_found(request:Request,exc):
    return templates.TemplateResponse(request=request,name='404.html',context=base_context(request),status_code=404)

# 仓库同步路由（必须在 render / require_admin / csrf_ok 定义之后注册）
register_repo_routes(app, db, render, require_admin, csrf_ok, PREFIX)

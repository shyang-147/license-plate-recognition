#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''单文件版车牌识别网站 (lpr_web portable)

浏览器上传车牌照片 -> 本机 MATLAB 实时识别 -> 网页显示车牌号/逐字符置信度/校正后的车牌图.
这一个文件里打包了: 网页 + MATLAB 识别内核 + worker, 运行时自动释放到临时目录,
所以拷这一个文件到任何地方都能跑, 不会出现"少了个 .py / .m"的问题.

用法:
    1. 双击同目录的 start_lan.bat     局域网访问: 手机/其他电脑连同一个 WiFi 就能打开
    2. 双击同目录的 start_public.bat  公网访问: 自动开 cloudflared 隧道(带访问口令), 4G 也能打开
    3. 或者命令行: python lpr_server.py

环境变量:
    LPR_HOST        绑定地址, 默认 0.0.0.0(允许其他设备访问); 设 127.0.0.1 则只允许本机
    LPR_PORT        端口, 默认 8765
    LPR_TOKEN       访问口令, 设了以后打开页面要输入口令(公网使用强烈建议设置)
    LPR_PUBLIC      1 = 启动时自动开 cloudflared 公网隧道(没有 cloudflared 会自动下载)
    LPR_MATLAB      指定 matlab.exe 路径, 默认从 PATH 里找
    LPR_NO_BROWSER  1 = 启动后不自动打开浏览器

识别引擎第一次启动要 10~20 秒(在起 MATLAB), 之后每张 0.2~0.5 秒.
'''

import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ============================ 内嵌资源 ============================
PKG_ID = '@PKG_ID@'
PAYLOAD_B64 = @PAYLOAD@

# ============================ 配置 ============================
HOST      = os.environ.get('LPR_HOST', '0.0.0.0')
PORT      = int(os.environ.get('LPR_PORT', '8765'))
TOKEN     = os.environ.get('LPR_TOKEN', '').strip()
PUBLIC    = os.environ.get('LPR_PUBLIC', '') == '1'
MATLAB    = os.environ.get('LPR_MATLAB') or shutil.which('matlab') or ''
MAX_BYTES = 20 * 1024 * 1024        # 单张图片上限 20MB
TIMEOUT   = 120                     # 等 MATLAB 出结果的最长时间(秒)
ALLOWED   = {'.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff'}
HISTORY   = []
HIST_MAX  = 20

STARTUP_GRACE   = 90                # worker 预热宽限期: 期间绝不重复拉起 MATLAB
LAUNCH_COOLDOWN = 15                # 两次拉起的最小间隔, 防止崩溃时开出几十个 MATLAB

_worker        = None
_last_launch   = 0.0
_launch_count  = 0
_shutting_down = False      # 关机中: 请求线程不许再拉起新的 MATLAB
_lock          = threading.Lock()
_tunnel       = None

BASE = os.path.dirname(os.path.abspath(__file__))


# ============================ 释放内嵌资源 ============================
def extract_payload():
    '''把打包进来的网页/MATLAB 内核/worker 释放到临时目录'''
    root = os.path.join(tempfile.gettempdir(), 'lpr_runtime_' + PKG_ID)
    if os.path.isfile(os.path.join(root, '.extracted')):
        return root
    for name in list(PAYLOAD_B64):
        p = os.path.join(root, name.replace('/', os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'wb') as f:
            f.write(base64.b64decode(PAYLOAD_B64[name]))
    with open(os.path.join(root, '.extracted'), 'w', encoding='utf-8') as f:
        f.write(PKG_ID)
    return root


ROOT     = extract_payload()
CORE_DIR = os.path.join(ROOT, 'core')
JOBS     = os.path.join(ROOT, 'jobs')
IN_DIR   = os.path.join(JOBS, 'incoming')
RES_DIR  = os.path.join(JOBS, 'results')
STATUS_F = os.path.join(JOBS, 'status.json')
STOP_F   = os.path.join(JOBS, 'stop')
LOG_F    = os.path.join(JOBS, 'worker.log')
WORKER_M = os.path.join(ROOT, 'worker', 'worker_lpr.m')
PAGE_HTML = os.path.join(ROOT, 'static', 'index.html')
DEMO_JPG  = os.path.join(ROOT, 'static', 'demo.jpg')


def local_ips():
    '''列出本机在局域网里的 IPv4 地址(只做路由查询, 不真的联网)'''
    ips = set()
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.connect(('8.8.8.8', 80))
        ips.add(sk.getsockname()[0])
        sk.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    return sorted(i for i in ips if not i.startswith('127.'))

# ============================ MATLAB worker 管理 ============================
def worker_state():
    '''返回 (state, job, lastText, 心跳年龄秒)'''
    try:
        with open(STATUS_F, 'r', encoding='utf-8') as f:
            st = json.load(f)
        age = time.time() - os.path.getmtime(STATUS_F)   # 用文件时间算心跳, 避免时钟/时区不一致
        if age > 8:
            return 'off', '', '', age
        return st.get('state', 'off'), st.get('job', ''), st.get('lastText', ''), age
    except Exception:
        return 'off', '', '', 1e9


def _kill_worker():
    global _worker
    if _worker is not None and _worker.poll() is None:
        try:
            _worker.terminate()
        except Exception:
            pass
        try:
            _worker.wait(timeout=6)
        except Exception:
            try:
                _worker.kill()
            except Exception:
                pass
    _worker = None


def _launch_worker():
    global _worker, _last_launch, _launch_count
    os.makedirs(JOBS, exist_ok=True)
    for d in (IN_DIR, RES_DIR):
        os.makedirs(d, exist_ok=True)
    if os.path.exists(STOP_F):
        try:
            os.remove(STOP_F)
        except OSError:
            pass
    q = lambda p: p.replace("'", "''")
    code = "addpath('%s'); worker_lpr('%s','%s')" % (
        q(os.path.dirname(WORKER_M)), q(JOBS), q(CORE_DIR))
    cmd = [MATLAB, '-batch', code]
    log = open(LOG_F, 'ab')
    log.write(('\n==== %s 启动 worker ====\n' % time.strftime('%Y-%m-%d %H:%M:%S')).encode('utf-8'))
    log.flush()
    _worker = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    _last_launch = time.time()
    _launch_count += 1
    print('[server] 已启动 MATLAB worker (pid=%s, 本进程第 %d 次), 预热约 10 秒...'
          % (_worker.pid, _launch_count))


def ensure_worker(force=False):
    '''确保 MATLAB worker 在跑; 正在预热时不会被重复拉起'''
    if _shutting_down:
        return              # 已经在关机了, 别再开新的 MATLAB, 否则会留下杀不掉的孤儿进程
    state, _, _, _ = worker_state()
    if state in ('idle', 'busy') and not force:
        return
    with _lock:
        state, _, _, _ = worker_state()
        if state in ('idle', 'busy') and not force:
            return
        if _worker is not None and _worker.poll() is None:
            alive_for = time.time() - _last_launch
            if alive_for < STARTUP_GRACE:
                return                  # 正在预热: 耐心等, 绝不另开一个 MATLAB
            if not force:
                return
            print('[server] worker 启动 %.0f 秒仍无心跳, 判定卡死, 重启' % alive_for)
            _kill_worker()
        if _last_launch and time.time() - _last_launch < LAUNCH_COOLDOWN:
            return                      # 刚拉起来就死了: 冷却期内不再重复拉
        if not MATLAB:
            raise RuntimeError('找不到 matlab 可执行文件, 请设置环境变量 LPR_MATLAB')
        _launch_worker()


def begin_shutdown(httpd):
    '''收到关机请求: 先立标志(挡住请求线程重启 MATLAB), 再停 HTTP 服务'''
    global _shutting_down
    _shutting_down = True
    threading.Thread(target=httpd.shutdown, daemon=True).start()


def stop_worker(wait=12):
    '''关掉 worker(含上一次 server 留下的孤儿 worker)'''
    global _last_launch
    try:
        with open(STOP_F, 'w', encoding='utf-8') as f:
            f.write('stop')
    except Exception:
        pass
    _kill_worker()
    deadline = time.time() + wait
    while time.time() < deadline:
        state, _, _, age = worker_state()
        if state == 'off' or age > 8:
            break
        time.sleep(0.3)
    try:
        if os.path.exists(STOP_F):
            os.remove(STOP_F)
    except Exception:
        pass
    _last_launch = 0.0


def recognize(data, filename):
    os.makedirs(IN_DIR, exist_ok=True)
    os.makedirs(RES_DIR, exist_ok=True)
    ensure_worker()

    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED:
        ext = '.jpg'
    jid = uuid.uuid4().hex[:12]

    part = os.path.join(IN_DIR, jid + ext + '.part')
    with open(part, 'wb') as f:                       # 先写 .part 再改名, 避免 worker 读到半个文件
        f.write(data)
    os.replace(part, os.path.join(IN_DIR, jid + ext))

    res_path = os.path.join(RES_DIR, jid + '.json')
    deadline = time.time() + TIMEOUT
    t_start = time.time()
    warned = False
    while time.time() < deadline:
        if os.path.exists(res_path):
            for _ in range(30):
                try:
                    with open(res_path, 'r', encoding='utf-8') as f:
                        res = json.load(f)
                    break
                except Exception:
                    res = None
                    time.sleep(0.1)
            try:
                os.remove(res_path)
            except Exception:
                pass
            if res is None:
                raise RuntimeError('读取识别结果失败')
            res['file'] = filename
            res['jobId'] = jid
            res['total'] = round(time.time() - t_start, 3)
            HISTORY.insert(0, {'time': time.strftime('%H:%M:%S'), 'file': filename,
                               'text': res.get('text', ''), 'ok': bool(res.get('ok')),
                               'thumbs': res.get('plateImagePng', '')})
            del HISTORY[HIST_MAX:]
            return res
        state, _, _, _ = worker_state()
        if state == 'off':
            if not warned:
                print('[server] worker 未就绪或掉线, 正在拉起...')
                warned = True
            ensure_worker()
        time.sleep(0.1)
    raise TimeoutError('识别超时(%.0f 秒)。首次识别需要启动 MATLAB, 请重试' % TIMEOUT)

# ============================ HTTP 服务 ============================
LOGIN_HTML = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>车牌识别 · 需要访问口令</title><style>
body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
background:#16181d;color:#e6e8ee;font:14px/1.6 -apple-system,"Microsoft YaHei",sans-serif}
.box{background:#1e2129;border:1px solid #2c303a;border-radius:14px;padding:28px;width:min(340px,86vw)}
h1{font-size:16px;margin:0 0 6px}p{margin:0 0 16px;color:#8b93a7;font-size:13px}
input{width:100%;box-sizing:border-box;padding:10px 12px;border-radius:8px;border:1px solid #333846;
background:#14161b;color:#e6e8ee;font-size:14px}
button{margin-top:12px;width:100%;padding:10px;border:0;border-radius:8px;background:#3b6ef6;
color:#fff;font-size:14px;cursor:pointer}
.err{color:#ffb4b0;font-size:13px;margin-bottom:10px}
</style></head><body><div class="box">
<h1>车牌识别 · 需要访问口令</h1><p>这是一个私人服务，请输入口令后使用。</p>
@ERR@
<form method="get" action="/">
<input name="token" type="password" placeholder="访问口令" autofocus><button>进入</button>
</form></div></body></html>'''


class Handler(BaseHTTPRequestHandler):
    server_version = 'lpr-portable/1.0'

    def log_message(self, fmt, *args):
        pass

    def auth_ok(self, query=''):
        '''没设口令就全部放行; 设了口令要 cookie 或 ?token= 匹配'''
        if not TOKEN:
            return True
        if ('lpr_token=' + TOKEN) in (self.headers.get('Cookie') or ''):
            return True
        return urllib.parse.parse_qs(query).get('token', [''])[0] == TOKEN

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, ctype, extra=None):
        with open(path, 'rb') as f:
            body = f.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_redirect(self, to, extra=None):
        self.send_response(302)
        self.send_header('Location', to)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def send_login(self, err):
        body = LOGIN_HTML.replace('@ERR@', '<div class="err">%s</div>' % err if err else '')
        body = body.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------- GET ----------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, parsed.query
        try:
            if path in ('/', '/index.html'):
                if not self.auth_ok(query):
                    bad = urllib.parse.parse_qs(query).get('token')
                    return self.send_login('口令不正确，请重试' if bad else '')
                if urllib.parse.parse_qs(query).get('token'):
                    # 口令对了就把口令存进 cookie 并跳转, 免得一直挂在地址栏
                    return self.send_redirect('/', {'Set-Cookie':
                            'lpr_token=%s; Path=/; Max-Age=604800' % TOKEN})
                return self.send_file(PAGE_HTML, 'text/html; charset=utf-8')

            if not self.auth_ok(query):
                return self.send_json({'error': '需要访问口令'}, 401)

            if path == '/static/demo.jpg':
                return self.send_file(DEMO_JPG, 'image/jpeg')
            if path == '/api/status':
                state, job, last, age = worker_state()
                return self.send_json({'worker': state, 'job': job, 'lastText': last,
                                       'age': round(age, 1), 'matlab': MATLAB,
                                       'launches': _launch_count, 'host': HOST,
                                       'port': PORT, 'token': bool(TOKEN),
                                       'core': CORE_DIR})
            if path == '/api/history':
                return self.send_json({'items': HISTORY})
            if path == '/api/shutdown':
                self.send_json({'bye': True})
                begin_shutdown(self.server)
                return
            if path == '/favicon.ico':
                self.send_response(204)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            return self.send_json({'error': 'not found'}, 404)
        except Exception as e:
            return self.send_json({'error': str(e)}, 500)

    # ---------- POST ----------
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not self.auth_ok():
            return self.send_json({'ok': False, 'error': '需要访问口令'}, 401)
        if path == '/api/stop':
            stop_worker()
            return self.send_json({'ok': True})
        if path != '/api/recognize':
            return self.send_json({'error': 'not found'}, 404)

        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return self.send_json({'ok': False, 'error': '没有收到图片数据'}, 400)
        if n > MAX_BYTES:
            return self.send_json({'ok': False, 'error': '图片太大(上限 20MB)'}, 413)
        data = b''
        remaining = n
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 1 << 20))
            if not chunk:
                break
            data += chunk
            remaining -= len(chunk)
        if not data:
            return self.send_json({'ok': False, 'error': '读取上传数据失败'}, 400)

        filename = self.headers.get('X-Filename', 'upload.jpg')
        try:
            return self.send_json(recognize(data, filename))
        except Exception as e:
            return self.send_json({'ok': False, 'error': str(e)}, 500)

# ============================ 公网隧道 (可选) ============================
CF_EXE  = 'cloudflared-windows-amd64.exe' if os.name == 'nt' else 'cloudflared-linux-amd64'
CF_URL  = 'https://github.com/cloudflare/cloudflared/releases/latest/download/' + CF_EXE
CF_NAME = 'cloudflared.exe' if os.name == 'nt' else 'cloudflared'
CF_MIN  = 20 * 1024 * 1024      # 真正的 cloudflared 有 40MB+; 比这小的一律当成没下完的残file删掉


def _ok_cf(path):
    '''文件存在而且大小像个真 cloudflared(半截文件运行会报 WinError 193)'''
    try:
        return os.path.isfile(path) and os.path.getsize(path) >= CF_MIN
    except OSError:
        return False


def find_cloudflared():
    env = os.environ.get('LPR_CD', '').strip()      # 也可以自己指定路径
    if _ok_cf(env):
        return env
    p = shutil.which('cloudflared')
    if p:
        return p
    for c in (os.path.join(BASE, CF_NAME), os.path.join(ROOT, CF_NAME)):
        if _ok_cf(c):
            return c
        if os.path.isfile(c):                       # 上次下载被中断留下的残file, 删掉重下
            print('[tunnel] 发现不完整的 cloudflared (%.1f MB), 已删除, 稍后重新下载'
                  % (os.path.getsize(c) / 1e6))
            try:
                os.remove(c)
            except OSError:
                pass
    return None


def download_cloudflared():
    '''下载 cloudflared(约 40MB)。网络太慢或下不动时, 给出手动放置的办法'''
    dst = os.path.join(ROOT, CF_NAME)
    print('[tunnel] 没找到 cloudflared, 开始下载(约 40MB, 只下一次)...')
    print('[tunnel] 也可以自己下载好放到 lpr_server.py 同目录, 或设置 LPR_CD=<路径>')
    got = 0
    try:
        import urllib.request
        req = urllib.request.Request(CF_URL, headers={'User-Agent': 'lpr-portable'})
        with urllib.request.urlopen(req, timeout=30) as r, open(dst, 'wb') as f:
            total = int(r.headers.get('Content-Length') or 0)
            mark = 0
            while True:                     # socket 超时 30 秒, 卡住会抛异常而不是一直等
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if got - mark >= 5 * 1024 * 1024:
                    mark = got
                    print('[tunnel] 已下载 %.1f MB%s'
                          % (got / 1e6, (' / %.1f MB' % (total / 1e6)) if total else ''))
        if got < 5 * 1024 * 1024:
            raise IOError('下载到的文件太小(%d 字节), 可能被网络拦截' % got)
        if os.name != 'nt':
            os.chmod(dst, 0o755)
        print('[tunnel] 下载完成: %s' % dst)
        return dst
    except Exception as e:
        print('[tunnel] 下载失败: %s' % e)
        print('[tunnel] 公网模式先跳过(局域网模式照常可用)。想用公网模式, 手动下载 cloudflared:')
        print('         下载地址: %s' % CF_URL)
        print('         放到这里: %s' % dst)
        print('         或者执行: winget install --id Cloudflare.cloudflared')
        try:
            if os.path.exists(dst):
                os.remove(dst)              # 删掉半截文件, 免得下次被当成完整的
        except Exception:
            pass
        return None


def start_public_tunnel():
    '''用 cloudflared 快速隧道拿一个临时公网地址(https://xxx.trycloudflare.com)'''
    global _tunnel
    exe = find_cloudflared() or download_cloudflared()
    if not exe:
        return None
    try:
        _tunnel = subprocess.Popen(
            [exe, 'tunnel', '--url', 'http://127.0.0.1:%d' % PORT, '--no-autoupdate'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception as e:
        print('[tunnel] 启动失败: %s' % e)
        if os.path.join(BASE, CF_NAME) in exe or os.path.join(ROOT, CF_NAME) in exe:
            print('[tunnel] 本地 cloudflared 可能是坏的/没下完, 已删除, 请重试或手动下载')
            try:
                os.remove(exe)
            except OSError:
                pass
        return None

    box = {'url': None}
    pat = re.compile(rb'https://[a-zA-Z0-9-]+\.trycloudflare\.com')

    def reader():
        for line in iter(_tunnel.stdout.readline, b''):
            if box['url'] is None:
                m = pat.search(line)
                if m:
                    box['url'] = m.group(0).decode()
    threading.Thread(target=reader, daemon=True).start()

    deadline = time.time() + 45
    while time.time() < deadline and box['url'] is None and _tunnel.poll() is None:
        time.sleep(0.3)
    return box['url']


# ============================ 启动 ============================
def main():
    print('=' * 64)
    print(' 单文件版车牌识别网站')
    print('   本机地址 : http://127.0.0.1:%d' % PORT)
    if HOST not in ('127.0.0.1', 'localhost'):
        for ip in local_ips():
            print('   局域网   : http://%s:%d' % (ip, PORT))
        print('   ^ 手机/其他电脑连同一个 WiFi, 浏览器里输入上面这个地址')
    else:
        print('   局域网   : 已关闭(只允许本机, 想开放设 LPR_HOST=0.0.0.0)')
    if TOKEN:
        print('   访问口令 : %s   (打开页面后输入这个口令)' % TOKEN)
    print('   MATLAB   : %s' % (MATLAB or '未找到! 请设置环境变量 LPR_MATLAB'))
    print('   运行时   : %s' % ROOT)
    print('   停止服务 : 在本窗口按 Ctrl+C')
    print('=' * 64)
    if not MATLAB:
        print('[警告] 找不到 matlab 可执行文件, 识别会失败。')

    try:
        httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        print('[错误] 端口 %d 起不来: %s' % (PORT, e))
        print('       可能是上一个服务还没关掉, 换个端口: set LPR_PORT=8766')
        return 1

    if PUBLIC:
        url = start_public_tunnel()
        if url:
            print('   公网地址 : %s%s' % (url, ('?token=' + TOKEN) if TOKEN else ''))
            print('   ^ 这个地址任何网络(含手机 4G)都能打开; 关掉本窗口即失效')
        else:
            print('[tunnel] 公网隧道没起来, 继续用局域网地址访问')

    print('[server] 正在预热 MATLAB 识别引擎(约 10 秒, 之后每张 0.2~0.5 秒)...')
    try:
        ensure_worker()
    except Exception as e:
        print('[server] 预热失败: %s' % e)

    if os.environ.get('LPR_NO_BROWSER') != '1':
        try:
            threading.Timer(1.0, lambda: webbrowser.open('http://127.0.0.1:%d' % PORT)).start()
        except Exception:
            pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\n[server] 收到 Ctrl+C, 正在退出...')
    finally:
        _shutting_down = True
        stop_worker()
        if _tunnel is not None and _tunnel.poll() is None:
            try:
                _tunnel.terminate()
            except Exception:
                pass
        httpd.server_close()
        print('[server] 已停止')
    return 0


if __name__ == '__main__':
    sys.exit(main())
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
车牌识别网站后端(License Plate Recognition)（只用 Python 标准库，不需要 pip install 任何东西）

架构:
    浏览器 ──(原始图片字节 POST /api/recognize)──> 本服务
                                                    │ 原子写入 jobs/incoming/<id>.jpg
                                                    │ 轮询 jobs/results/<id>.json
    MATLAB 常驻 worker（matlab -batch worker_lpr）──┘ 调 lpr_main 识别并写回 JSON

为什么这么设计:
    MATLAB 没有内置 HTTP 服务；MATLAB Engine for Python 对 Python 版本有严格限制。
    用"文件队列 + 常驻 worker"可以让识别进程只启动一次（约 10 秒），
    之后每张图 0.2~0.3 秒，且不依赖任何第三方包。

启动:  python server.py            →  http://127.0.0.1:8765
停止:  在本窗口按 Ctrl+C（会自动关掉 MATLAB worker）
环境变量:
    LPR_PORT=8765            监听端口
    LPR_HOST=0.0.0.0        绑定地址(默认 127.0.0.1 只允许本机;
                            改成 0.0.0.0 后同一局域网内的手机/其他电脑也能打开)
    LPR_MATLAB=D:\\MATLAB\\R2024b\\bin\\matlab.exe   指定 matlab 可执行文件
    LPR_NO_BROWSER=1         启动后不自动打开浏览器
"""

import atexit
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE     = os.path.dirname(os.path.abspath(__file__))


def project_dir():
    """MATLAB 识别内核所在目录（兼容 matlab/ 与 lpr_matlab/ 两种命名）"""
    for name in ('matlab', 'lpr_matlab'):
        p = os.path.join(BASE, '..', name)
        if os.path.isfile(os.path.join(p, 'lpr_main.m')):
            return os.path.abspath(p)
    return os.path.abspath(os.path.join(BASE, '..', 'matlab'))
JOBS     = os.path.join(BASE, 'jobs')
IN_DIR   = os.path.join(JOBS, 'incoming')
RES_DIR  = os.path.join(JOBS, 'results')
STATUS_F = os.path.join(JOBS, 'status.json')
STOP_F   = os.path.join(JOBS, 'stop')
LOG_F    = os.path.join(JOBS, 'worker.log')
STATIC   = os.path.join(BASE, 'static')
UPLOADS  = os.path.join(BASE, 'uploads')

HOST      = os.environ.get('LPR_HOST', '127.0.0.1')   # 0.0.0.0 = 允许局域网访问
PORT      = int(os.environ.get('LPR_PORT', '8765'))
MAX_BYTES = 20 * 1024 * 1024        # 单张图片上限 20MB
TIMEOUT   = 120                     # 等 MATLAB 出结果的最长时间(秒), 首次含引擎启动
ALLOWED   = {'.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff'}
HISTORY   = []
HIST_MAX  = 20

MATLAB = os.environ.get('LPR_MATLAB') or shutil.which('matlab') or ''

# MATLAB 启动要 10~20 秒，这段时间 worker 还没写心跳(状态是 off)。
# 如果一看到 off 就再拉一个，请求循环会在一秒内开出几十个 MATLAB 把内存吃光，
# 所以这里加两条硬约束: 启动宽限期 + 拉起冷却时间。
STARTUP_GRACE   = 90        # 秒: 进程还活着就耐心等这么久, 期间绝不重复拉起
LAUNCH_COOLDOWN = 15        # 秒: 两次拉起之间的最小间隔(防止崩溃后反复开 MATLAB)

_worker        = None
_last_launch   = 0.0
_launch_count  = 0
_shutting_down = False      # 关机中: 请求线程不许再拉起新的 MATLAB
_lock          = threading.Lock()


def local_ips():
    """列出本机在局域网里的 IPv4 地址(不联网, 只做路由查询)"""
    ips = set()
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.connect(('8.8.8.8', 80))          # UDP 不会真的发包
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


# --------------------------------------------------------------------------
# MATLAB worker 管理
# --------------------------------------------------------------------------
def worker_state():
    """返回 (state, job, lastText, seconds_since_beat)"""
    try:
        with open(STATUS_F, 'r', encoding='utf-8') as f:
            st = json.load(f)
        # 用文件修改时间算心跳, 避免 MATLAB 与本服务时钟/时区不一致
        age = time.time() - os.path.getmtime(STATUS_F)
        if age > 8:                       # 心跳超过 8 秒视为挂了
            return 'off', '', '', age
        return st.get('state', 'off'), st.get('job', ''), st.get('lastText', ''), age
    except Exception:
        return 'off', '', '', 1e9


def _kill_worker():
    """收掉当前的 MATLAB worker 进程"""
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
    """真正拉起一个 MATLAB worker（只在 ensure_worker 里被调用）"""
    global _worker, _last_launch, _launch_count
    os.makedirs(JOBS, exist_ok=True)
    for d in (IN_DIR, RES_DIR, UPLOADS):
        os.makedirs(d, exist_ok=True)
    if os.path.exists(STOP_F):
        try:
            os.remove(STOP_F)
        except OSError:
            pass
    cmd = [MATLAB, '-batch', "cd('%s'); worker_lpr" % BASE.replace("'", "''")]
    log = open(LOG_F, 'ab')
    log.write(('\n==== %s 启动 worker: %s ====\n'
               % (time.strftime('%Y-%m-%d %H:%M:%S'), ' '.join(cmd))).encode('utf-8'))
    log.flush()
    _worker = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    _last_launch = time.time()
    _launch_count += 1
    print('[server] 已启动 MATLAB worker (pid=%s, 本进程第 %d 次)，预热约 10 秒…'
          % (_worker.pid, _launch_count))


def ensure_worker(force=False):
    """确保 MATLAB worker 在跑；正在预热时不会被重复拉起"""
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
                return                  # 正在预热: 耐心等心跳, 不要另开一个 MATLAB
            if not force:
                return                  # 交给调用方继续等或超时
            print('[server] worker 启动 %.0f 秒仍无心跳，判定卡死，重启' % alive_for)
            _kill_worker()

        if _last_launch and time.time() - _last_launch < LAUNCH_COOLDOWN:
            return                      # 刚拉起来的进程已死: 冷却期内不再重复拉
        if not MATLAB:
            raise RuntimeError('未找到 matlab 可执行文件，请设置环境变量 LPR_MATLAB')
        _launch_worker()


def begin_shutdown(httpd):
    '''收到关机请求: 先立标志(挡住请求线程重启 MATLAB), 再停 HTTP 服务'''
    global _shutting_down
    _shutting_down = True
    threading.Thread(target=httpd.shutdown, daemon=True).start()


def stop_worker(wait=12):
    global _last_launch
    # stop 文件是"通用退出信号": 谁的 worker 看到都会退出,
    # 所以连上一次 server 留下的孤儿 worker 也一起收拾掉。
    try:
        with open(STOP_F, 'w', encoding='utf-8') as f:
            f.write('stop')
    except Exception:
        pass
    _kill_worker()
    deadline = time.time() + wait
    while time.time() < deadline:
        state, _, _, age = worker_state()
        if state == 'off' or age > 8:   # 'stopped' 只是最后一条心跳, 进程可能还在退出
            break
        time.sleep(0.3)             # 等孤儿 worker 看到 stop 文件后退出
    try:
        if os.path.exists(STOP_F):
            os.remove(STOP_F)       # 别让下一次启动的 worker 一上线就退出
    except Exception:
        pass
    _last_launch = 0.0              # 手动停止后再来请求可以立刻重启

def wait_ready(deadline):
    """等 worker 变成 idle（预热完成）"""
    while time.time() < deadline:
        state, _, _, _ = worker_state()
        if state == 'idle':
            return True
        if state == 'off':
            ensure_worker()
        time.sleep(0.2)
    return False


# --------------------------------------------------------------------------
# 识别
# --------------------------------------------------------------------------
def recognize(data, filename):
    os.makedirs(IN_DIR, exist_ok=True)
    os.makedirs(RES_DIR, exist_ok=True)
    ensure_worker()

    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED:
        ext = '.jpg'
    jid = uuid.uuid4().hex[:12]

    # 先写 .part 再原子改名，避免 worker 读到写了一半的文件
    part = os.path.join(IN_DIR, jid + ext + '.part')
    with open(part, 'wb') as f:
        f.write(data)
    os.replace(part, os.path.join(IN_DIR, jid + ext))

    # 存档原图
    try:
        stamp = time.strftime('%Y%m%d_%H%M%S')
        safe = os.path.basename(filename) or ('upload' + ext)
        shutil.copyfile(os.path.join(IN_DIR, jid + ext),
                        os.path.join(UPLOADS, '%s_%s' % (stamp, safe)))
    except Exception:
        pass

    res_path = os.path.join(RES_DIR, jid + '.json')
    deadline = time.time() + TIMEOUT
    t_start  = time.time()
    warned   = False
    while time.time() < deadline:
        if os.path.exists(res_path):
            # 等文件写完整（worker 是原子替换，能读到就是完整的）
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
            res['file']  = filename
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
                print('[server] worker 未就绪或掉线，正在拉起…')
                warned = True
            ensure_worker()          # 预热期间不会重复拉起, 见 ensure_worker
        time.sleep(0.1)
    raise TimeoutError('识别超时（%.0f 秒）。首次识别需要启动 MATLAB，请重试；'
                       '若反复超时请看 jobs/worker.log' % TIMEOUT)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = 'lpr_web/1.0'
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        sys.stdout.write('[http] %s %s\n' % (self.address_string(), fmt % args))

    # ---------- 工具 ----------
    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, ctype):
        with open(path, 'rb') as f:
            body = f.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------- GET ----------
    def do_GET(self):
        path = self.path.split('?')[0]
        try:
            if path in ('/', '/index.html'):
                return self.send_file(os.path.join(STATIC, 'index.html'),
                                      'text/html; charset=utf-8')
            if path.startswith('/static/'):
                name = os.path.basename(path)
                fp = os.path.join(STATIC, name)
                if os.path.isfile(fp):
                    ctype = 'text/css' if name.endswith('.css') else \
                            'application/javascript' if name.endswith('.js') else 'application/octet-stream'
                    return self.send_file(fp, ctype)
                return self.send_json({'error': 'not found'}, 404)
            if path == '/api/status':
                state, job, last, age = worker_state()
                return self.send_json({'worker': state, 'job': job, 'lastText': last,
                                       'age': round(age, 1), 'matlab': MATLAB, 'launches': _launch_count,
                                       'host': HOST,
                                       'project': os.path.abspath(
                                           project_dir()),
                                       'port': PORT})
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
            self.send_json({'error': 'not found'}, 404)
        except Exception as e:
            self.send_json({'error': str(e)}, 500)

    # ---------- POST ----------
    def do_POST(self):
        path = self.path.split('?')[0]
        if path == '/api/stop':                    # 主动关掉 MATLAB worker（省内存）
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
            return self.send_json({'ok': False, 'error': '图片太大（上限 20MB）'}, 413)

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
            res = recognize(data, filename)
            return self.send_json(res)
        except Exception as e:
            return self.send_json({'ok': False, 'error': str(e)}, 500)


def main():
    for d in (JOBS, IN_DIR, RES_DIR, UPLOADS):
        os.makedirs(d, exist_ok=True)
    if not MATLAB:
        print('[server] 警告: 没找到 matlab 可执行文件，识别功能会报错。'
              '请设置环境变量 LPR_MATLAB。')

    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    url = 'http://127.0.0.1:%d' % PORT
    lan = HOST not in ('127.0.0.1', 'localhost')
    print('=' * 58)
    print(' 车牌识别网站已启动')
    print('   本机地址 : %s' % url)
    if lan:
        ips = local_ips()
        for ip in ips:
            print('   局域网   : http://%s:%d' % (ip, PORT))
        if ips:
            print('   ↑ 手机 / 其他电脑的浏览器里输入上面这个地址即可')
        print('   提醒     : 该服务没有账号密码, 只请在可信的家庭/办公局域网内使用')
    else:
        print('   局域网   : 已关闭 (设置 LPR_HOST=0.0.0.0 后其他设备才能访问)')
    print('   MATLAB   : %s' % (MATLAB or '未找到'))
    print('   停止服务 : 在本窗口按 Ctrl+C')
    print('=' * 58)
    print('[server] 正在预热 MATLAB 识别引擎（约 10 秒，之后每张图 0.2~0.3 秒）…')
    try:
        ensure_worker()
    except Exception as e:
        print('[server] 预热失败: %s' % e)

    if os.environ.get('LPR_NO_BROWSER') != '1':
        try:
            import webbrowser
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        except Exception:
            pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\n[server] 收到 Ctrl+C，正在退出…')
    finally:
        _shutting_down = True
        stop_worker()
        httpd.server_close()
        print('[server] 已停止')


if __name__ == '__main__':
    atexit.register(stop_worker)
    main()
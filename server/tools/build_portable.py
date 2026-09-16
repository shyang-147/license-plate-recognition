#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 lpr_web + lpr_matlab 打包成单个 lpr_server.py(单文件版)

用法:  python tools/build_portable.py     -> 生成 portable/lpr_server.py

打包内容:
    static/index.html, static/demo.jpg            -> 网页和示例图
    worker_lpr.m                                  -> 常驻识别进程
    lpr_matlab 的 8 个 .m + templates.mat         -> 识别内核
运行时 lpr_server.py 会把它们释放到临时目录再调用 MATLAB, 所以用户只需要拷这一个文件。
"""

import base64
import hashlib
import io
import os
import py_compile
import sys

WEB  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # lpr_web
ROOT = os.path.dirname(WEB)                                          # 仓库根目录
MLAB = next((os.path.join(ROOT, n) for n in ('matlab', 'lpr_matlab')
             if os.path.isdir(os.path.join(ROOT, n))), os.path.join(ROOT, 'matlab'))

CORE_FILES = ['lpr_main.m', 'locatePlate.m', 'cropPlate.m', 'correctPlate.m',
              'segmentChars.m', 'normalizeChar.m', 'charFeature.m',
              'recognizeChars.m', 'templates.mat']


def collect():
    files = {}
    files['static/index.html'] = os.path.join(WEB, 'static', 'index.html')
    files['static/demo.jpg']   = os.path.join(WEB, 'static', 'demo.jpg')
    files['worker/worker_lpr.m'] = os.path.join(WEB, 'worker_lpr.m')
    for name in CORE_FILES:
        files['core/' + name] = os.path.join(MLAB, name)
    out = {}
    for key, path in files.items():
        if not os.path.isfile(path):
            print('缺少文件: %s' % path)
            sys.exit(1)
        with open(path, 'rb') as f:
            out[key] = base64.b64encode(f.read()).decode('ascii')
    return out


def payload_literal(payload, indent='    '):
    """生成大字典的源码文本, base64 每 110 字符换行, 便于阅读和 diff"""
    lines = ['{']
    for key in sorted(payload):
        b64 = payload[key]
        chunks = [b64[i:i + 110] for i in range(0, len(b64), 110)]
        lines.append("%s'%s': (" % (indent, key))
        for c in chunks:
            lines.append("%s    '%s'" % (indent, c))
        lines.append('%s),' % indent)
    lines.append('}')
    return '\n'.join(lines)


def main():
    tpl_path = os.path.join(WEB, 'tools', 'portable_template.py')
    tpl = io.open(tpl_path, encoding='utf-8').read()
    payload = collect()
    pkg_id = hashlib.sha1(''.join(payload[k] for k in sorted(payload)).encode()).hexdigest()[:10]

    code = tpl.replace('@PAYLOAD@', payload_literal(payload)).replace('@PKG_ID@', pkg_id)

    out_dir = os.path.join(WEB, 'portable')
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, 'lpr_server.py')
    io.open(out, 'w', encoding='utf-8', newline='\n').write(code)

    py_compile.compile(out, doraise=True)
    size = os.path.getsize(out)
    print('已生成 %s' % out)
    print('  打包文件 %d 个, 单文件大小 %.0f KB, PKG_ID=%s' % (len(payload), size / 1024.0, pkg_id))
    return 0


if __name__ == '__main__':
    sys.exit(main())
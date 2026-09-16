# -*- coding: utf-8 -*-
"""
把 src/ 里的源文件合成一个单文件网站 (../index.html)。

用法:  python build_site.py
改完 lpr_engine.js / ui.js / head.html / templates.json 之后重新跑一遍即可。

本脚本自身不依赖任何第三方库。
"""
import io, os, base64, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '..', 'index.html')

def R(name):
    return io.open(os.path.join(HERE, name), encoding='utf-8').read()

head = R('head.html')
ui = R('ui.js')
engine = R('lpr_engine.js')
templates = R('templates.json')
demo_b64 = base64.b64encode(open(os.path.join(HERE, 'demo_plate.jpg'), 'rb').read()).decode('ascii')

for name, text in (('lpr_engine.js', engine), ('ui.js', ui), ('head.html', head)):
    if '</script' in text.lower():
        sys.exit('错误: %s 里出现了 </script, 会提前结束 script 标签, 请转义成 <\\/script' % name)
if '</script' in templates.lower():
    sys.exit('错误: templates.json 里出现了 </script')

html = ''.join([
    head,
    '\n<!-- ============ 字符模板: 由 MATLAB 版 templates.mat 导出 ============ -->\n',
    '<script id="lpr-templates" type="application/json">', templates, '</script>\n',
    '\n<!-- ============ 识别引擎(纯 JS 移植自 MATLAB 工程) ============ -->\n',
    '<script>', engine, '</script>\n',
    '\n<!-- ============ 页面逻辑 ============ -->\n',
    '<script>', ui, '</script>\n',
    '\n<!-- 内置示例图片(车牌 京A12345), 首屏自动演示用 -->\n',
    '<img id="lpr-demo" alt="示例车牌" style="position:absolute;left:-9999px;width:1px;height:1px" src="data:image/jpeg;base64,',
    demo_b64,
    '">\n</body>\n</html>\n',
])

io.open(OUT, 'w', encoding='utf-8', newline='\n').write(html)
print('已生成: ' + os.path.abspath(OUT))
print('大小: %.1f KB  (引擎 %.1f KB / 模板 %.1f KB / 示例图 %.1f KB)' % (
    len(html.encode('utf-8')) / 1024.0,
    len(engine.encode('utf-8')) / 1024.0,
    len(templates.encode('utf-8')) / 1024.0,
    len(demo_b64) / 1024.0))

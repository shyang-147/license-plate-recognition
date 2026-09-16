import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BASE = process.env.LPR_URL || 'http://127.0.0.1:8765';
const OUT  = path.join(HERE, 'out');
const WEB  = path.join(HERE, '..');
fs.mkdirSync(OUT, { recursive: true });
const log  = (...a) => console.log(...a);

// ---- 1. 首页 ----
const r0 = await fetch(BASE + '/');
const html = await r0.text();
log('[1] GET /            ->', r0.status, r0.headers.get('content-type'), html.length + ' bytes');
const needed = ['drop','file','demo','go','clr','plate','chars','cimg','cropBox','meta','json','hist','stat','dot'];
const missing = needed.filter(id => !html.includes('id="' + id + '"'));
log('    页面关键元素缺失:', missing.length ? missing.join(',') : '无');

// ---- 2. 静态资源：前端"示例图片"按钮走的就是这个 ----
const r1 = await fetch(BASE + '/static/demo.jpg');
const demoBuf = Buffer.from(await r1.arrayBuffer());
log('[2] GET /static/demo.jpg ->', r1.status, demoBuf.length + ' bytes');
const mine = fs.readFileSync(path.join(WEB, 'static', 'demo.jpg'));
log('    与磁盘文件一致:', demoBuf.equals(mine));

// ---- 3. 引擎状态 ----
const st = await (await fetch(BASE + '/api/status')).json();
log('[3] GET /api/status   ->', JSON.stringify(st));

// ---- 4. 识别（与 index.html 完全相同的请求方式）----
const t0 = Date.now();
const res = await fetch(BASE + '/api/recognize', {
  method: 'POST',
  headers: { 'X-Filename': encodeURIComponent('demo_plate.jpg') },
  body: demoBuf
});
const d = await res.json();
const wall = (Date.now() - t0) / 1000;
log('[4] POST /api/recognize ->', res.status, '墙钟', wall.toFixed(2) + 's');
log('    ok        :', d.ok);
log('    text      :', d.text);
log('    nChars    :', d.nChars, '| jobId', d.jobId);
log('    每条字符  :', (d.details || []).map(c => c.char + '(' + (c.score * 100).toFixed(0) + '%)').join(' '));
log('    plateBox  :', JSON.stringify(d.plateBox));
log('    elapsed   :', d.elapsed, 's | 服务端 total', d.total, 's');
log('    车牌图b64 :', (d.plateImagePng || '').length, 'chars | 首字符图b64:', (d.details?.[0]?.png || '').length);
log('    warning   :', d.warning === '' ? '(空)' : d.warning);
log('    reason    :', d.reason === '' ? '(空)' : d.reason);
log('    quality   :', JSON.stringify(d.quality));

// ---- 5. 把前端会显示的两张图落盘，肉眼核对 ----
fs.writeFileSync(path.join(OUT, 'plate.png'), Buffer.from(d.plateImagePng, 'base64'));
for (const [i, c] of (d.details || []).entries())
  if (c.png) fs.writeFileSync(path.join(OUT, String(i + 1).padStart(2, '0') + '_' + c.char + '.png'), Buffer.from(c.png, 'base64'));

// ---- 6. 历史记录 ----
const h = await (await fetch(BASE + '/api/history')).json();
log('[5] GET /api/history  ->', h.items.length, '条, 最新:', JSON.stringify(h.items[0] && {t: h.items[0].time, text: h.items[0].text, ok: h.items[0].ok}));
const st2 = await (await fetch(BASE + '/api/status')).json();
log('[6] 识别后引擎状态    ->', st2.worker, '| lastText:', st2.lastText);
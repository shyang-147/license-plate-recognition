import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// 需要先启动一个开好调试端口的浏览器, 见 README 第 6 节:
//   msedge.exe --headless=new --remote-debugging-port=9222 --user-data-dir=<临时目录> about:blank
const HERE = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.LPR_CDP_PORT || 9222);
const BASE = process.env.LPR_URL || 'http://127.0.0.1:8765';
const TOK  = process.env.LPR_TOK || '';      // 设了访问口令时: 先带 ?token= 打开一次拿到 cookie
const OUT  = path.join(HERE, 'out');
fs.mkdirSync(OUT, { recursive: true });
const log  = (...a) => console.log(...a);
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ---------- 连接无头 Edge ----------
let page = null;
for (let i = 0; i < 40 && !page; i++) {
  try {
    const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
    page = list.find(t => t.type === 'page');
  } catch (e) { await sleep(300); }
}
if (!page) throw new Error('连不上无头 Edge 的调试端口');
log('[cdp] 已连接标签:', page.title || '(空)', page.url);

const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener('open', r));
let seq = 0; const pending = new Map();
ws.addEventListener('message', ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
});
const send = (method, params = {}) => {
  const id = ++seq; ws.send(JSON.stringify({ id, method, params }));
  return new Promise(r => pending.set(id, r));
};
const ev = async expr => {
  const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.result?.exceptionDetails) throw new Error(expr + ' -> ' + JSON.stringify(r.result.exceptionDetails));
  return r.result.result.value;
};
const shot = async name => {
  const r = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
  fs.writeFileSync(path.join(OUT, name), Buffer.from(r.result.data, 'base64'));
  log('    截图 ->', name);
};

await send('Page.enable');
await send('Runtime.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 1500, deviceScaleFactor: 1, mobile: false });
await send('Page.addScriptToEvaluateOnNewDocument', {
  source: 'window.__errs=[];addEventListener("error",e=>__errs.push(e.message));' +
          'addEventListener("unhandledrejection",e=>__errs.push("promise:"+e.reason));'
});

// ---------- 打开页面 ----------
await send('Page.navigate', { url: TOK ? BASE + '/?token=' + encodeURIComponent(TOK) : BASE });
await sleep(2500);
log('[1] 页面标题       :', await ev('document.title'));
log('    引擎状态文案   :', await ev(`document.getElementById('stat').textContent`));
log('    状态灯 class   :', await ev(`document.getElementById('dot').className`));
log('    历史条数       :', await ev(`document.querySelectorAll('#hist .row, #hist .item, #hist > *').length`));

// ---------- 点“用示例照片” ----------
await ev(`document.getElementById('demo').click()`);
let ok = false;
for (let i = 0; i < 40; i++) { if (await ev(`!document.getElementById('go').disabled`)) { ok = true; break; } await sleep(200); }
log('[2] 点“用示例照片”:', ok ? '预览已加载，识别按钮已启用' : '超时：按钮未启用');
log('    预览图 src     :', (await ev(`document.getElementById('pimg').src`)).slice(0, 40) + '…');
await shot('01_selected.png');

// ---------- 点“开始识别” ----------
await ev(`document.getElementById('go').click()`);
let done = false;
for (let i = 0; i < 300; i++) {
  done = await ev(`document.getElementById('jsonBox').style.display === 'block'`);
  if (done) break;
  await sleep(200);
}
log('[3] 点“开始识别”  :', done ? '已完成渲染' : '超时');
const result = await ev(`JSON.stringify({
  plate: document.getElementById('plate').textContent,
  plateClass: document.getElementById('plate').className,
  msg: document.getElementById('msg').textContent,
  chips: [...document.querySelectorAll('#chars .chip')].map(c => c.querySelector('.c').textContent + '/' + c.querySelector('.s').textContent),
  chipImgs: document.querySelectorAll('#chars .chip img').length,
  nChars: document.getElementById('mN').textContent,
  matlabTime: document.getElementById('mT').textContent,
  wallTime: document.getElementById('mE').textContent,
  cropShown: document.getElementById('cropBox').style.display,
  cropSrcLen: document.getElementById('cimg').src.length,
  json: document.getElementById('json').textContent
})`);
const R = JSON.parse(result);
log('    识别结果文字   :', R.plate, '| class =', R.plateClass);
log('    逐字符卡片     :', R.chips.join('  '), '| 缩略图数', R.chipImgs);
log('    指标           : 字符数', R.nChars, '| MATLAB', R.matlabTime, '| 总耗时', R.wallTime);
log('    校正车牌图     : display =', R.cropShown, '| src 长度', R.cropSrcLen);
log('    提示信息       :', R.msg || '(无)');
log('    原始 JSON      :', R.json.replace(/\s+/g, ' ').slice(0, 200) + '…');
await shot('02_recognized.png');

// ---------- 负例：纯噪声图，应给“未检测到车牌” ----------
await ev(`(async()=>{const c=document.createElement('canvas');c.width=800;c.height=600;const g=c.getContext('2d');
  g.fillStyle='#8a94a0';g.fillRect(0,0,800,600);
  for(let i=0;i<3000;i++){g.fillStyle='rgb('+(Math.random()*255|0)+','+(Math.random()*255|0)+','+(Math.random()*255|0)+')';
  g.fillRect(Math.random()*800,Math.random()*600,4,4);}
  const b=await new Promise(r=>c.toBlob(r,'image/jpeg'));
  pick(new File([b],'noise.jpg',{type:'image/jpeg'}));return true})()`);
await ev(`document.getElementById('go').click()`);
for (let i = 0; i < 300; i++) {
  if (await ev(`document.getElementById('jsonBox').style.display === 'block'`)) break;
  await sleep(200);
}
log('[4] 负例(纯噪声图):', await ev(`document.getElementById('plate').textContent`), '| 提示:', await ev(`document.getElementById('msg').textContent`), '| 定位质量:', await ev(`document.getElementById('mQ').textContent`), '| 提示可见:', await ev(`getComputedStyle(document.getElementById('msg')).display`));
await shot('03_noplate.png');

log('[5] 页面 JS 错误    :', JSON.stringify(await ev('window.__errs')));
ws.close();
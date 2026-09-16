/* 单文件网页版的端到端测试: 真的用无头浏览器打开 site/index.html,
 * 通过文件选择框把图塞进去, 读回页面上的识别结果, 跟期望值比对。
 *
 * 用法:  node site/tools/test_browser.mjs
 * 依赖:  Edge 或 Chrome, Node 18+ (自带 fetch / WebSocket / 不需要 npm install)
 * 可用环境变量: LPR_BROWSER 指定浏览器 exe
 */
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const PAGE = path.join(ROOT, 'site', 'index.html');
const PORT = Number(process.env.LPR_CDP_PORT || 9334);
const PROFILE = path.join(os.tmpdir(), 'lpr-edge-profile-' + process.pid);
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

const EDGE = process.env.LPR_BROWSER || [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
].find(p => fs.existsSync(p));

if (!EDGE) {
  console.error('找不到 Edge/Chrome, 请用 LPR_BROWSER=<exe 路径> 指定');
  process.exit(2);
}
if (!fs.existsSync(PAGE)) {
  console.error('找不到 ' + PAGE);
  process.exit(2);
}
const FILE_URL = 'file:///' + PAGE.replace(/\\/g, '/');
console.log('浏览器 : ' + EDGE);
console.log('页面   : ' + FILE_URL);

const child = spawn(EDGE, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--remote-debugging-port=' + PORT, '--user-data-dir=' + PROFILE, '--window-size=1200,1400', 'about:blank'],
  { stdio: 'ignore' });

let wsUrl = null;
for (let i = 0; i < 60 && !wsUrl; i++) {
  try {
    const list = await (await fetch('http://127.0.0.1:' + PORT + '/json/list')).json();
    const p = list.find(t => t.type === 'page');
    if (p) wsUrl = p.webSocketDebuggerUrl;
  } catch (e) { /* 端口还没起来 */ }
  if (!wsUrl) await sleep(400);
}
if (!wsUrl) { console.error('浏览器调试端口没起来'); child.kill(); process.exit(2); }

const ws = new WebSocket(wsUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
let id = 0;
const pending = new Map();
const errors = [];
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) {
    const p = pending.get(m.id);
    pending.delete(m.id);
    m.error ? p.reject(new Error(JSON.stringify(m.error))) : p.resolve(m.result);
    return;
  }
  if (m.method === 'Runtime.exceptionThrown') {
    errors.push(m.params.exceptionDetails.exception && m.params.exceptionDetails.exception.description
      || m.params.exceptionDetails.text);
  }
};
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const i = ++id;
  pending.set(i, { resolve, reject });
  ws.send(JSON.stringify({ id: i, method, params }));
});
const evaluate = async (expression) => {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
  return r.result.value;
};

await send('Runtime.enable');
await send('Page.enable');
await send('DOM.enable');
await send('Page.navigate', { url: FILE_URL });
await sleep(2200);

async function uploadTo(filePath, label, expect) {
  const before = await evaluate("document.getElementById('plateText').textContent");
  const doc = await send('DOM.getDocument');
  const q = await send('DOM.querySelector', { nodeId: doc.root.nodeId, selector: '#file' });
  await send('DOM.setFileInputFiles', { nodeId: q.nodeId, files: [filePath] });
  let text = '';
  for (let i = 0; i < 120; i++) {
    text = await evaluate("document.getElementById('plateText').textContent");
    if (text && text !== '识别中…' && text !== before) break;
    await sleep(200);
  }
  await sleep(150);
  const meta = await evaluate("document.getElementById('meta').textContent");
  const nbox = await evaluate("document.querySelectorAll('#chars .chbox').length");
  const ok = text === expect;
  console.log((ok ? 'PASS ' : 'FAIL ') + label.padEnd(20) + ' 结果=' + JSON.stringify(text).padEnd(14) +
              ' 期望=' + expect.padEnd(10) + ' 字符块=' + nbox);
  console.log('      meta: ' + meta);
  return ok;
}

let allOk = true;
const M = path.join(ROOT, 'matlab');
allOk = (await uploadTo(path.join(M, 'images', 'demo_plate.jpg'), 'demo_plate.jpg', '京A12345')) && allOk;
allOk = (await uploadTo(path.join(M, 'images', 'demo_plate2.jpg'), 'demo_plate2.jpg', '沪B8K9Z2')) && allOk;
allOk = (await uploadTo(path.join(M, 'images', 'demo_plate3.jpg'), 'demo_plate3.jpg', '粤B1234A')) && allOk;
allOk = (await uploadTo(path.join(M, 'bench', 'bench12.jpg'), 'bench12.jpg', '苏D03622')) && allOk;
allOk = (await uploadTo(path.join(M, 'bench', 'bench20.jpg'), 'bench20.jpg', '闽Z14533')) && allOk;

await evaluate("document.getElementById('reset').click()");
await sleep(200);
console.log('点"清空"后 =', JSON.stringify(await evaluate("document.getElementById('plateText').textContent")));

console.log('--- 页面 JS 报错 (' + errors.length + ') ---');
errors.forEach(e => console.log('  ! ' + String(e).slice(0, 300)));
ws.close();
child.kill();
process.exit((allOk && errors.length === 0) ? 0 : 1);
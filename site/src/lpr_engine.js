/* ================================================================
 * lpr_engine.js —— 纯浏览器车牌识别引擎 (MATLAB 工程的 JS 移植)
 *
 * 与 matlab 的 MATLAB 实现一一对应:
 *   locatePlate  -> 定位(颜色掩膜/边缘掩膜 + 打分 + 双闸门)
 *   cropPlate    -> 裁剪
 *   correctPlate -> 倾斜校正 + 高度归一化
 *   segmentChars -> 二值化 + 垂直投影分割
 *   recognizeChars -> 模板匹配(直接复用 MATLAB 导出的模板)
 *
 * 不依赖任何第三方库, 可直接在浏览器 / Node 中运行。
 * ================================================================ */
(function (global) {
'use strict';

var C_R = 0.298936021293775, C_G = 0.587043074451121, C_B = 0.114020904255104;

function clampIdx(i, n) { return i < 0 ? 0 : (i >= n ? n - 1 : i); }
function clamp(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }

/* ---------------- 重采样: 三角形核 + 抗锯齿(对齐 MATLAB imresize) ---------------- */
function weights1D(srcLen, dstLen) {
  var scale = dstLen / srcLen;
  var a = scale < 1 ? scale : 1;
  var sup = 1 / a;
  var cols = new Array(dstLen);
  for (var x = 0; x < dstLen; x++) {
    var c = (x + 0.5) / scale - 0.5;
    var i0 = Math.ceil(c - sup), i1 = Math.floor(c + sup);
    var idx = [], wts = [], sum = 0;
    for (var i = i0; i <= i1; i++) {
      var w = 1 - Math.abs((i - c) * a);
      if (w <= 0) { continue; }
      idx.push(clampIdx(i, srcLen));
      wts.push(w);
      sum += w;
    }
    if (sum <= 0) { idx = [clampIdx(Math.round(c), srcLen)]; wts = [1]; sum = 1; }
    for (var k = 0; k < wts.length; k++) { wts[k] /= sum; }
    cols[x] = { idx: idx, wts: wts };
  }
  return cols;
}

/* resize(src, sw, sh, dw, dh, ch) -> Float32Array (交错多通道) */
function resize(src, sw, sh, dw, dh, ch) {
  if (sw === dw && sh === dh) { return src; }
  var tmp = src;
  if (dw !== sw) {
    var cx = weights1D(sw, dw);
    tmp = new Float32Array(dw * sh * ch);
    for (var y = 0; y < sh; y++) {
      var ri = y * sw * ch, ro = y * dw * ch;
      for (var x = 0; x < dw; x++) {
        var e = cx[x], idx = e.idx, wts = e.wts, o = ro + x * ch;
        for (var c = 0; c < ch; c++) {
          var s = 0;
          for (var k = 0; k < idx.length; k++) { s += src[ri + idx[k] * ch + c] * wts[k]; }
          tmp[o + c] = s;
        }
      }
    }
  }
  if (dh === sh) { return tmp; }
  var cy = weights1D(sh, dh);
  var out = new Float32Array(dw * dh * ch);
  for (var y2 = 0; y2 < dh; y2++) {
    var e2 = cy[y2], idx2 = e2.idx, wts2 = e2.wts, ro2 = y2 * dw * ch;
    for (var x2 = 0; x2 < dw; x2++) {
      var o2 = ro2 + x2 * ch;
      for (var c2 = 0; c2 < ch; c2++) {
        var s2 = 0;
        for (var k2 = 0; k2 < idx2.length; k2++) { s2 += tmp[idx2[k2] * dw * ch + x2 * ch + c2] * wts2[k2]; }
        out[o2 + c2] = s2;
      }
    }
  }
  return out;
}

/* ---------------- 形态学: 矩形结构元(可分离 + van Herk 滑动极值) ---------------- */
function makeScratch() { return { g: new Float32Array(0), pre: new Float32Array(0), suf: new Float32Array(0) }; }

function morphLine(src, sOff, sStr, len, dst, dOff, dStr, k, d0, dilate, sc) {
  var i;
  if (k <= 1) {
    for (i = 0; i < len; i++) { dst[dOff + i * dStr] = src[sOff + i * sStr]; }
    return;
  }
  var init = dilate ? -1e30 : 1e30;
  var m = (Math.ceil(len / k) + 1) * k;
  if (sc.g.length < m) {
    sc.g = new Float32Array(m); sc.pre = new Float32Array(m); sc.suf = new Float32Array(m);
  }
  var g = sc.g, pre = sc.pre, suf = sc.suf;
  /* 注意: g 的索引范围必须覆盖到 len + k - 1, 不能用 i < len 截断,
     否则靠近图像下/右边界的窗口会取到错误数据(腐蚀会误判为"全部命中")。 */
  for (i = 0; i < m; i++) {
    var j = i - d0;
    g[i] = (j >= 0 && j < len) ? src[sOff + j * sStr] : init;
  }
  for (var b = 0; b < m; b += k) {
    var e = b + k, s = init;
    for (i = e - 1; i >= b; i--) { s = dilate ? (g[i] > s ? g[i] : s) : (g[i] < s ? g[i] : s); suf[i] = s; }
    s = init;
    for (i = b; i < e; i++) { s = dilate ? (g[i] > s ? g[i] : s) : (g[i] < s ? g[i] : s); pre[i] = s; }
  }
  for (i = 0; i < len; i++) {
    var a = suf[i], c = pre[i + k - 1];
    dst[dOff + i * dStr] = dilate ? (a > c ? a : c) : (a < c ? a : c);
  }
}

function morphRows(src, w, h, k, d0, dilate, sc) {
  var dst = new Float32Array(w * h);
  for (var y = 0; y < h; y++) { morphLine(src, y * w, 1, w, dst, y * w, 1, k, d0, dilate, sc); }
  return dst;
}

function morphCols(src, w, h, k, d0, dilate, sc) {
  var dst = new Float32Array(w * h);
  for (var x = 0; x < w; x++) { morphLine(src, x, w, h, dst, x, w, k, d0, dilate, sc); }
  return dst;
}

/* 矩形结构元膨胀/腐蚀; 通道数为 1 的 Float32 图 */
function rectMorph(src, w, h, kw, kh, dilate, sc) {
  var out = src;
  if (kh > 1) { out = morphCols(out, w, h, kh, Math.floor((kh - 1) / 2), dilate, sc); }
  if (kw > 1) { out = morphRows(out, w, h, kw, Math.floor((kw - 1) / 2), dilate, sc); }
  if (out === src) { out = src.slice(); }
  return out;
}

function imdilate(src, w, h, kw, kh, sc) { return rectMorph(src, w, h, kw, kh, true, sc); }
function imerode(src, w, h, kw, kh, sc) { return rectMorph(src, w, h, kw, kh, false, sc); }
function imclose(src, w, h, kw, kh, sc) { return imerode(imdilate(src, w, h, kw, kh, sc), w, h, kw, kh, sc); }
function imopen(src, w, h, kw, kh, sc) { return imdilate(imerode(src, w, h, kw, kh, sc), w, h, kw, kh, sc); }

/* ---------------- 连通域标记(8 邻域) + 区域统计 ---------------- */
function label8(mask, w, h) {
  var n = w * h;
  var labels = new Int32Array(n);
  var parent = new Int32Array(4096);
  var next = 1;
  parent[0] = 0;
  function find(x) { while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; } return x; }
  function union(a, b) { a = find(a); b = find(b); if (a === b) { return a; } if (a < b) { parent[b] = a; return a; } parent[a] = b; return b; }
  function push() {
    if (next >= parent.length) { var np = new Int32Array(parent.length * 2); np.set(parent); parent = np; }
    parent[next] = next; return next++;
  }
  for (var y = 0; y < h; y++) {
    for (var x = 0; x < w; x++) {
      var i = y * w + x;
      if (mask[i] <= 0) { continue; }
      var l = 0;
      if (x > 0 && labels[i - 1]) { l = labels[i - 1]; }
      if (y > 0) {
        if (x > 0 && labels[i - w - 1]) { l = l ? union(l, labels[i - w - 1]) : labels[i - w - 1]; }
        if (labels[i - w]) { l = l ? union(l, labels[i - w]) : labels[i - w]; }
        if (x < w - 1 && labels[i - w + 1]) { l = l ? union(l, labels[i - w + 1]) : labels[i - w + 1]; }
      }
      labels[i] = l ? l : push();
    }
  }
  var map = new Int32Array(next);
  var stats = [];
  for (var i2 = 0; i2 < n; i2++) {
    var v = labels[i2];
    if (!v) { continue; }
    var r = find(v);
    var id = map[r];
    if (!id) {
      id = map[r] = stats.length + 1;
      stats.push({ minX: 1e9, minY: 1e9, maxX: -1, maxY: -1, area: 0 });
    }
    var st = stats[id - 1];
    var xx = i2 % w, yy = (i2 - xx) / w;
    if (xx < st.minX) { st.minX = xx; }
    if (xx > st.maxX) { st.maxX = xx; }
    if (yy < st.minY) { st.minY = yy; }
    if (yy > st.maxY) { st.maxY = yy; }
    st.area++;
    labels[i2] = id;
  }
  return { labels: labels, stats: stats, w: w, h: h };
}

/* 孔洞填充(从边界对背景做 4 邻域洪泛, 未到达的背景即为孔洞) */
function fillHoles(mask, w, h) {
  var n = w * h, i;
  var seen = new Uint8Array(n);
  var stack = new Int32Array(n);
  var sp = 0;
  function push(idx) { if (!seen[idx] && mask[idx] <= 0) { seen[idx] = 1; stack[sp++] = idx; } }
  for (i = 0; i < w; i++) { push(i); push((h - 1) * w + i); }
  for (i = 0; i < h; i++) { push(i * w); push(i * w + w - 1); }
  while (sp > 0) {
    var cur = stack[--sp];
    var x = cur % w, y = (cur - x) / w;
    if (x > 0) { push(cur - 1); }
    if (x < w - 1) { push(cur + 1); }
    if (y > 0) { push(cur - w); }
    if (y < h - 1) { push(cur + w); }
  }
  var out = new Float32Array(n);
  for (i = 0; i < n; i++) { out[i] = (mask[i] > 0 || !seen[i]) ? 1 : 0; }
  return out;
}

/* 去掉面积小于 minArea 的连通域 */
function areaOpen(mask, w, h, minArea) {
  var r = label8(mask, w, h);
  var keep = new Uint8Array(r.stats.length + 1), i;
  for (i = 0; i < r.stats.length; i++) { if (r.stats[i].area >= minArea) { keep[i + 1] = 1; } }
  var out = new Float32Array(w * h);
  for (i = 0; i < w * h; i++) { out[i] = keep[r.labels[i]] ? 1 : 0; }
  return out;
}

/* ---------------- 灰度 / 颜色空间 ---------------- */
function rgbaToGrayF(rgba, w, h) {
  var n = w * h, g = new Float32Array(n);
  for (var i = 0, j = 0; i < n; i++, j += 4) {
    g[i] = (C_R * rgba[j] + C_G * rgba[j + 1] + C_B * rgba[j + 2]) / 255;
  }
  return g;
}

function rgb2hsvF(rgba, w, h) {
  var n = w * h;
  var H = new Float32Array(n), S = new Float32Array(n), V = new Float32Array(n);
  for (var i = 0, j = 0; i < n; i++, j += 4) {
    var r = rgba[j] / 255, g = rgba[j + 1] / 255, b = rgba[j + 2] / 255;
    var mx = r > g ? (r > b ? r : b) : (g > b ? g : b);
    var mn = r < g ? (r < b ? r : b) : (g < b ? g : b);
    var d = mx - mn;
    V[i] = mx;
    S[i] = mx === 0 ? 0 : d / mx;
    var hue;
    if (d === 0) { hue = 0; }
    else if (mx === r) { hue = ((g - b) / d) % 6; }
    else if (mx === g) { hue = (b - r) / d + 2; }
    else { hue = (r - g) / d + 4; }
    hue /= 6;
    if (hue < 0) { hue += 1; }
    H[i] = hue;
  }
  return { H: H, S: S, V: V };
}

/* ---------------- Otsu 阈值 ---------------- */
function otsu(img) {
  var hist = new Float64Array(256), n = img.length, i;
  for (i = 0; i < n; i++) {
    var b = Math.round(img[i] * 255);
    hist[b < 0 ? 0 : (b > 255 ? 255 : b)]++;
  }
  var sum = 0;
  for (i = 0; i < 256; i++) { sum += i * hist[i]; }
  var sumB = 0, wB = 0, best = -1, thr = 0;
  for (i = 0; i < 256; i++) {
    wB += hist[i];
    if (wB === 0) { continue; }
    var wF = n - wB;
    if (wF === 0) { break; }
    sumB += i * hist[i];
    var mB = sumB / wB, mF = (sum - sumB) / wF;
    var between = wB * wF * (mB - mF) * (mB - mF);
    if (between > best) { best = between; thr = i; }
  }
  return thr / 255;
}

/* ---------------- CLAHE(限制对比度自适应直方图均衡) ---------------- */
function clahe(img, w, h, tilesR, tilesC, clipLimit) {
  var tileH = Math.ceil(h / tilesR), tileW = Math.ceil(w / tilesC);
  var nr = Math.max(1, Math.ceil(h / tileH)), nc = Math.max(1, Math.ceil(w / tileW));
  var maps = new Array(nr * nc);
  for (var ty = 0; ty < nr; ty++) {
    for (var tx = 0; tx < nc; tx++) {
      var y1 = ty * tileH, y2 = Math.min(h, y1 + tileH);
      var x1 = tx * tileW, x2 = Math.min(w, x1 + tileW);
      var hist = new Float64Array(256), npix = 0;
      for (var y = y1; y < y2; y++) {
        for (var x = x1; x < x2; x++) {
          var b = Math.round(img[y * w + x] * 255);
          hist[b < 0 ? 0 : (b > 255 ? 255 : b)]++;
          npix++;
        }
      }
      var clip = Math.max(1, Math.round(clipLimit * npix / 256));
      var excess = 0, i;
      for (i = 0; i < 256; i++) { if (hist[i] > clip) { excess += hist[i] - clip; hist[i] = clip; } }
      var share = excess / 256;
      var cdf = new Float64Array(256), acc = 0, cdfMin = 0;
      for (i = 0; i < 256; i++) { hist[i] += share; acc += hist[i]; cdf[i] = acc; }
      for (i = 0; i < 256; i++) { if (cdf[i] > 0) { cdfMin = cdf[i]; break; } }
      var denom = npix - cdfMin;
      var m = new Float32Array(256);
      for (i = 0; i < 256; i++) {
        m[i] = denom > 0 ? clamp((cdf[i] - cdfMin) / denom, 0, 1) : i / 255;
      }
      maps[ty * nc + tx] = m;
    }
  }
  var out = new Float32Array(w * h);
  for (var yy = 0; yy < h; yy++) {
    var fy = (yy + 0.5) / tileH - 0.5;
    var ry = Math.floor(fy), wy = fy - ry;
    if (ry < 0) { ry = 0; wy = 0; }
    if (ry > nr - 1) { ry = nr - 1; wy = 0; }
    var ry2 = Math.min(nr - 1, ry + 1);
    for (var xx = 0; xx < w; xx++) {
      var fx = (xx + 0.5) / tileW - 0.5;
      var rx = Math.floor(fx), wx = fx - rx;
      if (rx < 0) { rx = 0; wx = 0; }
      if (rx > nc - 1) { rx = nc - 1; wx = 0; }
      var rx2 = Math.min(nc - 1, rx + 1);
      var v = img[yy * w + xx];
      var b2 = Math.round(v * 255);
      b2 = b2 < 0 ? 0 : (b2 > 255 ? 255 : b2);
      var v00 = maps[ry * nc + rx][b2], v01 = maps[ry * nc + rx2][b2];
      var v10 = maps[ry2 * nc + rx][b2], v11 = maps[ry2 * nc + rx2][b2];
      var top = v00 + (v01 - v00) * wx;
      var bot = v10 + (v11 - v10) * wx;
      out[yy * w + xx] = top + (bot - top) * wy;
    }
  }
  return out;
}

/* ---------------- 3x3 中值滤波 ---------------- */
function medfilt3(img, w, h) {
  var out = new Float32Array(w * h), buf = new Float32Array(9);
  for (var y = 0; y < h; y++) {
    for (var x = 0; x < w; x++) {
      var k = 0;
      for (var dy = -1; dy <= 1; dy++) {
        var yy = clampIdx(y + dy, h);
        for (var dx = -1; dx <= 1; dx++) { buf[k++] = img[yy * w + clampIdx(x + dx, w)]; }
      }
      /* 9 元素插入排序取中值 */
      for (var i = 1; i < 9; i++) {
        var v = buf[i], j = i - 1;
        while (j >= 0 && buf[j] > v) { buf[j + 1] = buf[j]; j--; }
        buf[j + 1] = v;
      }
      out[y * w + x] = buf[4];
    }
  }
  return out;
}

/* ---------------- Sobel 垂直边缘(MATLAB edge(...,'sobel','vertical')) ---------------- */
function sobelVertical(img, w, h) {
  var out = new Float32Array(w * h), i;
  for (var y = 1; y < h - 1; y++) {
    for (var x = 1; x < w - 1; x++) {
      var o = y * w + x;
      var gx = (img[o - w + 1] + 2 * img[o + 1] + img[o + w + 1])
             - (img[o - w - 1] + 2 * img[o - 1] + img[o + w - 1]);
      out[o] = Math.abs(gx) / 8;
    }
  }
  /* MATLAB edge 自动阈值: 约 0.75 * 平均梯度幅值 */
  var sum = 0;
  for (i = 0; i < w * h; i++) { sum += out[i]; }
  var thr = 0.75 * (sum / (w * h));
  var bw = new Float32Array(w * h);
  for (i = 0; i < w * h; i++) { bw[i] = out[i] > thr ? 1 : 0; }
  return bw;
}

/* ---------------- 盒式均值(可分离, 边缘按复制填充) ---------------- */
function boxMean(img, w, h, k) {
  var r = Math.floor(k / 2);
  var tmp = new Float32Array(w * h), out = new Float32Array(w * h);
  var i, x, y;
  for (y = 0; y < h; y++) {
    var ro = y * w;
    for (x = 0; x < w; x++) {
      var s = 0;
      for (i = -r; i <= r; i++) { s += img[ro + clampIdx(x + i, w)]; }
      tmp[ro + x] = s / k;
    }
  }
  for (x = 0; x < w; x++) {
    for (y = 0; y < h; y++) {
      var s2 = 0;
      for (i = -r; i <= r; i++) { s2 += tmp[clampIdx(y + i, h) * w + x]; }
      out[y * w + x] = s2 / k;
    }
  }
  return out;
}
/* ================================================================
 * 第二部分: 车牌定位 -> 校正 -> 分割 -> 识别
 * ================================================================ */

/* ---------------- 定位(对应 locatePlate.m) ---------------- */
function bestRegion(mask, w, h, gray, colorMask, minArea, sc) {
  var ed = sobelVertical(gray, w, h);
  var r = label8(mask, w, h);
  var best = null;
  var nCand = 0;
  for (var k = 0; k < r.stats.length; k++) {
    var st = r.stats[k];
    var bw = st.maxX - st.minX + 1, bh = st.maxY - st.minY + 1;
    var ar = bw / bh;
    var area = st.area;
    if (ar < 1.6 || ar > 6.5) { continue; }
    if (area < minArea) { continue; }
    if (bh > 0.60 * h) { continue; }
    if (bw > 0.95 * w) { continue; }
    nCand++;
    var sAR = Math.exp(-Math.pow((ar - 3.3) / 1.1, 2));
    var sArea = Math.min(1, area / (0.01 * h * w));
    var cm = 0, cnt = 0;
    for (var y = st.minY; y <= st.maxY; y++) {
      for (var x = st.minX; x <= st.maxX; x++) { cm += colorMask[y * w + x]; cnt++; }
    }
    var sCol = cnt > 0 ? cm / cnt : 0;
    var ec = 0;
    for (var y2 = st.minY; y2 <= st.maxY; y2++) {
      for (var x2 = st.minX; x2 <= st.maxX; x2++) { ec += ed[y2 * w + x2]; }
    }
    var eDen = cnt > 0 ? ec / cnt : 0;
    var sEdge = Math.min(1, eDen / 0.25);
    var extent = area / (bw * bh);
    var score = 0.30 * sAR + 0.15 * sArea + 0.25 * sCol + 0.20 * sEdge + 0.10 * extent;
    if (!best || score > best.score) {
      best = {
        score: score, aspect: ar, colorFrac: sCol, edgeDensity: eDen,
        extent: extent, area: area, box: [st.minX, st.minY, bw, bh],
        label: k + 1
      };
    }
  }
  if (best) { best.nCand = nCand; }
  return best;
}

function refineBox(labels, labelId, w, h, box) {
  var x1 = box[0], y1 = box[1], x2 = box[0] + box[2] - 1, y2 = box[1] + box[3] - 1;
  var colSum = new Float64Array(box[2]), rowSum = new Float64Array(box[3]);
  var i, x, y;
  var maxC = 0, maxR = 0;
  for (x = x1; x <= x2; x++) {
    var s = 0;
    for (y = y1; y <= y2; y++) { if (labels[y * w + x] === labelId) { s++; } }
    colSum[x - x1] = s;
    if (s > maxC) { maxC = s; }
  }
  for (y = y1; y <= y2; y++) {
    var s2 = 0;
    for (x = x1; x <= x2; x++) { if (labels[y * w + x] === labelId) { s2++; } }
    rowSum[y - y1] = s2;
    if (s2 > maxR) { maxR = s2; }
  }
  if (maxC <= 0 || maxR <= 0) { return box; }
  var cs = -1, ce = -1, rs = -1, re = -1;
  for (i = 0; i < colSum.length; i++) { if (colSum[i] > 0.25 * maxC) { if (cs < 0) { cs = i; } ce = i; } }
  for (i = 0; i < rowSum.length; i++) { if (rowSum[i] > 0.25 * maxR) { if (rs < 0) { rs = i; } re = i; } }
  if (cs < 0 || rs < 0) { return box; }
  return [x1 + cs, y1 + rs, ce - cs + 1, re - rs + 1];
}

function fracInBox(mask, w, h, box) {
  var x1 = clamp(box[0], 0, w - 1), y1 = clamp(box[1], 0, h - 1);
  var x2 = clamp(box[0] + box[2] - 1, 0, w - 1), y2 = clamp(box[1] + box[3] - 1, 0, h - 1);
  var cnt = 0, tot = 0;
  for (var y = y1; y <= y2; y++) {
    for (var x = x1; x <= x2; x++) { cnt += mask[y * w + x]; tot++; }
  }
  return tot > 0 ? cnt / tot : 0;
}

function locatePlate(rgba, w, h, opts) {
  var sc = makeScratch();
  var minArea = 0.0008 * h * w;
  var minExtent = 0.50, minColorFr = 0.30;
  var gray = rgbaToGrayF(rgba, w, h);
  var hsv = rgb2hsvF(rgba, w, h);

  /* 颜色掩膜 */
  var i, n = w * h;
  var colorMask = new Float32Array(n);
  for (i = 0; i < n; i++) {
    var Hh = hsv.H[i], S = hsv.S[i], V = hsv.V[i];
    var blue = (Hh > 0.52 && Hh < 0.72) && S > 0.30 && V > 0.18;
    var green = (Hh > 0.22 && Hh < 0.45) && S > 0.25 && V > 0.18;
    var yellow = (Hh > 0.09 && Hh < 0.20) && S > 0.35 && V > 0.40;
    colorMask[i] = (blue || green || yellow) ? 1 : 0;
  }
  var wLen = Math.max(9, Math.round(w / 80));
  var hLen = Math.max(3, Math.round(h / 200));
  var cMask = imclose(colorMask, w, h, wLen, hLen, sc);
  cMask = imopen(cMask, w, h, 3, 3, sc);
  cMask = fillHoles(cMask, w, h);
  cMask = areaOpen(cMask, w, h, Math.round(minArea));

  /* 边缘掩膜(备用通道, 兼容无颜色/黑白图) */
  var eMask = buildEdgeMask(gray, w, h, minArea, sc);

  var cReg = bestRegion(cMask, w, h, gray, colorMask, minArea, sc);
  var eReg = bestRegion(eMask, w, h, gray, colorMask, minArea, sc);

  var pick, source;
  if (cReg && (!eReg || cReg.score >= eReg.score)) { pick = cReg; source = 'color'; }
  else { pick = eReg; source = 'edge'; }

  if (!pick) {
    return {
      box: null, valid: false, source: source,
      reject: '没有找到长宽比/面积像车牌的候选区域',
      score: -1, extent: 0, colorFrac: 0, edgesFrac: 0, nCand: 0
    };
  }

  var mask = (source === 'color') ? cMask : eMask;
  var box = refineBoxFromMask(mask, w, h, pick.box);
  var colorFrac = fracInBox(colorMask, w, h, box);
  var edgesFrac = fracInBox(sobelVertical(gray, w, h), w, h, box);
  var extent = pick.extent;
  var valid = (extent >= minExtent) && (colorFrac >= minColorFr);
  var reject = '';
  if (!valid) {
    if (extent < minExtent) {
      reject = '候选区域矩形度 ' + extent.toFixed(2) + ' < ' + minExtent + ', 不像规整的矩形车牌';
    } else {
      reject = '框内车牌底色只占 ' + colorFrac.toFixed(2) + ' (< ' + minColorFr + '), 没有蓝/绿/黄底色';
    }
  }
  return {
    box: box, valid: valid, source: source, reject: reject,
    score: pick.score, extent: extent, colorFrac: colorFrac,
    edgesFrac: edgesFrac, nCand: pick.nCand, mask: mask
  };
}

function refineBoxFromMask(mask, w, h, box) {
  var x1 = box[0], y1 = box[1], x2 = box[0] + box[2] - 1, y2 = box[1] + box[3] - 1;
  var colSum = new Float64Array(box[2]), rowSum = new Float64Array(box[3]);
  var i, x, y, maxC = 0, maxR = 0;
  for (x = x1; x <= x2; x++) {
    var s = 0;
    for (y = y1; y <= y2; y++) { s += mask[y * w + x] > 0 ? 1 : 0; }
    colSum[x - x1] = s; if (s > maxC) { maxC = s; }
  }
  for (y = y1; y <= y2; y++) {
    var s2 = 0;
    for (x = x1; x <= x2; x++) { s2 += mask[y * w + x] > 0 ? 1 : 0; }
    rowSum[y - y1] = s2; if (s2 > maxR) { maxR = s2; }
  }
  if (maxC <= 0 || maxR <= 0) { return box; }
  var cs = -1, ce = -1, rs = -1, re = -1;
  for (i = 0; i < colSum.length; i++) { if (colSum[i] > 0.25 * maxC) { if (cs < 0) { cs = i; } ce = i; } }
  for (i = 0; i < rowSum.length; i++) { if (rowSum[i] > 0.25 * maxR) { if (rs < 0) { rs = i; } re = i; } }
  if (cs < 0 || rs < 0) { return box; }
  return [x1 + cs, y1 + rs, ce - cs + 1, re - rs + 1];
}

function buildEdgeMask(gray, w, h, minArea, sc) {
  var g = clahe(gray, w, h, 8, 8, 0.02);
  g = medfilt3(g, w, h);
  var bw = sobelVertical(g, w, h);
  var wLen = Math.max(15, Math.round(w / 40));
  bw = imclose(bw, w, h, wLen, 3, sc);
  bw = imdilate(bw, w, h, 3, 3, sc);
  bw = fillHoles(bw, w, h);
  return areaOpen(bw, w, h, Math.round(minArea));
}

/* ---------------- 裁剪 + 倾斜校正(对应 cropPlate/correctPlate) ---------------- */
function cropRGBA(rgba, w, h, box, pad) {
  var x1 = Math.max(0, Math.round(box[0]) - pad);
  var y1 = Math.max(0, Math.round(box[1]) - pad);
  var x2 = Math.min(w - 1, Math.round(box[0] + box[2] - 1) + pad);
  var y2 = Math.min(h - 1, Math.round(box[1] + box[3] - 1) + pad);
  var cw = x2 - x1 + 1, chh = y2 - y1 + 1;
  var out = new Uint8ClampedArray(cw * chh * 4);
  for (var y = 0; y < chh; y++) {
    var s = ((y1 + y) * w + x1) * 4, d = y * cw * 4;
    for (var i = 0; i < cw * 4; i++) { out[d + i] = rgba[s + i]; }
  }
  return { data: out, w: cw, h: chh, x0: x1, y0: y1 };
}

/* 旋转(loose): 输出尺寸扩大以容纳整幅旋转后的图像 */
function rotateLoose(src, w, h, ch, angDeg, bilinear) {
  var a = angDeg * Math.PI / 180;
  var ca = Math.cos(a), sa = Math.sin(a);
  var nw = Math.ceil(Math.abs(w * ca) + Math.abs(h * sa));
  var nh = Math.ceil(Math.abs(w * sa) + Math.abs(h * ca));
  var out = new Float32Array(nw * nh * ch);
  var cx = (w - 1) / 2, cy = (h - 1) / 2;
  var ncx = (nw - 1) / 2, ncy = (nh - 1) / 2;
  for (var y = 0; y < nh; y++) {
    for (var x = 0; x < nw; x++) {
      var dx = x - ncx, dy = y - ncy;
      var sx = ca * dx + sa * dy + cx;
      var sy = -sa * dx + ca * dy + cy;
      var o = (y * nw + x) * ch;
      if (bilinear) {
        var x0 = Math.floor(sx), y0 = Math.floor(sy);
        var fx = sx - x0, fy = sy - y0;
        for (var c = 0; c < ch; c++) {
          var v = 0;
          for (var j = 0; j < 2; j++) {
            var yy = y0 + j;
            if (yy < 0 || yy >= h) { continue; }
            for (var i2 = 0; i2 < 2; i2++) {
              var xx = x0 + i2;
              if (xx < 0 || xx >= w) { continue; }
              var wgt = (i2 ? fx : 1 - fx) * (j ? fy : 1 - fy);
              v += src[(yy * w + xx) * ch + c] * wgt;
            }
          }
          out[o + c] = v;
        }
      } else {
        var xi = Math.round(sx), yi = Math.round(sy);
        if (xi >= 0 && xi < w && yi >= 0 && yi < h) {
          for (var c2 = 0; c2 < ch; c2++) { out[o + c2] = src[(yi * w + xi) * ch + c2]; }
        }
      }
    }
  }
  return { data: out, w: nw, h: nh };
}

/* 由车牌掩膜的上下边界拟合直线, 估计倾斜角(度) */
function estimateSkew(mask, w, h) {
  var top = new Float64Array(w), bot = new Float64Array(w);
  var okTop = new Uint8Array(w), okBot = new Uint8Array(w);
  var any = false;
  for (var x = 0; x < w; x++) {
    var t = -1, b = -1;
    for (var y = 0; y < h; y++) { if (mask[y * w + x] > 0) { if (t < 0) { t = y; } b = y; } }
    if (t >= 0) { top[x] = t; bot[x] = b; okTop[x] = 1; okBot[x] = 1; any = true; }
  }
  if (!any) { return 0; }
  var a1 = fitLineAngle(top, okTop);
  var a2 = fitLineAngle(bot, okBot);
  var vals = [];
  if (!isNaN(a1)) { vals.push(a1); }
  if (!isNaN(a2)) { vals.push(a2); }
  if (!vals.length) { return 0; }
  return (vals[0] + vals[1]) / vals.length;
}

function fitLineAngle(yv, okv) {
  var xs = [], ys = [], i;
  for (i = 0; i < yv.length; i++) { if (okv[i]) { xs.push(i); ys.push(yv[i]); } }
  if (xs.length < 5) { return NaN; }
  var p = polyfit1(xs, ys, null);
  var res = [], med = [];
  for (i = 0; i < xs.length; i++) { var r = Math.abs(ys[i] - (p[0] * xs[i] + p[1])); res.push(r); med.push(r); }
  med.sort(function (a, b) { return a - b; });
  var m = med[Math.floor(med.length / 2)];
  var th = 2.5 * Math.max(1, m);
  var kx = [], ky = [];
  for (i = 0; i < xs.length; i++) { if (res[i] <= th) { kx.push(xs[i]); ky.push(ys[i]); } }
  if (kx.length >= 5) { p = polyfit1(kx, ky, null); }
  return Math.atan(p[0]) * 180 / Math.PI;
}

function polyfit1(xs, ys) {
  var n = xs.length, sx = 0, sy = 0, sxx = 0, sxy = 0;
  for (var i = 0; i < n; i++) { sx += xs[i]; sy += ys[i]; sxx += xs[i] * xs[i]; sxy += xs[i] * ys[i]; }
  var den = n * sxx - sx * sx;
  if (Math.abs(den) < 1e-12) { return [0, sy / n]; }
  var a = (n * sxy - sx * sy) / den;
  return [a, (sy - a * sx) / n];
}

function tightBox(mask, w, h) {
  var colSum = new Float64Array(w), rowSum = new Float64Array(h);
  var x, y, maxC = 0, maxR = 0;
  for (x = 0; x < w; x++) {
    var s = 0;
    for (y = 0; y < h; y++) { s += mask[y * w + x] > 0 ? 1 : 0; }
    colSum[x] = s; if (s > maxC) { maxC = s; }
  }
  for (y = 0; y < h; y++) {
    var s2 = 0;
    for (x = 0; x < w; x++) { s2 += mask[y * w + x] > 0 ? 1 : 0; }
    rowSum[y] = s2; if (s2 > maxR) { maxR = s2; }
  }
  if (maxC <= 0 || maxR <= 0) { return null; }
  var cs = -1, ce = -1, rs = -1, re = -1;
  for (x = 0; x < w; x++) { if (colSum[x] > 0.30 * maxC) { if (cs < 0) { cs = x; } ce = x; } }
  for (y = 0; y < h; y++) { if (rowSum[y] > 0.30 * maxR) { if (rs < 0) { rs = y; } re = y; } }
  if (cs < 0 || rs < 0) { return null; }
  return [cs, rs, ce - cs + 1, re - rs + 1];
}
/* ================================================================
 * 第三部分: 字符分割 / 模板匹配 / 主入口
 * ================================================================ */

/* 二值化阈值系数: 1.0 = 直接用 Otsu。略微调低可以补回被 Otsu 判为背景的细笔画
   (数字 1 的竖线、汉字细横等), 使笔画粗细更接近 MATLAB 版。 */
var THR_SCALE = 1.0;


/* ---------------- 光照归一化(除法模型, 兼容深底白字和浅底黑字) ---------------- */
function illumNormalize(gray, w, h) {
  var k = Math.max(5, Math.round(h * 0.6) | 1);
  var bg = boxMean(gray, w, h, k);
  var n = w * h, out = new Float32Array(n), i;
  var mn = Infinity, mx = -Infinity;
  for (i = 0; i < n; i++) {
    var v = gray[i] / (bg[i] + 1e-3);
    out[i] = v;
    if (v < mn) { mn = v; }
    if (v > mx) { mx = v; }
  }
  var d = mx - mn;
  if (d > 1e-6) { for (i = 0; i < n; i++) { out[i] = (out[i] - mn) / d; } }
  return out;
}

/* 清掉某一列中过长的连续笔画(车牌左右边框) */
function clearLongRunsCol(out, w, h, x, thr) {
  var y = 0;
  while (y < h) {
    if (out[y * w + x] > 0) {
      var s0 = y;
      while (y < h && out[y * w + x] > 0) { y++; }
      if (y - s0 > thr) { for (var k = s0; k < y; k++) { out[k * w + x] = 0; } }
    } else { y++; }
  }
}

/* 清掉某一行中过长的连续笔画(车牌上下边框) */
function clearLongRunsRow(out, w, h, y, thr) {
  var x = 0, ro = y * w;
  while (x < w) {
    if (out[ro + x] > 0) {
      var s0 = x;
      while (x < w && out[ro + x] > 0) { x++; }
      if (x - s0 > thr) { for (var k = s0; k < x; k++) { out[ro + k] = 0; } }
    } else { x++; }
  }
}

/* 去掉车牌外框(对应 segmentChars.m 的 removeFrame) */
function removeFrameLocal(bw, w, h) {
  var ring = Math.max(2, Math.round(0.03 * h));
  var out = bw.slice();
  var x, y, i;
  for (y = 0; y < ring; y++) { for (x = 0; x < w; x++) { out[y * w + x] = 0; out[(h - 1 - y) * w + x] = 0; } }
  for (x = 0; x < ring; x++) { for (y = 0; y < h; y++) { out[y * w + x] = 0; out[y * w + (w - 1 - x)] = 0; } }

  /* 车牌外框一定紧贴裁剪边界, 而字符都在中部。所以只在外圈窄带里清"超长直线":
       竖直方向: 外 6% 宽度的列中, 连续笔画超过 0.70*H 的判为左右边框;
       水平方向: 外 8% 高度的行中, 连续笔画超过 0.70*W 的判为上下边框。
     数字 1 的竖线位于车牌中部, 且长度通常不到 0.7*H, 因此不会被误删。 */
  var bandX = Math.max(2, Math.round(0.06 * w));
  var bandY = Math.max(2, Math.round(0.08 * h));
  var vThr = 0.70 * h, hThr = 0.70 * w;
  for (x = 0; x < bandX && x < w; x++) {
    clearLongRunsCol(out, w, h, x, vThr);
    clearLongRunsCol(out, w, h, w - 1 - x, vThr);
  }
  for (y = 0; y < bandY && y < h; y++) {
    clearLongRunsRow(out, w, h, y, hThr);
    clearLongRunsRow(out, w, h, h - 1 - y, hThr);
  }

  var sc = makeScratch();
  var hLine = imopen(out, w, h, Math.max(5, Math.round(0.60 * w)), 1, sc);
  var vLine = imopen(out, w, h, 1, Math.max(5, Math.round(0.85 * h)), sc);
  var frame = new Float32Array(w * h), any = false;
  for (i = 0; i < w * h; i++) {
    var f = (hLine[i] > 0 || vLine[i] > 0) ? 1 : 0;
    frame[i] = f;
    if (f) { any = true; }
  }
  if (!any) { return out; }
  frame = imdilate(frame, w, h, 3, 3, sc);
  for (i = 0; i < w * h; i++) { out[i] = (out[i] > 0 && frame[i] <= 0) ? 1 : 0; }
  return out;
}

/* 单字符归一化(对应 normalizeChar.m, mode='fill') */
function normalizeChar(bw, w, h, c0, c1, outH, outW, margin) {
  var minR = h, maxR = -1, minC = w, maxC = -1;
  var x, y;
  for (y = 0; y < h; y++) {
    for (x = c0; x <= c1; x++) {
      if (bw[y * w + x] > 0) {
        if (y < minR) { minR = y; }
        if (y > maxR) { maxR = y; }
        if (x < minC) { minC = x; }
        if (x > maxC) { maxC = x; }
      }
    }
  }
  var out = new Float32Array(outH * outW);
  if (maxR < 0) { return out; }
  var ch = maxR - minR + 1, cw = maxC - minC + 1;
  var availH = outH - 2 * margin, availW = outW - 2 * margin;
  var sub = new Float32Array(ch * cw);
  for (y = 0; y < ch; y++) {
    for (x = 0; x < cw; x++) { sub[y * cw + x] = bw[(minR + y) * w + minC + x]; }
  }
  var small = resize(sub, cw, ch, availW, availH, 1);
  var r0 = Math.floor((outH - availH) / 2), c0o = Math.floor((outW - availW) / 2);
  for (y = 0; y < availH; y++) {
    for (x = 0; x < availW; x++) {
      out[(r0 + y) * outW + c0o + x] = small[y * availW + x] > 0.5 ? 1 : 0;
    }
  }
  return out;
}

/* 字符特征: 降到 24x12 (对应 charFeature.m)
   注意: MATLAB 里 f = f(:)' 是"按列优先"展开(先第一列从上到下),
   模板 templates.mat 也是这个顺序, 所以这里必须同样按列优先展开,
   否则特征与模板错位, 匹配结果全是乱的。 */
function charFeature(img) {
  var small = resize(img, 16, 32, 12, 24, 1);
  var out = new Float32Array(288), k = 0;
  for (var x = 0; x < 12; x++) {
    for (var y = 0; y < 24; y++) { out[k++] = small[y * 12 + x]; }
  }
  return out;
}

/* 某个字符段内墨迹的最小外接框 -> {w, h, ar}
   ar(宽高比) 用来区分"细长笔画"和"正常宽度字符": 数字 1 的 ar 约 0.2,
   而 4/7/A 等都在 0.35 以上。 */
function inkBox(bw, w, h, c0, c1) {
  var minR = h, maxR = -1, minC = w, maxC = -1;
  for (var y = 0; y < h; y++) {
    for (var x = c0; x <= c1; x++) {
      if (bw[y * w + x] > 0) {
        if (y < minR) { minR = y; }
        if (y > maxR) { maxR = y; }
        if (x < minC) { minC = x; }
        if (x > maxC) { maxC = x; }
      }
    }
  }
  if (maxR < 0) { return { w: 0, h: 0, ar: 1 }; }
  var iw = maxC - minC + 1, ih = maxR - minR + 1;
  return { w: iw, h: ih, ar: iw / ih };
}

function logicalRuns(act) {
  var runs = [], s = -1;
  for (var i = 0; i < act.length; i++) {
    if (act[i] && s < 0) { s = i; }
    else if (!act[i] && s >= 0) { runs.push([s, i - 1]); s = -1; }
  }
  if (s >= 0) { runs.push([s, act.length - 1]); }
  return runs;
}

function mergeRuns(runs, maxGap) {
  if (!runs.length) { return runs; }
  var out = [runs[0].slice()];
  for (var i = 1; i < runs.length; i++) {
    if (runs[i][0] - out[out.length - 1][1] - 1 <= maxGap) { out[out.length - 1][1] = runs[i][1]; }
    else { out.push(runs[i].slice()); }
  }
  return out;
}

function adjustToCount(runs, proj, n) {
  var guard = 0;
  while (runs.length < n && guard++ < 50) {
    var wi = 0, wm = -1;
    for (var i = 0; i < runs.length; i++) {
      var ww = runs[i][1] - runs[i][0] + 1;
      if (ww > wm) { wm = ww; wi = i; }
    }
    if (wm < 8) { break; }
    var a = runs[wi][0], b = runs[wi][1];
    var m = Math.round(0.20 * (b - a));
    var s0 = a + m, s1 = b - m;
    if (s1 - s0 + 1 < 5) { break; }
    var cut = s0, bestV = Infinity;
    for (var x = s0; x <= s1; x++) { if (proj[x] < bestV) { bestV = proj[x]; cut = x; } }
    runs.splice(wi, 1, [a, cut], [cut + 1, b]);
  }
  while (runs.length > n) {
    var gi = 0, gm = Infinity, sumW = 0;
    for (var k = 0; k < runs.length; k++) { sumW += runs[k][1] - runs[k][0] + 1; }
    for (var k2 = 0; k2 + 1 < runs.length; k2++) {
      var gap = runs[k2 + 1][0] - runs[k2][1] - 1;
      if (gap < gm) { gm = gap; gi = k2; }
    }
    if (gm > 0.8 * (sumW / runs.length)) { break; }
    runs[gi][1] = runs[gi + 1][1];
    runs.splice(gi + 1, 1);
  }
  return runs;
}

/* 自校准定字数(对应 segmentChars.m 的 chooseCount)
   不要用 span/W 这类固定比例估字数: 7 位普通牌与 8 位新能源牌裁紧后 span/W
   都在 0.87 左右, 固定比例必然把 8 位牌(新能源绿牌)当成 7 位, 结果是被强制
   合并掉一个字, 整牌全错。改成用切分结果自校准:
   判据是相邻字符"中心间距"越均匀越好 —— 两个字被并成一段(间距约 2 倍)、
   或一个字被切成两段(间距约 0.5 倍)都会让间距忽大忽小, 所以间距的变异系数
   (标准差/均值)最小的那个候选就是最可能的真实字数。 */
function chooseCount(runs, proj) {
  var best = Infinity, bestRuns = runs, n, i, r, d, m, v;
  for (n = 6; n <= 8; n++) {
    var copy = [];
    for (i = 0; i < runs.length; i++) { copy.push(runs[i].slice()); }
    r = adjustToCount(copy, proj, n);
    if (r.length !== n) { continue; }
    d = [];
    for (i = 1; i < r.length; i++) {
      d.push((r[i][0] + r[i][1]) / 2 - (r[i - 1][0] + r[i - 1][1]) / 2);
    }
    if (d.length < 3) { continue; }
    m = 0;
    for (i = 0; i < d.length; i++) { m += d[i]; }
    m /= d.length;
    if (m <= 1e-9) { continue; }
    v = 0;
    for (i = 0; i < d.length; i++) { v += (d[i] - m) * (d[i] - m); }
    v /= d.length;
    if (Math.sqrt(v) / m < best - 1e-9) { best = Math.sqrt(v) / m; bestRuns = r; }
  }
  return bestRuns;
}

/* 去掉贴在裁剪边界上的窄条连通域(车牌左右边框没被清干净的残留)。
   这类残留会多出一个"字符", 并让字符总跨度 span 变大,
   进而使估算字数 nEst 偏大(7 位牌被当成 8 位)。 */
function dropEdgeSlivers(bw, w, h) {
  var r = label8(bw, w, h);
  var n = r.stats.length, i;
  if (n <= 1) { return bw; }
  var ws = [];
  for (i = 0; i < n; i++) { ws.push(r.stats[i].maxX - r.stats[i].minX + 1); }
  ws.sort(function (a, b) { return a - b; });
  var medW = ws[Math.floor(ws.length / 2)];
  var thin = Math.max(4, Math.round(0.35 * medW));
  var bandX = Math.max(2, Math.round(0.05 * w));
  var drop = new Uint8Array(n + 1);
  for (i = 0; i < n; i++) {
    var st = r.stats[i];
    var wdt = st.maxX - st.minX + 1;
    var nearEdge = (st.minX < bandX) || (st.maxX > w - 1 - bandX);
    if (nearEdge && wdt <= thin) { drop[i + 1] = 1; }
  }
  var out = new Float32Array(w * h);
  for (i = 0; i < w * h; i++) { out[i] = drop[r.labels[i]] ? 0 : bw[i]; }
  return out;
}

/* 字符分割(对应 segmentChars.m) */
function segmentChars(plateGray, w, h) {
  var n = w * h, i;
  var g = clahe(plateGray, w, h, 4, 8, 0.02);
  var t = otsu(g) * THR_SCALE;
  var bw = new Float32Array(n), white = 0;
  for (i = 0; i < n; i++) { var v = g[i] > t ? 1 : 0; bw[i] = v; white += v; }
  if (white > 0.45 * n) { for (i = 0; i < n; i++) { bw[i] = 1 - bw[i]; } }
  bw = removeFrameLocal(bw, w, h);
  bw = areaOpen(bw, w, h, Math.max(4, Math.round(0.0015 * w * h)));
  bw = dropEdgeSlivers(bw, w, h);

  var proj = new Float32Array(w), x, y;
  for (x = 0; x < w; x++) {
    var s = 0;
    for (y = 0; y < h; y++) { s += bw[y * w + x]; }
    proj[x] = s;
  }
  var pm = new Float32Array(w);
  for (x = 0; x < w; x++) {
    var a = Math.max(0, x - 1), b = Math.min(w - 1, x + 1), acc = 0;
    for (var j = a; j <= b; j++) { acc += proj[j]; }
    pm[x] = acc / (b - a + 1);
  }
  var thr = Math.max(1, 0.05 * h);
  var act = new Uint8Array(w);
  for (x = 0; x < w; x++) { act[x] = pm[x] > thr ? 1 : 0; }
  var runs = mergeRuns(logicalRuns(act), Math.max(2, Math.round(0.020 * w)));
  var minW = Math.max(2, Math.round(0.015 * w));
  runs = runs.filter(function (r) { return (r[1] - r[0] + 1) >= minW; });
  if (runs.length) {
    runs = chooseCount(runs, pm);
  }
  var chars = [], ink = [];
  for (var k = 0; k < runs.length; k++) {
    chars.push(normalizeChar(bw, w, h, runs[k][0], runs[k][1], 32, 16, 2));
    ink.push(inkBox(bw, w, h, runs[k][0], runs[k][1]));
  }
  return { chars: chars, ink: ink, bw: bw, bounds: runs };
}

/* ---------------- 车牌制式(对应 plateFormat.m) ---------------- */
var PLATE_PROV = '京津冀晋蒙辽吉黑沪苏浙皖闽赣鲁豫鄂湘粤桂琼渝川贵云藏陕甘青宁新';
var PLATE_LETTERS = 'ABCDEFGHJKLMNPQRSTUVWXYZ';   /* 车牌不用 I 和 O */
var PLATE_ALNUM = PLATE_LETTERS + '0123456789';

/* 依据 GA 36-2018:
     普通汽车(蓝底/黄底单排)  7 位 = 省简称 + 发牌机关字母 + 5 位字母数字
     新能源小型车(绿底单排)   8 位 = 省简称 + 发牌机关字母 + 字母 + 5 位字母数字 */
function plateFormat(n) {
  var sets = [], label, k;
  if (n === 8) {
    label = '新能源 8 位';
    sets = [PLATE_PROV, PLATE_LETTERS, PLATE_LETTERS];
    for (k = 3; k < 8; k++) { sets.push(PLATE_ALNUM); }
  } else if (n === 7) {
    label = '普通 7 位';
    sets = [PLATE_PROV, PLATE_LETTERS];
    for (k = 2; k < 7; k++) { sets.push(PLATE_ALNUM); }
  } else {
    label = n + ' 位(未收录制式)';
    for (k = 0; k < n; k++) {
      sets.push(k === 0 ? PLATE_PROV : (k === 1 ? PLATE_LETTERS : PLATE_ALNUM));
    }
  }
  return { n: n, label: label, sets: sets };
}

function restrictSet(set, allowed) {
  if (!allowed) { return set; }
  var items = [], labels = [];
  for (var i = 0; i < set.items.length; i++) {
    if (allowed.indexOf(set.items[i].label) >= 0) {
      items.push(set.items[i]);
      labels.push(set.items[i].label);
    }
  }
  return items.length ? { labels: labels, items: items } : set;
}

/* 第 k 位允许的模板集合: 先按位选模板组, 再用制式收窄 */
function positionSet(S, fmt, k) {
  var set = (k === 0) ? S.chinese : (k === 1 ? S.letters : S.alnum);
  if (fmt && fmt.sets && k < fmt.sets.length) { set = restrictSet(set, fmt.sets[k]); }
  return set;
}

/* ---------------- 模板库 ---------------- */
var TEMPLATES = null;

function b64ToBytes(str) {
  var bin = (typeof atob === 'function') ? atob(str) : Buffer.from(str, 'base64').toString('binary');
  var u8 = new Uint8Array(bin.length);
  for (var i = 0; i < bin.length; i++) { u8[i] = bin.charCodeAt(i); }
  return u8;
}

function prepSet(labels, featB64) {
  var out = { labels: labels.slice(), items: [] };
  for (var i = 0; i < featB64.length; i++) {
    var raw = b64ToBytes(featB64[i]);
    var f = new Float32Array(raw.length);
    var gb = new Uint8Array(raw.length);
    var mean = 0, ng = 0;
    for (var k = 0; k < raw.length; k++) {
      var v = raw[k] / 255;
      f[k] = v;
      mean += v;
      if (v > 0.5) { gb[k] = 1; ng++; }
    }
    mean /= raw.length;
    var cen = new Float32Array(raw.length), norm2 = 0;
    for (var k2 = 0; k2 < raw.length; k2++) { var d = f[k2] - mean; cen[k2] = d; norm2 += d * d; }
    out.items.push({ label: labels[i], gb: gb, ones: ng, cen: cen, norm: Math.sqrt(norm2), feat: f });
  }
  return out;
}

function setTemplates(json) {
  var S = {
    outH: json.outH, outW: json.outW,
    chinese: prepSet(json.chinese.labels, json.chinese.feat),
    letters: prepSet(json.letters.labels, json.letters.feat),
    digits: prepSet(json.digits.labels, json.digits.feat)
  };
  S.alnum = {
    labels: S.letters.labels.concat(S.digits.labels),
    items: S.letters.items.concat(S.digits.items)
  };
  TEMPLATES = S;
  return S;
}

function bestMatch(f, set, opts) {
  var n = f.length, i, k;
  var fb = new Uint8Array(n), nf = 0, meanF = 0;
  for (i = 0; i < n; i++) { if (f[i] > 0.5) { fb[i] = 1; nf++; } meanF += f[i]; }
  meanF /= n;
  var fa = new Float32Array(n), acc = 0;
  for (i = 0; i < n; i++) { var d = f[i] - meanF; fa[i] = d; acc += d * d; }
  var nfNorm = Math.sqrt(acc);
  var label = '?', score = -Infinity;
  for (k = 0; k < set.items.length; k++) {
    var it = set.items[k];
    if (opts && opts.only && it.label !== opts.only) { continue; }
    var inter = 0, uni = 0;
    for (i = 0; i < n; i++) {
      var a = fb[i], b = it.gb[i];
      if (a & b) { inter++; }
      if (a | b) { uni++; }
    }
    var iou = uni > 0 ? inter / uni : 0;
    var dot = 0;
    for (i = 0; i < n; i++) { dot += fa[i] * it.cen[i]; }
    var den = nfNorm * it.norm;
    var cc = den < 1e-12 ? 0 : dot / den;
    var sc = 0.65 * iou + 0.35 * cc;
    if (sc > score) { score = sc; label = it.label; }
  }
  return { label: label, score: score };
}

function recognizeChars(charImages, inkBoxes) {
  var S = TEMPLATES;
  if (!S) { throw new Error('templates not loaded'); }
  var fmt = plateFormat(charImages.length);
  var chars = [], scores = [], n = charImages.length;
  for (var k = 0; k < n; k++) {
    var f = charFeature(charImages[k]);
    var set = positionSet(S, fmt, k);   /* 只在该位允许的字符里找最优 */
    var r = bestMatch(f, set);
    /* 汉字之后的位置上, 如果这一段墨迹特别细长, 基本只可能是数字 1。
       归一化的 'fill' 模式会把细长笔画横向拉伸得很宽, 长得像 4, 容易误判,
       所以这里给一个很小的加分把它纠回来(只影响极窄的段)。 */
    if (k >= 2 && inkBoxes && inkBoxes[k] && inkBoxes[k].ar > 0 && inkBoxes[k].ar < 0.30) {
      var alt = bestMatch(f, restrictSet(set, '1'));
      if (alt.score + 0.06 > r.score) { r = alt; }
    }
    var lb = r.label;
    if (k === 0 && r.score < 0.45) { lb = '*'; }
    chars.push(lb);
    scores.push(r.score);
  }
  return { text: chars.join(''), chars: chars, scores: scores, format: fmt.label, charCount: n };
}

/* ---------------- 四角检测 + 透视校正(对应 correctPlate.m) ---------------- */

function hyp2(u, v) { return Math.hypot(u, v); }

/* 只保留面积最大的连通域(车牌外框残留常是贴边的细长条, 会把极值点带偏) */
function keepLargest(mask, w, h) {
  var r = label8(mask, w, h);
  if (r.stats.length <= 1) { return mask; }
  var bi = 0, i;
  for (i = 1; i < r.stats.length; i++) { if (r.stats[i].area > r.stats[bi].area) { bi = i; } }
  var out = new Float32Array(w * h);
  for (i = 0; i < w * h; i++) { if (r.labels[i] === bi + 1) { out[i] = mask[i]; } }
  return out;
}

/* 两向量夹角与 90 度的偏差(度) */
function rightAngleDev(u, v) {
  var c = (u[0] * v[0] + u[1] * v[1]) / Math.max(1e-9, hyp2(u[0], u[1]) * hyp2(v[0], v[1]));
  c = Math.max(-1, Math.min(1, c));
  return Math.abs(90 - Math.acos(c) * 180 / Math.PI);
}

function quadEdges(q) {
  var e = [];
  for (var i = 0; i < 4; i++) {
    e.push([q[(i + 1) % 4][0] - q[i][0], q[(i + 1) % 4][1] - q[i][1]]);
  }
  return e;
}

/* 四边形合理性: 有限、没跑出图像太远、是凸四边形、长宽比像车牌、掩膜基本填满它 */
function quadOK(q, w, h, mask) {
  var i;
  for (i = 0; i < 4; i++) {
    if (!isFinite(q[i][0]) || !isFinite(q[i][1])) { return false; }
    if (q[i][0] < -0.20 * w || q[i][0] > 1.20 * w) { return false; }
    if (q[i][1] < -0.20 * h || q[i][1] > 1.20 * h) { return false; }
  }
  var e = quadEdges(q), cr = [];
  for (i = 0; i < 4; i++) {
    cr.push(e[i][0] * e[(i + 1) % 4][1] - e[i][1] * e[(i + 1) % 4][0]);
  }
  var allPos = true, allNeg = true;
  for (i = 0; i < 4; i++) {
    if (cr[i] <= 0) { allPos = false; }
    if (cr[i] >= 0) { allNeg = false; }
  }
  if (!allPos && !allNeg) { return false; }
  var wAvg = (hyp2(e[0][0], e[0][1]) + hyp2(e[2][0], e[2][1])) / 2;
  var hAvg = (hyp2(e[1][0], e[1][1]) + hyp2(e[3][0], e[3][1])) / 2;
  if (wAvg < 0.45 * w || hAvg < 0.45 * h) { return false; }
  var ar = wAvg / Math.max(1e-9, hAvg);
  if (ar < 1.8 || ar > 4.5) { return false; }
  var area = 0, nz = 0;
  for (i = 0; i < 4; i++) {
    area += q[i][0] * q[(i + 1) % 4][1] - q[(i + 1) % 4][0] * q[i][1];
  }
  area = Math.abs(area) / 2;
  for (i = 0; i < mask.length; i++) { if (mask[i] > 0) { nz++; } }
  if (nz < 0.55 * area || nz > 1.60 * area) { return false; }
  return true;
}

/* 由掩膜的"极值点"取四角: 车牌是凸四边形, 而凸多边形上线性函数的最值一定在
   顶点取到, 所以 x+y 最小 -> 左上, x+y 最大 -> 右下, x-y 最大 -> 右上,
   x-y 最小 -> 左下。与车牌是转了、歪了、还是被拍成了梯形都无关。
   (试过再拟合四边直线去精修: 裁剪框是掩膜外接矩形, 车牌自己的边常贴着裁剪
   边界, 拟合容易被截断点带跑, 反而变差, 所以不用。) */
function detectQuad(mask, w, h) {
  if (w < 24 || h < 8) { return null; }
  var m = imclose(mask, w, h, 5, 5, makeScratch());
  m = fillHoles(m, w, h);
  m = areaOpen(m, w, h, 20);
  m = keepLargest(m, w, h);
  var sMin = Infinity, sMax = -Infinity, dMin = Infinity, dMax = -Infinity;
  var iSM = -1, iSMx = -1, iDMx = -1, iDM = -1, n = 0;
  for (var y = 0; y < h; y++) {
    for (var x = 0; x < w; x++) {
      if (m[y * w + x] <= 0) { continue; }
      n++;
      var sv = x + y, dv = x - y;
      if (sv < sMin) { sMin = sv; iSM = y * w + x; }
      if (sv > sMax) { sMax = sv; iSMx = y * w + x; }
      if (dv > dMax) { dMax = dv; iDMx = y * w + x; }
      if (dv < dMin) { dMin = dv; iDM = y * w + x; }
    }
  }
  if (n < 50) { return null; }
  var quad = [iSM, iDMx, iSMx, iDM].map(function (idx) {
    return [idx % w, Math.floor(idx / w)];
  });
  for (var a = 0; a < 4; a++) {
    for (var b = a + 1; b < 4; b++) {
      if (quad[a][0] === quad[b][0] && quad[a][1] === quad[b][1]) { return null; }
    }
  }
  return quadOK(quad, w, h, m) ? quad : null;
}

/* 四角是不是"明显被拍成了梯形"。纯旋转的车牌三项都接近 0, 走旋转校正即可;
   小车牌(几十像素宽)的掩膜是台阶状的, 极值点会抖一两个像素, 折算成角度能到
   七八度, 容易误判成梯形, 所以小牌一律不做透视。 */
function hasPerspective(q) {
  var e = quadEdges(q);
  var wT = hyp2(e[0][0], e[0][1]), wB = hyp2(e[2][0], e[2][1]);
  var hL = hyp2(e[3][0], e[3][1]), hR = hyp2(e[1][0], e[1][1]);
  var wAvg = (wT + wB) / 2, hAvg = (hL + hR) / 2;
  if (hAvg < 32 || wAvg < 96) { return false; }
  var dw = Math.abs(wT - wB) / Math.max(1e-9, wAvg);
  var dh = Math.abs(hL - hR) / Math.max(1e-9, hAvg);
  var neg = function (v) { return [-v[0], -v[1]]; };
  var dev = Math.max(
    rightAngleDev(e[0], neg(e[3])), rightAngleDev(e[1], neg(e[0])),
    rightAngleDev(e[2], neg(e[1])), rightAngleDev(e[3], neg(e[2])));
  return dw > 0.08 || dh > 0.08 || dev > 10;
}

/* 高斯消元解 8x8 线性方程组(列主元), 奇异时返回 null */
function solve8(A, b) {
  var n = 8, i, j, k;
  for (i = 0; i < n; i++) { A[i].push(b[i]); }
  for (i = 0; i < n; i++) {
    var piv = i;
    for (k = i + 1; k < n; k++) { if (Math.abs(A[k][i]) > Math.abs(A[piv][i])) { piv = k; } }
    if (Math.abs(A[piv][i]) < 1e-12) { return null; }
    var tmp = A[i]; A[i] = A[piv]; A[piv] = tmp;
    for (k = i + 1; k < n; k++) {
      var f = A[k][i] / A[i][i];
      if (f === 0) { continue; }
      for (j = i; j <= n; j++) { A[k][j] -= f * A[i][j]; }
    }
  }
  var x = new Array(n);
  for (i = n - 1; i >= 0; i--) {
    var acc = A[i][n];
    for (j = i + 1; j < n; j++) { acc -= A[i][j] * x[j]; }
    x[i] = acc / A[i][i];
  }
  return x;
}

/* 求把输出矩形映射到车牌四边形的单应矩阵([u v 1] ~ [x y 1] * H, 行优先 9 个数) */
function homographyRectToQuad(quad, outW, outH) {
  var src = [[1, 1], [outW, 1], [outW, outH], [1, outH]];
  var A = [], b = [];
  for (var i = 0; i < 4; i++) {
    var x = src[i][0], y = src[i][1], u = quad[i][0], v = quad[i][1];
    A.push([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.push(u);
    A.push([0, 0, 0, x, y, 1, -v * x, -v * y]); b.push(v);
  }
  var h = solve8(A, b);
  if (!h) { return null; }
  return [h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7], 1];
}

/* 把四边形区域重采样成 outW x outH 的矩形(双线性插值) */
function warpQuad(src, sw, sh, ch, quad, outW, outH) {
  var Hm = homographyRectToQuad(quad, outW, outH);
  var out = new Float32Array(outW * outH * ch);
  if (!Hm) { return { data: out, w: outW, h: outH }; }
  for (var v = 0; v < outH; v++) {
    for (var u = 0; u < outW; u++) {
      var x = u + 1, y = v + 1;
      var wq = Hm[6] * x + Hm[7] * y + Hm[8];
      var o = (v * outW + u) * ch;
      if (Math.abs(wq) < 1e-12) { continue; }
      var sx = (Hm[0] * x + Hm[1] * y + Hm[2]) / wq;
      var sy = (Hm[3] * x + Hm[4] * y + Hm[5]) / wq;
      var x0 = Math.floor(sx), y0 = Math.floor(sy);
      var fx = sx - x0, fy = sy - y0;
      for (var c = 0; c < ch; c++) {
        var acc = 0;
        for (var j = 0; j < 2; j++) {
          var yy = y0 + j;
          if (yy < 0 || yy >= sh) { continue; }
          for (var i2 = 0; i2 < 2; i2++) {
            var xx = x0 + i2;
            if (xx < 0 || xx >= sw) { continue; }
            acc += src[(yy * sw + xx) * ch + c] * ((i2 ? fx : 1 - fx) * (j ? fy : 1 - fy));
          }
        }
        out[o + c] = acc;
      }
    }
  }
  return { data: out, w: outW, h: outH };
}

/* ---------------- 主入口 ---------------- */
function downscaleRGBA(rgba, w, h, maxSize) {
  var m = Math.max(w, h);
  if (m <= maxSize) { return { data: rgba, w: w, h: h, scale: 1 }; }
  var s = maxSize / m;
  var dw = Math.max(1, Math.round(w * s)), dh = Math.max(1, Math.round(h * s));
  var f = new Float32Array(w * h * 4);
  for (var i = 0; i < f.length; i++) { f[i] = rgba[i]; }
  var r = resize(f, w, h, dw, dh, 4);
  var u8 = new Uint8ClampedArray(dw * dh * 4);
  for (var j = 0; j < u8.length; j++) { u8[j] = r[j]; }
  return { data: u8, w: dw, h: dh, scale: s };
}

function cropF32(src, w, h, box, pad) {
  var x1 = Math.max(0, Math.round(box[0]) - pad);
  var y1 = Math.max(0, Math.round(box[1]) - pad);
  var x2 = Math.min(w - 1, Math.round(box[0] + box[2] - 1) + pad);
  var y2 = Math.min(h - 1, Math.round(box[1] + box[3] - 1) + pad);
  var cw = x2 - x1 + 1, chh = y2 - y1 + 1;
  var out = new Float32Array(cw * chh);
  for (var y = 0; y < chh; y++) {
    for (var x = 0; x < cw; x++) { out[y * cw + x] = src[(y1 + y) * w + x1 + x]; }
  }
  return { data: out, w: cw, h: chh };
}

function cropRGBA(rgba, w, h, box, pad) {
  var x1 = Math.max(0, Math.round(box[0]) - pad);
  var y1 = Math.max(0, Math.round(box[1]) - pad);
  var x2 = Math.min(w - 1, Math.round(box[0] + box[2] - 1) + pad);
  var y2 = Math.min(h - 1, Math.round(box[1] + box[3] - 1) + pad);
  var cw = x2 - x1 + 1, chh = y2 - y1 + 1;
  var out = new Uint8ClampedArray(cw * chh * 4);
  for (var y = 0; y < chh; y++) {
    var s = ((y1 + y) * w + x1) * 4, d = y * cw * 4;
    for (var i = 0; i < cw * 4; i++) { out[d + i] = rgba[s + i]; }
  }
  return { data: out, w: cw, h: chh, x0: x1, y0: y1 };
}

function run(imgData, options) {
  options = options || {};
  var T = {};
  var t0 = Date.now();
  var W0 = imgData.width, H0 = imgData.height, rgba0 = imgData.data;
  var maxSize = options.maxSize || 1600;

  var work = downscaleRGBA(rgba0, W0, H0, maxSize);
  T.downscale = Date.now() - t0;
  var t1 = Date.now();
  var loc = locatePlate(work.data, work.w, work.h);
  T.locate = Date.now() - t1;

  var res = {
    ok: false, text: '', chars: [], scores: [], box: null, reason: '',
    scoreInfo: {
      score: loc.score, source: loc.source, extent: loc.extent,
      colorFrac: loc.colorFrac, edgesFrac: loc.edgesFrac, nCand: loc.nCand
    },
    plateColor: null, bwPlate: null, charBitmaps: null, charBounds: null,
    work: { w: work.w, h: work.h }, timing: T
  };
  if (!loc.box || !loc.valid) {
    res.reason = loc.reject || '未检测到车牌';
    T.total = Date.now() - t0;
    return res;
  }

  var sx = W0 / work.w, sy = H0 / work.h;
  var boxOrig = [loc.box[0] * sx, loc.box[1] * sy, loc.box[2] * sx, loc.box[3] * sy];
  res.box = boxOrig;

  /* 用工作分辨率的掩膜估计倾斜角(角度与尺度无关), 再对原分辨率车牌做旋转 */
  var padW = 2;
  var maskC = cropF32(loc.mask, work.w, work.h, loc.box, padW);
  var useAng = 0;
  var ang0 = estimateSkew(maskC.data, maskC.w, maskC.h);
  if (Math.abs(ang0) > 0.8 && Math.abs(ang0) < 25) {
    var c1 = rotateLoose(maskC.data, maskC.w, maskC.h, 1, ang0, false);
    var s1 = Math.abs(estimateSkew(c1.data, c1.w, c1.h));
    var c2 = rotateLoose(maskC.data, maskC.w, maskC.h, 1, -ang0, false);
    var s2 = Math.abs(estimateSkew(c2.data, c2.w, c2.h));
    useAng = (s1 <= s2) ? ang0 : -ang0;
  }
  res.skew = ang0;
  res.appliedSkew = useAng;

  var t2 = Date.now();
  var pad = Math.max(2, Math.round(2 * sx));
  var crop = cropRGBA(rgba0, W0, H0, boxOrig, pad);
  res.dbgCropPre = crop;
  var mw = maskC.w, mh = maskC.h;

  /* 透视校正: 掩膜四角明显是个梯形(斜拍)时才做, 旋转和透视一次解决;
     四角基本还是矩形时走下面的旋转校正 —— 本来就正的图没必要多插值一次,
     多一次重采样就多一次模糊, 对模板匹配是纯亏。 */
  var quadW = detectQuad(maskC.data, mw, mh);
  var quadFull = null;
  if (quadW) {
    var mx0 = Math.max(0, Math.round(loc.box[0]) - padW);
    var my0 = Math.max(0, Math.round(loc.box[1]) - padW);
    var qf = [], qi;
    for (qi = 0; qi < 4; qi++) {
      qf.push([(mx0 + quadW[qi][0]) * sx - crop.x0, (my0 + quadW[qi][1]) * sy - crop.y0]);
    }
    if (hasPerspective(qf)) { quadFull = qf; }
  }
  res.maskQuad = quadW;
  res.quad = quadFull;
  if (quadFull) {
    var qwT = hyp2(quadFull[1][0] - quadFull[0][0], quadFull[1][1] - quadFull[0][1]);
    var qwB = hyp2(quadFull[2][0] - quadFull[3][0], quadFull[2][1] - quadFull[3][1]);
    var qhL = hyp2(quadFull[3][0] - quadFull[0][0], quadFull[3][1] - quadFull[0][1]);
    var qhR = hyp2(quadFull[2][0] - quadFull[1][0], quadFull[2][1] - quadFull[1][1]);
    var ow = Math.round(64 * (qwT + qwB) / Math.max(1e-6, qhL + qhR));
    crop = warpQuad(crop.data, crop.w, crop.h, 4, quadFull, clamp(ow, 24, 512), 64);
    res.geom = 'perspective';
  } else if (useAng !== 0) {
    var rotC = rotateLoose(crop.data, crop.w, crop.h, 4, useAng, true);
    var rotM = rotateLoose(maskC.data, mw, mh, 1, useAng, false);
    res.dbgRot = { w: rotC.w, h: rotC.h, data: rotC.data };
    res.dbgMaskDims = [rotM.w, rotM.h, mw, mh, crop.w, crop.h, useAng];
    var bb = tightBox(rotM.data, rotM.w, rotM.h);
    res.dbgBB = bb ? bb.slice() : null;
    if (bb) {
      var k = crop.w / mw;
      var bx = Math.round(bb[0] * k), by = Math.round(bb[1] * k);
      var bwid = Math.round(bb[2] * k), bhei = Math.round(bb[3] * k);
      bx = clamp(bx, 0, rotC.w - 1); by = clamp(by, 0, rotC.h - 1);
      bwid = clamp(bwid, 1, rotC.w - bx); bhei = clamp(bhei, 1, rotC.h - by);
      crop = cropRGBA(rotC.data, rotC.w, rotC.h, [bx, by, bwid, bhei], 0);
    }
    res.geom = 'rotate';
  } else {
    res.geom = 'none';
  }
  T.rotate = Date.now() - t2;

  /* 高度归一化到 64 */
  var t3 = Date.now();
  var plateRGBA = crop;
  if (crop.h >= 8 && Math.abs(64 / crop.h - 1) > 0.01) {
    var fIn = new Float32Array(crop.w * crop.h * 4);
    for (var i = 0; i < fIn.length; i++) { fIn[i] = crop.data[i]; }
    var dh = 64, dw2 = Math.max(8, Math.round(crop.w * (64 / crop.h)));
    var fOut = resize(fIn, crop.w, crop.h, dw2, dh, 4);
    var u8 = new Uint8ClampedArray(dw2 * dh * 4);
    for (var j = 0; j < u8.length; j++) { u8[j] = fOut[j]; }
    plateRGBA = { data: u8, w: dw2, h: dh };
  }
  T.normalize = Date.now() - t3;

  var t4 = Date.now();
  var pg = rgbaToGrayF(plateRGBA.data, plateRGBA.w, plateRGBA.h);
  var seg = segmentChars(pg, plateRGBA.w, plateRGBA.h);
  T.segment = Date.now() - t4;

  var t5 = Date.now();
  var rec = recognizeChars(seg.chars, seg.ink);
  T.recognize = Date.now() - t5;

  res.ok = seg.chars.length > 0 && rec.text.length > 0;
  res.text = rec.text;
  res.chars = rec.chars;
  res.scores = rec.scores;
  res.plateColor = plateRGBA;
  res.bwPlate = { data: seg.bw, w: plateRGBA.w, h: plateRGBA.h };
  res.charBitmaps = seg.chars;
  res.charBounds = seg.bounds;
  res.charInk = seg.ink;
  res.reason = res.ok ? '' : '车牌定位成功但未能分割出字符';
  T.total = Date.now() - t0;
  return res;
}


var API = {
  version: '1.0',
  run: run,
  setTemplates: setTemplates,
  ready: function () { return TEMPLATES !== null; },
  setThrScale: function (v) { THR_SCALE = v; },
  _internals: {
    illumNormalize: illumNormalize, otsu: otsu, removeFrameLocal: removeFrameLocal,
    areaOpen: areaOpen, normalizeChar: normalizeChar, charFeature: charFeature,
    bestMatch: bestMatch, segmentChars: segmentChars, boxMean: boxMean,
    imopen: imopen, imerode: imerode, imdilate: imdilate, label8: label8,
    clahe: clahe, medfilt3: medfilt3, sobelVertical: sobelVertical,
    rotateLoose: rotateLoose, rgbaToGrayF: rgbaToGrayF, resize: resize,
    estimateSkew: estimateSkew, tightBox: tightBox, makeScratch: makeScratch,
    detectQuad: detectQuad, hasPerspective: hasPerspective, warpQuad: warpQuad,
    plateFormat: plateFormat, chooseCount: chooseCount,
    getTemplates: function () { return TEMPLATES; }
  }
};

if (typeof module !== 'undefined' && module.exports) { module.exports = API; }
global.LPREngine = API;

})(typeof window !== 'undefined' ? window : globalThis);

/* =====================================================================
 * 页面逻辑: 选图 / 拖拽 / 粘贴 -> 调 LPREngine 识别 -> 渲染结果
 * ===================================================================== */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  /* 字符模板由 MATLAB 的 templates.mat 导出, 内嵌在页面里 */
  LPREngine.setTemplates(JSON.parse($('lpr-templates').textContent));

  var drop = $('drop'), fileInput = $('file'), spin = $('spin'), spinText = $('spinText');
  var plateTextEl = $('plateText'), metaEl = $('meta'), charsEl = $('chars');
  var detailEl = $('detail'), kvEl = $('kv'), stagesWrap = $('stagesWrap');
  var work = document.createElement('canvas');
  var last = null;

  var MAX_SIDE = 2200;   /* 先按长边 2200 缩一次, 超大照片也不至于爆内存 */

  function setBusy(on, msg) {
    spin.classList.toggle('on', !!on);
    drop.classList.toggle('busy', !!on);
    if (msg) { spinText.textContent = msg; }
  }

  function showText(t, cls) {
    plateTextEl.textContent = t;
    plateTextEl.className = cls || '';
  }

  function drawStage(canvas, w, h) {
    canvas.width = w; canvas.height = h;
    return canvas.getContext('2d');
  }

  /* 把 RGBA(Uint8ClampedArray) 画到 canvas 上 */
  function putRGBA(canvas, rgba, w, h) {
    canvas.width = w; canvas.height = h;
    var ctx = canvas.getContext('2d');
    var img = ctx.createImageData(w, h);
    img.data.set(rgba);
    ctx.putImageData(img, 0, 0);
  }

  /* 把 0/1 灰度图放大 scale 倍画出来; inkIsBlack=true 时前景画成黑 */
  function putBinary(canvas, map, w, h, scale, inkIsBlack) {
    var nw = w * scale, nh = h * scale;
    canvas.width = nw; canvas.height = nh;
    var ctx = canvas.getContext('2d');
    var img = ctx.createImageData(nw, nh);
    var d = img.data;
    for (var y = 0; y < nh; y++) {
      for (var x = 0; x < nw; x++) {
        var v = map[((y / scale) | 0) * w + ((x / scale) | 0)] > 0;
        var g = v === !!inkIsBlack ? 0 : 255;
        var i = (y * nw + x) * 4;
        d[i] = d[i + 1] = d[i + 2] = g; d[i + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
  }

  /* 把若干 32x16 的字符位图横向拼成一行 */
  function putCharMosaic(canvas, bitmaps, scale, gap) {
    var H = 32, W = 16;
    var nw = bitmaps.length * W * scale + (bitmaps.length - 1) * gap;
    var nh = H * scale;
    canvas.width = Math.max(1, nw); canvas.height = nh;
    var ctx = canvas.getContext('2d');
    ctx.fillStyle = '#0b1220';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    for (var k = 0; k < bitmaps.length; k++) {
      var bm = bitmaps[k], ox = k * (W * scale + gap);
      for (var y = 0; y < H * scale; y++) {
        for (var x = 0; x < W * scale; x++) {
          if (bm[((y / scale) | 0) * W + ((x / scale) | 0)] > 0.5) {
            ctx.fillRect(ox + x, y, 1, 1);
          }
        }
      }
    }
  }

  function imgToImageData(img) {
    var w = img.naturalWidth || img.width, h = img.naturalHeight || img.height;
    var s = Math.min(1, MAX_SIDE / Math.max(w, h));
    var cw = Math.max(1, Math.round(w * s)), ch = Math.max(1, Math.round(h * s));
    work.width = cw; work.height = ch;
    var ctx = work.getContext('2d', { willReadFrequently: true });
    ctx.clearRect(0, 0, cw, ch);
    ctx.drawImage(img, 0, 0, cw, ch);
    return ctx.getImageData(0, 0, cw, ch);
  }

  function loadImage(src) {
    return new Promise(function (res, rej) {
      var im = new Image();
      im.onload = function () { res(im); };
      im.onerror = function () { rej(new Error('图片解码失败, 换一张试试')); };
      im.src = src;
    });
  }

  /* ---------------------- 渲染 ---------------------- */

  function render(res, srcImageData) {
    last = { res: res, src: srcImageData };

    if (!res.ok) {
      showText(res.reason || '没有识别出车牌', 'err');
      metaEl.textContent = '没找到符合条件的车牌区域，换一张更清晰、更正对车牌的照片试试';
      charsEl.innerHTML = '';
      detailEl.style.display = 'none';
      stagesWrap.style.display = 'none';
      return;
    }

    showText(res.text, '');
    var avg = res.scores.length
      ? Math.round(100 * res.scores.reduce(function (a, b) { return a + b; }, 0) / res.scores.length) : 0;
    var srcName = res.scoreInfo.source === 'color' ? '颜色通道' : '边缘通道';
    metaEl.textContent = '共 ' + res.chars.length + ' 位 · 平均置信度 ' + avg +
      '% · 用时 ' + res.timing.total + ' ms · 定位来源：' + srcName;

    /* 每个字符一个小方块 */
    charsEl.innerHTML = '';
    res.chars.forEach(function (ch, i) {
      var box = document.createElement('div');
      box.className = 'chbox' + (res.scores[i] < 0.55 ? ' warn' : '');
      var cv = document.createElement('canvas');
      var H = 32, W = 16, sc = 1;
      cv.width = W; cv.height = H;
      var ctx = cv.getContext('2d');
      ctx.fillStyle = '#0b1220'; ctx.fillRect(0, 0, W, H);
      ctx.fillStyle = '#fff';
      var bm = res.charBitmaps[i];
      for (var y = 0; y < H; y++) {
        for (var x = 0; x < W; x++) { if (bm[y * W + x] > 0.5) { ctx.fillRect(x, y, 1, 1); } }
      }
      var lb = document.createElement('div');
      lb.className = 'lb'; lb.textContent = ch;
      var scEl = document.createElement('div');
      scEl.className = 'sc'; scEl.textContent = Math.round(100 * res.scores[i]) + '%';
      box.appendChild(cv); box.appendChild(lb); box.appendChild(scEl);
      charsEl.appendChild(box);
    });

    /* 细节表 */
    var si = res.scoreInfo;
    var rows = [
      ['定位候选得分', si.score.toFixed(3)],
      ['长宽比', (res.box[2] / res.box[3]).toFixed(2)],
      ['矩形度 extent', si.extent.toFixed(2) + '（门槛 ≥ 0.50）'],
      ['底色占比 colorFrac', si.colorFrac.toFixed(2) + '（门槛 ≥ 0.30）'],
      ['估计倾角', (res.skew || 0).toFixed(2) + '°（实际校正 ' + (res.appliedSkew || 0).toFixed(2) + '°）'],
      ['车牌区域', 'x=' + Math.round(res.box[0]) + ' y=' + Math.round(res.box[1]) +
        ' w=' + Math.round(res.box[2]) + ' h=' + Math.round(res.box[3])],
      ['耗时明细', '定位 ' + res.timing.locate + 'ms · 校正 ' + res.timing.rotate +
        'ms · 分割 ' + res.timing.segment + 'ms · 识别 ' + res.timing.recognize + 'ms']
    ];
    kvEl.innerHTML = '';
    rows.forEach(function (r) {
      var a = document.createElement('b'); a.textContent = r[0];
      var b = document.createElement('span'); b.textContent = r[1];
      kvEl.appendChild(a); kvEl.appendChild(b);
    });
    detailEl.style.display = '';
    stagesWrap.style.display = '';

    /* ① 原图 + 定位框 */
    var c1 = $('c1');
    putRGBA(c1, srcImageData.data, srcImageData.width, srcImageData.height);
    var ctx1 = c1.getContext('2d');
    ctx1.lineWidth = Math.max(2, Math.round(c1.width / 300));
    ctx1.strokeStyle = '#facc15';
    ctx1.strokeRect(res.box[0], res.box[1], res.box[2], res.box[3]);

    /* ② 校正后的车牌 */
    putRGBA($('c2'), res.plateColor.data, res.plateColor.w, res.plateColor.h);

    /* ③ 二值化 */
    putBinary($('c3'), res.bwPlate.data, res.bwPlate.w, res.bwPlate.h,
              Math.max(1, Math.round(600 / res.bwPlate.w)), true);

    /* ④ 字符 */
    putCharMosaic($('c4'), res.charBitmaps, 3, 12);
  }

  /* ---------------------- 主流程 ---------------------- */

  function processImage(img) {
    setBusy(true, '识别中…');
    showText('识别中…', 'empty');
    return new Promise(function (resolve) {
      /* 先让浏览器把"识别中"画出来, 再开始算 */
      setTimeout(function () {
        try {
          var src = imgToImageData(img);
          var res = LPREngine.run(src);
          render(res, src);
        } catch (e) {
          showText('出错了：' + (e && e.message ? e.message : e), 'err');
          metaEl.textContent = '';
          charsEl.innerHTML = '';
        } finally {
          setBusy(false);
          resolve();
        }
      }, 40);
    });
  }

  function handleFile(file) {
    if (!file) { return; }
    if (!/^image\//.test(file.type)) {
      showText('请选择图片文件（JPG / PNG / WebP）', 'err');
      return;
    }
    var url = URL.createObjectURL(file);
    loadImage(url).then(function (im) {
      processImage(im).then(function () { URL.revokeObjectURL(url); });
    }).catch(function (e) {
      showText(e.message, 'err');
      URL.revokeObjectURL(url);
    });
  }

  /* ---------------------- 交互绑定 ---------------------- */

  drop.addEventListener('click', function () { fileInput.click(); });
  $('pick').addEventListener('click', function () { fileInput.click(); });
  fileInput.addEventListener('change', function () {
    if (fileInput.files && fileInput.files[0]) { handleFile(fileInput.files[0]); }
    fileInput.value = '';
  });

  ['dragenter', 'dragover'].forEach(function (ev) {
    drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add('over'); });
  });
  ['dragleave', 'drop'].forEach(function (ev) {
    drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove('over'); });
  });
  drop.addEventListener('drop', function (e) {
    var dt = e.dataTransfer;
    if (dt && dt.files && dt.files[0]) { handleFile(dt.files[0]); }
  });
  document.addEventListener('dragover', function (e) { e.preventDefault(); });
  document.addEventListener('drop', function (e) { e.preventDefault(); });

  document.addEventListener('paste', function (e) {
    var items = e.clipboardData && e.clipboardData.items;
    if (!items) { return; }
    for (var i = 0; i < items.length; i++) {
      if (items[i].type && items[i].type.indexOf('image') === 0) {
        handleFile(items[i].getAsFile());
        e.preventDefault();
        return;
      }
    }
  });

  $('demo').addEventListener('click', function () {
    var im = $('lpr-demo');
    if (im.complete && im.naturalWidth) { processImage(im); }
    else { im.onload = function () { processImage(im); }; }
  });

  $('reset').addEventListener('click', function () {
    showText('等待照片…', 'empty');
    metaEl.textContent = '';
    charsEl.innerHTML = '';
    detailEl.style.display = 'none';
    stagesWrap.style.display = 'none';
    last = null;
  });

  /* 首屏自动跑一遍示例图, 让人一进来就看到效果 */
  window.addEventListener('load', function () {
    var im = $('lpr-demo');
    if (im.complete && im.naturalWidth) { processImage(im); }
    else { im.onload = function () { processImage(im); }; }
  });
})();

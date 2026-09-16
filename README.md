# 车牌识别 · MATLAB 工程实例 + 免安装网页版

中国大陆车牌（蓝底白字 / 新能源绿牌 / 黄底黑字）照片识别的完整实现。
一条流水线 —— **车牌定位 → 倾斜校正 → 字符分割 → 字符识别** —— 三种交付形态，
算法与参数一一对应。

| 目录 | 形态 | 需要装什么 | 适合场景 |
|---|---|---|---|
| [`matlab/`](matlab/) | MATLAB 工程本体 | MATLAB R2024b + Image Processing Toolbox | 课程设计 / 毕设、讲清算法、调参 |
| [`site/`](site/) | **单文件网页版**（一个 `index.html`） | 什么都不用装，浏览器打开就行 | 随手试用、发给别人看 |
| [`server/`](server/) | Python + MATLAB 后台版 | Python 3 + MATLAB | 局域网 / 公网给多台设备用 |

网页版是把 `matlab/` 的流水线用 JavaScript 重写，字符模板直接从 `matlab/templates.mat`
导出（31 汉字 + 24 字母 + 10 数字），所以两版识别结果基本一致。

![MATLAB 流水线逐级调试图](docs/matlab-pipeline.png)

## 在线试用

不想装环境就点这个（GitHub Pages 纯静态页，**识别在你自己浏览器里完成，照片不会上传**）：

**https://shyang-147.github.io/license-plate-recognition/**

## 实测数据

三个测试集（全部是合成图，只用来做回归自查；每个数字都能按下面的命令复现）：

| 集合 | 内容 | 怎么来 |
|---|---|---|
| `matlab/bench/` | 20 张：随机省份/字母/数字、倾斜 ±9°、尺寸 0.6~1.15 倍、随机模糊噪声 | 随仓库提供（`tools/make_bench.py` 生成） |
| `matlab/images/` | 3 张演示图 | 随仓库提供 |
| `matlab/hard/` | 24 张难集：**新能源 8 位牌** / **斜拍（梯形畸变）** / **小尺寸（车牌仅 120~170 px 宽）** / **夜间** 各 6 张 | 本地跑 `python tools/make_hard.py` 生成（图不进版本库） |

| 指标 | MATLAB 版 | 网页版（JS） |
|---|---|---|
| 整牌正确率（bench 20 张） | 55.0% (11/20) | **65.0% (13/20)** |
| 字符正确率（bench 20 张） | **92.1% (129/140)** | 90.0% (126/140) |
| 首位汉字正确率（bench 20 张） | 80.0% (16/20) | — |
| 字符数分割正确率（bench 20 张） | 95.0% (19/20) | 100% (20/20) |
| 演示图 `images/` 3 张 | 3/3 全对 | 3/3 全对 |
| 整牌正确率（hard 24 张） | 58.3% (14/24) | 50.0% (12/24) |
| 　└ 新能源 8 位 / 斜拍 / 小尺寸 / 夜间 | 6/6 · 0/6 · 4/6 · 4/6 | 6/6 · 0/6 · 2/6 · 4/6 |
| 平均单张耗时 | 约 150 ms | 约 150~250 ms |

复现：

```matlab
cd matlab
bench_eval                          % bench 20 张
bench_eval(fullfile(pwd,'hard'))    % 先跑过 python ../tools/make_hard.py
```

```bash
node site/tools/test_browser.mjs     % 网页版：无头浏览器真实上传 5 张图，全 PASS
```

怎么读这张表：

- **瓶颈在字符识别，不在定位/校正**：定位和几何校正已经稳定，错的多是形近字符
  （`1↔4`、`9↔X`、`3↔V`、`6↔8`）和形近汉字。要再往上走只有换 CNN 识别（见第 6 节）。
- 网页版**整牌**更准（65% vs 55%），但**不是**因为"MATLAB 版少了两处去车牌边框的处理"。
  实测把 JS 版的 `removeFrameLocal` + `dropEdgeSlivers` 补进 MATLAB 后，bench 整牌
  仍是 11/20，只有 3 张图的字符级结果变了 —— 两版的差距主要来自二值化/分割参数在
  移植时的细微差异。**上一版 README 在这里的结论是错的，一并更正。**
- **合成集上的数字不能外推**：同一套参数用在真实路拍照片上（3 张：沪AF22933 新能源、
  浙A128T8、京A0000警）整牌 0/3。合成图的字体、清晰度、背景都太"友好"。

### 本轮改进（三项）

| 改动 | 文件 | 实测效果 |
|---|---|---|
| **① 8 位新能源车牌**：字符数不再按固定比例估，改成用切分结果自校准 | `matlab/segmentChars.m`、`site/src/lpr_engine.js` | 难集新能源 **0/6 → 6/6**（两版都是）；7 位普通牌不受影响 |
| **② 车牌制式后处理**：新增 `plateFormat.m` 按位限定候选字符集，细长墨迹偏向 `1` | `matlab/plateFormat.m`（新增）、`recognizeChars.m`、`lpr_engine.js` | 难集整牌 25.0% → 58.3%，字符 42.5% → 75.3%；bench 字符 87.9% → 92.1%（MATLAB）；JS 难集整牌 20.8% → 50.0% |
| **③ 透视校正**：车牌四角构成梯形时用单应变换拉正（旋转+透视一次解决） | `matlab/correctPlate.m`、`lpr_engine.js` | 斜拍样本字符级明显变好（如 pe01 `*KZP816N` → `*J39818`），但 6 张仍没有一张整牌全对 |

① 原来是 `nEst = min(8, max(6, round(span/(0.125*W))))`：车牌裁紧以后，7 位普通牌和
8 位新能源牌的 `span/W` 都在 0.87 左右，这个式子**永远算出 7**，8 位牌会被强行合并掉
一个字，整牌必错。现在改成在 6/7/8 三个候选里挑"相邻字符中心间距最均匀"的那个。

③ 只在车牌**四角明显是梯形**（上下边不等长 > 8% 或四角偏离直角 > 10°）**且车牌足够大**
（高 ≥ 32 px）时才启用，其余情况仍走原来的旋转校正：本来就正的图多插值一次只会更糊。
这道闸门是实测调出来的 —— 不加的时候，小尺寸样本（掩膜只有几十像素宽，台阶状的边界
让极值点抖一两个像素就能凑出"梯形"）会被误触发，难集整牌反而从 58.3% 掉到 50.0%。

## 算法流程

| 步骤 | 文件（MATLAB） | 做法 |
|---|---|---|
| ① 车牌定位 | `locatePlate.m` | 蓝/绿/黄底色掩膜 + Sobel 边缘掩膜 + 形态学闭运算连成候选块；按长宽比、面积、底色占比、边缘密度、矩形度打分取最优 |
| ② 真车牌闸门 | `locatePlate.m` | 矩形度 < 0.50 或框内底色占比 < 0.30 直接判为假车牌，返回"未检测到"（防蓝色广告牌误检） |
| ③ 几何校正 | `correctPlate.m` | 取车牌掩膜的四个极值点当四角：四角明显是梯形时用单应变换拉正（**透视校正**），否则沿用"上下边界拟合倾角 → 反向旋转"；统一高度归一到 64 px |
| ④ 字符分割 | `segmentChars.m` | CLAHE 增强 → Otsu 二值化 → 清除车牌外框/边框残留 → 垂直投影切字符段 → **按"间距均匀度"自校准字符数（6~8，兼容新能源 8 位牌）** → 归一化，同时输出每段墨迹宽高比 |
| ⑤ 字符识别 | `recognizeChars.m` | 单字符归一化到 32×16 → 降采样 24×12 特征 → 与模板库比对 `0.65×IoU + 0.35×相关系数`；**按 `plateFormat.m` 逐位限定候选字符集**（第 1 位汉字 / 第 2 位字母 / 新能源第 3 位字母 / 其余字母数字），极细长的墨迹偏向数字 `1`，首位置信度 < 0.45 输出 `*` 占位 |

## 目录结构

```
license-plate-recognition/
├─ matlab/                  MATLAB 工程本体（课程设计交这一套）
│  ├─ lpr_main.m            主入口：读图 → 定位 → 校正 → 分割 → 识别
│  ├─ locatePlate.m         车牌定位
│  ├─ cropPlate.m           按定位框裁剪（同步裁剪掩膜）
│  ├─ correctPlate.m        几何校正：透视拉正 / 倾斜旋转 + 高度归一化
│  ├─ segmentChars.m        二值化 + 字符分割（字符数自校准）
│  ├─ normalizeChar.m       单字符归一化到 32×16
│  ├─ charFeature.m         降采样成 24×12 特征
│  ├─ recognizeChars.m      模板匹配识别（按位限定候选集）
│  ├─ plateFormat.m         车牌制式：7 位普通 / 8 位新能源，逐位允许的字符集
│  ├─ recognizeCharsCNN.m   CNN 识别（可选，配合 train_char_cnn.m 训练）
│  ├─ templates.mat         字符模板库（31 汉字 + 24 字母 + 10 数字）
│  ├─ buildTemplates.m      重新生成模板库
│  ├─ verify_lpr.m          一键自检（29 项）
│  ├─ debug_one.m           逐级可视化，定位问题出在哪一步
│  ├─ bench_eval.m          带标注图集的批量评测
│  ├─ images/               3 张演示图 + expected.csv
│  └─ bench/                20 张基准图 + labels.csv
├─ site/                    单文件网页版
│  ├─ index.html            ← 双击就能用（自包含，0 个外部请求）
│  ├─ start_lan.bat         局域网共享（同一 WiFi 下手机可打开）
│  ├─ src/                  源文件，改完跑 python src/build_site.py 重新生成 index.html
│  └─ tools/test_browser.mjs 无头浏览器端到端测试
├─ server/                  Python + MATLAB 后台版
│  ├─ server.py             只用 Python 标准库，不需要 pip install
│  ├─ worker_lpr.m          MATLAB 常驻 worker（轮询任务、调 lpr_main）
│  ├─ static/index.html     网页前端
│  ├─ tools/                打包脚本 + API/浏览器测试脚本
│  └─ portable/             单文件打包版（支持公网隧道 + 访问口令）
├─ tools/                   测试图生成脚本（make_demo.py / make_bench.py / make_hard.py）
├─ docs/                    文档配图
└─ LICENSE                  MIT
```

## 快速上手

### ① 网页版（最快）

双击 `site/index.html`。点"选择照片"或直接 `Ctrl+V` 粘贴截图，几秒内出结果。

要发给别的设备：把 `index.html` 传过去用浏览器打开即可；同一 WiFi 下想直接用手机打开，
双击 `site/start_lan.bat`，它会打印一个形如 `http://192.168.x.x:8080/` 的地址。

### ② MATLAB 版

```matlab
cd matlab
lpr_main('images/demo_plate.jpg')   % 认一张图（会画出定位框、车牌、分割字符、结果）
verify_lpr                          % 一键自检 29 项
debug_one('images/demo_plate.jpg')  % 逐级调试图，看问题卡在哪一步
bench_eval                          % 跑 20 张基准集，输出准确率明细
```

### ③ 服务版

```bat
cd server
start_lan.bat        :: 局域网；首次运行会拉起 MATLAB worker（10~20 秒）
```

手机/其他电脑连同一个 WiFi，浏览器打开窗口里打印的局域网地址即可。
想在外网用，`server/portable/start_public.bat` 会开一条临时公网隧道并生成访问口令。

## 怎么验证程序是对的

| 想验证什么 | 怎么跑 | 期望 |
|---|---|---|
| MATLAB 环境、文件、各模块是否正常 | `verify_lpr` | 29 项全 PASS |
| 每张图的识别明细 | `bench_run` / `bench_eval` | 打印逐张结果 + 准确率汇总 |
| 到底哪一步出错 | `debug_one('图片路径')` | 6 张子图：原图+定位框 / 车牌掩膜 / 校正后车牌 / 二值化 / 分割字符 / 垂直投影 |
| 换算子/换参数有没有变好 | 改完再跑 `bench_eval` | 与上表对比 |
| 网页版是否正常 | `node site/tools/test_browser.mjs` | 无头浏览器真实上传 5 张图，全 PASS、页面 0 报错 |
| 服务版 API 是否正常 | `node server/tools/test_api.mjs`（先启动服务） | 逐项打印 HTTP 状态与时延 |

> 换自己的照片测试：放进 `matlab/bench/`，写一份 `labels.csv`（列为 `file,text`），
> 再跑 `bench_eval` 就能得到你自己的准确率。

## 已知限制

- 只认**中国大陆**车牌，依赖蓝/绿/黄底色，黑白或异形车牌不适用。
- 字符识别是模板匹配，形状相近字符（`1/4`、`9/X`、`3/V`）易混；要更准请走
  `train_char_cnn.m` 训练 CNN，用 `recognizeCharsCNN.m` 替换。
- 字符数是**自校准**的（7/8 位自适应），代价是个别图的切分会在 7/8 之间摇摆：
  bench 上字符数正确率从 100% 降到 95%（但整牌正确率不变）。
- **斜拍仍是弱项**：透视校正只在车牌被拍成明显梯形时才启用（否则退回旋转校正）。
  几何拉正能让斜拍样本的字符级明显变好，但拉正时的插值模糊又会拖累模板匹配，
  难集里 6 张斜拍图**没有一张整牌全对**（0/6）。
- 照片里车牌宽度至少 100 px；太远、太糊、过曝、夜拍、反光都会显著掉准确率。
- 照片里不要出现其它蓝底/绿底矩形物体（蓝色广告牌、蓝色车身），否则第一步可能定位错。
- 基准集是**合成图**（`tools/make_bench.py` 生成），不代表真实路拍场景的准确率。

## 许可

[MIT](LICENSE)
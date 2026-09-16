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

测试集：`matlab/bench/` 20 张合成车牌（随机省份/字母/数字、倾斜 ±9°、尺寸 0.6~1.15 倍、
随机模糊噪声，由 `tools/make_bench.py` 生成），另加 `matlab/images/` 3 张演示图。

| 指标 | MATLAB 版 | 网页版（JS） |
|---|---|---|
| 整牌正确率（bench 20 张，同口径） | 55.0% (11/20) | **70.0% (14/20)** |
| 整牌正确率（合计 23 张） | — | **73.9% (17/23)** |
| 字符正确率（bench 20 张） | **87.9% (123/140)** | 79.3% (111/140) |
| 字符正确率（合计 23 张） | — | 82.0% (132/161) |
| 首位汉字正确率（bench 20 张） | **80.0% (16/20)** | 75.0% (15/20) |
| 字符数分割正确率（bench 20 张） | 100% (20/20) | — |
| 演示图 `images/` 3 张 | 3/3 全对 | 3/3 全对 |
| 平均单张耗时 | 约 150 ms | 约 150~160 ms |

怎么读这张表：

- **定位、校正、分割已经稳定**（20/20 张切出的字符个数完全正确），瓶颈在字符识别。
- MATLAB 版**字符级**更准（模板匹配参数更全），网页版**整牌**更准 —— 因为移植时额外修掉了
  「车牌外框残留被当成一个字符」的问题（`site/src/lpr_engine.js` 里的 `removeFrameLocal` +
  `dropEdgeSlivers`），而 MATLAB 版还没把这两处同步回去，这是一个现成的改进点。
- 典型错误是形状相近字符混淆：`1↔4`、`9↔X`、`3↔V`、`6↔8`。

## 算法流程

| 步骤 | 文件（MATLAB） | 做法 |
|---|---|---|
| ① 车牌定位 | `locatePlate.m` | 蓝/绿/黄底色掩膜 + Sobel 边缘掩膜 + 形态学闭运算连成候选块；按长宽比、面积、底色占比、边缘密度、矩形度打分取最优 |
| ② 真车牌闸门 | `locatePlate.m` | 矩形度 < 0.50 或框内底色占比 < 0.30 直接判为假车牌，返回"未检测到"（防蓝色广告牌误检） |
| ③ 倾斜校正 | `correctPlate.m` | 车牌掩膜上下边界最小二乘拟合求倾角 → 反向旋转校正 → 高度归一化到 64 px |
| ④ 字符分割 | `segmentChars.m` | CLAHE 增强 → Otsu 二值化 → 清除车牌外框/边框残留 → 垂直投影切字符段 → 按估计字数拆分/合并 |
| ⑤ 字符识别 | `recognizeChars.m` | 单字符归一化到 32×16 → 降采样 24×12 特征 → 与模板库比对 `0.65×IoU + 0.35×相关系数`（第 1 位走汉字模板，第 2 位走字母模板） |

## 目录结构

```
license-plate-recognition/
├─ matlab/                  MATLAB 工程本体（课程设计交这一套）
│  ├─ lpr_main.m            主入口：读图 → 定位 → 校正 → 分割 → 识别
│  ├─ locatePlate.m         车牌定位
│  ├─ cropPlate.m           按定位框裁剪（同步裁剪掩膜）
│  ├─ correctPlate.m        倾斜校正 + 高度归一化
│  ├─ segmentChars.m        二值化 + 字符分割
│  ├─ normalizeChar.m       单字符归一化到 32×16
│  ├─ charFeature.m         降采样成 24×12 特征
│  ├─ recognizeChars.m      模板匹配识别
│  ├─ recognizeCharsCNN.m   CNN 识别（可选，配合 train_char_cnn.m 训练）
│  ├─ templates.mat         字符模板库（31 汉字 + 24 字母 + 10 数字）
│  ├─ buildTemplates.m      重新生成模板库
│  ├─ verify_lpr.m          一键自检（25 项）
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
├─ tools/                   测试图生成脚本（make_demo.py / make_bench.py）
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
verify_lpr                          % 一键自检 25 项
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
| MATLAB 环境、文件、各模块是否正常 | `verify_lpr` | 25 项全 PASS |
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
- 只做**旋转**校正，没做透视校正；拍摄角度太斜（>15°）时掉准确率明显。
- 照片里车牌宽度至少 100 px；太远、太糊、过曝、夜拍、反光都会显著掉准确率。
- 照片里不要出现其它蓝底/绿底矩形物体（蓝色广告牌、蓝色车身），否则第一步可能定位错。
- 基准集是**合成图**（`tools/make_bench.py` 生成），不代表真实路拍场景的准确率。

## 许可

[MIT](LICENSE)
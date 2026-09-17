function [gray, color, info] = correctPlate(gray, color, mask, nDigits)
%CORRECTPLATE 车牌几何校正(旋转 + 透视) + 尺寸归一化
%
%   [gray, color]       = CORRECTPLATE(gray, color, mask)
%   [gray, color, info] = CORRECTPLATE(gray, color, mask)
%   mask: 车牌掩膜(用于估计车牌四角), 可为空 []
%   nDigits: (可选) 车牌位数 7 / 8。已知就传进来(走国标 440/140 或 480/140);
%            留空则取 max(四角实测投影比例, 440/140) —— 只补透视压缩, 不制造压缩。
%            原因与取证见 TARGETASPECT。
%
%   做法分两级:
%     1) 透视校正(只在真的歪成梯形时启用): 用掩膜上的"极值点"定位车牌四角,
%        再用单应变换把它拉成矩形 —— 旋转和透视一次解决。斜拍(侧向拍摄)的
%        车牌上下边不等长, 只有这一步能救。
%     2) 只做旋转校正(其余情况): 四角接近矩形(纯旋转/纯平移)时, 多插值一次
%        反而更糊, 直接沿用"掩膜上下边界拟合倾角 -> 反向旋转"; 四角估计不可靠
%        (掩膜残缺、四角不凸、长宽比不像车牌)时也走这条兜底路径。
%
%   info 字段: mode  几何校正方式, 'perspective' / 'rotate' / 'none'
%              quad  采用的车牌四角(4x2, 左上->右上->右下->左下), 未用时为 []
%              skew  旋转校正的角度(度), 透视校正时为 0
%              reason 四角估计失败的原因(便于排查), 成功时为空
%              asp   透视校正采用的目标宽高比(未做透视时为 [])
%              outW  透视校正的输出宽度(未做透视时为 [])
%              aspSource 比例来源: 'digits-7' / 'digits-8' / 'meas-wide' / 'default-7'
%              aspMeas 四角量出来的实测投影宽高比(选择之前的原始值, 供诊断)
%
%   校正后统一缩放到高度 64 像素, 宽度 = max(四角实测投影比例, 国标 7 位规范) * 64 ——
%   比规范比例窄的牌照被拉宽(补回透视压缩), 比规范还宽的维持原样(多出来的宽度来自
%   检测框外扩, 不是车牌形状)。原因与取证见 TARGETASPECT。

if nargin < 4, nDigits = []; end     % MATLAB 里没传的入参是"未定义", 直接引用会报错

targetH = 64;
info = struct('mode', 'none', 'quad', [], 'skew', 0, 'reason', '', ...
              'asp', [], 'outW', [], 'aspSource', '', 'aspMeas', []);

% ---------------- 1. 优先: 四角 + 单应变换(旋转与透视一起校正) ----------------
if nargin > 2 && ~isempty(mask) && any(mask(:))
    [quad, why] = detectQuad(mask);
    info.reason = why;
    if ~isempty(quad) && ~hasPerspective(quad)
        % 四角基本还是个矩形(纯旋转/纯平移) -> 没必要重采样, 走下面的旋转校正,
        % 结果与老版本完全一致, 不会因为多插值一次而掉精度
        info.reason = '四角接近矩形, 改用旋转校正';
        quad = [];
    end
    if ~isempty(quad)
        wTop = norm(quad(2, :) - quad(1, :));
        wBot = norm(quad(3, :) - quad(4, :));
        hLft = norm(quad(4, :) - quad(1, :));
        hRgt = norm(quad(3, :) - quad(2, :));
        wAvg = (wTop + wBot) / 2;
        hAvg = max(eps, (hLft + hRgt) / 2);
        outH = targetH;
        aspMeas = wAvg / hAvg;
        [asp, aspSource] = targetAspect(nDigits, aspMeas);
        outW = min(max(round(asp * targetH), 24), 8 * targetH);
        tform = quadToRectTransform(quad, outW, outH);
        view  = imref2d([outH, outW]);
        gray  = imwarp(gray,  tform, 'OutputView', view, ...
                       'InterpolationMethod', 'bilinear', 'FillValues', 0);
        color = imwarp(color, tform, 'OutputView', view, ...
                       'InterpolationMethod', 'bilinear', 'FillValues', 0);
        info.mode = 'perspective';
        info.quad = quad;
        info.asp  = asp;
        info.outW = outW;
        info.aspSource = aspSource;
        info.aspMeas = aspMeas;
        return;
    end
end

% ---------------- 2. 兜底: 只做倾斜(旋转)校正 ----------------
if nargin > 2 && ~isempty(mask) && any(mask(:))
    ang = estimateSkew(mask);
    if abs(ang) > 0.8 && abs(ang) < 25          % 小角度不折腾, 大角度视为误检
        info.mode = 'rotate';
        info.skew = ang;
        gray  = imrotate(gray,  ang, 'bilinear', 'loose');
        color = imrotate(color, ang, 'bilinear', 'loose');
        mask  = imrotate(mask,  ang, 'nearest',  'loose');
        bb = tightBox(mask);
        if ~isempty(bb)
            bb = clampBox(bb, size(gray));
            gray  = imcrop(gray,  bb);
            color = imcrop(color, bb);
        end
    end
end

% ---------------- 3. 统一高度 ----------------
if size(gray, 1) >= 8
    s = targetH / size(gray, 1);
    if s > 1.05
        % 放大: 双三次 + 轻度 USM 锐化。小号牌(几十像素高)放大到 64 之后笔画是
        % 软的, 二值化会把笔画喂胖一圈(实测 105 px 车牌上 "0" 的孔洞被挤到只剩
        % 1~2 px), 匹配前先把边补回来。
        gray  = imresize(gray,  s, 'bicubic');
        color = imresize(color, s, 'bicubic');
        gray  = unsharp(gray);
    elseif abs(s - 1) > 0.01
        gray  = imresize(gray,  s, 'bilinear');
        color = imresize(color, s, 'bilinear');
    end
end
end

function [asp, src] = targetAspect(nDigits, aspMeas)
%TARGETASPECT 单应校正的目标宽高比: 用**国标规范比例**, 而不是四角量到的投影比例
%
%   四角点在图像里量出来的宽高比 = 车牌被透视压缩后的投影比例, 不是车牌本身的比例。
%   拿它当目标矩形, 等于**把透视压缩原封不动留在校正结果里** —— 斜拍车牌拉正后
%   字符仍是瘦长的, 而 segmentChars 的字符间隙阈值是**绝对像素**, 字间空隙跟着
%   变窄就并字。号牌尺寸有国标(GA 36-2018): 蓝/黄/白底 440x140, 新能源绿牌 480x140。
%
%   第十轮 A/B 取证(真值四角裁片, 主指标 = segmentChars 切出字符数 == 真值位数):
%     A 实测投影比例 53.1%  ->  C 一律按 7 位规范 59.1%  (+5.9pp)
%     字符率 14.8% -> 16.3%; 9 个 subset 里 8 个变好(只有 ccpd_db 掉 2 张)。
%
%   位数不知道怎么办 —— 调用方(lpr_main / 网页版)在校正这一步确实还不知道车牌
%   有几位, 位数本来要靠后面的分割/识别才能定。规则是 **只补压缩, 不制造压缩**:
%     实测比例 < 440/140  ->  拉到 440/140   (补回透视压缩, 即"改用规范比例"的收益)
%     实测比例 > 440/140  ->  维持实测比例   (多出来的宽度不是车牌形状, 不能压)
%   即 asp = max(aspMeas, 440/140)。
%
%   为什么不能"一律压到 440/140"(第十一轮的教训): locatePlate 给的四角比真值四角
%   **中位偏宽 +0.74**(走透视的 75 张: 检测 3.456 vs 真值 2.789; 7 位牌 3.379 vs
%   2.687)。对这 75 张里**本来就过宽**的那些再按 3.143 压回去, 等于把字符和字间空隙
%   一起压窄 —— 端到端实测(测试脚本/run_stage.m, 7 个数据集): 一律压会丢掉 hard 集
%   一张**原本完全正确**的牌(pe06, 检测比例 3.306), 而只补压缩在 7 个集合上
%   **零整牌回归**(字符数 ±1, 在噪声内; 见 结果记录/stage_第十一轮_*.txt)。
%
%   "按检测四角比例就近吸附到 440 或 480" 这条也试过, **已否决**(第十一轮): 62 张
%   7 位牌里 51.6% 被误吸到 480(凭空多给 9% 宽), 字符率反而更低(17.9% -> 16.1%)。
%   结论: 检测框比例不能拿来判位数 —— 有偏的信号不如不要。
%
%   真能提前知道位数时(例如已从颜色判出是新能源绿牌)传 nDigits 走精确分支:
%   lpr_main('PlateDigits', 8)。真值四角口径下 8 位绿牌按 480/140 校正,
%   字符率 A 29.3% -> 32.9%。
%   ⚠️ 只有"位数确实不是 7"时才值得传: 传 nDigits=7 会**强制**压到 440/140(等价于上面
%   被否决的 C), 在当前定位框偏宽时反而更差 —— hard/pe06 实测: 不传 -> 212 px 读对
%   (鲁M50007), 传 7 -> 201 px 读错(鲁Y50007)。7 位牌等定位框收紧之后再传。
AR_SMALL = 440 / 140;     % 蓝/黄/白底牌(7 位)
AR_GREEN = 480 / 140;     % 新能源绿牌(8 位)

if nargin < 2, aspMeas = []; end

if nargin >= 1 && ~isempty(nDigits) && nDigits == 8
    asp = AR_GREEN; src = 'digits-8';
elseif nargin >= 1 && ~isempty(nDigits)
    asp = AR_SMALL; src = 'digits-7';
elseif ~isempty(aspMeas) && aspMeas > AR_SMALL
    asp = aspMeas; src = 'meas-wide';
else
    asp = AR_SMALL; src = 'default-7';
end
end

function g = unsharp(g, amount, sigma)
%UNSHARP 轻度 USM 锐化 g + amount*(g - 高斯模糊(g)), 只用于"放大"这一步
%   只锐化灰度图 —— 后续分割/识别只用灰度, 彩色图只用于显示。
%   默认刻意取小(amount 0.6): 锐化过头会把插值的振铃放大成假笔画。
if nargin < 2 || isempty(amount), amount = 0.6; end
if nargin < 3 || isempty(sigma),  sigma  = 1.0; end
if amount <= 0, return; end
cls = class(g);
d = double(g);
if isinteger(g), d = d / double(intmax(cls)); end
b = imfilter(d, fspecial('gaussian', [5 5], sigma), 'replicate');
d = min(1, max(0, d + amount * (d - b)));
if isinteger(g), g = cast(d * double(intmax(cls)), cls); else, g = d; end
end

% ======================== 局部函数 ========================

function [quad, why] = detectQuad(mask)
%DETECTQUAD 车牌四角估计
%   输出 4x2 [x y], 顺序 左上 -> 右上 -> 右下 -> 左下; 估计不可靠时返回 []。
%
%   用"极值点"取四角: 车牌是凸四边形, 而凸多边形上线性函数的最值一定在顶点取到,
%   所以 x+y 最小 -> 左上, x+y 最大 -> 右下, x-y 最大 -> 右上, x-y 最小 -> 左下。
%   对任意凸四边形这四步都能直接命中四个顶点, 与车牌是转了、歪了、还是被拍成了
%   梯形都无关, 也不受"车牌边框/螺栓残留"以外的因素影响, 是最稳的估法。
%
%   试过再在四条边的中段拟合直线、用直线交点去精修: 实测弊大于利 —— 裁剪框
%   是掩膜的外接矩形, 车牌自己的边经常正好贴着裁剪边界, 拟合很容易被边界上的
%   截断点带跑, 精修出来的四边形反而偏离真值(基准集整牌准确率 55% 掉到 45%)。
%   所以这里只用极值点, 靠下面的 quadOK 做合理性检查。

quad = [];  why = '';
mask = imclose(mask, strel('disk', 2));
mask = imfill(mask, 'holes');
mask = bwareaopen(mask, 20);
mask = keepLargest(mask);      % 只留最大连通域: 车牌外框残留常是贴边的细长条,
                               % 面积过不了 bwareaopen 这关, 却会把极值点带偏
if ~any(mask(:)), why = '掩膜为空'; return; end
[H, W] = size(mask);
if W < 24 || H < 8, why = '车牌太小(不足 24x8)'; return; end

[rr, cc] = find(mask);
if numel(rr) < 50, why = '掩膜像素太少'; return; end
s = cc + rr;  d = cc - rr;
[~, i1] = min(s);  [~, i2] = max(s);
[~, i3] = max(d);  [~, i4] = min(d);
quad = [cc(i1), rr(i1); cc(i3), rr(i3); cc(i2), rr(i2); cc(i4), rr(i4)];
if size(unique(quad, 'rows'), 1) < 4
    quad = [];  why = '极值点重合, 掩膜形状不是四边形'; return;
end
if ~quadOK(quad, W, H, mask)
    quad = [];
    why = '掩膜形状不像车牌四边形(四角不凸/长宽比异常/掩膜填不满)';
end
end

function tf = hasPerspective(q)
%HASPERSPECTIVE 四角是不是"明显被拍成了梯形"
%   判据: 上下边不等长、左右边不等长、或四个角明显不是直角。
%   纯旋转的车牌这三项都接近 0, 走旋转校正即可; 只有斜拍(梯形)才值得做透视变换。
%   加这道闸门有两个原因:
%     1) 本来就正的图不需要多插值一次 —— 多一次重采样就多一次模糊,
%        对模板匹配这种吃细节的方法是纯亏;
%     2) 小车牌(几十像素宽)的掩膜本身是台阶状的, 极值点会左右抖一两个像素,
%        折算成角度能到七八度, 容易被误判成梯形。所以小牌一律不做透视。
e1 = q(2, :) - q(1, :);  e2 = q(3, :) - q(2, :);
e3 = q(4, :) - q(3, :);  e4 = q(1, :) - q(4, :);
wT = norm(e1);  wB = norm(e3);  hL = norm(e4);  hR = norm(e2);
wAvg = (wT + wB) / 2;  hAvg = (hL + hR) / 2;
if hAvg < 32 || wAvg < 96, tf = false; return; end      % 太小, 角度估计本身就不可信
dw = abs(wT - wB) / max(eps, wAvg);
dh = abs(hL - hR) / max(eps, hAvg);
dev = max([rightAngleDev(e1, -e4), rightAngleDev(e2, -e1), ...
           rightAngleDev(e3, -e2), rightAngleDev(e4, -e3)]);
tf = dw > 0.08 || dh > 0.08 || dev > 10;
end

function d = rightAngleDev(u, v)
%RIGHTANGLEdev 两向量夹角与 90 度的偏差(度)
c = dot(u, v) / max(eps, norm(u) * norm(v));
d = abs(90 - acosd(max(-1, min(1, c))));
end

function ok = quadOK(q, W, H, mask)
%QUADOK 四边形合理性检查: 有限、没跑出图像太远、是凸四边形、长宽比像车牌,
%       并且掩膜基本填满它(填不满说明四角估得太大, 是误检而非真车牌)
ok = false;
if any(~isfinite(q(:))), return; end
if any(q(:, 1) < -0.20 * W) || any(q(:, 1) > 1.20 * W), return; end
if any(q(:, 2) < -0.20 * H) || any(q(:, 2) > 1.20 * H), return; end
e1 = q(2, :) - q(1, :);  e2 = q(3, :) - q(2, :);
e3 = q(4, :) - q(3, :);  e4 = q(1, :) - q(4, :);
cr = [e1(1) * e2(2) - e1(2) * e2(1), e2(1) * e3(2) - e2(2) * e3(1), ...
      e3(1) * e4(2) - e3(2) * e4(1), e4(1) * e1(2) - e4(2) * e1(1)];
if ~(all(cr > 0) || all(cr < 0)), return; end      % 不凸
wAvg = (norm(e1) + norm(e3)) / 2;
hAvg = (norm(e2) + norm(e4)) / 2;
if wAvg < 0.45 * W || hAvg < 0.45 * H, return; end  % 裁紧后的车牌应基本占满裁剪框
ar = wAvg / max(eps, hAvg);
if ar < 1.8 || ar > 4.5, return; end
a = polyarea(q(:, 1), q(:, 2));
if nnz(mask) < 0.55 * a, return; end
if nnz(mask) > 1.60 * a, return; end
ok = true;
end

function m = keepLargest(mask)
%KEEPLARGEST 只保留面积最大的连通域(掩膜里偶尔混进贴边的细长残留)
lbl = bwlabel(mask, 8);
if max(lbl(:)) <= 1, m = mask; return; end
cnt = accumarray(lbl(lbl > 0), 1);
[~, k] = max(cnt);
m = lbl == k;
end

function tform = quadToRectTransform(quad, outW, outH)
%QUADTORECTTRANSFORM 直接把车牌四角映射到 outW x outH 矩形的单应变换
%   (自己解 8x8 线性方程组, 不依赖 fitgeotrans, 老版本 MATLAB 也能跑)
dst = [1 1; outW 1; outW outH; 1 outH];
A = zeros(8, 8); b = zeros(8, 1);
for i = 1:4
    x = quad(i, 1); y = quad(i, 2);
    u = dst(i, 1);  v = dst(i, 2);
    A(2 * i - 1, :) = [x, y, 1, 0, 0, 0, -u * x, -u * y];
    A(2 * i,     :) = [0, 0, 0, x, y, 1, -v * x, -v * y];
    b(2 * i - 1) = u;
    b(2 * i)     = v;
end
h = A \ b;
% 注意: 上面解出的是"列向量"约定 [x;y;1] -> [u;v;w] 的矩阵,
%       而 MATLAB 的 projective2d 用的是行向量约定, 所以要转置一次
T = [h(1), h(2), h(3); h(4), h(5), h(6); h(7), h(8), 1];
tform = projective2d(T');
end

function ang = estimateSkew(mask)
%ESTIMATESKEW 由车牌掩膜上下边界拟合直线, 返回需要旋转的角度(度)
mask = imclose(mask, strel('disk', 2));
mask = imfill(mask, 'holes');
mask = bwareaopen(mask, 20);
if ~any(mask(:)), ang = 0; return; end

[H, W] = size(mask); %#ok<ASGLU>
top = nan(1, W); bot = nan(1, W);
for c = 1:W
    r = find(mask(:, c), 1, 'first');
    if ~isempty(r), top(c) = r; end
    r = find(mask(:, c), 1, 'last');
    if ~isempty(r), bot(c) = r; end
end

v = [fitLineAngle(top), fitLineAngle(bot)];
v = v(~isnan(v));
if isempty(v)
    ang = 0;
else
    ang = mean(v);
end
end

function a = fitLineAngle(y)
%FITLINEANGLE 最小二乘拟合一条近似水平线, 返回倾角(度); 含一次离群点剔除
x = 1:numel(y);
ok = ~isnan(y);
x = x(ok); y = y(ok);
if numel(x) < 5, a = NaN; return; end

p = polyfit(x(:), y(:), 1);
res = abs(y(:) - polyval(p, x(:)));
th = 2.5 * max(1, median(res));
keep = res <= th;
if nnz(keep) >= 5
    p = polyfit(x(keep), y(keep), 1);
end
a = atand(p(1));      % imrotate 正角度为逆时针, 正好抵消图像坐标 y 向下的斜率
end

function bb = tightBox(mask)
%TIGHTBOX 掩膜的最小外接矩形 [x y w h]
colSum = sum(mask, 1);
rowSum = sum(mask, 2);
if ~any(colSum) || ~any(rowSum), bb = []; return; end
cs = find(colSum > 0.30 * max(colSum));
rs = find(rowSum > 0.30 * max(rowSum));
bb = [cs(1), rs(1), cs(end) - cs(1) + 1, rs(end) - rs(1) + 1];
end

function bb = clampBox(bb, sz)
bb(1) = max(1, min(bb(1), sz(2)));
bb(2) = max(1, min(bb(2), sz(1)));
bb(3) = max(1, min(bb(3), sz(2) - bb(1) + 1));
bb(4) = max(1, min(bb(4), sz(1) - bb(2) + 1));
end

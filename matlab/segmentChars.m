function [charImages, bwPlate, bounds] = segmentChars(plateGray)
%SEGMENTCHARS 车牌字符分割(垂直投影法)
%
%   [charImages, bwPlate, bounds] = SEGMENTCHARS(plateGray)
%   plateGray : 已校正、高度归一化(64)的灰度车牌
%   charImages: 1xN cell, 每个元素是 32x16 logical 的归一化字符
%   bwPlate   : 二值化结果(字符=白), 便于调试查看
%   bounds    : N x 2, 每个字符在车牌中的列区间 [起 止]
%
%   流程: 光照均衡 -> Otsu 二值化 -> 统一极性 -> 去外框 -> 去噪
%         -> 垂直投影取字符段 -> 按估计字数拆分/合并 -> 归一化
%
%   关键参数都在下面注释里标了, 分割不对时优先调这三处:
%     1) mergeRuns 的间隙阈值  (汉字被切散 -> 调大; 相邻字粘连 -> 调小)
%     2) 列阈值 thr            (窄笔画如数字 1 的撇被切掉 -> 调小)
%     3) 估算字数 nEst         (新能源 8 位牌 / 特殊制式)

if size(plateGray, 3) > 1
    plateGray = rgb2gray(plateGray);
end
[H, W] = size(plateGray);

% ---------------- 1. 光照均衡 + 二值化 ----------------
g = im2double(plateGray);
g = adapthisteq(g, 'NumTiles', [4 8], 'ClipLimit', 0.02);
bw = imbinarize(g, graythresh(g));

% 车牌底色面积远大于字符面积 -> 保证"字符 = 白(1)"
if nnz(bw) > 0.45 * numel(bw)
    bw = ~bw;
end

% ---------------- 2. 去掉车牌外框 ----------------
bw = removeFrame(bw);

% ---------------- 3. 去噪 ----------------
bw = bwareaopen(bw, max(4, round(0.0015 * H * W)));
bwPlate = bw;

% ---------------- 4. 垂直投影 -> 字符列区间 ----------------
proj = sum(bw, 1);
proj = movmean(proj, 3);

% 列阈值不能太高: 数字 1 的撇、字母 J 的钩等窄笔画只占字符高度的 5%~8%,
% 用 0.10*H 会把它们切掉, 字符变窄后归一化失真(1 会被误判成 4/8)
thr  = max(1, 0.05 * H);
runs = logicalRuns(proj > thr);

% 间隙阈值取 0.02*W: 要大于汉字被切散的缝(1~3 px), 小于字与字之间的间隙
runs = mergeRuns(runs, max(2, round(0.020 * W)));

% 去掉过窄的噪声段
w = runs(:, 2) - runs(:, 1) + 1;
runs = runs(w >= max(2, round(0.015 * W)), :);

% ---------------- 5. 按估计字数拆分/合并 ----------------
if ~isempty(runs)
    span = runs(end, 2) - runs(1, 1) + 1;
    % 中国车牌单字宽约 0.10*W, 含字间距后每个字约占 0.125*W
    nEst = min(8, max(6, round(span / (0.125 * W))));
    runs = adjustToCount(runs, proj, nEst);
end
bounds = runs;

% ---------------- 6. 输出归一化字符 ----------------
n = size(runs, 1);
charImages = cell(1, n);
for k = 1:n
    seg = bw(:, runs(k, 1):runs(k, 2));
    charImages{k} = normalizeChar(seg, 32, 16, 2, 'fill');
end
end

% ======================== 局部函数 ========================

function bw = removeFrame(bw)
%REMOVEFRAME 抹掉车牌外框, 分两步:
%   1) 清掉最外圈 2~3 像素(车牌外框通常紧贴车牌边缘)
%   2) 用"接近车牌整宽/整高"的长线条开运算提取残留外框再删除
%   注意: 线条长度必须明显大于字符最长笔画(如数字 1 的竖线约占牌高 70%),
%         用 W/3、H/3 会把数字 1 当成外框删掉。
[H, W] = size(bw);
ring = max(2, round(0.03 * H));
bw(1:ring, :) = false;      bw(end - ring + 1:end, :) = false;
bw(:, 1:ring) = false;      bw(:, end - ring + 1:end) = false;

hLine = imopen(bw, strel('line', max(5, round(0.60 * W)), 0));   % 水平长线
vLine = imopen(bw, strel('line', max(5, round(0.85 * H)), 90));  % 垂直长线
frame = hLine | vLine;
if any(frame(:))
    frame = imdilate(frame, strel('square', 3));
    bw = bw & ~frame;
end
end

function runs = logicalRuns(act)
%LOGICALRUNS 把逻辑向量中的连续 true 段转成 [起, 止] 列表
act = act(:)';
d = diff([false, act, false]);
s = find(d == 1);
e = find(d == -1) - 1;
runs = [s(:), e(:)];
end

function runs = mergeRuns(runs, maxGap)
%MERGERUNS 间隙小于 maxGap 的段合并(例如"川""湘"被切散的情况)
if isempty(runs), return; end
out = runs(1, :);
for i = 2:size(runs, 1)
    if runs(i, 1) - out(end, 2) - 1 <= maxGap
        out(end, 2) = runs(i, 2);
    else
        out(end + 1, :) = runs(i, :); %#ok<AGROW>
    end
end
runs = out;
end

function runs = adjustToCount(runs, proj, n)
%ADJUSTTOCOUNT 让字符段数等于估计字数 n
%   段数偏少 -> 从最宽的段开始, 在投影谷底切开
%   段数偏多 -> 合并间隙最小的一对相邻段
while size(runs, 1) < n
    w = runs(:, 2) - runs(:, 1) + 1;
    [wm, wi] = max(w);
    if wm < 8, break; end
    [runs, ok] = splitRun(runs, wi, proj);
    if ~ok, break; end
end
while size(runs, 1) > n
    gap = runs(2:end, 1) - runs(1:end - 1, 2) - 1;
    [gm, gi] = min(gap);
    if gm > 0.8 * mean(runs(:, 2) - runs(:, 1) + 1), break; end
    runs(gi, 2) = runs(gi + 1, 2);
    runs(gi + 1, :) = [];
end
end

function [runs, ok] = splitRun(runs, idx, proj)
%SPLITRUN 在段中部的投影最小值处切开(两端各留 20% 不参与找谷底)
ok = false;
a = runs(idx, 1);
b = runs(idx, 2);
m = round(0.20 * (b - a));
span = (a + m):(b - m);
if numel(span) < 5, return; end
[~, k]  = min(proj(span));
cut     = span(k);
runs    = [runs(1:idx - 1, :); [a, cut]; [cut + 1, b]; runs(idx + 1:end, :)];
ok = true;
end
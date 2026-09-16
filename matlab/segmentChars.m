function [charImages, bwPlate, bounds, inkAR] = segmentChars(plateGray)
%SEGMENTCHARS 车牌字符分割(垂直投影法)
%
%   [charImages, bwPlate, bounds] = SEGMENTCHARS(plateGray)
%   plateGray : 已校正、高度归一化(64)的灰度车牌
%   charImages: 1xN cell, 每个元素是 32x16 logical 的归一化字符
%   bwPlate   : 二值化结果(字符=白), 便于调试查看
%   bounds    : N x 2, 每个字符在车牌中的列区间 [起 止]
%   inkAR     : 1xN double, 每个字符墨迹外接框的宽高比(细长段 -> 数字 1)
%
%   流程: 光照均衡 -> Otsu 二值化 -> 统一极性 -> 去外框 -> 去噪
%         -> 垂直投影取字符段 -> 自校准定字数并拆分/合并 -> 归一化
%
%   关键参数都在下面注释里标了, 分割不对时优先调这三处:
%     1) mergeRuns 的间隙阈值  (汉字被切散 -> 调大; 相邻字粘连 -> 调小)
%     2) 列阈值 thr            (窄笔画如数字 1 的撇被切掉 -> 调小)
%     3) chooseCount 的候选范围 (目前 6~8, 双排牌切错时可临时收窄)

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

% ---------------- 5. 定字数, 再按字数拆分/合并 ----------------
% 不要用 span/W 这类固定比例估字数: 7 位普通牌与 8 位新能源牌裁紧后
% span/W 都在 0.87 左右, 固定比例必然把 8 位牌(新能源绿牌)当成 7 位,
% 结果是被强制合并掉一个字, 整牌全错。改成用切分结果自校准(见 chooseCount)。
if ~isempty(runs)
    runs = chooseCount(runs, proj);
end
bounds = runs;

% ---------------- 6. 输出归一化字符 ----------------
n = size(runs, 1);
charImages = cell(1, n);
inkAR      = zeros(1, n);
for k = 1:n
    seg = bw(:, runs(k, 1):runs(k, 2));
    [rr, cc] = find(seg);
    if ~isempty(rr)
        inkAR(k) = (max(cc) - min(cc) + 1) / (max(rr) - min(rr) + 1);
    end
    charImages{k} = normalizeChar(seg, 32, 16, 2, 'fill');
end
end

% ======================== 局部函数 ========================

function bw = removeFrame(bw)
%REMOVEFRAME 抹掉车牌外框, 分三步:
%   1) 清掉最外圈 2~3 像素(车牌外框通常紧贴车牌边缘)
%   2) 整块删掉"贴着外沿的细长连通域", 三项同时成立才算边框残留:
%        (a) 外接框触到外圈 ring+1 以内(车牌外框 / 白边一定贴边)
%        (b) 最长边 >= 0.30*H
%        (c) 满足任一"纤细"判据:
%              面积 / 最长边 <= 4.5 px   (细长残段, 如被 JPEG 打断的下边框)
%              面积 / 周长   <= 1.5 px   (线条状, 如闭合的矩形外框白线)
%      判据细节见 removeEdgeSlivers。
%   3) 全图再做一次长线开运算(水平 0.60*W / 垂直 0.85*H), 清完整的外框线
%
%   第 2 步原来按"外圈窄带内清超长笔画"(垂直 0.60*H / 水平 0.35*W)判断, 有两个毛病:
%   一是实拍牌照的边框被 JPEG 与模糊打断成几段, 每一段都短于阈值, 整条边框留了
%   下来(实测 real01 的下边框断成 51 px 和 142 px 两段), 把每个字符的墨迹包围盒从
%   42 行撑到 58 行, 归一化时字符被纵向压扁, 模板匹配得分从 0.7 掉到 0.3;
%   二是它只看"某一行 / 某一列的连续段", 斜边框每行只占 1~2 px, 长度判据根本检
%   不出来, 反过来又会因为某行笔画长就把整行抹掉 —— 合成图 bench01 左下角留下的
%   1 px 宽、23 px 高的边框残端, 就是被它漏掉后又当成独立字符, 使 7 位牌切出 8 段。
%   改成按连通域判断后: 边框残渣又长又薄(实测多为 1~2 px 厚)会被整块删掉; 字符
%   笔画有 4~6 px 厚, 即使贴到边也会放行。
%   数字 1 的竖线约占 0.70*H, 但它落在车牌中部, 不贴外沿, 不会被误删。
[H, W] = size(bw);
ring = max(2, round(0.03 * H));
bw(1:ring, :) = false;      bw(end - ring + 1:end, :) = false;
bw(:, 1:ring) = false;      bw(:, end - ring + 1:end) = false;

bw = removeEdgeSlivers(bw, ring, 0.30 * H, 4.5, 1.5);

hLine = imopen(bw, strel('line', max(5, round(0.60 * W)), 0));   % 完整水平外框
vLine = imopen(bw, strel('line', max(5, round(0.85 * H)), 90));  % 完整垂直外框
frame = hLine | vLine;
if any(frame(:))
    frame = imdilate(frame, strel('square', 3));
    bw = bw & ~frame;
end
end

function bw = removeEdgeSlivers(bw, ring, minLen, maxThick, maxLineT)
%REMOVEEDGESLIVERS 整块删掉"贴着车牌外沿、又长又细"的连通域(车牌外框 / 白边残留)
%
%   bw       : 已经清掉最外圈 ring 像素的二值车牌
%   minLen   : 最长边短于它的连通域一律不动(噪点、牌照上的小铆钉等)
%   maxThick : "平均厚度 = 面积 / 最长边"不超过它, 判为细长残段
%   maxLineT : "线宽 = 面积 / 周长"不超过它, 判为线条状(闭合外框线)
%
%   判据要三项同时成立: 贴边 + 够长 + 够细。两个厚度判据是"或"的关系:
%     * 被打断的下边框这类细长残段 -> 面积/最长边 很小;
%     * 闭合的矩形外框白线       -> 面积/最长边 约为真实线宽的 2 倍
%       (长边、短边的面积都算进分子, 却只除以一条长边), 实测 6.8 > 4.5,
%       单靠上式会整条漏掉; 而 面积/周长 对任何细线都约等于真实线宽
%       (实测外框 1.26, 汉字笔画 1.67~2.2), 所以补上这一条。
%   贴边但很粗的连通域 = 字符被裁到了边, 放行。
[H, W] = size(bw);
[L, n] = bwlabel(bw, 8);
if n == 0, return; end
st = regionprops(L, 'Area', 'BoundingBox', 'Perimeter');
drop = false(1, n);
for i = 1:n
    bb = st(i).BoundingBox;                  % [x y w h], 边界为像素边缘
    x0 = bb(1) + 0.5;      x1 = bb(1) + bb(3) - 0.5;
    y0 = bb(2) + 0.5;      y1 = bb(2) + bb(4) - 0.5;
    touches = x0 <= ring + 1 || y0 <= ring + 1 || x1 >= W - ring || y1 >= H - ring;
    if ~touches, continue; end
    len = max(bb(3), bb(4));
    if len < minLen, continue; end
    a = st(i).Area;
    if a / len <= maxThick || a / max(st(i).Perimeter, 1) <= maxLineT
        drop(i) = true;
    end
end
if any(drop)
    bw = bw & ~ismember(L, find(drop));
end
end

function runs = chooseCount(runs, proj)
%CHOOSECOUNT 在 6~8 之间挑一个最合理的字符数
%   判据: 相邻字符"中心间距"越均匀越好。两个字被并成一段(间距约 2 倍),
%   或一个字被切成两段(间距约 0.5 倍), 都会让间距忽大忽小; 所以间距的
%   变异系数(标准差/均值)最小的那个候选, 就是最可能的真实字数。
%   这样 7 位普通牌和 8 位新能源牌都能自适应, 不依赖 span/W 固定比例
%   (裁紧后两者的 span/W 都在 0.87 左右, 固定比例必然把 8 位牌当成 7 位)。
%   试过把"各段宽度是否均匀"也加进判据: 基准集与难集的字符数正确率都变差,
%   说明二值化后笔画粘连/断裂导致的宽度抖动, 比"字被劈成两半"更常见。
%   候选位数分两轮: 先只试 7 / 8(中国大陆单排车牌只有这两种制式: 普通牌
%   7 位、新能源牌 8 位), 都不成立时才退回 6~8 全范围。实拍图里笔画粘连/
%   断裂会让原始段数偏少, 而"中心间距最均匀"这个判据在段数越少时越容易偶然
%   取到很小的变异系数 —— 实测 real01 就被选成了 6 段。先用制式先验收窄范围
%   能挡住这类退化; 收窄后若一个都拟合不上(adjustToCount 会主动放弃而不是
%   硬切), 再退回全范围, 不会漏掉特殊号牌。
bestRuns = tryCounts(runs, proj, 7, 8);
if isempty(bestRuns)
    bestRuns = tryCounts(runs, proj, 6, 8);
end
if isempty(bestRuns)
    bestRuns = runs;
end
runs = bestRuns;
end

function bestRuns = tryCounts(runs, proj, lo, hi)
bestRuns = []; best = inf;
for n = lo:hi
    r = adjustToCount(runs, proj, n);
    if size(r, 1) ~= n, continue; end
    d = diff(mean(r, 2));
    if numel(d) < 3, continue; end
    cv = std(d) / max(eps, mean(d));
    if cv < best - 1e-9
        best = cv;  bestRuns = r;
    end
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

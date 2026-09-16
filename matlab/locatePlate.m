function [plateBox, plateMask, scoreInfo] = locatePlate(I)
%LOCATEPLATE 在整幅图像中定位车牌区域
%
%   [plateBox, plateMask, info] = LOCATEPLATE(I)
%   输入: I - uint8 彩色图像
%   输出: plateBox  - [x y w h]; 空表示未找到
%         plateMask - 车牌的二值掩膜 (与 I 同尺寸, 用于后续倾斜校正)
%         scoreInfo - 结构体, 最佳候选的评分细节(供 lpr_main 判断"是不是真车牌"):
%                     .score       综合得分
%                     .source      'color' 颜色通道 | 'edge' 边缘通道
%                     .aspect      长宽比
%                     .colorFrac   框内车牌底色像素占比   <-- 区分真假车牌最有效的量
%                     .edgeDensity 框内垂直边缘密度
%                     .extent      矩形度
%                     .area        连通域面积
%                     .nCand       通过硬性条件的候选个数
%                     .valid       是否真车牌(矩形度 + 底色占比双闸门)
%                     .reject      被判为假车牌的原因(可直接提示给用户)
%
%   思路:
%     1) 按颜色(蓝底/新能源绿底/黄底)得到候选掩膜
%     2) 另一路用"垂直边缘 + 水平形态学闭运算"得到候选掩膜(兼容灰度图)
%     3) 对每个连通域按 长宽比 / 面积 / 颜色一致性 / 边缘密度 / 矩形度 打分, 取最高分
%
%   调参提示:
%     - 车牌漏检  -> 放宽容色阈值(S > 0.30 改 0.20), 或降低 minAreaFrac
%     - 误检到别的蓝色物体 -> 提高 0.25*sCol 权重, 提高长宽比惩罚
%     - 车牌很大/很小 -> 调整 minAreaFrac (默认 0.0008)

if size(I, 3) == 1
    I = repmat(I, 1, 1, 3);
end
[H, W, ~] = size(I);
minArea = 0.0008 * H * W;          % 车牌最小面积(像素)

% ---- 真车牌闸门: 用来拒绝形状/颜色不像车牌的候选区域 ----
% 真车牌一定是规整的彩色矩形: 矩形度高, 且框内大部分像素是车牌底色。
% 实测(23 张真车牌 / 10 张无车牌干扰图):
%   真车牌  矩形度 0.65~0.95, 底色占比 0.67~0.85
%   干扰图  矩形度只有 0.35~0.48 (噪声/蓝天/棋盘格/路面/夜景)
minExtent  = 0.50;                % 矩形度下限
minColorFr = 0.30;                % 框内车牌底色占比下限
gray = rgb2gray(I);

% ---------------- 1. 颜色掩膜 ----------------
hsv = rgb2hsv(I);
Hh = hsv(:, :, 1); S = hsv(:, :, 2); V = hsv(:, :, 3);

mBlue   = (Hh > 0.52 & Hh < 0.72) & S > 0.30 & V > 0.18;   % 蓝底车牌
mGreen  = (Hh > 0.22 & Hh < 0.45) & S > 0.25 & V > 0.18;   % 新能源绿牌
mYellow = (Hh > 0.09 & Hh < 0.20) & S > 0.35 & V > 0.40;   % 黄底车牌
colorMask = mBlue | mGreen | mYellow;

% 形态学整理: 闭运算把"底色+字符"连成一个块, 开运算去孤立噪点
wLen = max(9, round(W / 80));
hLen = max(3, round(H / 200));
cMask = imclose(colorMask, strel('rectangle', [hLen, wLen]));
cMask = imopen(cMask, strel('rectangle', [3 3]));
cMask = imfill(cMask, 'holes');
cMask = bwareaopen(cMask, round(minArea));

% ---------------- 2. 边缘掩膜(备用通道) ----------------
eMask = buildEdgeMask(gray, W, minArea);

% ---------------- 3. 打分选优 ----------------
[boxC, maskC, scC] = bestRegion(cMask, gray, colorMask, minArea, minExtent, minColorFr);
[boxE, maskE, scE] = bestRegion(eMask, gray, colorMask, minArea, minExtent, minColorFr);

% 两个通道之间也按同一把尺子比: 先看谁给出了"通过双闸门"的候选, 再看分数。
% 否则边缘通道的高分糊块会盖掉颜色通道已经找到的真车牌。
if scC.gated == scE.gated
    takeColor = scC.score >= scE.score;
else
    takeColor = scC.gated;
end
if takeColor
    plateBox = boxC; plateMask = maskC; scoreInfo = scC; scoreInfo.source = 'color';
else
    plateBox = boxE; plateMask = maskE; scoreInfo = scE; scoreInfo.source = 'edge';
end

if ~isempty(plateBox)
    plateBox = refineBox(plateMask, plateBox);
    scoreInfo.colorFrac = fracColor(plateBox, colorMask);
    scoreInfo.edgesFrac = fracColor(plateBox, edge(gray, 'sobel', 'vertical'));
    scoreInfo.valid = scoreInfo.extent >= minExtent && scoreInfo.colorFrac >= minColorFr;
    if scoreInfo.extent < minExtent
        scoreInfo.reject = sprintf('候选区域矩形度 %.2f < %.2f, 不像规整的矩形车牌', scoreInfo.extent, minExtent);
    elseif scoreInfo.colorFrac < minColorFr
        scoreInfo.reject = sprintf('框内车牌底色只占 %.2f (< %.2f), 没有蓝/绿/黄底色', scoreInfo.colorFrac, minColorFr);
    end
end
end

% ======================== 局部函数 ========================

function mask = buildEdgeMask(gray, W, minArea)
%BUILDEDGEMASK 垂直边缘 + 水平闭运算: 把一行字符连成矩形块
g = adapthisteq(gray, 'NumTiles', [8 8], 'ClipLimit', 0.02);
g = medfilt2(g, [3 3]);
bw = edge(g, 'sobel', 'vertical');                % 字符以垂直边缘为主
wLen = max(15, round(W / 40));
bw = imclose(bw, strel('rectangle', [3, wLen]));
bw = imdilate(bw, strel('rectangle', [3 3]));
bw = imfill(bw, 'holes');
mask = bwareaopen(bw, round(minArea));
end

function [box, regionMask, info] = bestRegion(mask, gray, colorMask, minArea, minExtent, minColorFr)
%BESTREGION 对掩膜中所有连通域打分, 返回得分最高的车牌候选
%   双闸门(矩形度 + 框内底色占比)在这里参与选优, 不只是最后校验胜出者 —— 见下面注释。
box = []; regionMask = []; info = emptyInfo(); nCand = 0;
haveBest = false; bestScore = -inf; bestPass = false;
if ~any(mask(:)), return; end

CC = bwconncomp(mask);
stats = regionprops(CC, 'BoundingBox', 'Area', 'Extent', 'PixelIdxList');
ed = edge(gray, 'sobel', 'vertical');

[H, W] = size(gray);
for k = 1:numel(stats)
    bb = stats(k).BoundingBox;                 % [x y w h]
    ar = bb(3) / bb(4);                        % 长宽比
    area = stats(k).Area;

    % ---- 硬性条件 ----
    if ar < 1.6 || ar > 6.5, continue; end      % 中国车牌 440:140 = 3.14
    if area < minArea, continue; end
    if bb(4) > 0.60 * H, continue; end           % 过高, 不可能是车牌
    if bb(3) > 0.95 * W, continue; end
    nCand = nCand + 1;

    % ---- 软性打分 ----
    sAR   = exp(-((ar - 3.3) / 1.1)^2);                       % 长宽比越接近 3.3 越好
    sArea = min(1, area / (0.01 * H * W));                    % 面积
    x1 = max(1, floor(bb(1)));          y1 = max(1, floor(bb(2)));
    x2 = min(W, ceil(bb(1) + bb(3) - 1)); y2 = min(H, ceil(bb(2) + bb(4) - 1));
    cm = colorMask(y1:y2, x1:x2);
    sCol  = nnz(cm) / numel(cm);                              % 颜色一致性
    dR    = ed(y1:y2, x1:x2);
    eDen  = nnz(dR) / numel(dR);                              % 边缘密度(原始值)
    sEdge = min(1, eDen / 0.25);
    sExt  = stats(k).Extent;                                  % 矩形度

    score = 0.30 * sAR + 0.15 * sArea + 0.25 * sCol + 0.20 * sEdge + 0.10 * sExt;

    % 选优以"是否通过双闸门"为主序, 分数为次序。
    %
    % 原来这两个闸门只在 bestRegion 返回之后校验冠军。代价是: 只要有一个
    % 底色占比 0 的边缘糊块得分更高, 它就会把真正通过闸门的彩色候选挤掉,
    % 然后自己又被闸门否掉 —— 整张图报"未检测到车牌", 而正确答案其实一直
    % 躺在候选列表里。实测 real01(新能源绿牌): 颜色掩膜已经给出
    % [660 675 164 36] (中位 H=0.41 / S=0.71, 正是绿牌底色), 却因为一个
    % 边缘块胜出而被丢掉。改成主序选优后 real01 恢复正常, 且 bench/hard
    % 逐图结果完全不变。
    pass = (sExt >= minExtent) && (sCol >= minColorFr);
    if ~haveBest || (pass && ~bestPass) || (pass == bestPass && score > bestScore)
        haveBest = true; bestScore = score; bestPass = pass;
        box = bb;
        regionMask = false(H, W);
        regionMask(stats(k).PixelIdxList) = true;
        info = struct('score', score, 'source', '', 'aspect', ar, ...
                      'colorFrac', sCol, 'edgeDensity', eDen, 'edgesFrac', eDen, ...
                      'extent', sExt, 'area', area, 'nCand', 0, ...
                      'gated', pass, 'valid', false, 'reject', '');
    end
end
if ~isempty(box), info.nCand = nCand; end
end

function bb = refineBox(mask, bb)
%REFINEBOX 用掩膜的行列投影把候选框收紧到车牌实际边界
x1 = max(1, floor(bb(1)));            y1 = max(1, floor(bb(2)));
x2 = min(size(mask, 2), ceil(bb(1) + bb(3) - 1));
y2 = min(size(mask, 1), ceil(bb(2) + bb(4) - 1));
sub = mask(y1:y2, x1:x2);
colSum = sum(sub, 1); rowSum = sum(sub, 2);
if ~any(colSum) || ~any(rowSum), return; end
cs = find(colSum > 0.25 * max(colSum));
rs = find(rowSum > 0.25 * max(rowSum));
bb = [x1 + cs(1) - 1, y1 + rs(1) - 1, cs(end) - cs(1) + 1, rs(end) - rs(1) + 1];
end

function r = fracColor(bb, m)
%FRACCOLOR 统计框内某掩膜的像素占比
[H, W] = size(m);
x1 = max(1, floor(bb(1)));            y1 = max(1, floor(bb(2)));
x2 = min(W, ceil(bb(1) + bb(3) - 1)); y2 = min(H, ceil(bb(2) + bb(4) - 1));
if x2 < x1 || y2 < y1, r = 0; return; end
sub = m(y1:y2, x1:x2);
r = nnz(sub) / numel(sub);
end

function s = emptyInfo()
s = struct('score', -inf, 'source', '', 'aspect', 0, 'colorFrac', 0, ...
            'edgeDensity', 0, 'edgesFrac', 0, 'extent', 0, 'area', 0, 'nCand', 0, ...
            'gated', false, 'valid', false, ...
            'reject', '没有找到长宽比/面积像车牌的候选区域');
end

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
%                                  | 'white' 白底车牌通道(警用/军用/使领馆)
%                     .aspect      长宽比
%                     .colorFrac   框内车牌底色像素占比   <-- 区分真假车牌最有效的量
%                     .whiteFrac   框内白色像素占比(白底车牌用)
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
minEdgeDen = 0.02;                % 框内垂直边缘密度下限: 车牌上一定有成排的字
% ---- 白底车牌(警用/军用/使领馆)闸门 ----
% 白底牌没有蓝/绿/黄底色, 颜色掩膜整条路都失效; 而"白底 + 黑字"在边缘掩膜里
% 只剩一条字符带, 矩形度天然比整块彩色底板的低。所以白底这一路单独一套判据:
%   白底占比高 + 垂直边缘密度高(有字) + 面积像车牌, 三项同时成立才算候选。
% 实测 real03(白底警牌): 真车牌候选 [435 819 218 94] 矩形度 0.48(<0.50)、
% 底色占比 0、白底占比 0.53、垂直边缘密度 0.067; 而同一张图里胜出的蓝色假块
% 垂直边缘密度只有 0.014(一块没有字的纯色块), 靠 minEdgeDen 就能挡住。
minWhiteFr = 0.35;                % 框内白底占比下限
minExtentW = 0.40;                % 白底候选用更低的矩形度下限
% 白底通道补的两道判据(第五轮的误检测试逼出来的):
%   1) 光有"白底 + 杂点边缘"还不够, 框里得真有**成排的字符墨迹**。
%      判据用"相对框内背景的对比度"(不是固定的灰度阈值): real03 是过曝照片,
%      字符灰度只有 130~180、底板 208, 固定阈值(如 <0.45)会把真车牌一起杀掉;
%      而纯白墙的噪声幅度只有 ±16(实测暗于背景 45 的像素占 0.000,
%      real03 的候选框是 0.240)。
%   2) 白底广告牌上的字比车牌"扁": 实测白底中文牌 ar=4.16、英文牌 ar=5.21,
%      而中国车牌是 440x140 ≈ 3.14(白底 blob 会往上下多带一点, 实测真车牌
%      real03 的候选 blob ar=2.33), 所以上限收到 3.8。
%      ⚠️ 残余风险: 白底 + 成排黑字 + 长宽比 3 左右的广告牌/标牌仍然可能被
%      当成白底车牌(这是白底通道的原理性风险, 见第五轮报告第 6 节 B)。
minInkFr   = 0.05;                % 框内"有字符墨迹"的像素占比下限(相对框内背景)
inkDelta   = 45/255;              % 算墨迹时的灰度差(差这么多才算字)
maxAspectW = 3.8;                 % 白底候选长宽比上限
% ---- 结构证据: 框里必须有"成排的字"(见 hasCharStructure) ----
% 上面那些判据(长宽比/矩形度/底色占比/边缘密度)全是"尺度无关"的统计量, 纯随机
% 噪声图上总能凑出一块全部过关的斑块 —— 实测 680x1000 的纯噪声图 8/8 报出凭空
% 捏造的车牌号(第四轮起就有, 见 结果记录/第五轮_噪声图误检_实测证据.txt)。
% 这两条只在"定位框收紧之后"复算(见文件末尾的 valid): 它衡量的是"这个框喂给
% 分割会不会切出字", 必须在最终那个框上算; 在候选连通域的原框上算会把小号牌
% (车牌只占框的一部分)误判成"切不出字"。
minCharSegs = 2;                  % 字符段数下限(实测真车牌 2~9 段, 纯噪声恒为 1)
maxCharSegs = 12;                 % 字符段数上限
maxCompFr   = 0.25;               % 最大墨迹连通域占比上限(纯噪声/纯色块是一整坨)
gray = rgb2gray(I);

% ---------------- 1. 颜色掩膜 ----------------
hsv = rgb2hsv(I);
Hh = hsv(:, :, 1); S = hsv(:, :, 2); V = hsv(:, :, 3);

mBlue   = (Hh > 0.52 & Hh < 0.72) & S > 0.30 & V > 0.18;   % 蓝底车牌
mGreen  = (Hh > 0.22 & Hh < 0.45) & S > 0.25 & V > 0.18;   % 新能源绿牌
mYellow = (Hh > 0.09 & Hh < 0.20) & S > 0.35 & V > 0.40;   % 黄底车牌
colorMask = mBlue | mGreen | mYellow;
% 白底车牌的底板(警用/军用/使领馆/临时). 白色车身也是白的, 所以这一路
% 不能只看颜色, 必须配合"垂直边缘密度"(见 minEdgeDen)
mWhite = (S < 0.25) & (V > 0.40);

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
gates = struct('minExtent', minExtent, 'minColorFr', minColorFr, ...
               'minEdgeDen', minEdgeDen, 'minWhiteFr', minWhiteFr, 'minExtentW', minExtentW, ...
               'minInkFr', minInkFr, 'inkDelta', inkDelta, 'maxAspectW', maxAspectW, ...
               'minCharSegs', minCharSegs, 'maxCharSegs', maxCharSegs, 'maxCompFr', maxCompFr);
[boxC, maskC, scC] = bestRegion(cMask, gray, colorMask, minArea, gates);
[boxE, maskE, scE] = bestRegion(eMask, gray, colorMask, minArea, gates);

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

% ---------------- 4. 兜底: 白底车牌(警用/军用/使领馆) ----------------
% 蓝/绿/黄三路都没找到真车牌时才走这一步, 所以现有任何一张能定位到车牌的图
% 都不会受影响。白底车牌只能用边缘掩膜里的"字符带"找, 判据见 whitePlateRegion。
if ~scoreInfo.gated
    [boxW, maskW, scW] = whitePlateRegion(eMask, gray, mWhite, minArea, gates);
    if ~isempty(boxW)
        plateBox = boxW; plateMask = maskW; scoreInfo = scW; scoreInfo.source = 'white';
    end
end

if ~isempty(plateBox)
    if strcmp(scoreInfo.source, 'white')
        % 白底候选的掩膜只剩一条"字符带"(没有整块底板), 用默认 0.25 收紧会把
        % 笔画上下切掉。实测 real03: 0.25 -> 框高 55, 后 4 位全错; 0.10 -> 框高
        % 66, 序号 A/3/4/5 全部排第 1。彩色通道的掩膜是整块底板, 维持 0.25 不变。
        plateBox = refineBox(plateMask, plateBox, 0.10);
    else
        plateBox = refineBox(plateMask, plateBox);
    end
    scoreInfo.colorFrac = fracColor(plateBox, colorMask);
    scoreInfo.whiteFrac = fracColor(plateBox, mWhite);
    scoreInfo.edgesFrac = fracColor(plateBox, edge(gray, 'sobel', 'vertical'));
    % 结构证据也要按收紧后的框复算一遍: 与 bestRegion 里的 pass 判据保持一致
    x1 = max(1, floor(plateBox(1)));              y1 = max(1, floor(plateBox(2)));
    x2 = min(size(gray, 2), ceil(plateBox(1) + plateBox(3) - 1));
    y2 = min(size(gray, 1), ceil(plateBox(2) + plateBox(4) - 1));
    structOK = hasCharStructure(gray(y1:y2, x1:x2), gates);
    if strcmp(scoreInfo.source, 'white')
        scoreInfo.valid = scoreInfo.whiteFrac >= minWhiteFr && scoreInfo.edgesFrac >= minEdgeDen;
        if scoreInfo.whiteFrac < minWhiteFr
            scoreInfo.reject = sprintf('白底候选里白色只占 %.2f (< %.2f), 不像白底车牌', scoreInfo.whiteFrac, minWhiteFr);
        elseif scoreInfo.edgesFrac < minEdgeDen
            scoreInfo.reject = sprintf('框内垂直边缘密度 %.3f < %.3f, 没有成排的字符', scoreInfo.edgesFrac, minEdgeDen);
        end
    else
        % 四个闸门要跟 bestRegion 里的 pass 判据保持一致: 漏掉 edgesFrac 会让
        % "有颜色但没字"的候选(real03 那块蓝色车身就是)在最后一步被放行;
        % 漏掉结构证据同理(见 hasCharStructure)。
        scoreInfo.valid = scoreInfo.extent >= minExtent && scoreInfo.colorFrac >= minColorFr ...
                          && scoreInfo.edgesFrac >= minEdgeDen && structOK;
        if scoreInfo.extent < minExtent
            scoreInfo.reject = sprintf('候选区域矩形度 %.2f < %.2f, 不像规整的矩形车牌', scoreInfo.extent, minExtent);
        elseif scoreInfo.colorFrac < minColorFr
            scoreInfo.reject = sprintf('框内车牌底色只占 %.2f (< %.2f), 没有蓝/绿/黄底色', scoreInfo.colorFrac, minColorFr);
        elseif scoreInfo.edgesFrac < minEdgeDen
            scoreInfo.reject = sprintf('框内垂直边缘密度 %.3f < %.3f, 没有成排的字符', scoreInfo.edgesFrac, minEdgeDen);
        elseif ~structOK
            scoreInfo.reject = '框内切不出成排的字符(结构证据不足), 不像车牌';
        end
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

function ok = hasCharStructure(sub, gates)
%HASCHARSTRUCTURE 结构证据: 框里得真有"成排的字"(挡纯噪声 / 纯色块)
%   为什么要单加这一条: 其它闸门(长宽比 / 矩形度 / 底色占比 / 边缘密度)全是
%   "尺度无关"的统计量, 纯随机噪声图上总能凑出一块样样过关的斑块 —— 实测
%   680x1000 的纯噪声图 8/8 报出凭空捏造的车牌号(第四轮起就有, 见
%   结果记录/第五轮_噪声图误检_实测证据.txt)。
%   二值化沿用 segmentChars 前几步(CLAHE + Otsu + 保证"字符=少数"), 两项判据:
%     1) 墨迹不能是"一整坨": 最大墨迹连通域占框内像素的比例 <= maxCompFr。
%        纯噪声 / 纯色块二值化后墨迹连成一整块(实测 5 个噪声块 0.27~0.48),
%        真车牌则是十来个字符块(实测 47 张有车牌图 0.035~0.14);
%     2) 字符带内的列投影得切得出 >= 2 段(下限见 gates.minCharSegs):
%        纯噪声每列的墨迹量几乎相同, 列投影是一整条(实测 8 个种子全是 1 段),
%        真车牌实测 2~9 段(real01 8 段、real02 3 段、sm02 4 段)。
%   段数下限取 2 而不是 6~9: 实测小结牌(120~170 px 宽)本来就会糊成一两段,
%   卡 6 段会把它们全挡掉; 真正有判别力的是"1 段 = 一整坨"。
%   先去掉"贯穿整行"的边框亮线(车牌自己的白边): 它让每一列都带上几个墨迹像素,
%   会把字与字之间的间隙填平 —— 实测 bench12 的真车牌候选不去边框时列投影的
%   谷底是 6~7(高于阈值), 只数出 2 段; 去掉后是干净的 9 段。
%   ⚠️ 这条只说明"框里有成排的墨迹", 不等于"这是车牌": 蓝底广告牌、白底大字
%   标牌一样能过(那是本工程已知的原理性限制)。
[H, W] = size(sub);
ok = false;
if H < 10 || W < 30, return; end
g = adapthisteq(sub, 'NumTiles', [4 8], 'ClipLimit', 0.02);
bw = imbinarize(g, graythresh(g));
if nnz(bw) > 0.5 * numel(bw), bw = ~bw; end      % 保证"字符 = 少数"
rp = sum(bw, 2);
if ~any(rp), return; end
% 先去掉"贯穿整行"的边框亮线(车牌自己的白边): 它们让每一列都带上几个墨迹像素,
% 会把字与字之间的间隙填平, 段数就数不出来了
lmax = zeros(H, 1);
for y = 1:H
    d = diff([false, bw(y, :), false]);
    ss = find(d == 1);  ee = find(d == -1) - 1;
    if ~isempty(ss), lmax(y) = max(ee - ss + 1); end
end
bw(lmax >= 0.5 * W, :) = false;
rp = sum(bw, 2);
if ~any(rp), return; end
% "墨迹是不是一大坨": 纯噪声/纯色块二值化后墨迹连成一整块, 车牌则是十来个
% 字符块(实测 最大连通域占比: 纯噪声 0.27~0.48, 真车牌 0.035~0.14)
cc = bwconncomp(bw, 8);
maxCompFr = max(cellfun(@numel, cc.PixelIdxList)) / numel(bw);
rows = find(rp >= 0.15 * max(rp));
band = bw(rows(1):rows(end), :);
bh = size(band, 1);
cp = sum(band, 1);
% 阈值为"该列墨迹量"的相对值(0.25*峰值)而不是 0.10*带高: 小结牌(120~170 px 宽)
% 的字缝本来就只有几个像素深, 用绝对阈值会被判成"一整条"(实测 sm06: 谷底 6~11、
% 峰值 47、带高 56, 用 0.10*56=5.6 切出来只有 1 段; 换成 0.25*47=11.75 后字缝
% 全部露出来)。纯噪声的列投影本来就平坦(实测 峰/谷 = 14/4、13/2、15/5),
% 相对阈值切不出第二段。
[s, e] = runsOf(cp > 0.25 * max(cp));
if isempty(s), return; end
[s, e] = mergeCloser(s, e, max(1, round(0.008 * W)));     % 汉字被切散的缝
w = e - s + 1;
keep = w >= max(2, round(0.010 * W));
s = s(keep);  e = e(keep);
nSeg = numel(s);
if nSeg < gates.minCharSegs || nSeg > gates.maxCharSegs, return; end
if maxCompFr > gates.maxCompFr, return; end
ok = true;
end

function [s, e] = runsOf(act)
%RUNSOF 逻辑向量的连续 true 段 -> 起止下标
act = act(:)';
d = diff([false, act, false]);
s = find(d == 1);  e = find(d == -1) - 1;
end

function [s, e] = mergeCloser(s, e, maxGap)
%MERGECLOSER 把间隙 <= maxGap 的相邻段合并(例如汉字被切散的情况)
i = 1;
while i < numel(s)
    if s(i + 1) - e(i) - 1 <= maxGap
        e(i) = e(i + 1);  s(i + 1) = [];  e(i + 1) = [];
    else
        i = i + 1;
    end
end
end

function [box, regionMask, info] = bestRegion(mask, gray, colorMask, minArea, gates)
%BESTREGION 对掩膜中所有连通域打分, 返回得分最高的车牌候选
%   闸门(矩形度 + 框内底色占比 + 有字的垂直边缘)在这里参与选优, 不只是最后
%   校验胜出者 —— 见下面注释。
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
    % 第三个条件(框内垂直边缘密度)是这一轮加的: 车身上一块"又大又蓝"的区域也能
    % 满足前两条(实测 real03 的假候选 矩形度 0.76 / 底色占比 0.81), 但它没有
    % 成排的字符, 垂直边缘密度只有真车牌的 1/5。
    pass = (sExt >= gates.minExtent) && (sCol >= gates.minColorFr) && (eDen >= gates.minEdgeDen);
    if ~haveBest || (pass && ~bestPass) || (pass == bestPass && score > bestScore)
        haveBest = true; bestScore = score; bestPass = pass;
        box = bb;
        regionMask = false(H, W);
        regionMask(stats(k).PixelIdxList) = true;
        info = struct('score', score, 'source', '', 'aspect', ar, ...
                      'colorFrac', sCol, 'whiteFrac', 0, 'inkFrac', 0, 'edgeDensity', eDen, 'edgesFrac', eDen, ...
                      'extent', sExt, 'area', area, 'nCand', 0, ...
                      'gated', pass, 'valid', false, 'reject', '');
    end
end
if ~isempty(box), info.nCand = nCand; end
end

function [box, regionMask, info] = whitePlateRegion(mask, gray, whiteMask, minArea, gates)
%WHITEPLATEREGION 白底车牌(警用/军用/使领馆/临时)候选
%   白底车牌没有蓝/绿/黄底色, 颜色掩膜那一路完全失效, 只能从边缘掩膜里找:
%   "白底 + 黑字"在边缘掩膜里是一条字符带。判据是三项同时成立:
%     1) 框里大半是白色(白底占比 >= minWhiteFr) —— 车牌底板是白的;
%     2) 框里垂直边缘密度 >= minEdgeDen        —— 有成排的字符, 不是纯色块;
%     3) 长宽比/面积/矩形度像车牌 —— 白底候选的掩膜只剩字符带, 矩形度天然比
%        整块彩色底板的低, 所以下限放到 minExtentW。
%   白色车身也满足第 1 条, 但车身是光滑的, 第 2 条挡得住(实测 real03: 车身
%   区域垂直边缘密度约 0.01, 车牌字符带 0.067)。
%   三项都满足时按"白底占比 × 边缘密度 × 面积"排序 —— 任意一项弱, 乘积就掉
%   下来(实测 real03 的假候选 [483 53 103 36]: 白底占比 0.62 但面积只有真车牌
%   的 1/6, 乘积 0.02; 真车牌 0.23)。
box = []; regionMask = []; info = emptyInfo(); nCand = 0;
haveBest = false; bestEvidence = 0;
if ~any(mask(:)), return; end

CC = bwconncomp(mask);
stats = regionprops(CC, 'BoundingBox', 'Area', 'Extent', 'PixelIdxList');
ed = edge(gray, 'sobel', 'vertical');

[H, W] = size(gray);
for k = 1:numel(stats)
    bb = stats(k).BoundingBox;                 % [x y w h]
    ar = bb(3) / bb(4);
    area = stats(k).Area;

    % ---- 硬性条件(与 bestRegion 同一套, 只有矩形度下限放松、长宽比上限收紧) ----
    if ar < 1.6 || ar > gates.maxAspectW, continue; end
    if area < minArea, continue; end
    if bb(4) > 0.60 * H, continue; end
    if bb(3) > 0.95 * W, continue; end

    x1 = max(1, floor(bb(1)));            y1 = max(1, floor(bb(2)));
    x2 = min(W, ceil(bb(1) + bb(3) - 1)); y2 = min(H, ceil(bb(2) + bb(4) - 1));
    wm     = whiteMask(y1:y2, x1:x2);
    sWhite = nnz(wm) / numel(wm);
    % 字符墨迹: 以框内的"背景灰度"(中位数)为基准, 比它暗或亮 45/255 以上才算字。
    % 这样对过曝(real03 字符 130~180 / 底板 208)和欠曝都成立, 而纯色/纯噪声的块
    % (白墙噪声只有 ±16)算出来接近 0。
    gR     = double(gray(y1:y2, x1:x2)) / 255;   % 归一到 0~1, 与 inkDelta 同尺度
    gBg    = median(gR(:));
    sInk   = max(nnz(gR < gBg - gates.inkDelta), nnz(gR > gBg + gates.inkDelta)) / numel(gR);
    dR     = ed(y1:y2, x1:x2);
    eDen   = nnz(dR) / numel(dR);
    sExt   = stats(k).Extent;
    sArea  = min(1, area / (0.01 * H * W));

    if sExt < gates.minExtentW || sWhite < gates.minWhiteFr || eDen < gates.minEdgeDen ...
            || sInk < gates.minInkFr
        continue;
    end
    nCand = nCand + 1;
    evidence = sWhite * min(1, eDen / 0.08) * sArea;
    if ~haveBest || evidence > bestEvidence
        haveBest = true; bestEvidence = evidence;
        box = bb;
        regionMask = false(H, W);
        regionMask(stats(k).PixelIdxList) = true;
        info = struct('score', evidence, 'source', '', 'aspect', ar, ...
                      'colorFrac', 0, 'whiteFrac', sWhite, 'inkFrac', sInk, 'edgeDensity', eDen, ...
                      'edgesFrac', eDen, 'extent', sExt, 'area', area, 'nCand', 0, ...
                      'gated', true, 'valid', false, 'reject', '');
    end
end
if ~isempty(box), info.nCand = nCand; end
end

function bb = refineBox(mask, bb, thr)
%REFINEBOX 用掩膜的行列投影把候选框收紧到车牌实际边界
%   thr 为投影阈值(占最大投影的比例), 默认 0.25
if nargin < 3 || isempty(thr), thr = 0.25; end
x1 = max(1, floor(bb(1)));            y1 = max(1, floor(bb(2)));
x2 = min(size(mask, 2), ceil(bb(1) + bb(3) - 1));
y2 = min(size(mask, 1), ceil(bb(2) + bb(4) - 1));
sub = mask(y1:y2, x1:x2);
colSum = sum(sub, 1); rowSum = sum(sub, 2);
if ~any(colSum) || ~any(rowSum), return; end
cs = find(colSum > thr * max(colSum));
rs = find(rowSum > thr * max(rowSum));
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
s = struct('score', -inf, 'source', '', 'aspect', 0, 'colorFrac', 0, 'whiteFrac', 0, 'inkFrac', 0, ...
            'edgeDensity', 0, 'edgesFrac', 0, 'extent', 0, 'area', 0, 'nCand', 0, ...
            'gated', false, 'valid', false, ...
            'reject', '没有找到长宽比/面积像车牌的候选区域');
end

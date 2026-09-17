function S = bench_stage(dataDir, verbose, csvName, groupCol, maxSize)
%BENCH_STAGE 带「失败阶段归因」的评测: 定位 / 分割 / 识别, 各错在哪一步
%
%   S = BENCH_STAGE()                                   % 默认跑工程自带 bench/
%   S = BENCH_STAGE(dir)                                % 换目录
%   S = BENCH_STAGE(dir, false)                         % 不打印逐图明细(仍打印分组汇总)
%   S = BENCH_STAGE(dir, true, 'labels.csv', 'category')% 指定标注文件与分组列
%   S = BENCH_STAGE(dir, true, 'labels.csv', 'category', 1600)
%
%   和 bench_eval.m 的区别(两个都要留着, 用途不同):
%     1. bench_eval 只回答"整牌对不对"; 本函数把一张图的失败**归因到唯一一个阶段**
%        (定位失败 / 分割失败 / 识别失败), 因为"整牌 0/3"背后的病因完全不同。
%     2. 多了 GT 定位框(标注里的 box 列): 定位阶段单独判命中(IoU >= 0.5),
%        不再像 bench_eval 那样"只要切出了字符就默认定位没问题"。
%     3. 按 groupCol(默认 category)分组统计 —— 退化集就是靠这个看出
%        "车牌宽到多少像素时开始掉"的。
%
%   labels.csv 的列:
%     file,text     必需
%     bx,by,bw,bh   可选, 真值车牌框(原图坐标)。没有这四列就退回"有没有框"的弱判据。
%     qx1..qy4      可选, 真值四角(8 个数, 顺序任意)。有这八列就按**多边形**算 IoU
%                   (quadIoU.m) —— CCPD 这类旋转四角的外接框会把背景一起框进来,
%                   拿外接框当真值, IoU>=0.5 连"完美轴对齐检测器"都过不了(实测 14%)。
%     category/...  可选, 用于分组
%
%   负样本口径(第八轮 CCPD 真实集加进来时定的): 一行 **text 为空、又没有有效真值框**,
%   判为"这张图本来就没有车牌"(CCPD 的 ccpd_np 类)。它**不进**整牌/字符/定位/分割四个
%   分母 —— 这些指标对它没有意义; 尤其"什么都没检出"会让 strcmp(got,gt) 判成整牌
%   正确, 40 张负样本足以把整牌率凭空抬高 10 个点。负样本只回答一个问题: 有没有误检。
%   逐图归因里, 拒识记「正确」, 检出东西记「误检(无车牌图)」。
%
%   ⚠️ maxSize 必须与 lpr_main 的 ''MaxSize'' 一致(默认都是 1600)。lpr_main 内部会把
%      长边超过 MaxSize 的图等比缩小, 它返回的 plateBox 是**缩小后**那张图的坐标,
%      而标注里的框是**原图**坐标 —— 这里按同一个比例把真值框缩过去再比。
%      漏了这一步, 2272x1704 这种大图会算出 IoU=0 的假"定位失败"。
%
%   返回 S: n / posN(真有车牌的张数) / negTotal / negOk / negAcc
%           plateAcc / charAcc / segAcc / cnAcc / locAcc
%           attr(1x5: 正确/定位失败/分割失败/识别失败/误检(无车牌图) 的张数)
%           ceilIoU(逐图: 完美轴对齐框对真值四角的 IoU, 即该图 IoU 判据的上限)
%           negFP(误检的无车牌图文件名, cell)
%           groups(1xG: name,n,neg,negOk,plateAcc,charAcc,locAcc,attr)
%           file/expect/got/iou (逐图明细, cell)  isNeg/rejected (逐图逻辑标记)
%   没有负样本时 posN == n、negTotal == 0, attr 第 5 格恒为 0, 输出与老口径逐字节一致。
%
%   ⚠️「分割失败」用的是**字符数对不对**这个粗判据 —— 数对了但边界偏了会被归到
%      「识别失败」里去(real01 就是这样: 8 段对, 但定位框高只有真值的 0.72 倍,
%      上下沿的笔画被切掉)。所以本函数额外报一个「框高比」: 它显著小于 1 时,
%      那一行的「识别失败」里其实夹着**分割边界**的账。

if nargin < 1 || isempty(dataDir)
    dataDir = fullfile(fileparts(mfilename('fullpath')), 'bench');
end
if nargin < 2 || isempty(verbose),  verbose  = true; end
if nargin < 3 || isempty(csvName),  csvName  = 'labels.csv'; end
if nargin < 4 || isempty(groupCol), groupCol = 'category'; end
if nargin < 5 || isempty(maxSize),  maxSize  = 1600; end

csvFile = fullfile(dataDir, csvName);
if exist(csvFile, 'file') ~= 2
    cand = {'labels.csv', 'expected.csv'};
    for i = 1:numel(cand)
        q = fullfile(dataDir, cand{i});
        if exist(q, 'file') == 2, csvFile = q; break; end
    end
end
if exist(csvFile, 'file') ~= 2
    error('bench_stage:noLabels', '找不到标注文件: %s', fullfile(dataDir, csvName));
end
T = readtable(csvFile, 'Encoding', 'UTF-8', 'VariableNamingRule', 'preserve');
names = T.Properties.VariableNames;
boxCols  = {'bx', 'by', 'bw', 'bh'};
quadCols = {'qx1', 'qy1', 'qx2', 'qy2', 'qx3', 'qy3', 'qx4', 'qy4'};
hasBox  = all(ismember(boxCols, names));
hasQuad = all(ismember(quadCols, names));
hasGrp  = any(strcmp(names, groupCol));
if ~hasGrp, groupCol = ''; end

n = height(T);
S = struct();
S.n = n;
S.plateOk = 0; S.segOk = 0; S.charOk = 0; S.charTotal = 0;
S.cnOk = 0; S.cnTotal = 0; S.locOk = 0; S.locTotal = 0;
S.file = cell(n,1); S.expect = cell(n,1); S.got = cell(n,1); S.iou = nan(n,1);
S.group = cell(n,1); S.attrIdx = zeros(n,1); S.boxHRatio = nan(n,1);
S.ceilIoU = nan(n,1);               % 完美轴对齐检测器在这张图上的 IoU 上限
S.locCounted = false(n,1);          % 这一张有真值框、进了定位分母
S.isNeg    = false(n,1);            % 无车牌图(负样本) —— 不进四个分母
S.rejected = false(n,1);            % 负样本里"什么都没检出"的那些
S.negTotal = 0; S.negOk = 0; S.negFP = {};
ATTR = {'正确', '定位失败', '分割失败', '识别失败', '误检(无车牌图)'};
attr = zeros(1, 5);                 % 与 ATTR 一一对应(第 5 格只在有负样本时非 0)

if verbose
    fprintf('%-18s %-11s %-11s %5s %6s  %s\n', '文件', '期望', '识别', 'IoU', '字符', '归因');
end

for i = 1:n
    f  = T.file{i};
    gt = char(T.text{i});
    if hasGrp, S.group{i} = char(string(T.(groupCol)(i))); else, S.group{i} = 'all'; end
    gtBox = [];
    if hasBox
        v = zeros(1, 4);
        for c = 1:4
            x = T.(boxCols{c})(i);
            if iscell(x), x = x{1}; end
            v(c) = double(x);
        end
        if all(isfinite(v)) && v(3) > 0 && v(4) > 0, gtBox = v; end
    end
    gtQuad = [];
    if hasQuad
        q = zeros(1, 8);
        for c = 1:8
            x = T.(quadCols{c})(i);
            if iscell(x), x = x{1}; end
            q(c) = double(x);
        end
        if all(isfinite(q)) && any(q ~= 0), gtQuad = reshape(q, 2, 4)'; end
    end

    fp = fullfile(dataDir, f);
    warning('off', 'lpr:noPlate');
    if exist(fp, 'file') == 2
        if ~isempty(gtBox) || ~isempty(gtQuad)  % 真值换算到 lpr_main 看到的那张缩图上
            info = imfinfo(fp);
            mm = max(info(1).Height, info(1).Width);
            if mm > maxSize
                k = maxSize / mm;
                if ~isempty(gtBox),  gtBox  = gtBox  * k; end
                if ~isempty(gtQuad), gtQuad = gtQuad * k; end
            end
        end
        if verbose
            r = lpr_main(fp, 'ShowFigure', false);
        else
            evalc('r = lpr_main(fp, ''ShowFigure'', false);');
        end
    else
        warning('bench_stage:missingImage', '缺少图片: %s', f);
        r = struct('text','', 'chars',{{}}, 'plateBox',[]);
    end
    warning('on', 'lpr:noPlate');

    got = r.text;
    S.file{i} = f; S.expect{i} = gt; S.got{i} = got;
    isNeg = isempty(gt) && isempty(gtBox);        % 无车牌图: text 空 + 没有有效真值框
    S.isNeg(i) = isNeg;                           % ⚠ 下面必须判标量 isNeg 而不是 S.isNeg:
                                                  %   if <逻辑向量> 只在**全真**时才成立,
                                                  %   逐图累加 S.isNeg 会变成"永远走 else"

    if isNeg
        % ---- 负样本: 只回答"有没有误检" ----
        S.negTotal = S.negTotal + 1;
        S.rejected(i) = isempty(got) && isempty(r.plateBox);
        S.negOk = S.negOk + S.rejected(i);
        if S.rejected(i)
            a = 1;                                 % 拒识成功 = 正确
        else
            a = 5;                                 % 误检
            S.negFP{end+1} = f; %#ok<AGROW>
        end
    else
        % ---- 阶段一: 定位 ----
        if ~isempty(gtBox)
            S.locTotal = S.locTotal + 1;
            S.locCounted(i) = true;
            if ~isempty(r.plateBox)
                if ~isempty(gtQuad)
                    iou = quadIoU(r.plateBox, gtQuad);     % 真值四角: 按多边形算
                else
                    iou = boxIoU(r.plateBox, gtBox);       % 只有框: 退化成 框 vs 框
                end
                S.iou(i) = iou;
                S.boxHRatio(i) = r.plateBox(4) / max(eps, gtBox(4));
                locOK = iou >= 0.5;
            else
                locOK = false;
            end
            S.locOk = S.locOk + locOK;
            if ~isempty(gtQuad)
                S.ceilIoU(i) = quadIoU(gtBox, gtQuad);     % 完美轴对齐检测器的上限
            end
        else
            locOK = ~isempty(r.plateBox);      % 没有真值框: 只能退化成"有没有框"
            S.locTotal = S.locTotal + 1;
            S.locOk = S.locOk + locOK;
        end

        % ---- 阶段二/三: 分割 + 识别 ----
        nSegOK  = numel(r.chars) == numel(gt);
        k       = min(numel(got), numel(gt));
        S.segOk = S.segOk + nSegOK;
        S.charTotal = S.charTotal + numel(gt);
        S.charOk    = S.charOk + sum(got(1:k) == gt(1:k));
        if ~isempty(gt)
            S.cnTotal = S.cnTotal + 1;
            S.cnOk    = S.cnOk + (k >= 1 && got(1) == gt(1));
        end
        plateOK = strcmp(got, gt);
        S.plateOk = S.plateOk + plateOK;

        % ---- 归因: 一张图只算一个阶段 ----
        if plateOK
            a = 1;
        elseif ~locOK
            a = 2;
        elseif ~nSegOK
            a = 3;
        else
            a = 4;
        end
    end
    attr(a) = attr(a) + 1;
    S.attrIdx(i) = a;

    if verbose
        if isnan(S.iou(i)), iouS = '   --'; else, iouS = sprintf('%5.2f', S.iou(i)); end
        if isNeg
            gtS = '(无车牌)';
            if isempty(got), gotS = '(未检出)'; else, gotS = got; end
        else
            gtS = gt; gotS = got;
        end
        if ~isnan(S.boxHRatio(i)) && S.boxHRatio(i) < 0.8
            note = sprintf('  ⚠ 框高比 %.2f, 分割边界可能偏', S.boxHRatio(i));
        else
            note = '';
        end
        fprintf('%-18s %-11s %-11s %s %3d/%-3d  %s%s\n', f, gtS, gotS, iouS, ...
                numel(r.chars), numel(gt), ATTR{a}, note);
    end
end

S.attr  = attr;
S.posN  = n - S.negTotal;                          % 真有车牌的张数: 整牌/分割的分母
S.negAcc = S.negOk / max(1, S.negTotal);           % 无车牌图里"没误检"的比例
S.plateAcc = S.plateOk / max(1, S.posN);
S.charAcc  = S.charOk  / max(1, S.charTotal);
S.cnAcc    = S.cnOk    / max(1, S.cnTotal);
S.segAcc   = S.segOk   / max(1, S.posN);
S.locAcc   = S.locOk   / max(1, S.locTotal);

% ---------------- 分组统计 ----------------
gnames = unique(S.group, 'stable');
G = struct('name', {}, 'n', {}, 'neg', {}, 'negOk', {}, 'plateAcc', {}, ...
           'charAcc', {}, 'locAcc', {}, 'attr', {});
for g = 1:numel(gnames)
    m  = strcmp(S.group, gnames{g});
    mp = m(:) & ~S.isNeg;            % 分组指标只在"真有车牌"的图上算, 负样本另列
    e = struct('name', gnames{g}, 'n', sum(mp), 'neg', sum(m(:) & S.isNeg), ...
               'negOk', sum(m(:) & S.isNeg & S.rejected), ...
               'plateAcc', 0, 'charAcc', 0, 'locAcc', 0, 'attr', zeros(1,5));
    if any(mp)
        e.plateAcc = mean(strcmp(S.got(mp), S.expect(mp)));
    end
    cok = 0; ctot = 0;
    idx = find(mp);
    for j = idx(:)'
        L = min(numel(S.got{j}), numel(S.expect{j}));
        cok = cok + sum(S.got{j}(1:L) == S.expect{j}(1:L));
        ctot = ctot + numel(S.expect{j});
    end
    e.charAcc = cok / max(1, ctot);
    lok = 0; ltot = 0;
    for j = idx(:)'
        if S.locCounted(j)
            ltot = ltot + 1;
            lok = lok + (~isnan(S.iou(j)) && S.iou(j) >= 0.5);
        end
    end
    e.locAcc = lok / max(1, ltot);
    for a = 1:5, e.attr(a) = sum(m(:) & (S.attrIdx == a)); end
    G(end+1) = e; %#ok<AGROW>
end
S.groups = G;

% ---------------- 打印 ----------------
fprintf('\n================ 阶段归因 ================\n');
fprintf('整牌准确率      : %d/%d = %.1f%%\n', S.plateOk, S.posN, 100 * S.plateAcc);
fprintf('字符准确率      : %d/%d = %.1f%%\n', S.charOk, S.charTotal, 100 * S.charAcc);
fprintf('首位汉字准确率  : %d/%d = %.1f%%\n', S.cnOk, S.cnTotal, 100 * S.cnAcc);
fprintf('字符数分割正确  : %d/%d = %.1f%%\n', S.segOk, S.posN, 100 * S.segAcc);
if S.locTotal > 0
    fprintf('定位命中(IoU>=.5): %d/%d = %.1f%%\n', S.locOk, S.locTotal, 100 * S.locAcc);
end
ce = S.ceilIoU(~isnan(S.ceilIoU));
if ~isempty(ce)
    fprintf('定位上限(完美轴对齐框 vs 真值四角): 中位 %.2f; < 0.5 的有 %d/%d 张(IoU 判据在这几张上不可达)\n', ...
            median(ce), sum(ce < 0.5), numel(ce));
end
hr = S.boxHRatio(~isnan(S.boxHRatio));
if ~isempty(hr)
    fprintf('框高比(定位框高/真值框高): 中位 %.2f; < 0.8 的有 %d/%d 张(这些图的"识别失败"里夹着分割的账)\n', ...
            median(hr), sum(hr < 0.8), numel(hr));
end
fprintf('归因: %s %d | %s %d | %s %d | %s %d\n', ...
        ATTR{1}, attr(1), ATTR{2}, attr(2), ATTR{3}, attr(3), ATTR{4}, attr(4));
if S.negTotal > 0
    fprintf('无车牌图     : %d 张, 拒识 %d, 误检 %d (%s %d)\n', ...
            S.negTotal, S.negOk, S.negTotal - S.negOk, ATTR{5}, attr(5));
end

if numel(G) > 1
    if S.negTotal > 0
        fprintf('\n%-16s %4s %4s %4s %8s %8s %8s   %s\n', '分组', 'n', '无牌', '拒识', ...
                '整牌', '字符', '定位', '归因(正确/定位/分割/识别/误检)');
        for g = 1:numel(G)
            fprintf('%-16s %4d %4d %4d %7.1f%% %7.1f%% %7.1f%%   %d/%d/%d/%d/%d\n', ...
                    G(g).name, G(g).n, G(g).neg, G(g).negOk, 100*G(g).plateAcc, ...
                    100*G(g).charAcc, 100*G(g).locAcc, G(g).attr);
        end
    else
        fprintf('\n%-16s %4s %8s %8s %8s   %s\n', '分组', 'n', '整牌', '字符', '定位', '归因(正确/定位/分割/识别)');
        for g = 1:numel(G)
            fprintf('%-16s %4d %7.1f%% %7.1f%% %7.1f%%   %d/%d/%d/%d\n', ...
                    G(g).name, G(g).n, 100*G(g).plateAcc, 100*G(g).charAcc, 100*G(g).locAcc, G(g).attr(1:4));
        end
    end
end
end

% ======================== 局部函数 ========================

function iou = boxIoU(a, b)
%BOXIOU 两个 [x y w h] 框的交并比 (a 来自检测, b 是真值)
ax1 = a(1); ay1 = a(2); ax2 = a(1) + a(3) - 1; ay2 = a(2) + a(4) - 1;
bx1 = b(1); by1 = b(2); bx2 = b(1) + b(3) - 1; by2 = b(2) + b(4) - 1;
ix = max(0, min(ax2, bx2) - max(ax1, bx1) + 1);
iy = max(0, min(ay2, by2) - max(ay1, by1) + 1);
inter = ix * iy;
uni = a(3) * a(4) + b(3) * b(4) - inter;
iou = inter / max(eps, uni);
end

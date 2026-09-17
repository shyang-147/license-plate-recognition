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
%     category/...  可选, 用于分组
%
%   ⚠️ maxSize 必须与 lpr_main 的 ''MaxSize'' 一致(默认都是 1600)。lpr_main 内部会把
%      长边超过 MaxSize 的图等比缩小, 它返回的 plateBox 是**缩小后**那张图的坐标,
%      而标注里的框是**原图**坐标 —— 这里按同一个比例把真值框缩过去再比。
%      漏了这一步, 2272x1704 这种大图会算出 IoU=0 的假"定位失败"。
%
%   返回 S: n / plateAcc / charAcc / segAcc / cnAcc / locAcc
%           attr(1x4: 正确/定位失败/分割失败/识别失败 的张数)
%           groups(1xG: name,n,plateAcc,charAcc,locAcc,attr)
%           file/expect/got/iou (逐图明细, cell)
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
boxCols = {'bx', 'by', 'bw', 'bh'};
hasBox  = all(ismember(boxCols, names));
hasGrp  = any(strcmp(names, groupCol));
if ~hasGrp, groupCol = ''; end

n = height(T);
S = struct();
S.n = n;
S.plateOk = 0; S.segOk = 0; S.charOk = 0; S.charTotal = 0;
S.cnOk = 0; S.cnTotal = 0; S.locOk = 0; S.locTotal = 0;
S.file = cell(n,1); S.expect = cell(n,1); S.got = cell(n,1); S.iou = nan(n,1);
S.group = cell(n,1); S.attrIdx = zeros(n,1); S.boxHRatio = nan(n,1);
S.locCounted = false(n,1);          % 这一张有真值框、进了定位分母
ATTR = {'正确', '定位失败', '分割失败', '识别失败'};
attr = zeros(1, 4);                 % 与 ATTR 一一对应

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

    fp = fullfile(dataDir, f);
    warning('off', 'lpr:noPlate');
    if exist(fp, 'file') == 2
        if ~isempty(gtBox)                     % 真值框换算到 lpr_main 看到的那张缩图上
            info = imfinfo(fp);
            mm = max(info(1).Height, info(1).Width);
            if mm > maxSize, gtBox = gtBox * (maxSize / mm); end
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

    % ---- 阶段一: 定位 ----
    if ~isempty(gtBox)
        S.locTotal = S.locTotal + 1;
        S.locCounted(i) = true;
        if ~isempty(r.plateBox)
            iou = boxIoU(r.plateBox, gtBox);
            S.iou(i) = iou;
            S.boxHRatio(i) = r.plateBox(4) / max(eps, gtBox(4));
            locOK = iou >= 0.5;
        else
            locOK = false;
        end
        S.locOk = S.locOk + locOK;
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
    attr(a) = attr(a) + 1;
    S.attrIdx(i) = a;

    if verbose
        if isnan(S.iou(i)), iouS = '   --'; else, iouS = sprintf('%5.2f', S.iou(i)); end
        if ~isnan(S.boxHRatio(i)) && S.boxHRatio(i) < 0.8
            note = sprintf('  ⚠ 框高比 %.2f, 分割边界可能偏', S.boxHRatio(i));
        else
            note = '';
        end
        fprintf('%-18s %-11s %-11s %s %3d/%-3d  %s%s\n', f, gt, got, iouS, ...
                numel(r.chars), numel(gt), ATTR{a}, note);
    end
end

S.attr  = attr;
S.plateAcc = S.plateOk / max(1, n);
S.charAcc  = S.charOk  / max(1, S.charTotal);
S.cnAcc    = S.cnOk    / max(1, S.cnTotal);
S.segAcc   = S.segOk   / max(1, n);
S.locAcc   = S.locOk   / max(1, S.locTotal);

% ---------------- 分组统计 ----------------
gnames = unique(S.group, 'stable');
G = struct('name', {}, 'n', {}, 'plateAcc', {}, 'charAcc', {}, 'locAcc', {}, 'attr', {});
for g = 1:numel(gnames)
    m = strcmp(S.group, gnames{g});
    e = struct('name', gnames{g}, 'n', sum(m), 'plateAcc', 0, 'charAcc', 0, 'locAcc', 0, 'attr', zeros(1,4));
    e.plateAcc = mean(strcmp(S.got(m), S.expect(m)));
    cok = 0; ctot = 0;
    idx = find(m);
    for j = idx(:)'
        L = min(numel(S.got{j}), numel(S.expect{j}));
        cok = cok + sum(S.got{j}(1:L) == S.expect{j}(1:L));
        ctot = ctot + numel(S.expect{j});
    end
    e.charAcc = cok / max(1, ctot);
    if S.locTotal > 0
        lok = 0; ltot = 0;
        for j = idx(:)'
            if S.locCounted(j)
                ltot = ltot + 1;
                lok = lok + (~isnan(S.iou(j)) && S.iou(j) >= 0.5);
            end
        end
        e.locAcc = lok / max(1, ltot);
    end
    for a = 1:4, e.attr(a) = sum(m(:) & (S.attrIdx == a)); end
    G(end+1) = e; %#ok<AGROW>
end
S.groups = G;

% ---------------- 打印 ----------------
fprintf('\n================ 阶段归因 ================\n');
fprintf('整牌准确率      : %d/%d = %.1f%%\n', S.plateOk, n, 100 * S.plateAcc);
fprintf('字符准确率      : %d/%d = %.1f%%\n', S.charOk, S.charTotal, 100 * S.charAcc);
fprintf('首位汉字准确率  : %d/%d = %.1f%%\n', S.cnOk, S.cnTotal, 100 * S.cnAcc);
fprintf('字符数分割正确  : %d/%d = %.1f%%\n', S.segOk, n, 100 * S.segAcc);
if S.locTotal > 0
    fprintf('定位命中(IoU>=.5): %d/%d = %.1f%%\n', S.locOk, S.locTotal, 100 * S.locAcc);
end
hr = S.boxHRatio(~isnan(S.boxHRatio));
if ~isempty(hr)
    fprintf('框高比(定位框高/真值框高): 中位 %.2f; < 0.8 的有 %d/%d 张(这些图的"识别失败"里夹着分割的账)\n', ...
            median(hr), sum(hr < 0.8), numel(hr));
end
fprintf('归因: %s %d | %s %d | %s %d | %s %d\n', ...
        ATTR{1}, attr(1), ATTR{2}, attr(2), ATTR{3}, attr(3), ATTR{4}, attr(4));

if numel(G) > 1
    fprintf('\n%-16s %4s %8s %8s %8s   %s\n', '分组', 'n', '整牌', '字符', '定位', '归因(正确/定位/分割/识别)');
    for g = 1:numel(G)
        fprintf('%-16s %4d %7.1f%% %7.1f%% %7.1f%%   %d/%d/%d/%d\n', ...
                G(g).name, G(g).n, 100*G(g).plateAcc, 100*G(g).charAcc, 100*G(g).locAcc, G(g).attr);
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

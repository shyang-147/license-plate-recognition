function [plateText, chars, scores] = recognizeChars(charImages, varargin)
%RECOGNIZECHARS 字符识别(模板匹配 + 车牌制式按位约束)
%
%   [plateText, chars, scores] = RECOGNIZECHARS(charImages)
%   [plateText, chars, scores] = RECOGNIZECHARS(charImages, 'InkAR', inkAR, 'Format', F)
%
%   可选参数:
%       'TemplateFile'  模板文件路径, 默认用同目录的 templates.mat
%       'InkAR'         1xN, 每个字符墨迹外接框的宽高比(segmentChars 的第 4 个输出)
%       'Format'        plateFormat() 的返回值, 按位限制候选字符集
%       'ScoreMode'     相似度口径, 见文件末尾 bestMatch。默认 'iou'(历史口径)
%       'HoleWeight'    孔洞数不一致的罚分权重(默认 0.04), 见下方 bestMatch 说明
%
%   位置约定(按中国大陆车牌; 由 Format 给出, 不传 Format 时用下面的默认规则):
%       第 1 位 : 中文省份简称(31 个)   -> 用 chinese 模板
%       第 2 位 : 发牌机关代号(大写字母) -> 用 letters 模板
%       第 3 位起: 字母或数字(去掉 I、O) -> 用 alnum 模板
%       新能源 8 位牌的第 3 位也是字母(见 plateFormat.m)
%
%   匹配得分 = 0.65 * IoU(二值交并比) + 0.35 * 相关系数
%   模板文件不存在时自动调用 buildTemplates 生成 templates.mat

p = inputParser;
p.addParameter('TemplateFile', '');
p.addParameter('InkAR', []);
p.addParameter('Format', []);
p.addParameter('ScoreMode', 'iou');
p.addParameter('HoleWeight', 0.04);
p.parse(varargin{:});
opt = p.Results;

if isempty(opt.TemplateFile)
    opt.TemplateFile = fullfile(fileparts(mfilename('fullpath')), 'templates.mat');
end
S = loadTemplates(opt.TemplateFile);

n = numel(charImages);
chars  = repmat({'?'}, 1, n);
scores = zeros(1, n);

for k = 1:n
    f   = charFeature(charImages{k});
    set = positionSet(S, opt.Format, k);      % 只在该位允许的字符里找最优
    [lb, sc] = bestMatch(f, set, opt.ScoreMode, opt.HoleWeight);

    % 极细长的墨迹段基本只可能是数字 1 —— 车牌上唯一"又窄又高"的字符就是 1,
    % 字母 I 已被排除。normalizeChar 的 'fill' 模式会把这种窄段横向拉宽成实心
    % 方块(实测用户照片里 AR=0.132 的 1 被拉宽后更像 8/4/0, 对模板 "1" 的得分
    % 只有 0.31, 而 8 拿到 0.51), 所以这里按宽度分两档处理:
    %   AR < 0.22  : 直接判定为 1。逐图实测(见 README"已知限制")所有数据集里
    %                第 2 位起、AR < 0.22 的段 100% 都是 1。
    %   0.22~0.30  : 只给 "1" 一个小加分, 不强制 —— 新能源牌被纵向压缩时
    %                D/Q/5/9/F/G 的 AR 也会掉到 0.25 左右, 强制会误伤它们。
    if k >= 2 && numel(opt.InkAR) >= k && opt.InkAR(k) > 0 && opt.InkAR(k) < 0.30
        sub = restrict(set, '1');
        if ~isempty(sub.label)
            [lb1, sc1] = bestMatch(f, sub, opt.ScoreMode, opt.HoleWeight);
            if opt.InkAR(k) < 0.22
                lb = lb1;  sc = sc1;
            elseif sc1 + 0.06 > sc
                lb = lb1;  sc = sc1;
            end
        end
    end

    % 首位汉字置信度过低 -> 用 * 占位, 提示这一位不可信
    if k == 1 && sc < 0.45
        lb = '*';
    end

    chars{k}  = lb;
    scores(k) = sc;
end

plateText = [chars{:}];
end

% ======================== 局部函数 ========================

function set = positionSet(S, F, k)
%POSITIONSET 取第 k 位允许的模板集合: 先按位选模板组, 再用制式字符串收窄
if k == 1
    set = S.chinese;
elseif k == 2
    set = S.letters;
elseif isfield(S, 'alnumSpecial')
    set = S.alnumSpecial;      % 数字/字母 + 末位制式后缀(警), 由 Format 收窄
else
    set = S.alnum;
end
if ~isempty(F) && isstruct(F) && isfield(F, 'sets') && ...
        k <= numel(F.sets) && ~isempty(F.sets{k})
    narrow = restrict(set, F.sets{k});
    if ~isempty(narrow.label)
        set = narrow;
    end
end
end

function set = restrict(set, allowed)
%RESTRICT 只保留标签出现在 allowed 里的模板; 无交集时原样返回
if isempty(allowed) || isempty(set.label), return; end
keep = false(1, numel(set.label));
for i = 1:numel(set.label)
    keep(i) = any(set.label{i} == allowed);
end
if any(keep)
    set.label   = set.label(keep);
    set.feature = set.feature(keep);
end
end

function S = loadTemplates(f)
if exist(f, 'file') ~= 2
    fprintf('    模板文件不存在, 正在生成模板: %s\n', f);
    buildTemplates(f);
end
S = load(f);
if ~isfield(S, 'alnum')
    S.alnum = combineSets(S.letters, S.digits);
end
end

function s = combineSets(a, b)
s.label   = [a.label, b.label];
s.feature = [a.feature, b.feature];
end

function [label, score] = bestMatch(f, set, mode, holeWeight)
%BESTMATCH 在候选模板里找最像的一个, 返回 (标签, 得分)
%
%   mode 决定"像"怎么算。f 与 g 都是 charFeature 的输出 —— 值域 [0,1] 的**连续**
%   特征(二值字符图经双线性降采样), 不是 0/1:
%     'iou'   0.65*二值IoU + 0.35*相关系数   —— 历史口径(默认)
%     'soft'  0.65*软IoU  + 0.35*相关系数
%     'dice'  0.65*软Dice + 0.35*相关系数
%     'mix'   0.50*软IoU  + 0.50*相关系数
%     'cc'    只用相关系数
%
%   孔洞罚分(默认 0.04 生效): sc = sc - HoleWeight * (查询与模板的孔洞数是否不同)。
%   孔洞数是个**拓扑**量, 不看"笔画多粗", 所以插值把笔画喂胖也不影响 ——
%   实测 105 px 车牌上 "0" 的孔洞只剩 1~2 px, 与 "8" 的得分**精确打平**
%   (0.584 vs 0.584, 0.609 vs 0.609), 于是由模板顺序决定胜负, 而 "8" 排在前。
%   罚 0.04 就足以打破这种平局, 又小到不会推翻真正的形状差异。
%   另一种思路(软 IoU/Dice, 见上面 'soft'/'dice')只在低分辨率口径的对比里
%   略赢、但在 bench 上掉一档, 所以没有采用; 'soft' 等口径保留下来是为了可复现
%   那组对照实验(测试脚本/lr_score_ab.m)。
%
%   二值化阈值保持 0.5(模板也用它)。曾经为了找回被降采样"磨淡"的 1 px 细横画
%   把它降到 0.35~0.45, 但实测(见 README"已知限制")在修好去外框之后:
%     阈值 0.50 -> bench 0.850/0.979, hard 0.667/0.833, real 字符 0.045
%     阈值 0.35 -> bench 0.850/0.979, hard 0.500/0.805, real 字符 0.000
%   降阈值只会把笔画喂胖(3 变 8、0 变 9), 所以维持 0.5。
if nargin < 3 || isempty(mode), mode = 'iou'; end
if nargin < 4 || isempty(holeWeight), holeWeight = 0; end
hq = holesOf(f);
label = '?'; score = -inf;
for i = 1:numel(set.label)
    g  = set.feature{i};
    sc = pairScore(f, g, mode);
    if holeWeight > 0
        sc = sc - holeWeight * double(hq ~= holesOf(g));
    end
    if sc > score
        score = sc;
        label = set.label{i};
    end
end
end

function n = holesOf(f)
%HOLESOF 特征(24x12)里墨迹的孔洞个数 —— 一个**拓扑**量, 对分辨率不敏感
%   它能把 0/6/9/D/Q/A(1 个孔) 和 8/B(2 个孔) 分开, 而这正是低分辨率下最典型的
%   错法: 实测 105 px 车牌上 "0" 的孔洞只剩 1~2 px, 二值化后与 "8" 的得分打平
%   (0.584 vs 0.584), 只能靠模板顺序定胜负。孔洞数不用"多粗的笔画"来判, 所以
%   笔画被插值喂胖也不影响。
bw = reshape(f, 24, 12) > 0.5;
bw = imfill(bw, 'holes') & ~bw;
bw = bwareaopen(bw, 2);          % 2 px 以下当噪声, 不然字形边缘的毛刺会算成孔
n = bwconncomp(bw).NumObjects;
end

function sc = pairScore(f, g, mode)
%PAIRSCORE 两个连续特征的相似度(口径由 mode 决定)
%   相关系数对两幅图都做去均值, 所以对"底色整体偏亮/偏暗"是免疫的;
%   软 IoU 与软 Dice 都只在墨迹上累加(背景两幅都是 0, 对分子分母都没有贡献)。
a = f - mean(f);
b = g - mean(g);
d = norm(a) * norm(b);
if d < eps, cc = 0; else, cc = (a * b') / d; end
switch lower(mode)
    case 'cc'
        sc = cc;
    case 'soft'
        sc = 0.65 * softOverlap(f, g) + 0.35 * cc;
    case 'dice'
        sc = 0.65 * softDice(f, g) + 0.35 * cc;
    case 'mix'
        sc = 0.50 * softOverlap(f, g) + 0.50 * cc;
    otherwise                                  % 'iou': 历史口径
        fb = f > 0.5;  gb = g > 0.5;
        uni = nnz(fb | gb);
        if uni == 0, iou = 0; else, iou = nnz(fb & gb) / uni; end
        sc = 0.65 * iou + 0.35 * cc;
end
end

function v = softOverlap(f, g)
%SOFTOVERLAP 软交并比 sum(min)/sum(max)
u = sum(max(f, g));
if u < eps, v = 0; else, v = sum(min(f, g)) / u; end
end

function v = softDice(f, g)
%SOFTDICE 软 Dice 2*sum(f.*g)/(sum(f)+sum(g))
d = sum(f) + sum(g);
if d < eps, v = 0; else, v = 2 * sum(f .* g) / d; end
end

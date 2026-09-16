function [plateText, chars, scores] = recognizeChars(charImages, templateFile)
%RECOGNIZECHARS 字符识别(模板匹配)
%
%   [plateText, chars, scores] = RECOGNIZECHARS(charImages)
%
%   位置约定(按中国大陆车牌):
%       第 1 位 : 中文省份简称(31 个)   -> 用 chinese 模板
%       第 2 位 : 发牌机关代号(大写字母) -> 用 letters 模板
%       第 3 位起: 字母或数字(去掉 I、O) -> 用 alnum 模板
%
%   匹配得分 = 0.65 * IoU(二值交并比) + 0.35 * 相关系数
%   模板文件不存在时自动调用 buildTemplates 生成 templates.mat

if nargin < 2 || isempty(templateFile)
    templateFile = fullfile(fileparts(mfilename('fullpath')), 'templates.mat');
end
S = loadTemplates(templateFile);

n = numel(charImages);
chars  = repmat({'?'}, 1, n);
scores = zeros(1, n);

for k = 1:n
    f = charFeature(charImages{k});
    switch charType(k)
        case 'chinese'
            [lb, sc] = bestMatch(f, S.chinese);
            if sc < 0.45
                lb = '*';        % 置信度过低, 用 * 占位
            end
        case 'letter'
            [lb, sc] = bestMatch(f, S.letters);
        otherwise
            [lb, sc] = bestMatch(f, S.alnum);
    end
    chars{k}  = lb;
    scores(k) = sc;
end

plateText = [chars{:}];
end

% ======================== 局部函数 ========================

function t = charType(k)
if k == 1
    t = 'chinese';
elseif k == 2
    t = 'letter';
else
    t = 'alnum';
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

function [label, score] = bestMatch(f, set)
label = '?'; score = -inf;
fb = f > 0.5;
for i = 1:numel(set.label)
    g  = set.feature{i};
    gb = g > 0.5;
    uni = nnz(fb | gb);
    if uni == 0, continue; end
    iou = nnz(fb & gb) / uni;

    a = f - mean(f);
    b = g - mean(g);
    d = norm(a) * norm(b);
    if d < eps
        cc = 0;
    else
        cc = (a * b') / d;
    end

    sc = 0.65 * iou + 0.35 * cc;
    if sc > score
        score = sc;
        label = set.label{i};
    end
end
end
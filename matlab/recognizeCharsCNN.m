function [plateText, chars, scores, probs] = recognizeCharsCNN(cells, varargin)
%RECOGNIZECHARSCNN 用训练好的 CNN 识别字符(输入是"固定槽位"切出的灰度格)
%
%   [text, chars, scores, probs] = RECOGNIZECHARSCNN(cells)
%   [...] = RECOGNIZECHARSCNN(cells, 'Model', S, 'Format', F)
%
%   cells : 1xN cell, 每个是 slotChars 的输出 —— double 灰度图, 墨迹亮, 值域 [0,1]
%   可选参数
%     'Model'   已 load 的模型结构体(含 net / classNames)。批量识别同一块车牌时
%               传进来, 免得每调一次就 load 一次 charNet.mat
%     'ModelFile' 模型文件路径(默认同目录 charNet.mat)
%     'Format'  plateFormat(n) 的返回值, 按位限定候选字符集
%     'LowConf' 首位置信度低于它就输出 '*' 占位(默认 0.45, 与模板匹配口径一致)
%
%   为什么要灰度而不是二值图:
%     模板匹配用的是二值图, 因为"形状重合度"必须在二值域算。CNN 没有这个约束,
%     灰度的笔画粗细、边缘锐利度、局部对比度都是有效信息 —— 而真实照片掉准确率
%     恰恰掉在这些地方。喂二值图等于先把一部分判别信息扔掉。
%
%   依赖: Deep Learning Toolbox

p = inputParser;
p.addRequired('cells', @iscell);
p.addParameter('Model', []);
p.addParameter('ModelFile', '');
p.addParameter('Format', []);
p.addParameter('LowConf', 0.45);
p.parse(cells, varargin{:});
opt = p.Results;

S = opt.Model;
if isempty(S)
    f = opt.ModelFile;
    if isempty(f)
        f = fullfile(fileparts(mfilename('fullpath')), 'charNet.mat');
    end
    if exist(f, 'file') ~= 2
        error('recogCNN:noModel', ...
            '找不到模型文件 %s, 请先运行 train_char_cnn.m 训练', f);
    end
    S = load(f, 'net', 'classNames');
end
net = S.net; classNames = S.classNames;

n = numel(cells);
chars  = repmat({'?'}, 1, n);
scores = zeros(1, n);
probs  = [];

if n == 0, plateText = ''; return; end

inSize = net.Layers(1).InputSize;
X = zeros(inSize(1), inSize(2), 1, n, 'single');
for k = 1:n
    c = double(cells{k});
    if size(c, 1) ~= inSize(1) || size(c, 2) ~= inSize(2)
        c = imresize(c, inSize(1:2), 'bilinear');
    end
    c(c < 0) = 0; c(c > 1) = 1;
    X(:, :, 1, k) = single(c);
end
probs = predict(net, X);                 % n x C, 一次前向算完整块牌

for k = 1:n
    pr = probs(k, :);
    pr = maskByFormat(pr, classNames, opt.Format, k);
    [sc, i] = max(pr);
    lbl = classNames{i};
    if k == 1 && sc < opt.LowConf
        lbl = '*';                        % 首位太不确定就别硬报, 与模板口径一致
    end
    chars{k}  = lbl;
    scores(k) = sc;
end
plateText = [chars{:}];
end

% ======================== 局部函数 ========================

function pr = maskByFormat(pr, classNames, F, k)
%MASKBYFORMAT 把第 k 位不允许出现的字符概率清零, 再归一化
%   例如第 1 位只能是省级简称, 第 2 位只能是大写字母。不清零的话 CNN 会给出
%   "第 1 位像 Z" 这种结果, 而它其实是把汉字识别成了相近的字母。
if isempty(F) || ~isstruct(F) || ~isfield(F, 'sets') || k > numel(F.sets)
    return;
end
allowed = F.sets{k};
if isempty(allowed), return; end
keep = false(1, numel(classNames));
for i = 1:numel(classNames)
    keep(i) = numel(classNames{i}) == 1 && any(classNames{i} == allowed);
end
if ~any(keep), return; end               % 没有交集就别乱清零
pr(~keep) = 0;
s = sum(pr);
if s > 0, pr = pr / s; end
end
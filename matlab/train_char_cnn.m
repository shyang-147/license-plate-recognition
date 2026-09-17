function [net, classNames, info] = train_char_cnn(dataRoot, outFile, varargin)
%TRAIN_CHAR_CNN 训练车牌字符分类 CNN
%
%   [net, classNames, info] = TRAIN_CHAR_CNN()                 % 默认 ../matlab/charData
%   TRAIN_CHAR_CNN(dataRoot, outFile, 'MaxEpochs', 14, 'MaxPerClass', 400)
%
%   样本目录(由 tools/make_char_data.m 生成; 也兼容老的"每类一个子目录"结构):
%       dataRoot/real/<字符>/*.png     真实域(CCPD 真值裁片 + 固定槽位切字符)
%       dataRoot/syn/<字符>/*.png      合成域(系统字体排版 + 退化增广)
%   两个域的同名类别会被合并成同一个类别 —— imageDatastore 按**直接父目录名**打标签。
%
%   可选参数
%     'InputSize'   [32 16]  网络输入的高宽(与 slotChars 的输出一致)
%     'MaxEpochs'   14
%     'MiniBatchSize' 128
%     'MaxPerClass' 400      每个类别最多取多少张(用来压掉"皖"这种超多的类)
%     'ValFraction' 0.15
%     'Plots'       'none'   'training-progress' 可以直接看曲线(会慢一点)
%     'Seed'        1
%
%   输出: charNet.mat 内含 net / classNames / info, 供 recognizeCharsCNN.m 使用
%   依赖: Deep Learning Toolbox
%
%   为什么这么小: 目标机器不一定有 GPU。实测 CPU(16 核) 上 32x16 输入约
%   420 样本/秒, 2 万样本 14 轮 ≈ 11 分钟。网络再深收益很小 —— 字符识别是
%   70 类的近邻分类, 难点在"域"不在"容量"(见 评审与改进/第十轮_*.md)。

p = inputParser;
p.addRequired('dataRoot');
p.addOptional('outFile', '', @ischar);
p.addParameter('InputSize', [32 16], @(x) isnumeric(x) && numel(x) == 2);
p.addParameter('MaxEpochs', 14, @isscalar);
p.addParameter('MiniBatchSize', 128, @isscalar);
p.addParameter('MaxPerClass', 300, @isscalar);
p.addParameter('ValFraction', 0.15, @isscalar);
p.addParameter('Plots', 'none', @ischar);
p.addParameter('Seed', 1, @isscalar);
p.addParameter('Verbose', true, @islogical);
p.parse(dataRoot, outFile, varargin{:});
opt = p.Results;

if isempty(opt.outFile)
    opt.outFile = fullfile(fileparts(mfilename('fullpath')), 'charNet.mat');
end
if exist(opt.dataRoot, 'dir') ~= 7
    error('train_char_cnn:noData', ['找不到样本目录: %s\n' ...
        '先跑 tools/make_char_data.m 生成 charData/'], opt.dataRoot);
end

rng(opt.Seed);

% ---------------- 1. 读样本 + 类别平衡 ----------------
imds = imageDatastore(opt.dataRoot, 'IncludeSubfolders', true, 'LabelSource', 'foldernames');
if isempty(imds.Files)
    error('train_char_cnn:noFiles', '%s 下没有找到任何图片', opt.dataRoot);
end
classNames = categories(imds.Labels);
nAll = numel(imds.Files);

keep = [];
counts = zeros(1, numel(classNames));
for i = 1:numel(classNames)
    idx = find(imds.Labels == classNames{i});
    counts(i) = numel(idx);
    if numel(idx) > opt.MaxPerClass
        idx = idx(randperm(numel(idx), opt.MaxPerClass));   % 压掉超多的类
    end
    keep = [keep; idx]; %#ok<AGROW>
end
keep = keep(randperm(numel(keep)));
imds = subset(imds, keep);
nUse = numel(imds.Files);

fprintf('样本目录 %s\n', opt.dataRoot);
fprintf('  读到 %d 张 / %d 类; 平衡后 %d 张 (每类上限 %d)\n', ...
        nAll, numel(classNames), nUse, opt.MaxPerClass);
fprintf('  最少 %d 张的类: %s\n', min(counts), strjoin(classNames(counts == min(counts))', ', '));
if nnz(counts > 0) < numel(classNames)
    fprintf('  ⚠ 有 %d 类一张样本都没有\n', nnz(counts == 0));
end

imdsTrain = subset(imds, 1:round(nUse * (1 - opt.ValFraction)));
imdsVal   = subset(imds, round(nUse * (1 - opt.ValFraction)) + 1:nUse);
fprintf('  训练 %d / 验证 %d\n', numel(imdsTrain.Files), numel(imdsVal.Files));

% ---------------- 2. 数据管道 ----------------
% 样本 PNG 都是单通道灰度(imwrite 写二维数组), outputSize 带第 3 维 = 1,
inSize = [opt.InputSize 1];
% 合成域已经做过一轮退化增广, 这里补几何扰动。
% 横向位移刻意给得大(±3 px, 格子宽才 16 px): 不同来源的车牌排版有 1~3% 牌宽的
% 差异(自产合成集和真实号牌的字符格中心对不齐), 位移增广就是用来吸收这个差异的。
aug = imageDataAugmenter('RandRotation', [-6 6], ...
        'RandXTranslation', [-3 3], 'RandYTranslation', [-2 2], ...
        'RandXScale', [0.90 1.10], 'RandYScale', [0.90 1.10]);
% ⚠ augmentedImageDatastore **只做几何变换和类型转换, 不做数值归一化** ——
% uint8 的 PNG 读出来是 0~255 的 single, 不是 0~1。不加这一步, 训练吃的是
% 0~255、而 slotChars 推理给的是 0~1, 结果是模型在真图上输出恒定值(全判同一个字)。
% 这个坑在第十轮踩过一次, 见 评审与改进/第十轮_*.md 的排查记录。
dsTrain = transform(augmentedImageDatastore(inSize, imdsTrain, 'DataAugmentation', aug), ...
                    @rescaleInputTable);
dsVal   = transform(augmentedImageDatastore(inSize, imdsVal), @rescaleInputTable);

% ---------------- 3. 网络 ----------------
numClasses = numel(classNames);
layers = [
    imageInputLayer(inSize, 'Name', 'input', 'Normalization', 'none')
    convolution2dLayer(3, 32, 'Padding', 'same', 'Name', 'conv1')
    batchNormalizationLayer('Name', 'bn1')
    reluLayer('Name', 'relu1')
    convolution2dLayer(3, 32, 'Padding', 'same', 'Name', 'conv2')
    batchNormalizationLayer('Name', 'bn2')
    reluLayer('Name', 'relu2')
    maxPooling2dLayer(2, 'Stride', 2, 'Name', 'pool1')
    convolution2dLayer(3, 64, 'Padding', 'same', 'Name', 'conv3')
    batchNormalizationLayer('Name', 'bn3')
    reluLayer('Name', 'relu3')
    convolution2dLayer(3, 64, 'Padding', 'same', 'Name', 'conv4')
    batchNormalizationLayer('Name', 'bn4')
    reluLayer('Name', 'relu4')
    maxPooling2dLayer(2, 'Stride', 2, 'Name', 'pool2')
    dropoutLayer(0.3, 'Name', 'drop')
    fullyConnectedLayer(numClasses, 'Name', 'fc')
    softmaxLayer('Name', 'softmax')
    classificationLayer('Name', 'classoutput')];

opts = trainingOptions('adam', ...
    'InitialLearnRate', 1e-3, ...
    'MaxEpochs', opt.MaxEpochs, ...
    'MiniBatchSize', opt.MiniBatchSize, ...
    'Shuffle', 'every-epoch', ...
    'ValidationData', dsVal, ...
    'ValidationFrequency', 60, ...
    'LearnRateSchedule', 'piecewise', ...
    'LearnRateDropFactor', 0.5, ...
    'LearnRateDropPeriod', 5, ...
    'ExecutionEnvironment', 'cpu', ...
    'Verbose', opt.Verbose, ...
    'VerboseFrequency', 60, ...
    'Plots', opt.Plots);

% ---------------- 4. 训练与评估 ----------------
t = tic;
net = trainNetwork(dsTrain, layers, opts);
elapsed = toc(t);

Y = classify(net, dsVal);
acc = mean(Y == imdsVal.Labels);
fprintf('训练完成: %.1f 分钟, 验证集字符准确率 %.2f%% (%d/%d)\n', ...
        elapsed / 60, 100 * acc, nnz(Y == imdsVal.Labels), numel(Y));

% 逐类召回: 字符表里有些类样本极少(真实域只覆盖皖), 单看总准确率会骗人
rec = zeros(1, numClasses);
for i = 1:numClasses
    m = imdsVal.Labels == classNames{i};
    if any(m), rec(i) = mean(Y(m) == imdsVal.Labels(m)); end
end
[~, ord] = sort(rec);
fprintf('召回最差的 8 类: ');
for i = 1:min(8, numel(ord))
    fprintf('%s=%.2f ', classNames{ord(i)}, rec(ord(i)));
end
fprintf('\n');
if any(strcmp(classNames, '皖'))
    k = find(imdsVal.Labels == '皖');
    fprintf('  参考: 验证集里"皖"有 %d 张, 召回 %.3f\n', numel(k), mean(Y(k) == '皖'));
end

info = struct('inputSize', opt.InputSize, 'numClasses', numClasses, ...
              'nAll', nAll, 'nUse', nUse, 'perClass', counts, ...
              'valAcc', acc, 'minutes', elapsed / 60, ...
              'created', datestr(now), 'source', opt.dataRoot);

save(opt.outFile, 'net', 'classNames', 'info');
fprintf('模型已保存: %s\n', opt.outFile);
end

function t = rescaleInputTable(t)
%RESCALEINPUTTABLE 把 augmentedImageDatastore 吐出的 0~255 图拉到 0~1
t.input = cellfun(@(x) x / 255, t.input, 'UniformOutput', false);
end

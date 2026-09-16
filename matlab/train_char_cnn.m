function [net, classNames] = train_char_cnn(dataRoot, outFile)
%TRAIN_CHAR_CNN 训练车牌字符分类 CNN (可选模块)
%
%   net = TRAIN_CHAR_CNN(dataRoot)
%   dataRoot 目录结构(每个字符一个子文件夹, 文件夹名即为类别标签):
%       dataRoot/0/*.png ... dataRoot/9/*.png
%       dataRoot/A/*.png ... dataRoot/Z/*.png
%       dataRoot/京/*.png ... dataRoot/新/*.png     (汉字)
%
%   样本来源建议:
%       1) CCPD / CBLPRD 车牌数据集, 用本工程的 locatePlate + segmentChars
%          批量切出字符, 再人工归到对应文件夹(半自动标注)
%       2) 每个类别建议 >= 300 张, 汉字类别越多越好
%
%   输出: charNet.mat (含 net 与 classNames), 供 recognizeCharsCNN.m 使用
%   依赖: Deep Learning Toolbox

if nargin < 1 || isempty(dataRoot)
    dataRoot = fullfile(fileparts(mfilename('fullpath')), 'charData');
end
if nargin < 2 || isempty(outFile)
    outFile = fullfile(fileparts(mfilename('fullpath')), 'charNet.mat');
end
if exist(dataRoot, 'dir') ~= 7
    error('train_cnn:noData', '找不到样本目录: %s', dataRoot);
end

% ---------------- 数据 ----------------
imds = imageDatastore(dataRoot, 'IncludeSubfolders', true, 'LabelSource', 'foldernames');
[imdsTrain, imdsRest] = splitEachLabel(imds, 0.80, 'randomized');
[imdsVal, imdsTest]   = splitEachLabel(imdsRest, 0.50, 'randomized');

inputSize  = [32 16];
classNames = categories(imds.Labels);
numClasses = numel(classNames);
fprintf('类别数 %d, 训练 %d / 验证 %d / 测试 %d\n', numClasses, ...
        numel(imdsTrain.Files), numel(imdsVal.Files), numel(imdsTest.Files));

aug = imageDataAugmenter('RandRotation', [-8 8], ...
        'RandXTranslation', [-2 2], 'RandYTranslation', [-2 2], ...
        'RandXScale', [0.9 1.1], 'RandYScale', [0.9 1.1]);

dsTrain = augmentedImageDatastore(inputSize, imdsTrain, 'DataAugmentation', aug);
dsVal   = augmentedImageDatastore(inputSize, imdsVal);
dsTest  = augmentedImageDatastore(inputSize, imdsTest);

% ---------------- 网络 ----------------
layers = [
    imageInputLayer([inputSize 1], 'Name', 'input', 'Normalization', 'none')
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
    maxPooling2dLayer(2, 'Stride', 2, 'Name', 'pool2')
    dropoutLayer(0.3, 'Name', 'drop')
    fullyConnectedLayer(numClasses, 'Name', 'fc')
    softmaxLayer('Name', 'softmax')
    classificationLayer('Name', 'classoutput')];

opts = trainingOptions('adam', ...
    'InitialLearnRate', 1e-3, ...
    'MaxEpochs', 40, ...
    'MiniBatchSize', 128, ...
    'Shuffle', 'every-epoch', ...
    'ValidationData', dsVal, ...
    'ValidationFrequency', 50, ...
    'LearnRateSchedule', 'piecewise', ...
    'LearnRateDropFactor', 0.5, ...
    'LearnRateDropPeriod', 8, ...
    'Verbose', true, ...
    'Plots', 'training-progress');

% ---------------- 训练与评估 ----------------
net = trainNetwork(dsTrain, layers, opts);

Y   = classify(net, dsTest);
acc = mean(Y == imdsTest.Labels);
fprintf('测试集字符准确率: %.2f%%\n', acc * 100);

save(outFile, 'net', 'classNames');
fprintf('模型已保存: %s\n', outFile);
end
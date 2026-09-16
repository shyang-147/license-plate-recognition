function [plateText, chars, scores] = recognizeCharsCNN(charImages, netFile)
%RECOGNIZECHARSCNN 用训练好的 CNN 识别字符(工程推荐方案)
%
%   [plateText, chars, scores] = RECOGNIZECHARSCNN(charImages)
%   netFile: train_char_cnn.m 生成的 charNet.mat (含 net 和 classNames)
%
%   用法: 把 lpr_main.m 中的
%           [plateText, chars, scores] = recognizeChars(charImages);
%         改成
%           [plateText, chars, scores] = recognizeCharsCNN(charImages);
%   或在 lpr_main.m 中增加一个 'Engine' 参数二选一。
%
%   依赖: Deep Learning Toolbox

if nargin < 2 || isempty(netFile)
    netFile = fullfile(fileparts(mfilename('fullpath')), 'charNet.mat');
end
if exist(netFile, 'file') ~= 2
    error('recogCNN:noModel', '找不到模型文件 %s, 请先运行 train_char_cnn.m', netFile);
end

S = load(netFile, 'net', 'classNames');
net = S.net;
inSize = net.Layers(1).InputSize;      % [h w c]

n = numel(charImages);
chars  = repmat({'?'}, 1, n);
scores = zeros(1, n);

for k = 1:n
    img = double(charImages{k});
    if size(img, 1) ~= inSize(1) || size(img, 2) ~= inSize(2)
        img = imresize(img, inSize(1:2), 'bilinear');
    end
    img = reshape(single(img), [inSize(1), inSize(2), 1, 1]);

    [lb, sc] = classify(net, img);
    chars{k}  = char(lb);
    scores(k) = max(sc);
end

plateText = [chars{:}];
end
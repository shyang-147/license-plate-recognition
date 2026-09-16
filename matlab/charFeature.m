function f = charFeature(img, outH, outW)
%CHARFEATURE 把归一化字符图降采样成定长特征行向量(抗轻微错位)
%
%   f = CHARFEATURE(img)        -> 1 x 288  (24 x 12)
%   f = CHARFEATURE(img, 20, 10)

if nargin < 2 || isempty(outH), outH = 24; end
if nargin < 3 || isempty(outW), outW = 12; end

f = imresize(double(img), [outH outW], 'bilinear');
f = f(:)';
end
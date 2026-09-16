function img = normalizeChar(bwChar, outH, outW, margin, mode)
%NORMALIZECHAR 单个字符归一化: 裁到最小外接矩形 -> 缩放 -> 居中放到固定画布
%
%   img = NORMALIZECHAR(bwChar)               % 32x16, 拉伸填满
%   img = NORMALIZECHAR(bwChar, 32, 16, 2, 'fill')
%
%   mode = 'fill' : 裁掉空白后直接拉伸到 (outH-2m) x (outW-2m)。模板用系统字体、
%                   实测字符来自车牌字体, 两者长宽比往往不同; 拉伸填满可以把
%                   长宽比差异"归一化"掉, 匹配明显更稳(推荐)。
%   mode = 'keep' : 保持长宽比等比缩放并居中。
%
%   模板生成与实测字符必须用同一套归一化参数, 否则特征不可比。

if nargin < 2 || isempty(outH),   outH = 32;   end
if nargin < 3 || isempty(outW),   outW = 16;   end
if nargin < 4 || isempty(margin), margin = 2;  end
if nargin < 5 || isempty(mode),   mode = 'fill'; end

img = false(outH, outW);
if isempty(bwChar) || ~any(bwChar(:)), return; end

[r, c] = find(bwChar);
bwChar = bwChar(min(r):max(r), min(c):max(c));
[h, w] = size(bwChar);

availH = outH - 2 * margin;
availW = outW - 2 * margin;

if strcmpi(mode, 'keep')
    s  = min(availH / h, availW / w);
    nh = max(1, round(h * s));
    nw = max(1, round(w * s));
else
    nh = availH;
    nw = availW;
end

small = imresize(double(bwChar), [nh nw], 'bilinear') > 0.5;

r0 = floor((outH - nh) / 2) + 1;
c0 = floor((outW - nw) / 2) + 1;
img(r0:r0 + nh - 1, c0:c0 + nw - 1) = small;
end
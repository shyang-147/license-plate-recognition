function [gray, color] = correctPlate(gray, color, mask)
%CORRECTPLATE 车牌倾斜校正 + 尺寸归一化
%
%   [gray, color] = CORRECTPLATE(gray, color, mask)
%   mask: 车牌掩膜(用于估计倾斜角), 可为空 []
%
%   倾斜角由车牌掩膜的上下边界拟合直线得到; 校正后统一缩放到高度 64 像素,
%   宽度按原长宽比缩放, 使后续垂直投影分割的参数可以写死。

targetH = 64;

if nargin > 2 && ~isempty(mask) && any(mask(:))
    ang = estimateSkew(mask);
    if abs(ang) > 0.8 && abs(ang) < 25          % 小角度不折腾, 大角度视为误检
        gray  = imrotate(gray,  ang, 'bilinear', 'loose');
        color = imrotate(color, ang, 'bilinear', 'loose');
        mask  = imrotate(mask,  ang, 'nearest',  'loose');
        bb = tightBox(mask);
        if ~isempty(bb)
            bb = clampBox(bb, size(gray));
            gray  = imcrop(gray,  bb);
            color = imcrop(color, bb);
        end
    end
end

% ---- 统一高度 ----
if size(gray, 1) >= 8
    s = targetH / size(gray, 1);
    if abs(s - 1) > 0.01
        gray  = imresize(gray,  s, 'bilinear');
        color = imresize(color, s, 'bilinear');
    end
end
end

% ======================== 局部函数 ========================

function ang = estimateSkew(mask)
%ESTIMATESKEW 由车牌掩膜上下边界拟合直线, 返回需要旋转的角度(度)
mask = imclose(mask, strel('disk', 2));
mask = imfill(mask, 'holes');
mask = bwareaopen(mask, 20);
if ~any(mask(:)), ang = 0; return; end

[H, W] = size(mask); %#ok<ASGLU>
top = nan(1, W); bot = nan(1, W);
for c = 1:W
    r = find(mask(:, c), 1, 'first');
    if ~isempty(r), top(c) = r; end
    r = find(mask(:, c), 1, 'last');
    if ~isempty(r), bot(c) = r; end
end

v = [fitLineAngle(top), fitLineAngle(bot)];
v = v(~isnan(v));
if isempty(v)
    ang = 0;
else
    ang = mean(v);
end
end

function a = fitLineAngle(y)
%FITLINEANGLE 最小二乘拟合一条近似水平线, 返回倾角(度); 含一次离群点剔除
x = 1:numel(y);
ok = ~isnan(y);
x = x(ok); y = y(ok);
if numel(x) < 5, a = NaN; return; end

p = polyfit(x(:), y(:), 1);
res = abs(y(:) - polyval(p, x(:)));
th = 2.5 * max(1, median(res));
keep = res <= th;
if nnz(keep) >= 5
    p = polyfit(x(keep), y(keep), 1);
end
a = atand(p(1));      % imrotate 正角度为逆时针, 正好抵消图像坐标 y 向下的斜率
end

function bb = tightBox(mask)
%TIGHTBOX 掩膜的最小外接矩形 [x y w h]
colSum = sum(mask, 1);
rowSum = sum(mask, 2);
if ~any(colSum) || ~any(rowSum), bb = []; return; end
cs = find(colSum > 0.30 * max(colSum));
rs = find(rowSum > 0.30 * max(rowSum));
bb = [cs(1), rs(1), cs(end) - cs(1) + 1, rs(end) - rs(1) + 1];
end

function bb = clampBox(bb, sz)
bb(1) = max(1, min(bb(1), sz(2)));
bb(2) = max(1, min(bb(2), sz(1)));
bb(3) = max(1, min(bb(3), sz(2) - bb(1) + 1));
bb(4) = max(1, min(bb(4), sz(1) - bb(2) + 1));
end
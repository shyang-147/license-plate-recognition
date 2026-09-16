function [gray, color, mask] = cropPlate(I, box, plateMask)
%CROPPLATE 按定位框裁剪车牌, 并同步裁剪车牌掩膜
%
%   [gray, color, mask] = CROPPLATE(I, box, plateMask)
%   box : [x y w h] (imcrop 坐标系, 从 1 开始)

pad = 2;                                        % 向外扩 2 像素, 保住车牌边框
[H, W, ~] = size(I);
x1 = max(1, round(box(1)) - pad);
y1 = max(1, round(box(2)) - pad);
x2 = min(W, round(box(1) + box(3) - 1) + pad);
y2 = min(H, round(box(2) + box(4) - 1) + pad);

color = I(y1:y2, x1:x2, :);
gray  = rgb2gray(color);

if nargin > 2 && ~isempty(plateMask)
    mask = plateMask(y1:y2, x1:x2);
else
    mask = true(size(gray));
end
end
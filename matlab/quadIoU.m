function [iou, interA] = quadIoU(rect, quad)
%QUADIOU 轴对齐检测框 与 真值四边形(凸) 的交并比 —— Sutherland-Hodgman 裁剪求交
%
%   iou = QUADIOU(rect, quad)
%     rect : [x y w h]  检测框(轴对齐), 与 boxIoU 同一套坐标约定(右下角算作 x+w-1)
%     quad : 4x2 或 1x8 真值四角 —— 顺序随便, 内部会按质心极角重排成环
%
%   为什么需要它: CCPD 的车牌标注是**旋转**四角, 它的轴对齐外接框会把背景一起框进来。
%   实测这批 320 张里, 一个"完美轴对齐检测器"(直接输出外接框)对外接框的 IoU
%   中位只有 0.776; ccpd_tilt 组 0.486、ccpd_rotate 组 0.530 —— 也就是说在
%   这两组上, IoU>=0.5 这一关连完美检测器自己都过不了(全批 14% 的图如此)。
%   所以真值侧必须用多边形, 而不是外接框。
%
%   凸多边形对半平面裁剪用参数插值 t = sa/(sa-sb) 是**精确**的, 没有栅格化误差。

if isvector(quad)                 % 1x8 / 8x1 都摊成 4x2
    quad = reshape(quad, 2, 4)';
end
% ⚠ 这里必须先判形状再 reshape: 4x2 本来就是对的, 再 reshape(2,4)' 会把
%   四个顶点**交错重排**成一个自交四边形 —— 实测皖S69016 那张 IoU 从 0.834 掉到 0.401。
quad = orderQuad(quad);
rect = rect(:)';

x1 = rect(1);              y1 = rect(2);
x2 = rect(1) + rect(3) - 1; y2 = rect(2) + rect(4) - 1;   % 与 boxIoU 一致: 端点算在内

p = quad;
planes = {@(q) q(:,1) - x1, @(q) x2 - q(:,1), @(q) q(:,2) - y1, @(q) y2 - q(:,2)};
for e = 1:4
    p = clipHalf(p, planes{e});
    if size(p, 1) < 3, p = zeros(0, 2); break; end
end

interA = polyArea(p);
aA = max(0, rect(3)) * max(0, rect(4));
aB = polyArea(quad);
iou = interA / max(eps, aA + aB - interA);
end

% ======================== 局部函数 ========================

function p = orderQuad(p)
%ORDERQUAD 按质心极角把 4 个顶点排成环 —— CCPD 少数图的顶点序是乱的, 不排会自交
c   = mean(p, 1);
ang = atan2(p(:,2) - c(2), p(:,1) - c(1));
[~, k] = sort(ang);
p = p(k, :);
end

function q = clipHalf(p, sfun)
%CLIPHALF 用半平面 sfun >= 0 裁剪多边形 p(保留 sfun >= 0 的一侧)
n = size(p, 1);
q = zeros(0, 2);
if n == 0, return; end
s = sfun(p);
for i = 1:n
    j = mod(i, n) + 1;
    if s(i) >= 0, q(end+1, :) = p(i, :); end %#ok<AGROW>
    if (s(i) >= 0) ~= (s(j) >= 0)
        t = s(i) / (s(i) - s(j));
        q(end+1, :) = p(i, :) + t * (p(j, :) - p(i, :)); %#ok<AGROW>
    end
end
end

function a = polyArea(p)
%POLYAREA 鞋带公式, 返回绝对值
n = size(p, 1);
if n < 3, a = 0; return; end
x = p(:, 1); y = p(:, 2);
a = abs(sum(x .* y([2:end 1]) - y .* x([2:end 1]))) / 2;
end

function [chars, edges] = slotChars(plateGray, n, varargin)
%SLOTCHARS 按固定槽位从校正后的车牌上切字符(不依赖二值化与投影分割)
%
%   [chars, edges] = SLOTCHARS(plateGray, 7)
%   [chars, edges] = SLOTCHARS(plateGray, 8, 'OutSize', [32 16], 'Jitter', 0.0)
%
%   输入
%     plateGray : 已校正的车牌灰度图(高 64 最佳, 其它高度也能用), 也可传 RGB
%     n         : 字符位数(7 普通 / 8 新能源)
%   可选参数
%     'OutSize' [32 16]  输出字符图的高宽, 默认 [32 16](与模板匹配的字符口径一致)
%     'Jitter'  0        槽位边界的随机抖动幅度(相对槽宽的比例), 只在造训练
%                        数据时用: 让 CNN 学会容忍几个像素的槽位偏差
%     'Stretch' true     是否做逐格对比度拉伸
%     'Polarity' 'auto'  'auto' | 'bright'(墨迹亮) | 'dark'(墨迹暗)
%   输出
%     chars : 1xN cell, 每格是 OutSize(1) x OutSize(2) 的 double 图, 值域 [0,1],
%             **已统一成"墨迹亮、底色暗"**: 蓝牌本来就是白字(不用动), 黄牌/绿牌/
%             白底警用牌的黑字会被反相, 于是 CNN 只需要学一套极性。
%     edges : 1x(N+1), 实际用到的槽位边界
%
%   为什么用固定槽位而不是分割:
%     第九轮取证(测试脚本/probe_ccpd_gt_seg.m)显示, 即使用 CCPD 的**真值四角**
%     把车牌裁得严丝合缝, segmentChars 也只有 53.1% 能切对字符位数 —— 真实照片上
%     "CLAHE + Otsu + 垂直投影"这条链本身就是瓶颈。拉正后的车牌字符位置固定,
%     直接按槽位切可以把这一整条链绕开。槽位边界见 slotEdges.m。
%
%   极性怎么判: 用整块车牌的**中位数**当底色参考。车牌上底色面积远大于字符,
%   所以中位数基本就是底色亮度。格内均值明显低于它 = 格子里的字比底色暗(黄牌/
%   绿牌/白底警用牌) -> 反相。这个判据只看一个数, 比"先二值化再数比例"稳。

opt = parseOpts(varargin{:});

if size(plateGray, 3) > 1
    plateGray = rgb2gray(plateGray);
end
g = im2double(plateGray);
[H, W] = size(g);

edges = slotEdges(n);
if opt.Jitter > 0
    edges = jitterEdges(edges, opt.Jitter);
end

bg = median(g(:));
chars = cell(1, n);
for k = 1:n
    % 每个格子**各自**向左右外扩 Widen(按车牌宽的比例), 相邻格子因此会重叠。
    % 不能靠"把内部边界往外挪"来实现: 挪一条内部边界只会把这格撑宽、把隔壁压窄。
    a = min(W, max(1, floor((edges(k)     - opt.Widen) * W) + 1));
    b = min(W, max(a + 2, ceil((edges(k + 1) + opt.Widen) * W)));
    c = g(:, a:b);

    if strcmpi(opt.Polarity, 'auto')
        flip = mean(c(:)) < bg - 0.02;      % 墨迹比底色暗 -> 反相
    else
        flip = strcmpi(opt.Polarity, 'dark');
    end
    if flip
        c = 1 - c;
    end

    if opt.Stretch
        c = stretchCell(c);
    end
    chars{k} = imresize(c, opt.OutSize, 'bilinear');
end
end

% ======================== 局部函数 ========================

function opt = parseOpts(varargin)
opt = struct('OutSize', [32 16], 'Jitter', 0, 'Stretch', true, 'Polarity', 'auto', 'Widen', 0.015);
if mod(numel(varargin), 2) ~= 0
    error('slotChars:badArgs', '参数必须成对出现: Name, Value');
end
for i = 1:2:numel(varargin)
    if ~isfield(opt, varargin{i})
        error('slotChars:badArgs', '未知参数: %s', varargin{i});
    end
    opt.(varargin{i}) = varargin{i + 1};
end
end



function e = jitterEdges(edges, amt)
%JITTEREDGES 给槽位边界加随机抖动(保持单调)
%   造训练数据时用: 推理时槽位边界是固定的, 而真实车牌的长宽比、检测框都有
%   几个像素的偏差, 抖动能让 CNN 对这种偏差免疫, 而不是死记"第 37 列是边界"。
e = edges(:)';
n = numel(e) - 1;
for k = 2:n
    wLocal = min(e(k) - e(k - 1), e(k + 1) - e(k));
    e(k) = e(k) + (rand - 0.5) * 2 * amt * wLocal;
end
% 外侧两条边界也往里/往外挪一点(模拟车牌左右边缘留白不一致)
e(1)   = max(0, e(1)   + (rand - 0.5) * 2 * amt * (edges(2) - edges(1)) * 0.5);
e(n+1) = min(1, e(n+1) + (rand - 0.5) * 2 * amt * (edges(n+1) - edges(n)) * 0.5);
% 单调化: 每个边界至少比前一个大一点点
minGap = 0.02;
for k = 2:n+1
    e(k) = max(e(k), e(k - 1) + minGap);
end
e = min(e, 1);
end

function c = stretchCell(c)
%STRETCHCELL 逐格对比度拉伸到 [0,1]
%   真实照片上同一块车牌左右亮度可以差一倍, 全局阈值一定顾此失彼。逐格拉到
%   满量程之后, CNN 看到的是"形状", 而不是"这块牌子整体是亮还是暗"。
%   拉伸下限用 5%/95% 分位而不是最小/最大, 免得单个噪点把量程拉爆。
lo = quantile(c(:), 0.05);
hi = quantile(c(:), 0.95);
span = hi - lo;
if span < 0.12
    span = 0.12;        % 格子里几乎没墨迹(空槽/极糊): 别把噪声放大成笔画
end
c = (c - lo) / span;
c(c < 0) = 0;
c(c > 1) = 1;
end
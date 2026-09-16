function buildTemplates(outFile, fontLatin, fontChinese)
%BUILDTEMPLATES 生成字符模板库 templates.mat
%
%   buildTemplates()                         % 默认字体 Arial / SimHei
%   buildTemplates('templates.mat','Arial','SimHei')
%
%   用系统字体渲染 0-9、A-Z(去掉 I/O) 与 31 个省级简称, 再走与分割阶段
%   完全相同的归一化流程(32x16 -> 24x12 特征), 保证两端特征可比。
%
%   ！中文字体与车牌实际字体有差异, 模板匹配对汉字只能做到"演示级";
%     工程上建议用真实标注样本替换, 或改用 train_char_cnn.m 训练 CNN

if nargin < 1 || isempty(outFile)
    outFile = fullfile(fileparts(mfilename('fullpath')), 'templates.mat');
end
if nargin < 2 || isempty(fontLatin),   fontLatin = 'Arial';   end
if nargin < 3 || isempty(fontChinese), fontChinese = 'SimHei'; end

letters = setdiff('A':'Z', 'IO');      % 车牌不使用 I 和 O
digits  = '0':'9';
prov = {'京','津','冀','晋','蒙','辽','吉','黑','沪','苏','浙','皖','闽','赣', ...
        '鲁','豫','鄂','湘','粤','桂','琼','渝','川','贵','云','藏','陕','甘', ...
        '青','宁','新'};

fprintf('    渲染 %d 字母 / %d 数字 / %d 汉字 ...\n', ...
        numel(letters), numel(digits), numel(prov));

T = struct();
T.letters = packSet(num2cell(letters), fontLatin);
T.digits  = packSet(num2cell(digits),  fontLatin);
T.chinese = packSet(prov,              fontChinese);

% 注意: 不能用 struct('label', {cellArray}) 的形式, 那会生成 struct 数组
T.alnum         = struct();
T.alnum.label   = [T.letters.label,   T.digits.label];
T.alnum.feature = [T.letters.feature, T.digits.feature];

T.info = struct('created', datestr(now), 'version', 2, ...
                'fontLatin', fontLatin, 'fontChinese', fontChinese);

save(outFile, '-struct', 'T');
fprintf('    模板已保存: %s\n', outFile);
end

% ======================== 局部函数 ========================

function s = packSet(chars, fontName)
n = numel(chars);
s = struct();
s.label   = cell(1, n);
s.feature = cell(1, n);
for k = 1:n
    ch  = chars{k};
    bw  = renderChar(ch, fontName);
    img = normalizeChar(bw, 32, 16, 2);
    if ~any(img(:))
        warning('buildTemplates:emptyTemplate', '字符 "%s" 的模板为空', ch);
    end
    s.label{k}   = ch;
    s.feature{k} = charFeature(img);
end
end

function bw = renderChar(ch, fontName)
%RENDERCHAR 渲染单字符并裁剪到最小外接矩形
isCJK = double(ch(1)) > 127;
canvasSize = 300;
if isCJK
    fontSize = 110;
else
    fontSize = 140;
end

canvas = zeros(canvasSize, canvasSize, 'uint8');
out = canvas;
try
    out = insertText(canvas, [canvasSize/2 canvasSize/2], ch, ...
        'FontSize', fontSize, 'BoxOpacity', 0, 'TextColor', 'white', ...
        'AnchorPoint', 'Center', 'Font', fontName);
catch
    % insertText 不可用(无 Computer Vision Toolbox)时走 figure 渲染
end
bw = toBinary(out);

if ~any(bw(:))
    bw = renderCharFigure(ch, fontName);
end

[r, c] = find(bw);
if isempty(r)
    error('buildTemplates:renderFail', '字符 "%s" 渲染失败, 请检查字体 %s 是否安装', ch, fontName);
end
bw = bw(min(r):max(r), min(c):max(c));
end

function bw = toBinary(out)
%TOBINARY 统一成二维逻辑图
%   注意: insertText 对二维(灰度)输入返回的是三通道结果, 必须先转灰度,
%   否则后面 find/索引会按三维数组处理, 索引越界导致模板为空。
if isempty(out)
    bw = false(0, 0);
    return;
end
if ndims(out) == 3
    out = rgb2gray(out);
end
bw = reshape(out, size(out, 1), size(out, 2)) > 128;
end

function bw = renderCharFigure(ch, fontName)
%RENDERCHARFIGURE 备用渲染方案: 黑底白字 figure -> print 成 PNG -> 读回
f = figure('Visible', 'off', 'Color', 'k', 'MenuBar', 'none', 'ToolBar', 'none', ...
           'NumberTitle', 'off', 'Units', 'pixels', 'Position', [50 50 320 320]);
closeFcn = onCleanup(@() close(f)); %#ok<NASGU>
set(f, 'InvertHardcopy', 'off');     % 关掉自动反色, 否则背景会变成白色

ax = axes('Parent', f, 'Color', 'k', 'Units', 'normalized', 'Position', [0 0 1 1], ...
          'XLim', [0 1], 'YLim', [0 1], 'XTick', [], 'YTick', []);
text(0.5, 0.5, ch, 'Parent', ax, 'Color', 'w', 'FontName', fontName, ...
     'FontWeight', 'bold', 'FontSize', 110, ...
     'HorizontalAlignment', 'center', 'VerticalAlignment', 'middle');

tmpFile = [tempname '.png'];
print(f, tmpFile, '-dpng', '-r96');
bw = toBinary(imread(tmpFile));
delete(tmpFile);
end
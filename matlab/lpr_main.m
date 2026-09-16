function results = lpr_main(imagePath, varargin)
%LPR_MAIN 车牌识别主流程: 读图 -> 定位 -> 校正 -> 分割 -> 识别
%
%   results = LPR_MAIN(imagePath)
%   results = LPR_MAIN(imagePath, 'ShowFigure', false, 'MaxSize', 1600)
%
%   返回结构体字段:
%       text        识别出的车牌号字符串
%       chars       每个字符的识别结果 (cell)
%       scores      每个字符的匹配置信度 (1xN double)
%       plateBox    车牌在原图中的位置 [x y w h]
%       plateImage  倾斜校正后的车牌彩色图
%       charImages  分割出的字符二值图 (cell, 每个为 logical)
%
%   依赖: Image Processing Toolbox
%         Computer Vision Toolbox (仅模板生成时用于 insertText, 可选)
%
%   示例:
%       lpr_main('car1.jpg');
%       r = lpr_main('images/demo_plate.jpg', 'ShowFigure', false);
%
%   说明: 本工程针对中国大陆车牌 (蓝底白字 / 绿底黑字 / 黄底黑字),
%         轿车照片分辨率建议 640~2000 像素宽, 车牌宽度不少于 100 像素。

% ------------------------- 参数解析 -------------------------
p = inputParser;
p.addRequired('imagePath');
p.addParameter('ShowFigure', true);
p.addParameter('MaxSize', 1600);      % 长边限制, 加速处理
p.parse(imagePath, varargin{:});
opt = p.Results;

imagePath = char(imagePath);
if exist(imagePath, 'file') ~= 2
    error('lpr:fileNotFound', '找不到图像文件: %s', imagePath);
end

% ------------------------- 0. 读图与预处理 -------------------------
I = imread(imagePath);
I = ensureRGB(I);
I = limitSize(I, opt.MaxSize);
fprintf('[1/5] 读入 %s  (%d x %d)\n', imagePath, size(I, 1), size(I, 2));

% ------------------------- 1. 车牌定位 -------------------------
[plateBox, plateMask, scInfo] = locatePlate(I);
if isempty(plateBox) || ~scInfo.valid
    % 定位到了某块区域, 但它不满足真车牌的双闸门(矩形度 + 底色占比),
    % 属于噪声/蓝天/路面一类的假候选, 这里按未检测到车牌处理。
    why = scInfo.reject;
    if isempty(why)
        why = '没有找到长宽比/面积像车牌的候选区域';
    end
    fprintf('[2/5] 未检测到车牌: %s\n', why);
    warning('lpr:noPlate', '未检测到车牌: %s。建议换一张车牌更清晰、更居中的照片。', why);
    results = emptyResult();
    results.reason = why;
    results.scoreInfo = scInfo;
    if opt.ShowFigure
        figure('Name', '车牌识别', 'NumberTitle', 'off');
        imshow(I); title(sprintf('未检测到车牌: %s', why), 'Interpreter', 'none');
    end
    return;
end
fprintf('[2/5] 车牌位置: x=%d y=%d w=%d h=%d (矩形度 %.2f, 底色占比 %.2f)\n', ...
        round(plateBox), scInfo.extent, scInfo.colorFrac);

% ------------------------- 2. 裁剪 + 倾斜校正 -------------------------
[plateGray, plateColor, plateMask] = cropPlate(I, plateBox, plateMask);
[plateGray, plateColor] = correctPlate(plateGray, plateColor, plateMask);
fprintf('[3/5] 校正后车牌: %d x %d\n', size(plateGray, 1), size(plateGray, 2));

% ------------------------- 3. 字符分割 -------------------------
[charImages, bwPlate] = segmentChars(plateGray);
fprintf('[4/5] 分割出 %d 个字符\n', numel(charImages));

% ------------------------- 4. 字符识别 -------------------------
[plateText, chars, scores] = recognizeChars(charImages);
fprintf('[5/5] 识别结果: %s\n', plateText);

% ------------------------- 5. 组织输出 -------------------------
results         = struct();
results.text    = plateText;
results.chars   = chars;
results.scores  = scores;
results.plateBox= plateBox;
results.plateImage = plateColor;
results.charImages = charImages;
results.reason  = '';
results.scoreInfo = scInfo;

if opt.ShowFigure
    showResult(I, plateBox, plateColor, bwPlate, charImages, plateText);
end
end

% ======================== 以下为局部函数 ========================

function r = emptyResult()
r = struct();
r.text = ''; r.chars = {}; r.scores = []; r.plateBox = [];
r.plateImage = []; r.charImages = {};
r.reason = ''; r.scoreInfo = struct();
end

function I = ensureRGB(I)
% 统一成 uint8 三通道图像
if ismatrix(I)
    I = repmat(I, 1, 1, 3);
elseif size(I, 3) == 2
    I = repmat(I(:, :, 1), 1, 1, 3);
elseif size(I, 3) > 3
    I = I(:, :, 1:3);
end
if ~isa(I, 'uint8')
    I = im2uint8(I);
end
end

function I = limitSize(I, maxSize)
m = max(size(I, 1), size(I, 2));
if m > maxSize
    I = imresize(I, maxSize / m, 'bilinear');
end
end

function showResult(I, box, plateColor, bwPlate, charImages, plateText)
figure('Name', '车牌识别结果', 'NumberTitle', 'off');

subplot(2, 3, 1);
imshow(I); title('原图 + 定位框');
rectangle('Position', box, 'EdgeColor', 'y', 'LineWidth', 2);

subplot(2, 3, 2);
imshow(plateColor); title('校正后的车牌');

subplot(2, 3, 3);
imshow(bwPlate); title('二值化 (字符=白)');

subplot(2, 3, [4 5 6]);
if isempty(charImages)
    axis off; text(0.05, 0.5, '未分割出字符', 'FontSize', 12);
else
    mosaic = [];
    for k = 1:numel(charImages)
        ch = charImages{k};
        if isempty(mosaic)
            mosaic = ch;
        else
            mosaic = [mosaic, false(size(ch, 1), 4), ch]; %#ok<AGROW>
        end
    end
    mosaic = imresize(mosaic, 3, 'nearest');   % 放大便于查看
    imshow(mosaic);
    title(['识别结果: ' plateText]);
end
end
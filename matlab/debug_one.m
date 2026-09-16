function results = debug_one(imagePath, varargin)
%DEBUG_ONE 单张车牌的逐级可视化, 用来判断问题出在哪一步
%
%   debug_one('images/demo_plate.jpg')
%   debug_one('my_car.jpg', 'SavePng', true)     % 同时存成 PNG 方便贴到报告里
%
%   输出 6 个子图:
%     ① 原图 + 定位框   ② 车牌掩膜(定位结果)   ③ 校正后的车牌
%     ④ 二值化          ⑤ 分割出的字符         ⑥ 垂直投影 + 分割边界
%   命令窗口同时打印每一位的识别结果与置信度。

p = inputParser;
p.addRequired('imagePath');
p.addParameter('SavePng', false);
p.parse(imagePath, varargin{:});
opt = p.Results;

imagePath = char(imagePath);
if exist(imagePath, 'file') ~= 2
    error('debug_one:notFound', '找不到图片: %s', imagePath);
end

% ---------- 逐级执行, 每一步都检查 ----------
I = imread(imagePath);
if size(I, 3) == 1, I = repmat(I, 1, 1, 3); end
if ~isa(I, 'uint8'), I = im2uint8(I); end
m = max(size(I, 1), size(I, 2));
if m > 1600, I = imresize(I, 1600 / m); end
fprintf('① 输入图像: %d x %d\n', size(I, 1), size(I, 2));

[box, plateMask] = locatePlate(I);
if isempty(box)
    figure('Name', ['车牌识别调试 - ' imagePath], 'NumberTitle', 'off');
    imshow(I); title('未检测到车牌: 请检查 locatePlate 的颜色/长宽比阈值');
    fprintf('② 车牌定位失败, 后续步骤无法进行。\n');
    results = struct('text', '', 'chars', {{}}, 'scores', [], 'plateBox', []);
    return;
end
fprintf('② 车牌定位: x=%d y=%d w=%d h=%d  (长宽比 %.2f)\n', round(box), box(3) / box(4));

[gray, color, mask] = cropPlate(I, box, plateMask);
[gray, color]  = correctPlate(gray, color, mask);
fprintf('③ 校正后车牌: %d x %d\n', size(gray, 1), size(gray, 2));

[charImages, bwPlate, bounds] = segmentChars(gray);
fprintf('④ 二值化前景占比: %.1f%%\n', 100 * nnz(bwPlate) / numel(bwPlate));
fprintf('⑤ 分割出 %d 个字符\n', numel(charImages));

[text, charText, scores] = recognizeChars(charImages);
fprintf('⑥ 识别结果: %s\n', text);
for k = 1:numel(charText)
    fprintf('     第 %d 位: %s  (置信度 %.2f)\n', k, charText{k}, scores(k));
end

% ---------- 画图 ----------
fig = figure('Name', ['车牌识别调试 - ' imagePath], 'NumberTitle', 'off', ...
             'Position', [60 60 1280 700]);

subplot(2, 3, 1);
imshow(I); title('① 原图 + 定位框');
rectangle('Position', box, 'EdgeColor', 'y', 'LineWidth', 2);

subplot(2, 3, 2);
imshow(plateMask); title('② 车牌掩膜(定位得分最高的连通域)');

subplot(2, 3, 3);
imshow(color); title('③ 校正后的车牌');

subplot(2, 3, 4);
imshow(bwPlate); title('④ 二值化 (字符=白)');

subplot(2, 3, 5);
if isempty(charImages)
    axis off; text(0.05, 0.5, '未分割出字符');
else
    mosaic = [];
    for k = 1:numel(charImages)
        ch = charImages{k};
        if isempty(mosaic)
            mosaic = ch;
        else
            mosaic = [mosaic, false(size(ch, 1), 6), ch]; %#ok<AGROW>
        end
    end
    imshow(imresize(mosaic, 4, 'nearest'));
    title(['⑤ 分割+识别: ' text]);
end

subplot(2, 3, 6);
proj = sum(bwPlate, 1);
plot(proj, 'k', 'LineWidth', 1); hold on;
yl = [0, max(1, max(proj))];
for k = 1:size(bounds, 1)
    plot([bounds(k, 1) bounds(k, 1)], yl, 'r-');
    plot([bounds(k, 2) bounds(k, 2)], yl, 'b:');
end
title('⑥ 垂直投影(黑) + 分割边界(红=起, 蓝=止)');
xlabel('列'); ylabel('白点数'); grid on;

% 关掉坐标区工具栏图标, 否则导出的 PNG 会盖住子图标题
for ax = findall(fig, 'Type', 'axes')'
    set(ax, 'Toolbar', []);
end

if opt.SavePng
    [pth, nm] = fileparts(imagePath);
    outPng = fullfile(pth, [nm '_debug.png']);
    exportgraphics(fig, outPng, 'Resolution', 110);
    fprintf('调试图已保存: %s\n', outPng);
end

results = struct();
results.text = text; results.chars = charText; results.scores = scores;
results.plateBox = box; results.plateImage = color; results.charImages = charImages;
end
%% DEMO_RUN 批量处理 images 文件夹下的所有车牌照片
% 用法: 把待识别照片放进本工程目录的 images 子文件夹, 然后运行本脚本。

clear; clc; close all;

rootDir = fileparts(mfilename('fullpath'));
imgDir  = fullfile(rootDir, 'images');

if exist(imgDir, 'dir') ~= 7
    fprintf('请先创建目录并放入照片: %s\n', imgDir);
    return;
end

files = [dir(fullfile(imgDir, '*.jpg')); ...
         dir(fullfile(imgDir, '*.jpeg')); ...
         dir(fullfile(imgDir, '*.png')); ...
         dir(fullfile(imgDir, '*.bmp'))];

fprintf('在 %s 下找到 %d 张图片\n', imgDir, numel(files));
allText = cell(numel(files), 1);

for i = 1:numel(files)
    fprintf('\n=== [%d/%d] %s ===\n', i, numel(files), files(i).name);
    try
        r = lpr_main(fullfile(imgDir, files(i).name), 'ShowFigure', true);
        allText{i} = r.text;
        fprintf('结果: %s   (平均置信度 %.2f)\n', r.text, mean(r.scores));
    catch err
        warning('处理失败: %s', err.message);
    end
    drawnow;
end

fprintf('\n===== 汇总 =====\n');
for i = 1:numel(files)
    fprintf('%-30s %s\n', files(i).name, allText{i});
end
%% BENCH_RUN 在 bench/ 目录的随机测试图上评估识别率(明细版)
% 测试图特点: 随机省份/字母/数字, 倾斜 -9~9 度, 车牌大小 0.6~1.15 倍, 随机模糊与噪声
%
% 换成你自己的照片: 把图片放进 bench/, 并写一份 labels.csv (列: file,text), 再运行本脚本

rootDir = fileparts(mfilename('fullpath'));
addpath(genpath(rootDir));
bench_eval(fullfile(rootDir, 'bench'), true);
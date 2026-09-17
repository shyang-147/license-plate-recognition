function R = recognizePlateCNN(plateGray, varargin)
%RECOGNIZEPLATECNN 整块车牌识别: 固定槽位切字符 + CNN 分类, 自动定 7 位 / 8 位
%
%   R = RECOGNIZEPLATECNN(plateGray)
%   R = RECOGNIZEPLATECNN(plateGray, 'Model', S, 'Lengths', [7 8])
%
%   输入 plateGray: 已校正(拉正、高 64)的车牌灰度图
%   可选参数
%     'Model'    已 load 的模型(含 net / classNames), 批量评测时传进来省去反复 load
%     'ModelFile' 模型路径(默认同目录 charNet.mat)
%     'Lengths'  要试的位数, 默认 [7 8]
%   输出 R 字段
%     .text/.chars/.scores  识别结果(与 recognizeChars 同口径, 便于替换)
%     .len        最终采用的位数
%     .format     plateFormat(len)
%     .cells      采用的槽位切图(1xN cell)
%     .perLen     struct 数组, 每种位数试出来的 text / score, 便于排查
%
%   为什么要同时试 7 位和 8 位, 而不是先判位数:
%     分割法可以"数出几个字符段", 固定槽位没有这个信息 —— 但代价换来的是
%     不再依赖二值化。这里改成"两种位数各认一遍, 取平均对数概率高的那个"。
%     7 位牌按 8 位切会把每个字切窄, 认出来的平均置信度会明显更低, 所以这个
%     判据是有效的(实测见 结果记录/第十轮_*.txt)。
%
%   长宽比为什么要重新拉: 号牌尺寸有国标 —— 蓝/黄/白底 440x140, 小型新能源
%   绿牌 480x140。correctPlate 用的是**检测框在图像里的投影比例**, 斜拍时它不是
%   车牌真实比例(第十轮 A/B 取证: 目标比例换成规范值后, 位数切对率 53.1%->59.7%)。
%   固定槽位要的是"字符格子的相对位置", 所以这里按位数把宽度拉到规范值。

p = inputParser;
p.addRequired('plateGray');
p.addParameter('Model', []);
p.addParameter('ModelFile', '');
p.addParameter('Lengths', [7 8], @isnumeric);
p.addParameter('LowConf', 0.45);
p.parse(plateGray, varargin{:});
opt = p.Results;

S = opt.Model;
if isempty(S)
    f = opt.ModelFile;
    if isempty(f)
        f = fullfile(fileparts(mfilename('fullpath')), 'charNet.mat');
    end
    if exist(f, 'file') ~= 2
        error('recogCNN:noModel', '找不到模型文件 %s, 请先运行 train_char_cnn.m', f);
    end
    S = load(f, 'net', 'classNames');
end

if size(plateGray, 3) > 1, plateGray = rgb2gray(plateGray); end
H = size(plateGray, 1);

perLen = struct('len', {}, 'text', {}, 'score', {}, 'chars', {}, 'scores', {});
best = struct('score', -inf, 'len', 0, 'text', '', 'chars', {{}}, ...
              'scores', [], 'cells', {{}}, 'format', []);

for len = opt.Lengths(:)'
    asp = plateAspect(len);
    Wt  = max(24, round(asp * H));
    P   = imresize(plateGray, [H Wt], 'bilinear');
    cells = slotChars(P, len);
    F   = plateFormat(len);
    [text, chars, scores] = recognizeCharsCNN(cells, 'Model', S, 'Format', F, ...
                                              'LowConf', opt.LowConf);
    sc = mean(log(max(scores(:), 1e-6)));
    perLen(end + 1) = struct('len', len, 'text', text, 'score', sc, ...
                             'chars', {chars}, 'scores', scores); %#ok<AGROW>
    if sc > best.score
        best = struct('score', sc, 'len', len, 'text', text, 'chars', {chars}, ...
                      'scores', scores, 'cells', {cells}, 'format', F);
    end
end

R = struct();
R.text   = best.text;
R.chars  = best.chars;
R.scores = best.scores;
R.len    = best.len;
R.format = best.format;
R.cells  = best.cells;
R.score  = best.score;
R.perLen = perLen;
end

% ======================== 局部函数 ========================

function a = plateAspect(len)
%PLATEASPECT 位数 -> 号牌规范宽高比(GA 36-2018: 蓝/黄 440x140, 新能源 480x140)
if len == 8, a = 480/140; else, a = 440/140; end
end
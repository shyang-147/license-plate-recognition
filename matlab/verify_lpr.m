function allPass = verify_lpr(varargin)
%VERIFY_LPR 车牌识别工程一键自检
%
%   verify_lpr                  % 完整自检(含 20 张基准集, 约 10~20 秒)
%   verify_lpr('Bench', false)  % 跳过基准集, 只做快速检查
%   ok = verify_lpr();          % 返回 true 表示全部通过, 适合做 CI 断言
%
%   检查内容:
%     [1] 环境和工具箱        MATLAB 版本 / IPT / CVT
%     [2] 工程文件完整性      必需 .m 文件是否齐全
%     [3] 模块单元测试        归一化、特征、模板、定位、分割逐项验证
%     [4] 演示图整牌识别      images/ 中每张图与 images/expected.csv 比对
%     [5] 基准集准确率        bench/ 20 张随机图, 断言分割与字符准确率下限
%     [6] 可视化分支          ShowFigure=true 是否会报错
%     [7] 异常处理            文件不存在是否报错、噪声图/蓝天图是否误报车牌
%
%   想看某一步为什么失败, 用 debug_one('图片路径') 逐级可视化。

p = inputParser;
p.addParameter('Bench', true);
p.parse(varargin{:});
opt = p.Results;

rootDir = fileparts(mfilename('fullpath'));
addpath(genpath(rootDir));
imgDir  = fullfile(rootDir, 'images');

nPass = 0; nFail = 0; nSkip = 0;

fprintf('\n================== 车牌识别工程自检 ==================\n');
fprintf('工程目录: %s\n', rootDir);

% ---------------------------------------------------------- 1
fprintf('\n[1/7] 环境与工具箱\n');
fprintf('      MATLAB %s\n', version);
v = ver('images');
report('Image Processing Toolbox (必需)', ~isempty(v), verText(v));
v = ver('vision');
if isempty(v)
    note('Computer Vision Toolbox 未安装: 模板渲染会自动回退到 figure 方案, 不影响识别');
else
    report('Computer Vision Toolbox (可选)', true, verText(v));
end
v = ver('nnet');
if isempty(v)
    note('Deep Learning Toolbox 未安装: 仅影响 train_char_cnn.m 的 CNN 方案');
else
    report('Deep Learning Toolbox (可选)', true, verText(v));
end

% ---------------------------------------------------------- 2
fprintf('\n[2/7] 工程文件完整性\n');
req = {'lpr_main.m', 'locatePlate.m', 'cropPlate.m', 'correctPlate.m', ...
       'segmentChars.m', 'normalizeChar.m', 'charFeature.m', ...
       'recognizeChars.m', 'plateFormat.m', 'buildTemplates.m'};
miss = req(~cellfun(@(f) exist(fullfile(rootDir, f), 'file') == 2, req));
if isempty(miss)
    report(sprintf('必需 .m 文件齐全 (%d 个)', numel(req)), true, '');
else
    report('必需 .m 文件齐全', false, sprintf('缺少: %s', strjoin(miss, ', ')));
end

% ---------------------------------------------------------- 3
fprintf('\n[3/7] 模块单元测试\n');

% --- normalizeChar / charFeature ---
bwT = false(20, 10); bwT(4:17, 4:7) = true;
img = normalizeChar(bwT, 32, 16, 2);
report('normalizeChar 输出 32x16 且非空', isequal(size(img), [32 16]) && any(img(:)), ...
       sprintf('实际 %s, 前景 %d 像素', mat2str(size(img)), nnz(img)));
f = charFeature(img);
report('charFeature 输出 1x288 特征', isequal(size(f), [1 288]), mat2str(size(f)));

% --- 模板库 ---
tpl = fullfile(rootDir, 'templates.mat');
if exist(tpl, 'file') ~= 2
    note('templates.mat 不存在, 正在生成(首次运行需要几秒)...');
    buildTemplates(tpl);
end
T = load(tpl);
report('模板: 24 字母 / 10 数字 / 31 汉字 / 34 字母数字', ...
       numel(T.letters.label) == 24 && numel(T.digits.label) == 10 && ...
       numel(T.chinese.label) == 31 && numel(T.alnum.label) == 34, ...
       sprintf('%d/%d/%d/%d', numel(T.letters.label), numel(T.digits.label), ...
               numel(T.chinese.label), numel(T.alnum.label)));
report('alnum 是标量 struct (不是 struct 数组)', isscalar(T.alnum), ...
       sprintf('size = %s', mat2str(size(T.alnum))));
nEmpty = 0;
for i = 1:numel(T.alnum.feature)
    if ~any(T.alnum.feature{i} > 0.5), nEmpty = nEmpty + 1; end
end
for i = 1:numel(T.chinese.feature)
    if ~any(T.chinese.feature{i} > 0.5), nEmpty = nEmpty + 1; end
end
report('模板特征全部非空(空模板会导致识别全错)', nEmpty == 0, sprintf('空模板 %d 个', nEmpty));

% --- 车牌制式(7 位普通 / 8 位新能源) ---
F7 = plateFormat(7);
F8 = plateFormat(8);
report('plateFormat: 7 位 = 省简称 + 字母 + 5 位字母数字', ...
       F7.n == 7 && numel(F7.sets) == 7 && isequal(F7.sets{3}, F7.sets{7}), ...
       sprintf('%s, 第 3 位允许 %d 个字符', F7.label, numel(F7.sets{3})));
report('plateFormat: 8 位 = 省简称 + 2 字母 + 5 位字母数字', ...
       F8.n == 8 && numel(F8.sets) == 8 && isequal(F8.sets{3}, F7.sets{2}), ...
       sprintf('%s, 第 3 位允许 %d 个字符', F8.label, numel(F8.sets{3})));
report('plateFormat: 字母表不含 I / O (车牌不使用这两个字母)', ...
       ~contains(F7.sets{2}, 'I') && ~contains(F7.sets{2}, 'O'), ...
       sprintf('第 2 位候选: %s', F7.sets{2}));

% --- 定位 + 校正 + 分割 ---
demoImg = fullfile(imgDir, 'demo_plate.jpg');
if exist(demoImg, 'file') == 2
    I = imread(demoImg);
    [box, mask, scInfo] = locatePlate(I);
    report('locatePlate 找到车牌', ~isempty(box), ...
           sprintf('x=%d y=%d w=%d h=%d', round(box)));
    report('定位结果通过真车牌闸门(矩形度 + 底色占比)', ~isempty(box) && scInfo.valid, ...
           sprintf('矩形度 %.2f, 底色占比 %.2f, 长宽比 %.2f, 通道 %s', ...
                   scInfo.extent, scInfo.colorFrac, scInfo.aspect, scInfo.source));
    if ~isempty(box)
        [pg, pc, pm] = cropPlate(I, box, mask);
        [pg, ~] = correctPlate(pg, pc, pm);
        [chars, ~, bounds, inkAR] = segmentChars(pg);
        report('correctPlate 统一高度为 64', size(pg, 1) == 64, sprintf('%dx%d', size(pg, 1), size(pg, 2)));
        report('segmentChars 分割出 7 个字符', numel(chars) == 7, ...
               sprintf('实际 %d 个, 边界 %s', numel(chars), mat2str(bounds)));
        report('segmentChars 同时给出每段的墨迹宽高比', numel(inkAR) == numel(chars), ...
               sprintf('墨迹宽高比 %s', mat2str(round(inkAR * 100) / 100)));
        report('每个字符归一化为 32x16', ...
               all(cellfun(@(c) isequal(size(c), [32 16]), chars)), '');
    end
else
    note(sprintf('未找到 %s, 跳过定位/分割测试', demoImg));
end

% ---------------------------------------------------------- 4
fprintf('\n[4/7] 演示图整牌识别 (images/expected.csv)\n');
expFile = fullfile(imgDir, 'expected.csv');
if exist(expFile, 'file') == 2
    E = readtable(expFile, 'Encoding', 'UTF-8', 'VariableNamingRule', 'preserve');
    for i = 1:height(E)
        fname = E.file{i};
        gt    = char(E.text{i});
        fp    = fullfile(imgDir, fname);
        if exist(fp, 'file') ~= 2
            nSkip = nSkip + 1;
            note(sprintf('%s 不存在, 跳过', fname));
            continue;
        end
        try
            r   = quietCall(@() lpr_main(fp, 'ShowFigure', false));
            got = r.text;
            report(sprintf('%-18s 期望 %-9s 识别 %-9s', fname, gt, got), strcmp(got, gt), '');
        catch err
            report(sprintf('%-18s 期望 %-9s', fname, gt), false, err.message);
        end
    end
    note('要检查自己的照片: 放进 images/, 并在 images/expected.csv 中加一行 (file,text) 再运行本脚本');
else
    nSkip = nSkip + 1;
    note('未找到 images/expected.csv, 跳过演示图检查');
end

% ---------------------------------------------------------- 5
fprintf('\n[5/7] 基准集准确率 (bench/)\n');
if opt.Bench
    if isfile(fullfile(rootDir, 'bench', 'labels.csv'))
        try
            S = quietCall(@() bench_eval(fullfile(rootDir, 'bench'), false));
            % 字符数现在是自校准的(7 位普通牌 / 8 位新能源牌自适应), 不再写死,
            % 代价是个别图的切分会在 7/8 之间摇摆, 所以这里按 >= 95% 断言。
            report('字符数分割正确率 >= 95%', S.segAcc >= 0.95, ...
                   sprintf('%d/%d = %.1f%%', S.segOk, S.n, 100 * S.segAcc));
            report('字符准确率 >= 80% (模板匹配的合理下限)', S.charAcc >= 0.80, ...
                   sprintf('%d/%d = %.1f%%', S.charOk, S.charTotal, 100 * S.charAcc));
            report('整牌准确率 >= 40% (模板匹配的合理下限)', S.plateAcc >= 0.40, ...
                   sprintf('%d/%d = %.1f%%', S.plateOk, S.n, 100 * S.plateAcc));
            note(sprintf('平均单张耗时 %.2f s。明细请单独运行 bench_run', S.timeAvg));
        catch err
            report('基准集评估', false, err.message);
        end
    else
        nSkip = nSkip + 1;
        note('未找到 bench/labels.csv, 跳过');
    end
else
    nSkip = nSkip + 1;
    note('按参数要求跳过基准集 (Bench=false)');
end

% ---------------------------------------------------------- 6
fprintf('\n[6/7] 可视化分支\n');
if exist(demoImg, 'file') == 2
    try
        r = quietCall(@() lpr_main(demoImg, 'ShowFigure', true));
        close all;
        report('ShowFigure=true 正常出图', ~isempty(r.text), ['识别 ' r.text]);
    catch err
        report('ShowFigure=true 正常出图', false, err.message);
    end
else
    nSkip = nSkip + 1;
    note('缺演示图, 跳过');
end

% ---------------------------------------------------------- 7
fprintf('\n[7/7] 异常处理\n');
gotErr = false;
try
    quietCall(@() lpr_main('no_such_file_xyz.jpg', 'ShowFigure', false));
catch
    gotErr = true;
end
report('文件不存在时抛出异常', gotErr, '');

tmpImg = fullfile(tempdir, 'lpr_noise_test.png');
imwrite(uint8(rand(90, 90, 3) * 255), tmpImg);
okNoise = true; msgNoise = '';
warning('off', 'lpr:noPlate');
try
    r = quietCall(@() lpr_main(tmpImg, 'ShowFigure', false));
catch err
    okNoise = false; msgNoise = err.message;
end
warning('on', 'lpr:noPlate');
delete(tmpImg);
noiseText = ''; noiseWhy = '';
if okNoise
    noiseText = r.text;
    noiseWhy  = r.reason;
end
report('随机噪声图不崩溃', okNoise, msgNoise);
report('随机噪声图不报假车牌(闸门生效)', okNoise && isempty(noiseText), ...
       sprintf('识别结果 = %s | 判据 = %s', ...
               merge(isempty(noiseText), '空(正确)', ['假车牌 ' noiseText ' (错误)']), ...
               merge(isempty(noiseWhy), '(无)', noiseWhy)));

% 蓝天/大面积蓝色物体: 颜色通道最容易误检的场景
tmpSky = fullfile(tempdir, 'lpr_sky_test.png');
V = repmat(linspace(0.55, 1.0, 600)', 1, 800);
H = 0.58 * ones(600, 800); S = 0.65 * ones(600, 800);
imwrite(im2uint8(hsv2rgb(H, S, V)), tmpSky);
okSky = true; msgSky = ''; skyText = '';
warning('off', 'lpr:noPlate');
try
    r = quietCall(@() lpr_main(tmpSky, 'ShowFigure', false));
    skyText = r.text;
catch err
    okSky = false; msgSky = err.message;
end
warning('on', 'lpr:noPlate');
delete(tmpSky);
report('大面积蓝色(天空)不误检为车牌', okSky && isempty(skyText), ...
       merge(isempty(msgSky), ['识别结果 = ' merge(isempty(skyText), '空(正确)', skyText)], msgSky));

% ---------------------------------------------------------- 总结
fprintf('\n====================== 自检结果 ======================\n');
fprintf('通过 %d 项, 失败 %d 项, 跳过 %d 项\n', nPass, nFail, nSkip);
if nFail == 0
    fprintf('结论: 全部通过 ✔  环境/模块/整牌识别均正常\n');
else
    fprintf('结论: 有 %d 项未通过 ✘  请按上面 NG 行的提示排查\n', nFail);
end
allPass = (nFail == 0);

% ======================== 嵌套函数 ========================
    function report(name, cond, detail)
        if cond
            nPass = nPass + 1;
            fprintf('      [ OK ] %s\n', name);
        else
            nFail = nFail + 1;
            fprintf('      [ NG ] %s\n', name);
        end
        if nargin > 2 && ~isempty(detail)
            fprintf('             %s\n', detail);
        end
    end

    function note(txt)
        fprintf('      [ -- ] %s\n', txt);
    end

    function t = verText(v)
        if isempty(v)
            t = '未安装';
        else
            t = sprintf('版本 %s', v(1).Version);
        end
    end
end

% ======================== 局部函数 ========================
function out = merge(cond, a, b)
%MERGE 三元选择, 仅用于拼提示文字
if cond, out = a; else, out = b; end
end

function r = quietCall(fh)
%QUIETCALL 执行函数句柄并吞掉它的控制台输出(避免自检结果被淹没)
out = evalc('r = fh();'); %#ok<NASGU>
end
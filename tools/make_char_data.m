function make_char_data(outRoot, varargin)
%MAKE_CHAR_DATA 生成字符 CNN 的训练数据(真实域 + 合成域)
%
%   make_char_data                       % 默认写到 ../matlab/charData
%   make_char_data('charData', 'SynPerClass', 250, 'MaxPlates', 600)
%
%   输出目录
%     <outRoot>/real/<字符>/<n>.png   真实域: CCPD 真值四角裁片 + 固定槽位切字符
%     <outRoot>/syn/<字符>/<n>.png    合成域: 系统字体排版成整块车牌 + 退化增广
%     <outRoot>/summary.txt           逐类样本数
%
%   为什么两个域都要:
%     - 真实域(CCPD)带来真实的相机、光照、模糊、压缩分布 —— 这是模板匹配最缺的东西;
%       但 CCPD 只有安徽号牌, 覆盖不到 31 个省级简称里的其它 30 个。
%     - 合成域覆盖全部 70 个字符, 且能精确控制类别平衡与退化强度;
%       但字体与真实号牌有差异, 单靠它还是"自己考自己"。
%     两个域混着训, 才能既覆盖字符表、又见过真实照片。
%
%   三个"必须做对"的细节(都是第十轮实测踩出来的, 见 评审与改进/第十轮_*.md):
%     ① 合成车牌里**非目标槽位**的填充字符必须合法(第 1 位只能是省级简称,
%        第 2 位只能是字母, 第 3 位起才是字母/数字)。随便从 70 类里抽的话,
%        CNN 会看到"数字位出现汉字"这种车牌上不可能出现的画面, 位置先验被搅乱。
%     ② 字体要做**增广**: 只用人一种字体渲染, CNN 会把"这个字体长什么样"当成
%        判据。实测只用 Arial 渲染字母/数字时, 模型在自己的合成样本上 93.5%,
%        换到自产合成集(bench 用黑体渲染全部字符)只剩 43.8%。
%     ③ 真实号牌排版与自产合成集的排版并不完全一致(格子中心差 1~3% 牌宽),
%        所以合成时整体排版要小幅随机伸缩/平移, 让 CNN 不依赖"字一定在格子正中"。
%
%   ⚠ 训练用的 CCPD 与评测用的 CCPD 必须不重叠。默认用 `测试数据/ccpd_train`
%     (由 tools/fetch_ccpd_sample.py --exclude 测试数据/ccpd/labels.csv 抓取),
%     **不要**指向 `测试数据/ccpd` —— 那是七套评测集里的真实集, 拿它训练等于作弊。
%
%   依赖: Image Processing Toolbox(+ Computer Vision Toolbox 用于 insertText 渲染字体)

p = inputParser;
p.addRequired('outRoot');
p.addParameter('RealRoot', '', @ischar);
p.addParameter('SynPerClass', 250, @isscalar);
p.addParameter('MaxPlates', inf, @isscalar);
p.addParameter('DoReal', true, @islogical);
p.addParameter('DoSyn', true, @islogical);
p.addParameter('Seed', 20260917, @isscalar);
p.addParameter('FontLatin',   {'Arial', 'SimHei', 'Consolas'}, @iscell);
p.addParameter('FontChinese', {'SimHei', 'Microsoft YaHei', 'SimSun'}, @iscell);
p.parse(outRoot, varargin{:});
opt = p.Results;

here = fileparts(mfilename('fullpath'));
if isempty(opt.RealRoot)
    opt.RealRoot = fullfile(here, '..', '..', '测试数据', 'ccpd_train', 'labels.csv');
end
if ~isempty(strfind(opt.RealRoot, [filesep 'ccpd' filesep 'labels.csv'])) %#ok<STREMP>
    error('make_char_data:leak', [ ...
        'RealRoot 指向了评测集 测试数据/ccpd/labels.csv。' ...
        '那是七套评测集里的真实集, 拿它训练会让后续所有 CCPD 指标失效。']);
end

rng(opt.Seed);
if exist(outRoot, 'dir') ~= 7, mkdir(outRoot); end
if opt.DoReal, makeReal(outRoot, opt); end
if opt.DoSyn,  makeSyn(outRoot, opt);  end
printSummary(outRoot);
end

% ======================== 真实域 ========================

function makeReal(outRoot, opt)
fprintf('[真实域] %s\n', opt.RealRoot);
if exist(opt.RealRoot, 'file') ~= 2
    fprintf('    找不到 %s, 跳过真实域\n', opt.RealRoot);
    return;
end
T = readtable(opt.RealRoot, 'Encoding', 'UTF-8', 'VariableNamingRule', 'preserve');
dataDir = fileparts(opt.RealRoot);

n = min(height(T), opt.MaxPlates);
nPlate = 0; nCell = 0; nEmpty = 0;
for k = 1:n
    text = char(T.text{k});
    len  = numel(text);
    quad = [T.qx1(k) T.qy1(k); T.qx2(k) T.qy2(k); T.qx3(k) T.qy3(k); T.qx4(k) T.qy4(k)];
    if len < 6 || ~validQuad(quad), continue; end
    fp = fullfile(dataDir, char(T.file{k}));
    if exist(fp, 'file') ~= 2, continue; end

    I = imread(fp);
    if ismatrix(I), I = repmat(I, 1, 1, 3); end
    plate = warpCanonical(rgb2gray(I), quad, len);

    cells = slotChars(plate, len, 'Jitter', 0);   % 真值槽位不抖: 真实样本自带检测框误差
    nPlate = nPlate + 1;
    ed = slotEdges(len);
    Wp = size(plate, 2);
    for s = 1:len
        ch = text(s);
        if isempty(findClass(ch)), continue; end
        % 空槽判据要用**拉伸前**的原始像素: 拉伸会把空格子里的噪声放大到满量程
        a = min(Wp, max(1, floor(ed(s) * Wp) + 1));
        b = min(Wp, max(a + 2, ceil(ed(s + 1) * Wp)));
        raw = plate(:, a:b);
        if (quantile(raw(:), 0.95) - quantile(raw(:), 0.05)) < 0.06
            nEmpty = nEmpty + 1; continue;
        end
        d = fullfile(outRoot, 'real', ch);
        if exist(d, 'dir') ~= 7, mkdir(d); end
        imwrite(uint8(255 * cells{s}), fullfile(d, sprintf('p%04d_s%d.png', k, s)));
        nCell = nCell + 1;
    end
end
fprintf('    车牌 %d 张 -> 字符样本 %d 个 (空槽/无墨迹跳过 %d)\n', nPlate, nCell, nEmpty);
end

% ======================== 合成域 ========================

function makeSyn(outRoot, opt)
fprintf('[合成域] 每类 %d 个样本\n', opt.SynPerClass);
fprintf('    渲染字体: 字母/数字 %s ; 汉字 %s\n', ...
        strjoin(opt.FontLatin, '/'), strjoin(opt.FontChinese, '/'));

classes = allClasses();
n = numel(classes);

% 每个字符渲染**多套字体**的字形(高度 200 的逻辑图), 之后每个样本只是缩放+贴图。
% 一套字体渲染出来的模型会把"字体轮廓"当成判据, 换一套就崩(实测见文件头 ②)。
glyph = cell(1, n);
nVar  = 0;
for i = 1:n
    ch = classes{i};
    if double(ch(1)) > 127, fonts = opt.FontChinese; else, fonts = opt.FontLatin; end
    v = cell(1, numel(fonts));
    for f = 1:numel(fonts)
        v{f} = renderGlyph(ch, fonts{f});
    end
    ok = ~cellfun(@(g) isempty(g) || ~any(g(:)), v);
    if ~any(ok)
        warning('make_char_data:glyph', '字符 %s 在所有字体下都渲染为空, 已跳过', ch);
        glyph{i} = {};
        continue;
    end
    glyph{i} = v(ok);
    if nVar == 0, nVar = numel(glyph{i}); end
    nVar = min(nVar, numel(glyph{i}));
end
fprintf('    每类可用字体变体数: %d\n', nVar);

cnt = zeros(1, n);
for i = 1:n
    if isempty(glyph{i}), continue; end
    target = classes{i};
    for t = 1:opt.SynPerClass
        cellImg = synSample(glyph, classes, i);
        if isempty(cellImg), continue; end
        d = fullfile(outRoot, 'syn', target);
        if exist(d, 'dir') ~= 7, mkdir(d); end
        imwrite(uint8(255 * cellImg), fullfile(d, sprintf('s%05d.png', t)));
        cnt(i) = cnt(i) + 1;
    end
end
fprintf('    写出 %d 个字符类, 共 %d 个样本\n', nnz(cnt > 0), sum(cnt));
end

function cellImg = synSample(glyph, classes, targetIdx)
%SYNSAMPLE 随机排一块"像车牌"的图, 再走 slotChars 把目标格切出来
%   关键点: 合成样本不是"把字贴在格子中央", 而是**先排整块车牌再按槽位切**,
%   走的是和推理完全相同的 slotChars 路径。这样训练/推理两边看到的预处理
%   (极性统一、逐格拉伸、缩放) 严格一致, 不会出现"训练好看推理难看"。
cellImg = [];
n = numel(classes);

len = 7 + (rand < 0.25);                    % 25% 排成新能源 8 位牌
aspect = 440/140; if len == 8, aspect = 480/140; end

Hp = round(48 + rand * 132);                % 车牌高 48~180 px -> 覆盖分辨率档位
Wp = max(24, round(Hp * aspect));

% 底色/字色(按亮度): 蓝牌=暗底亮字; 绿牌/黄牌/白底警用牌=亮底暗字
style = randi(4);
switch style
    case 1, bg = 0.10 + 0.18 * rand;  fg = 0.72 + 0.26 * rand;   % 蓝底白字
    case 2, bg = 0.32 + 0.22 * rand;  fg = 0.05 + 0.16 * rand;   % 绿底黑字
    case 3, bg = 0.72 + 0.24 * rand;  fg = 0.03 + 0.14 * rand;   % 黄底黑字
    otherwise, bg = 0.80 + 0.18 * rand; fg = 0.03 + 0.16 * rand; % 白底黑字
end

plate = bg * ones(Hp, Wp);
% 排版基准是 CCPD 统计出来的真实号牌槽位, 但**整体**再小幅随机伸缩 + 平移。
% 不同来源的车牌排版并不一致(自产合成集是等宽格子, 真实号牌格子略宽、起点更靠左,
% 中心差 1~3% 牌宽), 训练时把这个差异随机化, CNN 才不会去记"字一定在格子正中"。
% 注意幅度要克制: 综合位移一旦超过半个格子, 训练信号就变成噪声, 实测会明显掉点。
e = slotEdges(len);
c  = (e(1) + e(end)) / 2;
e  = c + (e - c) * (0.97 + 0.06 * rand);       % 整体伸缩 ±3%
e  = e + (rand - 0.5) * 0.03;                  % 整体平移 ±1.5% 牌宽

% 整块牌用同一套字形变体(真实号牌一块牌子只有一种字体)
v = randi(numel(glyph{targetIdx}));
slot = slotForClass(classes{targetIdx}, len);

for k = 1:len
    if k == slot, ci = targetIdx; else, ci = fillerIdx(classes, len, k); end
    gv = glyph{ci};
    if isempty(gv), continue; end
    g = gv{min(v, numel(gv))};
    if isempty(g) || ~any(g(:)), continue; end

    a = max(1, floor(e(k) * Wp) + 1);
    b = min(Wp, max(a + 2, ceil(e(k + 1) * Wp)));
    cellW = b - a + 1;

    gh = max(6, round(Hp * (0.58 + 0.12 * rand)));           % 字高 ≈ 牌高的 0.58~0.70
    gw = max(4, round(gh * size(g, 2) / size(g, 1)));
    if gw > cellW * 0.95                                     % 别让字压到隔壁格
        gw = max(4, round(cellW * 0.95));
        gh = max(6, round(gw * size(g, 1) / size(g, 2)));
    end
    gm = imresize(double(g), [gh gw], 'bilinear');

    dy = round((Hp - gh) / 2 + (rand - 0.5) * 0.10 * Hp);
    dx = round((a + b - 1 - gw) / 2 + (rand - 0.5) * 0.10 * cellW);
    plate = pasteMask(plate, gm, dy, dx, fg);
end

plate = degrade(plate, Hp);
cells = slotChars(plate, len, 'Jitter', 0.05);
cellImg = cells{slot};
end

function ci = fillerIdx(classes, len, k)
%FILLERIDX 给非目标槽位随机挑一个**该位置合法**的字符
%   必须按制式来: 第 1 位只能是省级简称, 第 2 位只能是大写字母, 第 3 位起才是
%   字母/数字。早期版本在这里随便从 70 类里抽, 等于让 CNN 看到"数字位出现汉字"
%   这种车牌上不可能出现的画面, 位置先验被搅乱, 首位汉字错得尤其厉害。
F = plateFormat(len);
allowed = '';
if isstruct(F) && isfield(F, 'sets') && k <= numel(F.sets)
    allowed = F.sets{k};
end
if isempty(allowed)
    allowed = 'ABCDEFGHJKLMNPQRSTUVWXYZ0123456789';
end
cand = [];
for i = 1:numel(classes)
    if numel(classes{i}) == 1 && any(classes{i} == allowed)
        cand(end + 1) = i; %#ok<AGROW>
    end
end
if isempty(cand), cand = 1:numel(classes); end
ci = cand(randi(numel(cand)));
end

function s = slotForClass(ch, len)
%SLOTFORCLASS 该字符在车牌上允许出现的槽位(随机取一个合法的)
if any(ch == '京津冀晋蒙辽吉黑沪苏浙皖闽赣鲁豫鄂湘粤桂琼渝川贵云藏陕甘青宁新')
    s = 1;
elseif any(ch == '警挂学领使')
    s = len;
else
    s = 1 + randi(max(1, len - 1));        % 2..len
end
end

function img = pasteMask(img, gm, dy, dx, fg)
%PASTEMASK 把字形灰度(0~1)按 fg 混进车牌图
[H, W] = size(img);
[gh, gw] = size(gm);
r0 = dy + 1; c0 = dx + 1;
r1 = r0 + gh - 1; c1 = c0 + gw - 1;
if r1 < 1 || c1 < 1 || r0 > H || c0 > W, return; end
rr0 = max(1, r0); cc0 = max(1, c0);
rr1 = min(H, r1); cc1 = min(W, c1);
sub = gm((rr0 - r0 + 1):(rr1 - r0 + 1), (cc0 - c0 + 1):(cc1 - c0 + 1));
region = img(rr0:rr1, cc0:cc1);
img(rr0:rr1, cc0:cc1) = region .* (1 - sub) + fg * sub;
end

function img = degrade(img, Hp)
%DEGRADE 退化增广: 模糊 / 运动模糊 / 噪声 / 光照梯度 / 对比度 / 旋转 / 缩放 / JPEG
%   系数按"车牌像素高"缩放, 这样小目标自然就更糊 —— 与真实相机一致。
sc = max(0.35, Hp / 140);

if rand < 0.75                                    % 离焦/运动模糊
    sigma = rand * 1.9 * sc;
    if rand < 0.35
        mlen = max(3, round((3 + 9 * rand) * sc));
        h = fspecial('motion', mlen, rand * 180);
    else
        h = fspecial('gaussian', max(3, 2 * ceil(1.5 * sigma) + 1), max(0.2, sigma));
    end
    img = imfilter(img, h, 'replicate');
end

if rand < 0.14                                    % 上下采样来回一次: 模拟缩放重采样
    s = 0.5 + 0.5 * rand;
    img = imresize(imresize(img, s, 'bilinear'), size(img), 'bilinear');
end

if rand < 0.55                                    % 光照梯度(单侧过曝是实拍最常见的退化)
    [H, W] = size(img);
    amp  = 0.10 + 0.45 * rand;
    ramp = repmat(linspace(-1, 1, W) * (2 * rand - 1), H, 1);
    img  = img + amp * ramp * (0.5 + 0.5 * rand);
end

if rand < 0.35                                    % 对比度 / gamma
    img = max(0, min(1, img)) .^ (0.5 + 1.4 * rand);
    img = (img - 0.5) * (0.55 + 0.9 * rand) + 0.5 + (rand - 0.5) * 0.2;
end

if rand < 0.55                                    % 噪声
    img = img + (0.01 + 0.05 * rand) * randn(size(img));
    if rand < 0.25
        img = imnoise(max(0, min(1, img)), 'salt & pepper', 0.01 * rand);
    end
end

if rand < 0.45                                    % 残余旋转(定位框/校正不完美)
    img = imrotate(img, (rand - 0.5) * 8, 'bilinear', 'crop');
end

img = max(0, min(1, img));

if rand < 0.25                                    % JPEG 压缩痕迹
    tmp = [tempname '.jpg'];
    imwrite(uint8(255 * img), tmp, 'jpg', 'Quality', round(25 + 60 * rand));
    img = im2double(imread(tmp));
    delete(tmp);
end
end

% ======================== 渲染与字符表 ========================

function g = renderGlyph(ch, fontName)
%RENDERGLYPH 用指定字体渲染单字符, 裁到墨迹外接框后统一到高 200
%   做法与 buildTemplates.renderChar 一致(insertText + 裁剪), 保证合成样本
%   与模板库的字符形态同源。
isCJK = double(ch(1)) > 127;
if isCJK, fs = 150; else, fs = 190; end
canvas = zeros(600, 600, 'uint8');
try
    out = insertText(canvas, [300 300], ch, 'FontSize', fs, 'BoxOpacity', 0, ...
                     'TextColor', 'white', 'AnchorPoint', 'Center', 'Font', fontName);
catch
    return;                      % 该字体不可用 -> 返回空, 由调用方过滤
end
if isempty(out), return; end
if ndims(out) == 3, out = rgb2gray(out); end
bw = double(reshape(out, size(out, 1), size(out, 2))) / 255;
[r, c] = find(bw > 0.5);
if isempty(r), g = false(0, 0); return; end
g = bw(min(r):max(r), min(c):max(c));
if isempty(g), return; end
g = imresize(g, [200, max(1, round(200 * size(g, 2) / size(g, 1)))], 'bilinear');
end

function c = allClasses()
%ALLCLASSES 车牌字符全集: 31 省简称 + 24 字母(去 IO) + 10 数字 + 5 制式后缀字
prov = '京津冀晋蒙辽吉黑沪苏浙皖闽赣鲁豫鄂湘粤桂琼渝川贵云藏陕甘青宁新';
letters = 'ABCDEFGHJKLMNPQRSTUVWXYZ';
digits = '0123456789';
special = '警挂学领使';
c = cell(1, 0);
for s = {prov, letters, digits, special}
    v = s{1};
    for i = 1:numel(v), c{end+1} = v(i); end %#ok<AGROW>
end
end

function ch = findClass(c)
%FINDCLASS 该字符是否在字符表里(不在就丢掉, 免得训练出多余类别)
if double(c) > 127
    ok = any(c == '京津冀晋蒙辽吉黑沪苏浙皖闽赣鲁豫鄂湘粤桂琼渝川贵云藏陕甘青宁新警挂学领使');
else
    ok = any(c == 'ABCDEFGHJKLMNPQRSTUVWXYZ0123456789');
end
if ok, ch = c; else, ch = ''; end
end

% ======================== 工具 ========================

function plate = warpCanonical(I, q, len)
%WARPCANONICAL 用四角点拉正, 目标宽为**规范宽高比**对应的宽
%   实测四角点在图像里的宽高比 = 车牌被透视压缩后的投影比例, 不是车牌本身的比例。
%   拿它当目标矩形等于把透视压缩留在结果里(第十轮 A/B: 位数切对率 53.1% -> 59.7%)。
%   号牌尺寸有国标: 蓝/黄/白底 440x140, 小型新能源绿牌 480x140。
if len == 8, aspect = 480/140; else, aspect = 440/140; end
outH = 64;
outW = max(24, min(8 * outH, round(aspect * outH)));
tform = fitgeotrans(q, [1 1; outW 1; outW outH; 1 outH], 'projective');
plate = imwarp(I, tform, 'OutputView', imref2d([outH, outW]), ...
               'InterpolationMethod', 'bilinear', 'FillValues', 0);
end

function tf = validQuad(q)
tf = false;
if any(~isfinite(q(:))), return; end
if min(pdist(q)) < 2, return; end
if abs(polyarea(q(:, 1), q(:, 2))) < 100, return; end
tf = true;
end

function printSummary(outRoot)
L = {};
for dom = {'real', 'syn'}
    L{end+1} = sprintf('[%s]', dom{1});
    tot = 0; ncls = 0;
    for ch = allClasses()
        d = fullfile(outRoot, dom{1}, ch{1});
        if exist(d, 'dir') ~= 7, continue; end
        f = dir(fullfile(d, '*.png'));
        if isempty(f), continue; end
        ncls = ncls + 1; tot = tot + numel(f);
        L{end+1} = sprintf('    %s  %4d', ch{1}, numel(f));
    end
    L{end+1} = sprintf('    类别 %d, 样本 %d', ncls, tot);
end
fid = fopen(fullfile(outRoot, 'summary.txt'), 'w', 'n', 'UTF-8');
for i = 1:numel(L), fprintf(fid, '%s\n', L{i}); end
fclose(fid);
fprintf('汇总已写: %s\n', fullfile(outRoot, 'summary.txt'));
end
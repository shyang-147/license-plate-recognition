function worker_lpr(jobsDir, projDir)
%WORKER_LPR 车牌识别常驻工作进程(被 server/server.py 网站后端调用)
%
%   手动启动:  matlab -batch "cd('<server 目录>'); worker_lpr"
%   通常由 server.py 自动拉起, 不用手动运行。
%
%   工作方式: 轮询 jobs/incoming 下的图片 -> 调 lpr_main 识别 ->
%             把结果写成 JSON 到 jobs/results/<id>.json, 再删掉输入文件。
%   JSON 里带着车牌图/字符图的 base64, 前端可以直接显示。
%   退出: 在 jobs 目录下放一个名为 stop 的空文件。

if nargin < 1 || isempty(jobsDir)
    jobsDir = fullfile(fileparts(mfilename('fullpath')), 'jobs');
end
if nargin < 2 || isempty(projDir)
    projDir = defaultProjDir(fileparts(mfilename('fullpath')));
end

inDir   = fullfile(jobsDir, 'incoming');
resDir  = fullfile(jobsDir, 'results');
for d = {inDir, resDir}
    if exist(d{1}, 'dir') ~= 7, mkdir(d{1}); end
end
statusFile = fullfile(jobsDir, 'status.json');
stopFile   = fullfile(jobsDir, 'stop');

addpath(genpath(projDir));
if exist('lpr_main', 'file') ~= 2
    error('worker_lpr:noProject', '找不到 lpr_main.m, 请检查工程目录: %s', projDir);
end

fprintf('[worker] 就绪 | 工程目录 %s\n', projDir);
fprintf('[worker] 监听中 %s\n', inDir);
writeStatus(statusFile, 'idle', '', '');

lastBeat = 0;
while true
    if exist(stopFile, 'file') == 2
        delete(stopFile);
        writeStatus(statusFile, 'stopped', '', '');
        fprintf('[worker] 收到 stop 信号, 退出\n');
        break;
    end

    d = [dir(fullfile(inDir, '*.jpg'));  dir(fullfile(inDir, '*.jpeg')); ...
         dir(fullfile(inDir, '*.png'));  dir(fullfile(inDir, '*.bmp')); ...
         dir(fullfile(inDir, '*.tif'));  dir(fullfile(inDir, '*.tiff'))];
    d = d(~[d.isdir]);

    if isempty(d)
        pause(0.15);
        if now - lastBeat > 2 / 86400
            writeStatus(statusFile, 'idle', '', '');
            lastBeat = now;
        end
        continue;
    end

    [~, k]  = min([d.datenum]);          % 先到先处理
    inFile  = fullfile(inDir, d(k).name);
    [~, id] = fileparts(d(k).name);
    fprintf('[worker] 处理 %s\n', d(k).name);
    writeStatus(statusFile, 'busy', id, '');

    t0  = tic;
    out = struct();
    out.id = id;
    try
        r = lpr_main(inFile, 'ShowFigure', false);
        out.ok       = true;
        out.text     = r.text;
        out.chars    = r.chars;
        out.scores   = r.scores;
        out.plateBox = r.plateBox;
        out.nChars   = numel(r.chars);
        out.plateImagePng = img2b64(upscalePlate(r.plateImage));
        out.details  = buildDetails(r.chars, r.scores, r.charImages);
        out.warning  = '';
        out.reason   = '';
        out.quality  = qualityOf(r);
        if isempty(r.text)
            out.reason = r.reason;
            if isempty(out.reason)
                out.warning = '未检测到车牌, 请换一张车牌更清晰、更居中的照片';
            else
                out.warning = ['未检测到车牌: ' out.reason '。建议换一张车牌更清晰、更居中的照片'];
            end
        end
    catch err
        out.ok      = false;
        out.text    = '';
        out.chars   = {};
        out.scores  = [];
        out.details = {};
        out.warning = '';
        out.reason  = '';
        out.quality = qualityOf([]);
        out.error   = err.message;
    end
    out.elapsed = toc(t0);

    tmpF = fullfile(resDir, [id '.json.tmp']);
    outF = fullfile(resDir, [id '.json']);
    fid  = fopen(tmpF, 'w', 'n', 'UTF-8');
    if fid < 0
        warning('worker_lpr:writeFail', '无法写入 %s', tmpF);
    else
        fwrite(fid, jsonencode(out), 'char');
        fclose(fid);
        movefile(tmpF, outF, 'f');       % 原子替换: 服务器见到 .json 即完成
    end
    delete(inFile);
    fprintf('[worker] 完成 %s -> %s (%.2f s)\n', id, out.text, out.elapsed);
    writeStatus(statusFile, 'idle', '', out.text);
    lastBeat = now;
end
end

% ======================== 局部函数 ========================

function writeStatus(f, state, jobId, lastText)
pid = 0;
try, pid = feature('getpid'); catch, end
s = struct('state', state, 'job', jobId, 'lastText', lastText, ...
           'time', posixtime(datetime('now', 'TimeZone', 'local')), 'pid', pid);
tmp = [f '.tmp'];
fid = fopen(tmp, 'w', 'n', 'UTF-8');
if fid >= 0
    fwrite(fid, jsonencode(s), 'char');
    fclose(fid);
    movefile(tmp, f, 'f');
end
end

function D = buildDetails(chars, scores, charImages)
%BUILDDETAILS 每个字符: 识别结果 + 置信度 + 二值图(base64 PNG)
D = cell(1, numel(chars));
for k = 1:numel(chars)
    e = struct();
    e.char  = chars{k};
    if k <= numel(scores), e.score = scores(k); else, e.score = 0; end
    e.png = '';
    if k <= numel(charImages) && ~isempty(charImages{k})
        big = imresize(charImages{k}, 6, 'nearest');
        rgb = repmat(uint8(255) * uint8(big), 1, 1, 3);
        e.png = img2b64(rgb);
    end
    D{k} = e;
end
end

function big = upscalePlate(img)
%UPSCALEPLATE 网页上要把车牌图放大显示, 先在 MATLAB 里双三次放大, 比浏览器拉伸清晰
big = img;
if isempty(img) || size(img, 2) >= 500, return; end
big = imresize(img, 600 / size(img, 2), 'bicubic');
end

function q = qualityOf(r)
%QUALITYOF 定位质量指标(矩形度/底色占比/长宽比), 前端可用来判断结果是否可靠
q = struct('score', 0, 'extent', 0, 'colorFrac', 0, 'aspect', 0, 'source', '');
if isempty(r) || ~isfield(r, 'scoreInfo'), return; end
s = r.scoreInfo;
for fn = {'score', 'extent', 'colorFrac', 'aspect'}
    if isfield(s, fn{1}) && ~isempty(s.(fn{1})), q.(fn{1}) = double(s.(fn{1})); end
end
if isfield(s, 'source'), q.source = s.source; end
end

function b64 = img2b64(img)
%IMG2B64 图像 -> base64(PNG), 用于塞进 JSON 给网页显示
b64 = '';
if isempty(img), return; end
tmp = [tempname '.png'];
try
    imwrite(img, tmp);
    fid = fopen(tmp, 'r');
    raw = fread(fid, inf, '*uint8');
    fclose(fid);
    delete(tmp);
    b64 = char(matlab.net.base64encode(raw));
catch
    b64 = '';
end
end


function d = defaultProjDir(here)
%DEFAULTPROJDIR 定位 MATLAB 识别内核所在目录 (兼容 matlab/ 与 lpr_matlab/ 两种目录名)
cands = {fullfile(here, '..', 'matlab'), fullfile(here, '..', 'lpr_matlab')};
for k = 1:numel(cands)
    if exist(fullfile(cands{k}, 'lpr_main.m'), 'file') == 2
        d = cands{k};
        return
    end
end
d = cands{1};
end

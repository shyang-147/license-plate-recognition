function S = bench_eval(benchDir, verbose)
%BENCH_EVAL 在带标注的图片集上评估识别率(供 bench_run.m / verify_lpr.m 调用)
%
%   S = BENCH_EVAL()                     % 默认用工程自带 bench/
%   S = BENCH_EVAL(benchDir, false)      % 不打印明细
%
%   benchDir 目录下需有 labels.csv, 列为 file,text (文件名 + 正确车牌号)
%   返回结构体: n / plateOk / plateAcc / charOk / charTotal / charAcc
%               cnOk / cnTotal / cnAcc / segOk / segAcc / timeAvg / got / expect

if nargin < 1 || isempty(benchDir)
    benchDir = fullfile(fileparts(mfilename('fullpath')), 'bench');
end
if nargin < 2 || isempty(verbose), verbose = true; end

csvFile = fullfile(benchDir, 'labels.csv');
if exist(csvFile, 'file') ~= 2
    error('bench_eval:noLabels', '找不到标注文件: %s', csvFile);
end
T = readtable(csvFile, 'Encoding', 'UTF-8', 'VariableNamingRule', 'preserve');

if verbose
    fprintf('%-14s %-10s %-10s %-6s %-8s %s\n', '文件', '期望', '识别', '字符', '置信度', '结果');
end

n = height(T);
S = struct();
S.n = n;
S.plateOk = 0; S.segOk = 0; S.charOk = 0; S.charTotal = 0;
S.cnOk = 0; S.cnTotal = 0; S.timeTotal = 0;
S.got = cell(n, 1); S.expect = cell(n, 1);

for i = 1:n
    f  = T.file{i};
    gt = char(T.text{i});
    fp = fullfile(benchDir, f);
    t0 = tic;
    if exist(fp, 'file') == 2
        r = lpr_main(fp, 'ShowFigure', false);
    else
        warning('bench_eval:missingImage', '缺少图片: %s', f);
        r = struct('text', '', 'chars', {{}}, 'scores', []);
    end
    S.timeTotal = S.timeTotal + toc(t0);
    got = r.text;
    S.got{i} = got; S.expect{i} = gt;

    S.segOk  = S.segOk  + (numel(r.chars) == numel(gt));
    k        = min(numel(got), numel(gt));
    S.charTotal = S.charTotal + numel(gt);
    S.charOk    = S.charOk + sum(got(1:k) == gt(1:k));
    if ~isempty(gt)
        S.cnTotal = S.cnTotal + 1;
        S.cnOk    = S.cnOk + (k >= 1 && got(1) == gt(1));
    end
    ok = strcmp(got, gt);
    S.plateOk = S.plateOk + ok;

    if verbose
        if isempty(r.scores), ms = NaN; else, ms = mean(r.scores); end
        if ok, tag = '正确'; else, tag = '错误'; end
        fprintf('%-14s %-10s %-10s %d/%d    %.2f     %s\n', ...
                f, gt, got, numel(r.chars), numel(gt), ms, tag);
    end
end

S.plateAcc = S.plateOk / max(1, n);
S.charAcc  = S.charOk / max(1, S.charTotal);
S.cnAcc    = S.cnOk   / max(1, S.cnTotal);
S.segAcc   = S.segOk  / max(1, n);
S.timeAvg  = S.timeTotal / max(1, n);

if verbose
    fprintf('\n================ 统计 ================\n');
    fprintf('整牌准确率      : %d/%d = %.1f%%\n', S.plateOk, n, 100 * S.plateAcc);
    fprintf('字符准确率      : %d/%d = %.1f%%\n', S.charOk, S.charTotal, 100 * S.charAcc);
    fprintf('首位汉字准确率  : %d/%d = %.1f%%\n', S.cnOk, S.cnTotal, 100 * S.cnAcc);
    fprintf('字符数分割正确  : %d/%d = %.1f%%\n', S.segOk, n, 100 * S.segAcc);
    fprintf('平均单张耗时    : %.2f s\n', S.timeAvg);
end
end
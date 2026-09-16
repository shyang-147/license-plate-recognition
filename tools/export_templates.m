function export_templates(templateFile, outFile)
%EXPORT_TEMPLATES 把 MATLAB 的 templates.mat 导出成网页版用的 templates.json
%
%   export_templates()   % ../matlab/templates.mat -> ../site/src/templates.json
%
%   模板的唯一来源是 MATLAB 的 templates.mat; 网页版只读导出结果, 两边必须一致。
%   所以换字体、加字符(如制式后缀"警")之后要重跑本脚本, 再跑
%   site/src/build_site.py 重新打包 index.html。
%
%   特征按列优先展开成 288 个字节(0~255), 与 lpr_engine.js 里 prepSet 的读法一致
%   (raw[k]/255), 也和 charFeature 的 f(:) 顺序一致。

here = fileparts(mfilename('fullpath'));
if nargin < 1 || isempty(templateFile)
    templateFile = fullfile(here, '..', 'matlab', 'templates.mat');
end
if nargin < 2 || isempty(outFile)
    outFile = fullfile(here, '..', 'site', 'src', 'templates.json');
end

T = load(templateFile);
outH = 24;  outW = 12;                      % charFeature 的默认输出尺寸
groups = {'chinese', 'letters', 'digits', 'special'};
parts = {}; counts = [];
for g = 1:numel(groups)
    if ~isfield(T, groups{g}), continue; end
    S = T.(groups{g});
    n = numel(S.feature);
    if n ~= numel(S.label)
        error('export_templates:badSet', '%s 的标签数与特征数不一致', groups{g});
    end
    feat = cell(1, n);
    for k = 1:n
        f = S.feature{k}(:);                % 列优先展开
        if numel(f) ~= outH * outW
            error('export_templates:badFeature', '%s/%s 特征长度是 %d, 期望 %d', ...
                  groups{g}, S.label{k}, numel(f), outH * outW);
        end
        b = uint8(round(min(max(f, 0), 1) * 255));
        feat{k} = matlab.net.base64encode(b);
    end
    parts{end + 1} = sprintf('"%s":{"labels":[%s],"feat":[%s]}', groups{g}, ... %#ok<AGROW>
        strjoin(cellfun(@(s) ['"' s '"'], S.label, 'UniformOutput', false), ','), ...
        strjoin(cellfun(@(s) ['"' s '"'], feat,    'UniformOutput', false), ','));
    counts(end + 1) = n; %#ok<AGROW>
end

json = sprintf('{"outH":%d,"outW":%d,"counts":[%s],%s}', outH, outW, ...
               strjoin(arrayfun(@(c) sprintf('%d', c), counts, 'UniformOutput', false), ','), ...
               strjoin(parts, ','));
if contains(json, '</script')
    error('export_templates:scriptTag', '模板里出现 </script, 不能内嵌进 HTML');
end

fid = fopen(outFile, 'w', 'n', 'UTF-8');
if fid < 0, error('export_templates:openFail', '打不开 %s', outFile); end
fwrite(fid, json, 'char');
fclose(fid);
fprintf('已导出 %s (%.1f KB): %s\n', outFile, numel(json) / 1024, ...
        strjoin(arrayfun(@(c) sprintf('%d', c), counts, 'UniformOutput', false), '/'));
end
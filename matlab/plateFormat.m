function F = plateFormat(n)
%PLATEFORMAT 中国车牌制式: 字符位数与每一位允许出现的字符集
%
%   F = PLATEFORMAT(n)     n = 分割出的字符数(通常 7 或 8)
%
%   返回结构:
%       .n       字符位数
%       .label   制式名称(给界面/日志用)
%       .sets    1xN cell, 每一位允许出现的字符(字符串形式)
%
%   依据(公安部 GA 36-2018):
%       普通汽车(蓝底/黄底单排)  7 位 = 省简称 + 发牌机关字母 + 5 位字母数字
%       新能源小型车(绿底单排)   8 位 = 省简称 + 发牌机关字母 + 字母 + 5 位字母数字
%   字母表里不含 I 和 O —— 车牌不使用这两个字母(易与 1、0 混淆)。
%
%   这个表有两个用处:
%     1) recognizeChars 按位约束候选模板, 减少"数字位认成字母"这类错;
%     2) 结果校验: 认出来的字符不在该位允许集合里时, 说明这一位可疑。

prov    = '京津冀晋蒙辽吉黑沪苏浙皖闽赣鲁豫鄂湘粤桂琼渝川贵云藏陕甘青宁新';
letters = 'ABCDEFGHJKLMNPQRSTUVWXYZ';           % 去掉 I O
alnum   = [letters, '0123456789'];

F = struct('n', n, 'label', '', 'sets', {{}});
switch n
    case 8
        F.label = '新能源 8 位';
        F.sets  = {prov, letters, letters, alnum, alnum, alnum, alnum, alnum};
    case 7
        F.label = '普通 7 位';
        F.sets  = {prov, letters, alnum, alnum, alnum, alnum, alnum};
    otherwise
        % 没收录的位数(双排黄牌切错、图片有问题等): 只给个宽松规则, 不硬套
        F.label = sprintf('%d 位(未收录制式)', n);
        F.sets  = cell(1, n);
        if n >= 1, F.sets{1} = prov;    end
        if n >= 2, F.sets{2} = letters; end
        for k = 3:n, F.sets{k} = alnum; end
end
end

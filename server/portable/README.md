# 单文件版车牌识别网站 (portable)

**拷一个文件就能跑。** 这个目录里的 `lpr_server.py` 把网页、MATLAB 识别内核、worker
全部打包在一起（186 KB），运行时自动释放到临时目录，所以不会出现"少了个 .py / .m"的问题。

| 文件 | 说明 |
|---|---|
| `lpr_server.py` | 全部东西都在这里面（网页 + 识别内核 + worker） |
| `start_lan.bat` | 局域网模式：手机和其他电脑连同一个 WiFi 就能打开 |
| `start_public.bat` | 公网模式：自动开 cloudflared 隧道 + 访问口令，手机用 4G 也能打开 |

## 用法

1. **`lpr_server.py` 和 `.bat` 必须放在同一个文件夹里**，双击 `start_lan.bat`。
2. 窗口里会打印出局域网地址，例如：

```
 单文件版车牌识别网站
   本机地址 : http://127.0.0.1:8765
   局域网   : http://192.168.1.23:8765
   ^ 手机/其他电脑连同一个 WiFi, 浏览器里输入上面这个地址
```

3. 手机浏览器输入上面那个**局域网**地址即可（手机不用装任何东西，MATLAB 只在这台电脑上跑）。
4. 关掉：在窗口里按 `Ctrl+C`，或者直接关窗口。

> 如果窗口一闪就没了，或者提示 `lpr_server.py is NOT in this folder`，
> 说明两个文件没有放在一起 —— 把 `lpr_server.py` 和 `start_lan.bat` 放到同一个目录再双击。

## 公网模式（不在同一个 WiFi 也能用）

双击 `start_public.bat`，它会：
1. 随机生成一个**访问口令**（每次运行都不一样）；
2. 需要时自动下载 `cloudflared`（大约 40MB，只下载一次）；
3. 开一条临时隧道，并打印一个 `https://xxxx.trycloudflare.com` 的公网地址；
4. 页面打开时会要求输入口令，口令不对什么都看不到。

手机用 4G、或者在外地，打开那个 `https://xxxx.trycloudflare.com` 地址就能用了。
**关掉窗口隧道立刻失效**，不会一直暴露在公网上。

> 说明：`trycloudflare.com` 是 Cloudflare 提供的免费临时隧道，地址每次运行都会变。
> 想要固定域名的正式部署，见上级 README 的"部署"一节。

## 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 提示 `lpr_server.py is NOT in this folder` | 两个文件没放在一起，见上面 |
| 提示 `Python not found` | 装 Python 3.8+（python.org），安装时勾选 Add python.exe to PATH |
| 手机打不开局域网地址 | 手机和电脑要在同一个 WiFi；校园网/公共 WiFi 可能开了"客户端隔离"，改用手机热点或公网模式；也检查 Windows 防火墙 |
| 第一次识别很慢 | 正常，是在启动 MATLAB（10~20 秒），之后每张 0.2~0.5 秒 |
| 提示未检测到车牌 | 换一张车牌更大更正的照片；算法会给出具体原因（矩形度/底色占比不达标） |
| 想彻底清理 | 关掉窗口后删掉 `%TEMP%\lpr_runtime_xxxx` 目录即可，里面是释放出来的内核和日志 |

## 想改成自己的版本

`lpr_server.py` 是自动生成的，不要直接改。改这两个地方再重新打包：

```bat
:: 1. 改网页 -> server/static/index.html
:: 2. 改识别算法 -> matlab/*.m
python tools/build_portable.py      :: 重新生成 portable/lpr_server.py
```

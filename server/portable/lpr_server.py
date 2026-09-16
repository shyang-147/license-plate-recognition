#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''单文件版车牌识别网站 (lpr_web portable)

浏览器上传车牌照片 -> 本机 MATLAB 实时识别 -> 网页显示车牌号/逐字符置信度/校正后的车牌图.
这一个文件里打包了: 网页 + MATLAB 识别内核 + worker, 运行时自动释放到临时目录,
所以拷这一个文件到任何地方都能跑, 不会出现"少了个 .py / .m"的问题.

用法:
    1. 双击同目录的 start_lan.bat     局域网访问: 手机/其他电脑连同一个 WiFi 就能打开
    2. 双击同目录的 start_public.bat  公网访问: 自动开 cloudflared 隧道(带访问口令), 4G 也能打开
    3. 或者命令行: python lpr_server.py

环境变量:
    LPR_HOST        绑定地址, 默认 0.0.0.0(允许其他设备访问); 设 127.0.0.1 则只允许本机
    LPR_PORT        端口, 默认 8765
    LPR_TOKEN       访问口令, 设了以后打开页面要输入口令(公网使用强烈建议设置)
    LPR_PUBLIC      1 = 启动时自动开 cloudflared 公网隧道(没有 cloudflared 会自动下载)
    LPR_MATLAB      指定 matlab.exe 路径, 默认从 PATH 里找
    LPR_NO_BROWSER  1 = 启动后不自动打开浏览器

识别引擎第一次启动要 10~20 秒(在起 MATLAB), 之后每张 0.2~0.5 秒.
'''

import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ============================ 内嵌资源 ============================
PKG_ID = '36cd274627'
PAYLOAD_B64 = {
    'core/charFeature.m': (
        'ZnVuY3Rpb24gZiA9IGNoYXJGZWF0dXJlKGltZywgb3V0SCwgb3V0VykKJUNIQVJGRUFUVVJFIOaKiuW9kuS4gOWMluWtl+espuWbvumZjemHh+'
        'agt+aIkOWumumVv+eJueW+geihjOWQkemHjyjmipfovbvlvq7plJnkvY0pCiUKJSAgIGYgPSBDSEFSRkVBVFVSRShpbWcpICAgICAgICAtPiAx'
        'IHggMjg4ICAoMjQgeCAxMikKJSAgIGYgPSBDSEFSRkVBVFVSRShpbWcsIDIwLCAxMCkKCmlmIG5hcmdpbiA8IDIgfHwgaXNlbXB0eShvdXRIKS'
        'wgb3V0SCA9IDI0OyBlbmQKaWYgbmFyZ2luIDwgMyB8fCBpc2VtcHR5KG91dFcpLCBvdXRXID0gMTI7IGVuZAoKZiA9IGltcmVzaXplKGRvdWJs'
        'ZShpbWcpLCBbb3V0SCBvdXRXXSwgJ2JpbGluZWFyJyk7CmYgPSBmKDopJzsKZW5k'
    ),
    'core/correctPlate.m': (
        'ZnVuY3Rpb24gW2dyYXksIGNvbG9yLCBpbmZvXSA9IGNvcnJlY3RQbGF0ZShncmF5LCBjb2xvciwgbWFzaykKJUNPUlJFQ1RQTEFURSDovabniY'
        'zlh6DkvZXmoKHmraMo5peL6L2sICsg6YCP6KeGKSArIOWwuuWvuOW9kuS4gOWMlgolCiUgICBbZ3JheSwgY29sb3JdICAgICAgID0gQ09SUkVD'
        'VFBMQVRFKGdyYXksIGNvbG9yLCBtYXNrKQolICAgW2dyYXksIGNvbG9yLCBpbmZvXSA9IENPUlJFQ1RQTEFURShncmF5LCBjb2xvciwgbWFzay'
        'kKJSAgIG1hc2s6IOi9pueJjOaOqeiGnCjnlKjkuo7kvLDorqHovabniYzlm5vop5IpLCDlj6/kuLrnqbogW10KJQolICAg5YGa5rOV5YiG5Lik'
        '57qnOgolICAgICAxKSDpgI/op4bmoKHmraMo5Y+q5Zyo55yf55qE5q2q5oiQ5qKv5b2i5pe25ZCv55SoKTog55So5o6p6Iac5LiK55qEIuaege'
        'WAvOeCuSLlrprkvY3ovabniYzlm5vop5IsCiUgICAgICAgIOWGjeeUqOWNleW6lOWPmOaNouaKiuWug+aLieaIkOefqeW9oiDigJTigJQg5peL'
        '6L2s5ZKM6YCP6KeG5LiA5qyh6Kej5Yaz44CC5pac5ouNKOS+p+WQkeaLjeaRhCnnmoQKJSAgICAgICAg6L2m54mM5LiK5LiL6L655LiN562J6Z'
        'W/LCDlj6rmnInov5nkuIDmraXog73mlZHjgIIKJSAgICAgMikg5Y+q5YGa5peL6L2s5qCh5q2jKOWFtuS9meaDheWGtSk6IOWbm+inkuaOpei/'
        'keefqeW9oijnuq/ml4vovawv57qv5bmz56e7KeaXtiwg5aSa5o+S5YC85LiA5qyhCiUgICAgICAgIOWPjeiAjOabtOeziiwg55u05o6l5rK/55'
        'SoIuaOqeiGnOS4iuS4i+i+ueeVjOaLn+WQiOWAvuinkiAtPiDlj43lkJHml4vovawiOyDlm5vop5LkvLDorqHkuI3lj6/pnaAKJSAgICAgICAg'
        'KOaOqeiGnOaui+e8uuOAgeWbm+inkuS4jeWHuOOAgemVv+WuveavlOS4jeWDj+i9pueJjCnml7bkuZ/otbDov5nmnaHlhZzlupXot6/lvoTjgI'
        'IKJQolICAgaW5mbyDlrZfmrrU6IG1vZGUgIOWHoOS9leagoeato+aWueW8jywgJ3BlcnNwZWN0aXZlJyAvICdyb3RhdGUnIC8gJ25vbmUnCiUg'
        'ICAgICAgICAgICAgIHF1YWQgIOmHh+eUqOeahOi9pueJjOWbm+inkig0eDIsIOW3puS4ii0+5Y+z5LiKLT7lj7PkuIstPuW3puS4iyksIOacqu'
        'eUqOaXtuS4uiBbXQolICAgICAgICAgICAgICBza2V3ICDml4vovazmoKHmraPnmoTop5LluqYo5bqmKSwg6YCP6KeG5qCh5q2j5pe25Li6IDAK'
        'JSAgICAgICAgICAgICAgcmVhc29uIOWbm+inkuS8sOiuoeWksei0peeahOWOn+WboCjkvr/kuo7mjpLmn6UpLCDmiJDlip/ml7bkuLrnqboKJQ'
        'olICAg5qCh5q2j5ZCO57uf5LiA57yp5pS+5Yiw6auY5bqmIDY0IOWDj+e0oCwg5a695bqm5oyJ5qCh5q2j5ZCO55qE6L2m54mM6ZW/5a695q+U'
        '57yp5pS+LAolICAg5L2/5ZCO57ut5Z6C55u05oqV5b2x5YiG5Ymy55qE5Y+C5pWw5Y+v5Lul5YaZ5q2744CCCgp0YXJnZXRIID0gNjQ7CmluZm'
        '8gPSBzdHJ1Y3QoJ21vZGUnLCAnbm9uZScsICdxdWFkJywgW10sICdza2V3JywgMCwgJ3JlYXNvbicsICcnKTsKCiUgLS0tLS0tLS0tLS0tLS0t'
        'LSAxLiDkvJjlhYg6IOWbm+inkiArIOWNleW6lOWPmOaNoijml4vovazkuI7pgI/op4bkuIDotbfmoKHmraMpIC0tLS0tLS0tLS0tLS0tLS0KaW'
        'YgbmFyZ2luID4gMiAmJiB+aXNlbXB0eShtYXNrKSAmJiBhbnkobWFzayg6KSkKICAgIFtxdWFkLCB3aHldID0gZGV0ZWN0UXVhZChtYXNrKTsK'
        'ICAgIGluZm8ucmVhc29uID0gd2h5OwogICAgaWYgfmlzZW1wdHkocXVhZCkgJiYgfmhhc1BlcnNwZWN0aXZlKHF1YWQpCiAgICAgICAgJSDlm5'
        'vop5Lln7rmnKzov5jmmK/kuKrnn6nlvaIo57qv5peL6L2sL+e6r+W5s+enuykgLT4g5rKh5b+F6KaB6YeN6YeH5qC3LCDotbDkuIvpnaLnmoTm'
        'l4vovazmoKHmraMsCiAgICAgICAgJSDnu5PmnpzkuI7ogIHniYjmnKzlrozlhajkuIDoh7QsIOS4jeS8muWboOS4uuWkmuaPkuWAvOS4gOasoe'
        'iAjOaOieeyvuW6pgogICAgICAgIGluZm8ucmVhc29uID0gJ+Wbm+inkuaOpei/keefqeW9oiwg5pS555So5peL6L2s5qCh5q2jJzsKICAgICAg'
        'ICBxdWFkID0gW107CiAgICBlbmQKICAgIGlmIH5pc2VtcHR5KHF1YWQpCiAgICAgICAgd1RvcCA9IG5vcm0ocXVhZCgyLCA6KSAtIHF1YWQoMS'
        'wgOikpOwogICAgICAgIHdCb3QgPSBub3JtKHF1YWQoMywgOikgLSBxdWFkKDQsIDopKTsKICAgICAgICBoTGZ0ID0gbm9ybShxdWFkKDQsIDop'
        'IC0gcXVhZCgxLCA6KSk7CiAgICAgICAgaFJndCA9IG5vcm0ocXVhZCgzLCA6KSAtIHF1YWQoMiwgOikpOwogICAgICAgIHdBdmcgPSAod1RvcC'
        'ArIHdCb3QpIC8gMjsKICAgICAgICBoQXZnID0gbWF4KGVwcywgKGhMZnQgKyBoUmd0KSAvIDIpOwogICAgICAgIG91dEggPSB0YXJnZXRIOwog'
        'ICAgICAgIG91dFcgPSBtaW4obWF4KHJvdW5kKHdBdmcgKiB0YXJnZXRIIC8gaEF2ZyksIDI0KSwgOCAqIHRhcmdldEgpOwogICAgICAgIHRmb3'
        'JtID0gcXVhZFRvUmVjdFRyYW5zZm9ybShxdWFkLCBvdXRXLCBvdXRIKTsKICAgICAgICB2aWV3ICA9IGltcmVmMmQoW291dEgsIG91dFddKTsK'
        'ICAgICAgICBncmF5ICA9IGltd2FycChncmF5LCAgdGZvcm0sICdPdXRwdXRWaWV3JywgdmlldywgLi4uCiAgICAgICAgICAgICAgICAgICAgIC'
        'AgJ0ludGVycG9sYXRpb25NZXRob2QnLCAnYmlsaW5lYXInLCAnRmlsbFZhbHVlcycsIDApOwogICAgICAgIGNvbG9yID0gaW13YXJwKGNvbG9y'
        'LCB0Zm9ybSwgJ091dHB1dFZpZXcnLCB2aWV3LCAuLi4KICAgICAgICAgICAgICAgICAgICAgICAnSW50ZXJwb2xhdGlvbk1ldGhvZCcsICdiaW'
        'xpbmVhcicsICdGaWxsVmFsdWVzJywgMCk7CiAgICAgICAgaW5mby5tb2RlID0gJ3BlcnNwZWN0aXZlJzsKICAgICAgICBpbmZvLnF1YWQgPSBx'
        'dWFkOwogICAgICAgIHJldHVybjsKICAgIGVuZAplbmQKCiUgLS0tLS0tLS0tLS0tLS0tLSAyLiDlhZzlupU6IOWPquWBmuWAvuaWnCjml4vova'
        'wp5qCh5q2jIC0tLS0tLS0tLS0tLS0tLS0KaWYgbmFyZ2luID4gMiAmJiB+aXNlbXB0eShtYXNrKSAmJiBhbnkobWFzayg6KSkKICAgIGFuZyA9'
        'IGVzdGltYXRlU2tldyhtYXNrKTsKICAgIGlmIGFicyhhbmcpID4gMC44ICYmIGFicyhhbmcpIDwgMjUgICAgICAgICAgJSDlsI/op5LluqbkuI'
        '3mipjohb4sIOWkp+inkuW6puinhuS4uuivr+ajgAogICAgICAgIGluZm8ubW9kZSA9ICdyb3RhdGUnOwogICAgICAgIGluZm8uc2tldyA9IGFu'
        'ZzsKICAgICAgICBncmF5ICA9IGltcm90YXRlKGdyYXksICBhbmcsICdiaWxpbmVhcicsICdsb29zZScpOwogICAgICAgIGNvbG9yID0gaW1yb3'
        'RhdGUoY29sb3IsIGFuZywgJ2JpbGluZWFyJywgJ2xvb3NlJyk7CiAgICAgICAgbWFzayAgPSBpbXJvdGF0ZShtYXNrLCAgYW5nLCAnbmVhcmVz'
        'dCcsICAnbG9vc2UnKTsKICAgICAgICBiYiA9IHRpZ2h0Qm94KG1hc2spOwogICAgICAgIGlmIH5pc2VtcHR5KGJiKQogICAgICAgICAgICBiYi'
        'A9IGNsYW1wQm94KGJiLCBzaXplKGdyYXkpKTsKICAgICAgICAgICAgZ3JheSAgPSBpbWNyb3AoZ3JheSwgIGJiKTsKICAgICAgICAgICAgY29s'
        'b3IgPSBpbWNyb3AoY29sb3IsIGJiKTsKICAgICAgICBlbmQKICAgIGVuZAplbmQKCiUgLS0tLS0tLS0tLS0tLS0tLSAzLiDnu5/kuIDpq5jluq'
        'YgLS0tLS0tLS0tLS0tLS0tLQppZiBzaXplKGdyYXksIDEpID49IDgKICAgIHMgPSB0YXJnZXRIIC8gc2l6ZShncmF5LCAxKTsKICAgIGlmIGFi'
        'cyhzIC0gMSkgPiAwLjAxCiAgICAgICAgZ3JheSAgPSBpbXJlc2l6ZShncmF5LCAgcywgJ2JpbGluZWFyJyk7CiAgICAgICAgY29sb3IgPSBpbX'
        'Jlc2l6ZShjb2xvciwgcywgJ2JpbGluZWFyJyk7CiAgICBlbmQKZW5kCmVuZAoKJSA9PT09PT09PT09PT09PT09PT09PT09PT0g5bGA6YOo5Ye9'
        '5pWwID09PT09PT09PT09PT09PT09PT09PT09PQoKZnVuY3Rpb24gW3F1YWQsIHdoeV0gPSBkZXRlY3RRdWFkKG1hc2spCiVERVRFQ1RRVUFEIO'
        'i9pueJjOWbm+inkuS8sOiuoQolICAg6L6T5Ye6IDR4MiBbeCB5XSwg6aG65bqPIOW3puS4iiAtPiDlj7PkuIogLT4g5Y+z5LiLIC0+IOW3puS4'
        'izsg5Lyw6K6h5LiN5Y+v6Z2g5pe26L+U5ZueIFtd44CCCiUKJSAgIOeUqCLmnoHlgLzngrki5Y+W5Zub6KeSOiDovabniYzmmK/lh7jlm5vovr'
        'nlvaIsIOiAjOWHuOWkmui+ueW9ouS4iue6v+aAp+WHveaVsOeahOacgOWAvOS4gOWumuWcqOmhtueCueWPluWIsCwKJSAgIOaJgOS7pSB4K3kg'
        '5pyA5bCPIC0+IOW3puS4iiwgeCt5IOacgOWkpyAtPiDlj7PkuIssIHgteSDmnIDlpKcgLT4g5Y+z5LiKLCB4LXkg5pyA5bCPIC0+IOW3puS4i+'
        'OAggolICAg5a+55Lu75oSP5Ye45Zub6L655b2i6L+Z5Zub5q2l6YO96IO955u05o6l5ZG95Lit5Zub5Liq6aG254K5LCDkuI7ovabniYzmmK/o'
        'vazkuobjgIHmrarkuobjgIHov5jmmK/ooqvmi43miJDkuoYKJSAgIOair+W9oumDveaXoOWFsywg5Lmf5LiN5Y+XIui9pueJjOi+ueahhi/onr'
        'rmoJPmrovnlZki5Lul5aSW55qE5Zug57Sg5b2x5ZONLCDmmK/mnIDnqLPnmoTkvLDms5XjgIIKJQolICAg6K+V6L+H5YaN5Zyo5Zub5p2h6L65'
        '55qE5Lit5q615ouf5ZCI55u057q/44CB55So55u057q/5Lqk54K55Y6757K+5L+uOiDlrp7mtYvlvIrlpKfkuo7liKkg4oCU4oCUIOijgeWJqu'
        'ahhgolICAg5piv5o6p6Iac55qE5aSW5o6l55+p5b2iLCDovabniYzoh6rlt7HnmoTovrnnu4/luLjmraPlpb3otLTnnYDoo4HliarovrnnlYws'
        'IOaLn+WQiOW+iOWuueaYk+iiq+i+ueeVjOS4iueahAolICAg5oiq5pat54K55bim6LeRLCDnsr7kv67lh7rmnaXnmoTlm5vovrnlvaLlj43ogI'
        'zlgY/nprvnnJ/lgLwo5Z+65YeG6ZuG5pW054mM5YeG56Gu546HIDU1JSDmjonliLAgNDUlKeOAggolICAg5omA5Lul6L+Z6YeM5Y+q55So5p6B'
        '5YC854K5LCDpnaDkuIvpnaLnmoQgcXVhZE9LIOWBmuWQiOeQhuaAp+ajgOafpeOAggoKcXVhZCA9IFtdOyAgd2h5ID0gJyc7Cm1hc2sgPSBpbW'
        'Nsb3NlKG1hc2ssIHN0cmVsKCdkaXNrJywgMikpOwptYXNrID0gaW1maWxsKG1hc2ssICdob2xlcycpOwptYXNrID0gYndhcmVhb3BlbihtYXNr'
        'LCAyMCk7Cm1hc2sgPSBrZWVwTGFyZ2VzdChtYXNrKTsgICAgICAlIOWPqueVmeacgOWkp+i/numAmuWfnzog6L2m54mM5aSW5qGG5q6L55WZ5b'
        'i45piv6LS06L6555qE57uG6ZW/5p2hLAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgJSDpnaLnp6/ov4fkuI3kuoYgYndhcmVhb3Bl'
        'biDov5nlhbMsIOWNtOS8muaKiuaegeWAvOeCueW4puWBjwppZiB+YW55KG1hc2soOikpLCB3aHkgPSAn5o6p6Iac5Li656m6JzsgcmV0dXJuOy'
        'BlbmQKW0gsIFddID0gc2l6ZShtYXNrKTsKaWYgVyA8IDI0IHx8IEggPCA4LCB3aHkgPSAn6L2m54mM5aSq5bCPKOS4jei2syAyNHg4KSc7IHJl'
        'dHVybjsgZW5kCgpbcnIsIGNjXSA9IGZpbmQobWFzayk7CmlmIG51bWVsKHJyKSA8IDUwLCB3aHkgPSAn5o6p6Iac5YOP57Sg5aSq5bCRJzsgcm'
        'V0dXJuOyBlbmQKcyA9IGNjICsgcnI7ICBkID0gY2MgLSBycjsKW34sIGkxXSA9IG1pbihzKTsgIFt+LCBpMl0gPSBtYXgocyk7Clt+LCBpM10g'
        'PSBtYXgoZCk7ICBbfiwgaTRdID0gbWluKGQpOwpxdWFkID0gW2NjKGkxKSwgcnIoaTEpOyBjYyhpMyksIHJyKGkzKTsgY2MoaTIpLCBycihpMi'
        'k7IGNjKGk0KSwgcnIoaTQpXTsKaWYgc2l6ZSh1bmlxdWUocXVhZCwgJ3Jvd3MnKSwgMSkgPCA0CiAgICBxdWFkID0gW107ICB3aHkgPSAn5p6B'
        '5YC854K56YeN5ZCILCDmjqnohpzlvaLnirbkuI3mmK/lm5vovrnlvaInOyByZXR1cm47CmVuZAppZiB+cXVhZE9LKHF1YWQsIFcsIEgsIG1hc2'
        'spCiAgICBxdWFkID0gW107CiAgICB3aHkgPSAn5o6p6Iac5b2i54q25LiN5YOP6L2m54mM5Zub6L655b2iKOWbm+inkuS4jeWHuC/plb/lrr3m'
        'r5TlvILluLgv5o6p6Iac5aGr5LiN5ruhKSc7CmVuZAplbmQKCmZ1bmN0aW9uIHRmID0gaGFzUGVyc3BlY3RpdmUocSkKJUhBU1BFUlNQRUNUSV'
        'ZFIOWbm+inkuaYr+S4jeaYryLmmI7mmL7ooqvmi43miJDkuobmoq/lvaIiCiUgICDliKTmja46IOS4iuS4i+i+ueS4jeetiemVv+OAgeW3puWP'
        's+i+ueS4jeetiemVv+OAgeaIluWbm+S4quinkuaYjuaYvuS4jeaYr+ebtOinkuOAggolICAg57qv5peL6L2s55qE6L2m54mM6L+Z5LiJ6aG56Y'
        'O95o6l6L+RIDAsIOi1sOaXi+i9rOagoeato+WNs+WPrzsg5Y+q5pyJ5pac5ouNKOair+W9oinmiY3lgLzlvpflgZrpgI/op4blj5jmjaLjgIIK'
        'JSAgIOWKoOi/memBk+mXuOmXqOacieS4pOS4quWOn+WboDoKJSAgICAgMSkg5pys5p2l5bCx5q2j55qE5Zu+5LiN6ZyA6KaB5aSa5o+S5YC85L'
        'iA5qyhIOKAlOKAlCDlpJrkuIDmrKHph43ph4fmoLflsLHlpJrkuIDmrKHmqKHns4osCiUgICAgICAgIOWvueaooeadv+WMuemFjei/meenjeWQ'
        'g+e7huiKgueahOaWueazleaYr+e6r+S6jzsKJSAgICAgMikg5bCP6L2m54mMKOWHoOWNgeWDj+e0oOWuvSnnmoTmjqnohpzmnKzouqvmmK/lj7'
        'DpmLbnirbnmoQsIOaegeWAvOeCueS8muW3puWPs+aKluS4gOS4pOS4quWDj+e0oCwKJSAgICAgICAg5oqY566X5oiQ6KeS5bqm6IO95Yiw5LiD'
        '5YWr5bqmLCDlrrnmmJPooqvor6/liKTmiJDmoq/lvaLjgILmiYDku6XlsI/niYzkuIDlvovkuI3lgZrpgI/op4bjgIIKZTEgPSBxKDIsIDopIC'
        '0gcSgxLCA6KTsgIGUyID0gcSgzLCA6KSAtIHEoMiwgOik7CmUzID0gcSg0LCA6KSAtIHEoMywgOik7ICBlNCA9IHEoMSwgOikgLSBxKDQsIDop'
        'Owp3VCA9IG5vcm0oZTEpOyAgd0IgPSBub3JtKGUzKTsgIGhMID0gbm9ybShlNCk7ICBoUiA9IG5vcm0oZTIpOwp3QXZnID0gKHdUICsgd0IpIC'
        '8gMjsgIGhBdmcgPSAoaEwgKyBoUikgLyAyOwppZiBoQXZnIDwgMzIgfHwgd0F2ZyA8IDk2LCB0ZiA9IGZhbHNlOyByZXR1cm47IGVuZCAgICAg'
        'ICUg5aSq5bCPLCDop5LluqbkvLDorqHmnKzouqvlsLHkuI3lj6/kv6EKZHcgPSBhYnMod1QgLSB3QikgLyBtYXgoZXBzLCB3QXZnKTsKZGggPS'
        'BhYnMoaEwgLSBoUikgLyBtYXgoZXBzLCBoQXZnKTsKZGV2ID0gbWF4KFtyaWdodEFuZ2xlRGV2KGUxLCAtZTQpLCByaWdodEFuZ2xlRGV2KGUy'
        'LCAtZTEpLCAuLi4KICAgICAgICAgICByaWdodEFuZ2xlRGV2KGUzLCAtZTIpLCByaWdodEFuZ2xlRGV2KGU0LCAtZTMpXSk7CnRmID0gZHcgPi'
        'AwLjA4IHx8IGRoID4gMC4wOCB8fCBkZXYgPiAxMDsKZW5kCgpmdW5jdGlvbiBkID0gcmlnaHRBbmdsZURldih1LCB2KQolUklHSFRBTkdMRWRl'
        'diDkuKTlkJHph4/lpLnop5LkuI4gOTAg5bqm55qE5YGP5beuKOW6pikKYyA9IGRvdCh1LCB2KSAvIG1heChlcHMsIG5vcm0odSkgKiBub3JtKH'
        'YpKTsKZCA9IGFicyg5MCAtIGFjb3NkKG1heCgtMSwgbWluKDEsIGMpKSkpOwplbmQKCmZ1bmN0aW9uIG9rID0gcXVhZE9LKHEsIFcsIEgsIG1h'
        'c2spCiVRVUFET0sg5Zub6L655b2i5ZCI55CG5oCn5qOA5p+lOiDmnInpmZDjgIHmsqHot5Hlh7rlm77lg4/lpKrov5zjgIHmmK/lh7jlm5vovr'
        'nlvaLjgIHplb/lrr3mr5Tlg4/ovabniYwsCiUgICAgICAg5bm25LiU5o6p6Iac5Z+65pys5aGr5ruh5a6DKOWhq+S4jea7oeivtOaYjuWbm+in'
        'kuS8sOW+l+WkquWkpywg5piv6K+v5qOA6ICM6Z2e55yf6L2m54mMKQpvayA9IGZhbHNlOwppZiBhbnkofmlzZmluaXRlKHEoOikpKSwgcmV0dX'
        'JuOyBlbmQKaWYgYW55KHEoOiwgMSkgPCAtMC4yMCAqIFcpIHx8IGFueShxKDosIDEpID4gMS4yMCAqIFcpLCByZXR1cm47IGVuZAppZiBhbnko'
        'cSg6LCAyKSA8IC0wLjIwICogSCkgfHwgYW55KHEoOiwgMikgPiAxLjIwICogSCksIHJldHVybjsgZW5kCmUxID0gcSgyLCA6KSAtIHEoMSwgOi'
        'k7ICBlMiA9IHEoMywgOikgLSBxKDIsIDopOwplMyA9IHEoNCwgOikgLSBxKDMsIDopOyAgZTQgPSBxKDEsIDopIC0gcSg0LCA6KTsKY3IgPSBb'
        'ZTEoMSkgKiBlMigyKSAtIGUxKDIpICogZTIoMSksIGUyKDEpICogZTMoMikgLSBlMigyKSAqIGUzKDEpLCAuLi4KICAgICAgZTMoMSkgKiBlNC'
        'gyKSAtIGUzKDIpICogZTQoMSksIGU0KDEpICogZTEoMikgLSBlNCgyKSAqIGUxKDEpXTsKaWYgfihhbGwoY3IgPiAwKSB8fCBhbGwoY3IgPCAw'
        'KSksIHJldHVybjsgZW5kICAgICAgJSDkuI3lh7gKd0F2ZyA9IChub3JtKGUxKSArIG5vcm0oZTMpKSAvIDI7CmhBdmcgPSAobm9ybShlMikgKy'
        'Bub3JtKGU0KSkgLyAyOwppZiB3QXZnIDwgMC40NSAqIFcgfHwgaEF2ZyA8IDAuNDUgKiBILCByZXR1cm47IGVuZCAgJSDoo4HntKflkI7nmoTo'
        'vabniYzlupTln7rmnKzljaDmu6Hoo4HliarmoYYKYXIgPSB3QXZnIC8gbWF4KGVwcywgaEF2Zyk7CmlmIGFyIDwgMS44IHx8IGFyID4gNC41LC'
        'ByZXR1cm47IGVuZAphID0gcG9seWFyZWEocSg6LCAxKSwgcSg6LCAyKSk7CmlmIG5ueihtYXNrKSA8IDAuNTUgKiBhLCByZXR1cm47IGVuZApp'
        'ZiBubnoobWFzaykgPiAxLjYwICogYSwgcmV0dXJuOyBlbmQKb2sgPSB0cnVlOwplbmQKCmZ1bmN0aW9uIG0gPSBrZWVwTGFyZ2VzdChtYXNrKQ'
        'olS0VFUExBUkdFU1Qg5Y+q5L+d55WZ6Z2i56ev5pyA5aSn55qE6L+e6YCa5Z+fKOaOqeiGnOmHjOWBtuWwlOa3t+i/m+i0tOi+ueeahOe7humV'
        'v+aui+eVmSkKbGJsID0gYndsYWJlbChtYXNrLCA4KTsKaWYgbWF4KGxibCg6KSkgPD0gMSwgbSA9IG1hc2s7IHJldHVybjsgZW5kCmNudCA9IG'
        'FjY3VtYXJyYXkobGJsKGxibCA+IDApLCAxKTsKW34sIGtdID0gbWF4KGNudCk7Cm0gPSBsYmwgPT0gazsKZW5kCgpmdW5jdGlvbiB0Zm9ybSA9'
        'IHF1YWRUb1JlY3RUcmFuc2Zvcm0ocXVhZCwgb3V0Vywgb3V0SCkKJVFVQURUT1JFQ1RUUkFOU0ZPUk0g55u05o6l5oqK6L2m54mM5Zub6KeS5p'
        'ig5bCE5YiwIG91dFcgeCBvdXRIIOefqeW9oueahOWNleW6lOWPmOaNogolICAgKOiHquW3seinoyA4eDgg57q/5oCn5pa556iL57uELCDkuI3k'
        'vp3otZYgZml0Z2VvdHJhbnMsIOiAgeeJiOacrCBNQVRMQUIg5Lmf6IO96LeRKQpkc3QgPSBbMSAxOyBvdXRXIDE7IG91dFcgb3V0SDsgMSBvdX'
        'RIXTsKQSA9IHplcm9zKDgsIDgpOyBiID0gemVyb3MoOCwgMSk7CmZvciBpID0gMTo0CiAgICB4ID0gcXVhZChpLCAxKTsgeSA9IHF1YWQoaSwg'
        'Mik7CiAgICB1ID0gZHN0KGksIDEpOyAgdiA9IGRzdChpLCAyKTsKICAgIEEoMiAqIGkgLSAxLCA6KSA9IFt4LCB5LCAxLCAwLCAwLCAwLCAtdS'
        'AqIHgsIC11ICogeV07CiAgICBBKDIgKiBpLCAgICAgOikgPSBbMCwgMCwgMCwgeCwgeSwgMSwgLXYgKiB4LCAtdiAqIHldOwogICAgYigyICog'
        'aSAtIDEpID0gdTsKICAgIGIoMiAqIGkpICAgICA9IHY7CmVuZApoID0gQSBcIGI7CiUg5rOo5oSPOiDkuIrpnaLop6Plh7rnmoTmmK8i5YiX5Z'
        'CR6YePIue6puWumiBbeDt5OzFdIC0+IFt1O3Y7d10g55qE55+p6Zi1LAolICAgICAgIOiAjCBNQVRMQUIg55qEIHByb2plY3RpdmUyZCDnlKjn'
        'moTmmK/ooYzlkJHph4/nuqblrposIOaJgOS7peimgei9rOe9ruS4gOasoQpUID0gW2goMSksIGgoMiksIGgoMyk7IGgoNCksIGgoNSksIGgoNi'
        'k7IGgoNyksIGgoOCksIDFdOwp0Zm9ybSA9IHByb2plY3RpdmUyZChUJyk7CmVuZAoKZnVuY3Rpb24gYW5nID0gZXN0aW1hdGVTa2V3KG1hc2sp'
        'CiVFU1RJTUFURVNLRVcg55Sx6L2m54mM5o6p6Iac5LiK5LiL6L6555WM5ouf5ZCI55u057q/LCDov5Tlm57pnIDopoHml4vovaznmoTop5Lluq'
        'Yo5bqmKQptYXNrID0gaW1jbG9zZShtYXNrLCBzdHJlbCgnZGlzaycsIDIpKTsKbWFzayA9IGltZmlsbChtYXNrLCAnaG9sZXMnKTsKbWFzayA9'
        'IGJ3YXJlYW9wZW4obWFzaywgMjApOwppZiB+YW55KG1hc2soOikpLCBhbmcgPSAwOyByZXR1cm47IGVuZAoKW0gsIFddID0gc2l6ZShtYXNrKT'
        'sgJSNvazxBU0dMVT4KdG9wID0gbmFuKDEsIFcpOyBib3QgPSBuYW4oMSwgVyk7CmZvciBjID0gMTpXCiAgICByID0gZmluZChtYXNrKDosIGMp'
        'LCAxLCAnZmlyc3QnKTsKICAgIGlmIH5pc2VtcHR5KHIpLCB0b3AoYykgPSByOyBlbmQKICAgIHIgPSBmaW5kKG1hc2soOiwgYyksIDEsICdsYX'
        'N0Jyk7CiAgICBpZiB+aXNlbXB0eShyKSwgYm90KGMpID0gcjsgZW5kCmVuZAoKdiA9IFtmaXRMaW5lQW5nbGUodG9wKSwgZml0TGluZUFuZ2xl'
        'KGJvdCldOwp2ID0gdih+aXNuYW4odikpOwppZiBpc2VtcHR5KHYpCiAgICBhbmcgPSAwOwplbHNlCiAgICBhbmcgPSBtZWFuKHYpOwplbmQKZW'
        '5kCgpmdW5jdGlvbiBhID0gZml0TGluZUFuZ2xlKHkpCiVGSVRMSU5FQU5HTEUg5pyA5bCP5LqM5LmY5ouf5ZCI5LiA5p2h6L+R5Ly85rC05bmz'
        '57q/LCDov5Tlm57lgL7op5Io5bqmKTsg5ZCr5LiA5qyh56a7576k54K55YmU6ZmkCnggPSAxOm51bWVsKHkpOwpvayA9IH5pc25hbih5KTsKeC'
        'A9IHgob2spOyB5ID0geShvayk7CmlmIG51bWVsKHgpIDwgNSwgYSA9IE5hTjsgcmV0dXJuOyBlbmQKCnAgPSBwb2x5Zml0KHgoOiksIHkoOiks'
        'IDEpOwpyZXMgPSBhYnMoeSg6KSAtIHBvbHl2YWwocCwgeCg6KSkpOwp0aCA9IDIuNSAqIG1heCgxLCBtZWRpYW4ocmVzKSk7CmtlZXAgPSByZX'
        'MgPD0gdGg7CmlmIG5ueihrZWVwKSA+PSA1CiAgICBwID0gcG9seWZpdCh4KGtlZXApLCB5KGtlZXApLCAxKTsKZW5kCmEgPSBhdGFuZChwKDEp'
        'KTsgICAgICAlIGltcm90YXRlIOato+inkuW6puS4uumAhuaXtumSiCwg5q2j5aW95oq15raI5Zu+5YOP5Z2Q5qCHIHkg5ZCR5LiL55qE5pac54'
        '6HCmVuZAoKZnVuY3Rpb24gYmIgPSB0aWdodEJveChtYXNrKQolVElHSFRCT1gg5o6p6Iac55qE5pyA5bCP5aSW5o6l55+p5b2iIFt4IHkgdyBo'
        'XQpjb2xTdW0gPSBzdW0obWFzaywgMSk7CnJvd1N1bSA9IHN1bShtYXNrLCAyKTsKaWYgfmFueShjb2xTdW0pIHx8IH5hbnkocm93U3VtKSwgYm'
        'IgPSBbXTsgcmV0dXJuOyBlbmQKY3MgPSBmaW5kKGNvbFN1bSA+IDAuMzAgKiBtYXgoY29sU3VtKSk7CnJzID0gZmluZChyb3dTdW0gPiAwLjMw'
        'ICogbWF4KHJvd1N1bSkpOwpiYiA9IFtjcygxKSwgcnMoMSksIGNzKGVuZCkgLSBjcygxKSArIDEsIHJzKGVuZCkgLSBycygxKSArIDFdOwplbm'
        'QKCmZ1bmN0aW9uIGJiID0gY2xhbXBCb3goYmIsIHN6KQpiYigxKSA9IG1heCgxLCBtaW4oYmIoMSksIHN6KDIpKSk7CmJiKDIpID0gbWF4KDEs'
        'IG1pbihiYigyKSwgc3ooMSkpKTsKYmIoMykgPSBtYXgoMSwgbWluKGJiKDMpLCBzeigyKSAtIGJiKDEpICsgMSkpOwpiYig0KSA9IG1heCgxLC'
        'BtaW4oYmIoNCksIHN6KDEpIC0gYmIoMikgKyAxKSk7CmVuZAo='
    ),
    'core/cropPlate.m': (
        'ZnVuY3Rpb24gW2dyYXksIGNvbG9yLCBtYXNrXSA9IGNyb3BQbGF0ZShJLCBib3gsIHBsYXRlTWFzaykKJUNST1BQTEFURSDmjInlrprkvY3moY'
        'boo4HliarovabniYwsIOW5tuWQjOatpeijgeWJqui9pueJjOaOqeiGnAolCiUgICBbZ3JheSwgY29sb3IsIG1hc2tdID0gQ1JPUFBMQVRFKEks'
        'IGJveCwgcGxhdGVNYXNrKQolICAgYm94IDogW3ggeSB3IGhdIChpbWNyb3Ag5Z2Q5qCH57O7LCDku44gMSDlvIDlp4spCgpwYWQgPSAyOyAgIC'
        'AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAlIOWQkeWkluaJqSAyIOWDj+e0oCwg5L+d5L2P6L2m54mM6L655qGGCltILCBX'
        'LCB+XSA9IHNpemUoSSk7CngxID0gbWF4KDEsIHJvdW5kKGJveCgxKSkgLSBwYWQpOwp5MSA9IG1heCgxLCByb3VuZChib3goMikpIC0gcGFkKT'
        'sKeDIgPSBtaW4oVywgcm91bmQoYm94KDEpICsgYm94KDMpIC0gMSkgKyBwYWQpOwp5MiA9IG1pbihILCByb3VuZChib3goMikgKyBib3goNCkg'
        'LSAxKSArIHBhZCk7Cgpjb2xvciA9IEkoeTE6eTIsIHgxOngyLCA6KTsKZ3JheSAgPSByZ2IyZ3JheShjb2xvcik7CgppZiBuYXJnaW4gPiAyIC'
        'YmIH5pc2VtcHR5KHBsYXRlTWFzaykKICAgIG1hc2sgPSBwbGF0ZU1hc2soeTE6eTIsIHgxOngyKTsKZWxzZQogICAgbWFzayA9IHRydWUoc2l6'
        'ZShncmF5KSk7CmVuZAplbmQ='
    ),
    'core/locatePlate.m': (
        'ZnVuY3Rpb24gW3BsYXRlQm94LCBwbGF0ZU1hc2ssIHNjb3JlSW5mb10gPSBsb2NhdGVQbGF0ZShJKQolTE9DQVRFUExBVEUg5Zyo5pW05bmF5Z'
        'u+5YOP5Lit5a6a5L2N6L2m54mM5Yy65Z+fCiUKJSAgIFtwbGF0ZUJveCwgcGxhdGVNYXNrLCBpbmZvXSA9IExPQ0FURVBMQVRFKEkpCiUgICDo'
        'vpPlhaU6IEkgLSB1aW50OCDlvanoibLlm77lg48KJSAgIOi+k+WHujogcGxhdGVCb3ggIC0gW3ggeSB3IGhdOyDnqbrooajnpLrmnKrmib7liL'
        'AKJSAgICAgICAgIHBsYXRlTWFzayAtIOi9pueJjOeahOS6jOWAvOaOqeiGnCAo5LiOIEkg5ZCM5bC65a+4LCDnlKjkuo7lkI7nu63lgL7mlpzm'
        'oKHmraMpCiUgICAgICAgICBzY29yZUluZm8gLSDnu5PmnoTkvZMsIOacgOS9s+WAmemAieeahOivhOWIhue7huiKgijkvpsgbHByX21haW4g5Y'
        'ik5patIuaYr+S4jeaYr+ecn+i9pueJjCIpOgolICAgICAgICAgICAgICAgICAgICAgLnNjb3JlICAgICAgIOe7vOWQiOW+l+WIhgolICAgICAg'
        'ICAgICAgICAgICAgICAgLnNvdXJjZSAgICAgICdjb2xvcicg6aKc6Imy6YCa6YGTIHwgJ2VkZ2UnIOi+uee8mOmAmumBkwolICAgICAgICAgIC'
        'AgICAgICAgICAgLmFzcGVjdCAgICAgIOmVv+WuveavlAolICAgICAgICAgICAgICAgICAgICAgLmNvbG9yRnJhYyAgIOahhuWGhei9pueJjOW6'
        'leiJsuWDj+e0oOWNoOavlCAgIDwtLSDljLrliIbnnJ/lgYfovabniYzmnIDmnInmlYjnmoTph48KJSAgICAgICAgICAgICAgICAgICAgIC5lZG'
        'dlRGVuc2l0eSDmoYblhoXlnoLnm7TovrnnvJjlr4bluqYKJSAgICAgICAgICAgICAgICAgICAgIC5leHRlbnQgICAgICDnn6nlvaLluqYKJSAg'
        'ICAgICAgICAgICAgICAgICAgIC5hcmVhICAgICAgICDov57pgJrln5/pnaLnp68KJSAgICAgICAgICAgICAgICAgICAgIC5uQ2FuZCAgICAgIC'
        'DpgJrov4fnoazmgKfmnaHku7bnmoTlgJnpgInkuKrmlbAKJSAgICAgICAgICAgICAgICAgICAgIC52YWxpZCAgICAgICDmmK/lkKbnnJ/ovabn'
        'iYwo55+p5b2i5bqmICsg5bqV6Imy5Y2g5q+U5Y+M6Ze46ZeoKQolICAgICAgICAgICAgICAgICAgICAgLnJlamVjdCAgICAgIOiiq+WIpOS4uu'
        'WBh+i9pueJjOeahOWOn+WboCjlj6/nm7TmjqXmj5DnpLrnu5nnlKjmiLcpCiUKJSAgIOaAnei3rzoKJSAgICAgMSkg5oyJ6aKc6ImyKOiTneW6'
        'lS/mlrDog73mupDnu7/lupUv6buE5bqVKeW+l+WIsOWAmemAieaOqeiGnAolICAgICAyKSDlj6bkuIDot6/nlKgi5Z6C55u06L6557yYICsg5r'
        'C05bmz5b2i5oCB5a2m6Zet6L+Q566XIuW+l+WIsOWAmemAieaOqeiGnCjlhbzlrrnngbDluqblm74pCiUgICAgIDMpIOWvueavj+S4qui/numA'
        'muWfn+aMiSDplb/lrr3mr5QgLyDpnaLnp68gLyDpopzoibLkuIDoh7TmgKcgLyDovrnnvJjlr4bluqYgLyDnn6nlvaLluqYg5omT5YiGLCDlj5'
        'bmnIDpq5jliIYKJQolICAg6LCD5Y+C5o+Q56S6OgolICAgICAtIOi9pueJjOa8j+ajgCAgLT4g5pS+5a695a656Imy6ZiI5YC8KFMgPiAwLjMw'
        'IOaUuSAwLjIwKSwg5oiW6ZmN5L2OIG1pbkFyZWFGcmFjCiUgICAgIC0g6K+v5qOA5Yiw5Yir55qE6JOd6Imy54mp5L2TIC0+IOaPkOmrmCAwLj'
        'I1KnNDb2wg5p2D6YeNLCDmj5Dpq5jplb/lrr3mr5Tmg6nnvZoKJSAgICAgLSDovabniYzlvojlpKcv5b6I5bCPIC0+IOiwg+aVtCBtaW5BcmVh'
        'RnJhYyAo6buY6K6kIDAuMDAwOCkKCmlmIHNpemUoSSwgMykgPT0gMQogICAgSSA9IHJlcG1hdChJLCAxLCAxLCAzKTsKZW5kCltILCBXLCB+XS'
        'A9IHNpemUoSSk7Cm1pbkFyZWEgPSAwLjAwMDggKiBIICogVzsgICAgICAgICAgJSDovabniYzmnIDlsI/pnaLnp68o5YOP57SgKQoKJSAtLS0t'
        'IOecn+i9pueJjOmXuOmXqDog55So5p2l5ouS57ud5b2i54q2L+minOiJsuS4jeWDj+i9pueJjOeahOWAmemAieWMuuWfnyAtLS0tCiUg55yf6L'
        '2m54mM5LiA5a6a5piv6KeE5pW055qE5b2p6Imy55+p5b2iOiDnn6nlvaLluqbpq5gsIOS4lOahhuWGheWkp+mDqOWIhuWDj+e0oOaYr+i9pueJ'
        'jOW6leiJsuOAggolIOWunua1iygyMyDlvKDnnJ/ovabniYwgLyAxMCDlvKDml6DovabniYzlubLmibDlm74pOgolICAg55yf6L2m54mMICDnn6'
        'nlvaLluqYgMC42NX4wLjk1LCDlupXoibLljaDmr5QgMC42N34wLjg1CiUgICDlubLmibDlm74gIOefqeW9ouW6puWPquaciSAwLjM1fjAuNDgg'
        'KOWZquWjsC/ok53lpKkv5qOL55uY5qC8L+i3r+mdoi/lpJzmma8pCm1pbkV4dGVudCAgPSAwLjUwOyAgICAgICAgICAgICAgICAlIOefqeW9ou'
        'W6puS4i+mZkAptaW5Db2xvckZyID0gMC4zMDsgICAgICAgICAgICAgICAgJSDmoYblhoXovabniYzlupXoibLljaDmr5TkuIvpmZAKZ3JheSA9'
        'IHJnYjJncmF5KEkpOwoKJSAtLS0tLS0tLS0tLS0tLS0tIDEuIOminOiJsuaOqeiGnCAtLS0tLS0tLS0tLS0tLS0tCmhzdiA9IHJnYjJoc3YoSS'
        'k7CkhoID0gaHN2KDosIDosIDEpOyBTID0gaHN2KDosIDosIDIpOyBWID0gaHN2KDosIDosIDMpOwoKbUJsdWUgICA9IChIaCA+IDAuNTIgJiBI'
        'aCA8IDAuNzIpICYgUyA+IDAuMzAgJiBWID4gMC4xODsgICAlIOiTneW6lei9pueJjAptR3JlZW4gID0gKEhoID4gMC4yMiAmIEhoIDwgMC40NS'
        'kgJiBTID4gMC4yNSAmIFYgPiAwLjE4OyAgICUg5paw6IO95rqQ57u/54mMCm1ZZWxsb3cgPSAoSGggPiAwLjA5ICYgSGggPCAwLjIwKSAmIFMg'
        'PiAwLjM1ICYgViA+IDAuNDA7ICAgJSDpu4TlupXovabniYwKY29sb3JNYXNrID0gbUJsdWUgfCBtR3JlZW4gfCBtWWVsbG93OwoKJSDlvaLmgI'
        'HlrabmlbTnkIY6IOmXrei/kOeul+aKiiLlupXoibIr5a2X56ymIui/nuaIkOS4gOS4quWdlywg5byA6L+Q566X5Y675a2k56uL5Zmq54K5CndM'
        'ZW4gPSBtYXgoOSwgcm91bmQoVyAvIDgwKSk7CmhMZW4gPSBtYXgoMywgcm91bmQoSCAvIDIwMCkpOwpjTWFzayA9IGltY2xvc2UoY29sb3JNYX'
        'NrLCBzdHJlbCgncmVjdGFuZ2xlJywgW2hMZW4sIHdMZW5dKSk7CmNNYXNrID0gaW1vcGVuKGNNYXNrLCBzdHJlbCgncmVjdGFuZ2xlJywgWzMg'
        'M10pKTsKY01hc2sgPSBpbWZpbGwoY01hc2ssICdob2xlcycpOwpjTWFzayA9IGJ3YXJlYW9wZW4oY01hc2ssIHJvdW5kKG1pbkFyZWEpKTsKCi'
        'UgLS0tLS0tLS0tLS0tLS0tLSAyLiDovrnnvJjmjqnohpwo5aSH55So6YCa6YGTKSAtLS0tLS0tLS0tLS0tLS0tCmVNYXNrID0gYnVpbGRFZGdl'
        'TWFzayhncmF5LCBXLCBtaW5BcmVhKTsKCiUgLS0tLS0tLS0tLS0tLS0tLSAzLiDmiZPliIbpgInkvJggLS0tLS0tLS0tLS0tLS0tLQpbYm94Qy'
        'wgbWFza0MsIHNjQ10gPSBiZXN0UmVnaW9uKGNNYXNrLCBncmF5LCBjb2xvck1hc2ssIG1pbkFyZWEpOwpbYm94RSwgbWFza0UsIHNjRV0gPSBi'
        'ZXN0UmVnaW9uKGVNYXNrLCBncmF5LCBjb2xvck1hc2ssIG1pbkFyZWEpOwoKaWYgc2NDLnNjb3JlID49IHNjRS5zY29yZQogICAgcGxhdGVCb3'
        'ggPSBib3hDOyBwbGF0ZU1hc2sgPSBtYXNrQzsgc2NvcmVJbmZvID0gc2NDOyBzY29yZUluZm8uc291cmNlID0gJ2NvbG9yJzsKZWxzZQogICAg'
        'cGxhdGVCb3ggPSBib3hFOyBwbGF0ZU1hc2sgPSBtYXNrRTsgc2NvcmVJbmZvID0gc2NFOyBzY29yZUluZm8uc291cmNlID0gJ2VkZ2UnOwplbm'
        'QKCmlmIH5pc2VtcHR5KHBsYXRlQm94KQogICAgcGxhdGVCb3ggPSByZWZpbmVCb3gocGxhdGVNYXNrLCBwbGF0ZUJveCk7CiAgICBzY29yZUlu'
        'Zm8uY29sb3JGcmFjID0gZnJhY0NvbG9yKHBsYXRlQm94LCBjb2xvck1hc2spOwogICAgc2NvcmVJbmZvLmVkZ2VzRnJhYyA9IGZyYWNDb2xvci'
        'hwbGF0ZUJveCwgZWRnZShncmF5LCAnc29iZWwnLCAndmVydGljYWwnKSk7CiAgICBzY29yZUluZm8udmFsaWQgPSBzY29yZUluZm8uZXh0ZW50'
        'ID49IG1pbkV4dGVudCAmJiBzY29yZUluZm8uY29sb3JGcmFjID49IG1pbkNvbG9yRnI7CiAgICBpZiBzY29yZUluZm8uZXh0ZW50IDwgbWluRX'
        'h0ZW50CiAgICAgICAgc2NvcmVJbmZvLnJlamVjdCA9IHNwcmludGYoJ+WAmemAieWMuuWfn+efqeW9ouW6piAlLjJmIDwgJS4yZiwg5LiN5YOP'
        '6KeE5pW055qE55+p5b2i6L2m54mMJywgc2NvcmVJbmZvLmV4dGVudCwgbWluRXh0ZW50KTsKICAgIGVsc2VpZiBzY29yZUluZm8uY29sb3JGcm'
        'FjIDwgbWluQ29sb3JGcgogICAgICAgIHNjb3JlSW5mby5yZWplY3QgPSBzcHJpbnRmKCfmoYblhoXovabniYzlupXoibLlj6rljaAgJS4yZiAo'
        'PCAlLjJmKSwg5rKh5pyJ6JOdL+e7vy/pu4TlupXoibInLCBzY29yZUluZm8uY29sb3JGcmFjLCBtaW5Db2xvckZyKTsKICAgIGVuZAplbmQKZW'
        '5kCgolID09PT09PT09PT09PT09PT09PT09PT09PSDlsYDpg6jlh73mlbAgPT09PT09PT09PT09PT09PT09PT09PT09CgpmdW5jdGlvbiBtYXNr'
        'ID0gYnVpbGRFZGdlTWFzayhncmF5LCBXLCBtaW5BcmVhKQolQlVJTERFREdFTUFTSyDlnoLnm7TovrnnvJggKyDmsLTlubPpl63ov5Dnrpc6IO'
        'aKiuS4gOihjOWtl+espui/nuaIkOefqeW9ouWdlwpnID0gYWRhcHRoaXN0ZXEoZ3JheSwgJ051bVRpbGVzJywgWzggOF0sICdDbGlwTGltaXQn'
        'LCAwLjAyKTsKZyA9IG1lZGZpbHQyKGcsIFszIDNdKTsKYncgPSBlZGdlKGcsICdzb2JlbCcsICd2ZXJ0aWNhbCcpOyAgICAgICAgICAgICAgIC'
        'AlIOWtl+espuS7peWeguebtOi+uee8mOS4uuS4uwp3TGVuID0gbWF4KDE1LCByb3VuZChXIC8gNDApKTsKYncgPSBpbWNsb3NlKGJ3LCBzdHJl'
        'bCgncmVjdGFuZ2xlJywgWzMsIHdMZW5dKSk7CmJ3ID0gaW1kaWxhdGUoYncsIHN0cmVsKCdyZWN0YW5nbGUnLCBbMyAzXSkpOwpidyA9IGltZm'
        'lsbChidywgJ2hvbGVzJyk7Cm1hc2sgPSBid2FyZWFvcGVuKGJ3LCByb3VuZChtaW5BcmVhKSk7CmVuZAoKZnVuY3Rpb24gW2JveCwgcmVnaW9u'
        'TWFzaywgaW5mb10gPSBiZXN0UmVnaW9uKG1hc2ssIGdyYXksIGNvbG9yTWFzaywgbWluQXJlYSkKJUJFU1RSRUdJT04g5a+55o6p6Iac5Lit5o'
        'mA5pyJ6L+e6YCa5Z+f5omT5YiGLCDov5Tlm57lvpfliIbmnIDpq5jnmoTovabniYzlgJnpgIkKYm94ID0gW107IHJlZ2lvbk1hc2sgPSBbXTsg'
        'YmVzdFNjb3JlID0gLWluZjsgaW5mbyA9IGVtcHR5SW5mbygpOyBuQ2FuZCA9IDA7CmlmIH5hbnkobWFzayg6KSksIHJldHVybjsgZW5kCgpDQy'
        'A9IGJ3Y29ubmNvbXAobWFzayk7CnN0YXRzID0gcmVnaW9ucHJvcHMoQ0MsICdCb3VuZGluZ0JveCcsICdBcmVhJywgJ0V4dGVudCcsICdQaXhl'
        'bElkeExpc3QnKTsKZWQgPSBlZGdlKGdyYXksICdzb2JlbCcsICd2ZXJ0aWNhbCcpOwoKW0gsIFddID0gc2l6ZShncmF5KTsKZm9yIGsgPSAxOm'
        '51bWVsKHN0YXRzKQogICAgYmIgPSBzdGF0cyhrKS5Cb3VuZGluZ0JveDsgICAgICAgICAgICAgICAgICUgW3ggeSB3IGhdCiAgICBhciA9IGJi'
        'KDMpIC8gYmIoNCk7ICAgICAgICAgICAgICAgICAgICAgICAgJSDplb/lrr3mr5QKICAgIGFyZWEgPSBzdGF0cyhrKS5BcmVhOwoKICAgICUgLS'
        '0tLSDnoazmgKfmnaHku7YgLS0tLQogICAgaWYgYXIgPCAxLjYgfHwgYXIgPiA2LjUsIGNvbnRpbnVlOyBlbmQgICAgICAlIOS4reWbvei9pueJ'
        'jCA0NDA6MTQwID0gMy4xNAogICAgaWYgYXJlYSA8IG1pbkFyZWEsIGNvbnRpbnVlOyBlbmQKICAgIGlmIGJiKDQpID4gMC42MCAqIEgsIGNvbn'
        'RpbnVlOyBlbmQgICAgICAgICAgICUg6L+H6auYLCDkuI3lj6/og73mmK/ovabniYwKICAgIGlmIGJiKDMpID4gMC45NSAqIFcsIGNvbnRpbnVl'
        'OyBlbmQKICAgIG5DYW5kID0gbkNhbmQgKyAxOwoKICAgICUgLS0tLSDova/mgKfmiZPliIYgLS0tLQogICAgc0FSICAgPSBleHAoLSgoYXIgLS'
        'AzLjMpIC8gMS4xKV4yKTsgICAgICAgICAgICAgICAgICAgICAgICUg6ZW/5a695q+U6LaK5o6l6L+RIDMuMyDotorlpb0KICAgIHNBcmVhID0g'
        'bWluKDEsIGFyZWEgLyAoMC4wMSAqIEggKiBXKSk7ICAgICAgICAgICAgICAgICAgICAlIOmdouenrwogICAgeDEgPSBtYXgoMSwgZmxvb3IoYm'
        'IoMSkpKTsgICAgICAgICAgeTEgPSBtYXgoMSwgZmxvb3IoYmIoMikpKTsKICAgIHgyID0gbWluKFcsIGNlaWwoYmIoMSkgKyBiYigzKSAtIDEp'
        'KTsgeTIgPSBtaW4oSCwgY2VpbChiYigyKSArIGJiKDQpIC0gMSkpOwogICAgY20gPSBjb2xvck1hc2soeTE6eTIsIHgxOngyKTsKICAgIHNDb2'
        'wgID0gbm56KGNtKSAvIG51bWVsKGNtKTsgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAlIOminOiJsuS4gOiHtOaApwogICAgZFIgICAg'
        'PSBlZCh5MTp5MiwgeDE6eDIpOwogICAgZURlbiAgPSBubnooZFIpIC8gbnVtZWwoZFIpOyAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIC'
        'Ug6L6557yY5a+G5bqmKOWOn+Wni+WAvCkKICAgIHNFZGdlID0gbWluKDEsIGVEZW4gLyAwLjI1KTsKICAgIHNFeHQgID0gc3RhdHMoaykuRXh0'
        'ZW50OyAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAlIOefqeW9ouW6pgoKICAgIHNjb3JlID0gMC4zMCAqIHNBUiArIDAuMTUgKi'
        'BzQXJlYSArIDAuMjUgKiBzQ29sICsgMC4yMCAqIHNFZGdlICsgMC4xMCAqIHNFeHQ7CgogICAgaWYgc2NvcmUgPiBiZXN0U2NvcmUKICAgICAg'
        'ICBiZXN0U2NvcmUgPSBzY29yZTsKICAgICAgICBib3ggPSBiYjsKICAgICAgICByZWdpb25NYXNrID0gZmFsc2UoSCwgVyk7CiAgICAgICAgcm'
        'VnaW9uTWFzayhzdGF0cyhrKS5QaXhlbElkeExpc3QpID0gdHJ1ZTsKICAgICAgICBpbmZvID0gc3RydWN0KCdzY29yZScsIHNjb3JlLCAnc291'
        'cmNlJywgJycsICdhc3BlY3QnLCBhciwgLi4uCiAgICAgICAgICAgICAgICAgICAgICAnY29sb3JGcmFjJywgc0NvbCwgJ2VkZ2VEZW5zaXR5Jy'
        'wgZURlbiwgJ2VkZ2VzRnJhYycsIGVEZW4sIC4uLgogICAgICAgICAgICAgICAgICAgICAgJ2V4dGVudCcsIHNFeHQsICdhcmVhJywgYXJlYSwg'
        'J25DYW5kJywgMCwgLi4uCiAgICAgICAgICAgICAgICAgICAgICAndmFsaWQnLCBmYWxzZSwgJ3JlamVjdCcsICcnKTsKICAgIGVuZAplbmQKaW'
        'YgfmlzZW1wdHkoYm94KSwgaW5mby5uQ2FuZCA9IG5DYW5kOyBlbmQKZW5kCgpmdW5jdGlvbiBiYiA9IHJlZmluZUJveChtYXNrLCBiYikKJVJF'
        'RklORUJPWCDnlKjmjqnohpznmoTooYzliJfmipXlvbHmiorlgJnpgInmoYbmlLbntKfliLDovabniYzlrp7pmYXovrnnlYwKeDEgPSBtYXgoMS'
        'wgZmxvb3IoYmIoMSkpKTsgICAgICAgICAgICB5MSA9IG1heCgxLCBmbG9vcihiYigyKSkpOwp4MiA9IG1pbihzaXplKG1hc2ssIDIpLCBjZWls'
        'KGJiKDEpICsgYmIoMykgLSAxKSk7CnkyID0gbWluKHNpemUobWFzaywgMSksIGNlaWwoYmIoMikgKyBiYig0KSAtIDEpKTsKc3ViID0gbWFzay'
        'h5MTp5MiwgeDE6eDIpOwpjb2xTdW0gPSBzdW0oc3ViLCAxKTsgcm93U3VtID0gc3VtKHN1YiwgMik7CmlmIH5hbnkoY29sU3VtKSB8fCB+YW55'
        'KHJvd1N1bSksIHJldHVybjsgZW5kCmNzID0gZmluZChjb2xTdW0gPiAwLjI1ICogbWF4KGNvbFN1bSkpOwpycyA9IGZpbmQocm93U3VtID4gMC'
        '4yNSAqIG1heChyb3dTdW0pKTsKYmIgPSBbeDEgKyBjcygxKSAtIDEsIHkxICsgcnMoMSkgLSAxLCBjcyhlbmQpIC0gY3MoMSkgKyAxLCBycyhl'
        'bmQpIC0gcnMoMSkgKyAxXTsKZW5kCgpmdW5jdGlvbiByID0gZnJhY0NvbG9yKGJiLCBtKQolRlJBQ0NPTE9SIOe7n+iuoeahhuWGheafkOaOqe'
        'iGnOeahOWDj+e0oOWNoOavlApbSCwgV10gPSBzaXplKG0pOwp4MSA9IG1heCgxLCBmbG9vcihiYigxKSkpOyAgICAgICAgICAgIHkxID0gbWF4'
        'KDEsIGZsb29yKGJiKDIpKSk7CngyID0gbWluKFcsIGNlaWwoYmIoMSkgKyBiYigzKSAtIDEpKTsgeTIgPSBtaW4oSCwgY2VpbChiYigyKSArIG'
        'JiKDQpIC0gMSkpOwppZiB4MiA8IHgxIHx8IHkyIDwgeTEsIHIgPSAwOyByZXR1cm47IGVuZApzdWIgPSBtKHkxOnkyLCB4MTp4Mik7CnIgPSBu'
        'bnooc3ViKSAvIG51bWVsKHN1Yik7CmVuZAoKZnVuY3Rpb24gcyA9IGVtcHR5SW5mbygpCnMgPSBzdHJ1Y3QoJ3Njb3JlJywgLWluZiwgJ3NvdX'
        'JjZScsICcnLCAnYXNwZWN0JywgMCwgJ2NvbG9yRnJhYycsIDAsIC4uLgogICAgICAgICAgICAnZWRnZURlbnNpdHknLCAwLCAnZWRnZXNGcmFj'
        'JywgMCwgJ2V4dGVudCcsIDAsICdhcmVhJywgMCwgJ25DYW5kJywgMCwgLi4uCiAgICAgICAgICAgICd2YWxpZCcsIGZhbHNlLCAncmVqZWN0Jy'
        'wgJ+ayoeacieaJvuWIsOmVv+WuveavlC/pnaLnp6/lg4/ovabniYznmoTlgJnpgInljLrln58nKTsKZW5k'
    ),
    'core/lpr_main.m': (
        'ZnVuY3Rpb24gcmVzdWx0cyA9IGxwcl9tYWluKGltYWdlUGF0aCwgdmFyYXJnaW4pCiVMUFJfTUFJTiDovabniYzor4bliKvkuLvmtYHnqIs6IO'
        'ivu+WbviAtPiDlrprkvY0gLT4g5qCh5q2jIC0+IOWIhuWJsiAtPiDor4bliKsKJQolICAgcmVzdWx0cyA9IExQUl9NQUlOKGltYWdlUGF0aCkK'
        'JSAgIHJlc3VsdHMgPSBMUFJfTUFJTihpbWFnZVBhdGgsICdTaG93RmlndXJlJywgZmFsc2UsICdNYXhTaXplJywgMTYwMCkKJQolICAg6L+U5Z'
        'ue57uT5p6E5L2T5a2X5q61OgolICAgICAgIHRleHQgICAgICAgIOivhuWIq+WHuueahOi9pueJjOWPt+Wtl+espuS4sgolICAgICAgIGNoYXJz'
        'ICAgICAgIOavj+S4quWtl+espueahOivhuWIq+e7k+aenCAoY2VsbCkKJSAgICAgICBzY29yZXMgICAgICDmr4/kuKrlrZfnrKbnmoTljLnphY'
        '3nva7kv6HluqYgKDF4TiBkb3VibGUpCiUgICAgICAgcGxhdGVCb3ggICAg6L2m54mM5Zyo5Y6f5Zu+5Lit55qE5L2N572uIFt4IHkgdyBoXQol'
        'ICAgICAgIHBsYXRlSW1hZ2UgIOWAvuaWnOagoeato+WQjueahOi9pueJjOW9qeiJsuWbvgolICAgICAgIGNoYXJJbWFnZXMgIOWIhuWJsuWHuu'
        'eahOWtl+espuS6jOWAvOWbviAoY2VsbCwg5q+P5Liq5Li6IGxvZ2ljYWwpCiUKJSAgIOS+nei1ljogSW1hZ2UgUHJvY2Vzc2luZyBUb29sYm94'
        'CiUgICAgICAgICBDb21wdXRlciBWaXNpb24gVG9vbGJveCAo5LuF5qih5p2/55Sf5oiQ5pe255So5LqOIGluc2VydFRleHQsIOWPr+mAiSkKJQ'
        'olICAg56S65L6LOgolICAgICAgIGxwcl9tYWluKCdjYXIxLmpwZycpOwolICAgICAgIHIgPSBscHJfbWFpbignaW1hZ2VzL2RlbW9fcGxhdGUu'
        'anBnJywgJ1Nob3dGaWd1cmUnLCBmYWxzZSk7CiUKJSAgIOivtOaYjjog5pys5bel56iL6ZKI5a+55Lit5Zu95aSn6ZmG6L2m54mMICjok53lup'
        'Xnmb3lrZcgLyDnu7/lupXpu5HlrZcgLyDpu4TlupXpu5HlrZcpLAolICAgICAgICAg6L2/6L2m54Wn54mH5YiG6L6o546H5bu66K6uIDY0MH4y'
        'MDAwIOWDj+e0oOWuvSwg6L2m54mM5a695bqm5LiN5bCR5LqOIDEwMCDlg4/ntKDjgIIKCiUgLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLSDlj4'
        'LmlbDop6PmnpAgLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLQpwID0gaW5wdXRQYXJzZXI7CnAuYWRkUmVxdWlyZWQoJ2ltYWdlUGF0aCcpOwpw'
        'LmFkZFBhcmFtZXRlcignU2hvd0ZpZ3VyZScsIHRydWUpOwpwLmFkZFBhcmFtZXRlcignTWF4U2l6ZScsIDE2MDApOyAgICAgICUg6ZW/6L656Z'
        'mQ5Yi2LCDliqDpgJ/lpITnkIYKcC5wYXJzZShpbWFnZVBhdGgsIHZhcmFyZ2luezp9KTsKb3B0ID0gcC5SZXN1bHRzOwoKaW1hZ2VQYXRoID0g'
        'Y2hhcihpbWFnZVBhdGgpOwppZiBleGlzdChpbWFnZVBhdGgsICdmaWxlJykgfj0gMgogICAgZXJyb3IoJ2xwcjpmaWxlTm90Rm91bmQnLCAn5o'
        'm+5LiN5Yiw5Zu+5YOP5paH5Lu2OiAlcycsIGltYWdlUGF0aCk7CmVuZAoKJSAtLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tIDAuIOivu+WbvuS4'
        'jumihOWkhOeQhiAtLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tCkkgPSBpbXJlYWQoaW1hZ2VQYXRoKTsKSSA9IGVuc3VyZVJHQihJKTsKSSA9IG'
        'xpbWl0U2l6ZShJLCBvcHQuTWF4U2l6ZSk7CmZwcmludGYoJ1sxLzVdIOivu+WFpSAlcyAgKCVkIHggJWQpXG4nLCBpbWFnZVBhdGgsIHNpemUo'
        'SSwgMSksIHNpemUoSSwgMikpOwoKJSAtLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tIDEuIOi9pueJjOWumuS9jSAtLS0tLS0tLS0tLS0tLS0tLS'
        '0tLS0tLS0tCltwbGF0ZUJveCwgcGxhdGVNYXNrLCBzY0luZm9dID0gbG9jYXRlUGxhdGUoSSk7CmlmIGlzZW1wdHkocGxhdGVCb3gpIHx8IH5z'
        'Y0luZm8udmFsaWQKICAgICUg5a6a5L2N5Yiw5LqG5p+Q5Z2X5Yy65Z+fLCDkvYblroPkuI3mu6HotrPnnJ/ovabniYznmoTlj4zpl7jpl6go55'
        '+p5b2i5bqmICsg5bqV6Imy5Y2g5q+UKSwKICAgICUg5bGe5LqO5Zmq5aOwL+iTneWkqS/ot6/pnaLkuIDnsbvnmoTlgYflgJnpgIksIOi/memH'
        'jOaMieacquajgOa1i+WIsOi9pueJjOWkhOeQhuOAggogICAgd2h5ID0gc2NJbmZvLnJlamVjdDsKICAgIGlmIGlzZW1wdHkod2h5KQogICAgIC'
        'AgIHdoeSA9ICfmsqHmnInmib7liLDplb/lrr3mr5Qv6Z2i56ev5YOP6L2m54mM55qE5YCZ6YCJ5Yy65Z+fJzsKICAgIGVuZAogICAgZnByaW50'
        'ZignWzIvNV0g5pyq5qOA5rWL5Yiw6L2m54mMOiAlc1xuJywgd2h5KTsKICAgIHdhcm5pbmcoJ2xwcjpub1BsYXRlJywgJ+acquajgOa1i+WIsO'
        'i9pueJjDogJXPjgILlu7rorq7mjaLkuIDlvKDovabniYzmm7TmuIXmmbDjgIHmm7TlsYXkuK3nmoTnhafniYfjgIInLCB3aHkpOwogICAgcmVz'
        'dWx0cyA9IGVtcHR5UmVzdWx0KCk7CiAgICByZXN1bHRzLnJlYXNvbiA9IHdoeTsKICAgIHJlc3VsdHMuc2NvcmVJbmZvID0gc2NJbmZvOwogIC'
        'AgaWYgb3B0LlNob3dGaWd1cmUKICAgICAgICBmaWd1cmUoJ05hbWUnLCAn6L2m54mM6K+G5YirJywgJ051bWJlclRpdGxlJywgJ29mZicpOwog'
        'ICAgICAgIGltc2hvdyhJKTsgdGl0bGUoc3ByaW50Zign5pyq5qOA5rWL5Yiw6L2m54mMOiAlcycsIHdoeSksICdJbnRlcnByZXRlcicsICdub2'
        '5lJyk7CiAgICBlbmQKICAgIHJldHVybjsKZW5kCmZwcmludGYoJ1syLzVdIOi9pueJjOS9jee9rjogeD0lZCB5PSVkIHc9JWQgaD0lZCAo55+p'
        '5b2i5bqmICUuMmYsIOW6leiJsuWNoOavlCAlLjJmKVxuJywgLi4uCiAgICAgICAgcm91bmQocGxhdGVCb3gpLCBzY0luZm8uZXh0ZW50LCBzY0'
        'luZm8uY29sb3JGcmFjKTsKCiUgLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLSAyLiDoo4HliaogKyDlgL7mlpzmoKHmraMgLS0tLS0tLS0tLS0t'
        'LS0tLS0tLS0tLS0tLQpbcGxhdGVHcmF5LCBwbGF0ZUNvbG9yLCBwbGF0ZU1hc2tdID0gY3JvcFBsYXRlKEksIHBsYXRlQm94LCBwbGF0ZU1hc2'
        'spOwpbcGxhdGVHcmF5LCBwbGF0ZUNvbG9yLCBjaW5mb10gPSBjb3JyZWN0UGxhdGUocGxhdGVHcmF5LCBwbGF0ZUNvbG9yLCBwbGF0ZU1hc2sp'
        'OwpmcHJpbnRmKCdbMy81XSDmoKHmraPlkI7ovabniYw6ICVkIHggJWQgKOWHoOS9leagoeatozogJXMsIOWAvuinkiAlLjJmwrApXG4nLCAuLi'
        '4KICAgICAgICBzaXplKHBsYXRlR3JheSwgMSksIHNpemUocGxhdGVHcmF5LCAyKSwgY2luZm8ubW9kZSwgY2luZm8uc2tldyk7CgolIC0tLS0t'
        'LS0tLS0tLS0tLS0tLS0tLS0tLS0gMy4g5a2X56ym5YiG5YmyIC0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0KW2NoYXJJbWFnZXMsIGJ3UGxhdG'
        'UsIH4sIGlua0FSXSA9IHNlZ21lbnRDaGFycyhwbGF0ZUdyYXkpOwpmbXQgPSBwbGF0ZUZvcm1hdChudW1lbChjaGFySW1hZ2VzKSk7ICAgICAg'
        'ICAgICAgJSDmjInkvY3mlbDliKTlrprovabniYzliLblvI8oNyDkvY3mma7pgJogLyA4IOS9jeaWsOiDvea6kCkKZnByaW50ZignWzQvNV0g5Y'
        'iG5Ymy5Ye6ICVkIOS4quWtl+espiAo5Yi25byPOiAlcylcbicsIG51bWVsKGNoYXJJbWFnZXMpLCBmbXQubGFiZWwpOwoKJSAtLS0tLS0tLS0t'
        'LS0tLS0tLS0tLS0tLS0tIDQuIOWtl+espuivhuWIqyAtLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tCiUg5oyJ5Yi25byP6YCQ5L2N6ZmQ5a6a5Y'
        'CZ6YCJ5a2X56ym6ZuGLCDlubbnlKjloqjov7nlrr3pq5jmr5TnuqDmraMgMS80IOi/meexu+W9oueKtua3t+a3hgpbcGxhdGVUZXh0LCBjaGFy'
        'cywgc2NvcmVzXSA9IHJlY29nbml6ZUNoYXJzKGNoYXJJbWFnZXMsICdJbmtBUicsIGlua0FSLCAnRm9ybWF0JywgZm10KTsKaWYgaXNlbXB0eS'
        'hzY29yZXMpCiAgICBtcyA9IDA7CmVsc2UKICAgIG1zID0gbWVhbihzY29yZXMpOwplbmQKZnByaW50ZignWzUvNV0g6K+G5Yir57uT5p6cOiAl'
        'cyAoJXMsIOW5s+Wdh+e9ruS/oeW6piAlLjJmKVxuJywgcGxhdGVUZXh0LCBmbXQubGFiZWwsIG1zKTsKCiUgLS0tLS0tLS0tLS0tLS0tLS0tLS'
        '0tLS0tLSA1LiDnu4Tnu4fovpPlh7ogLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLQpyZXN1bHRzICAgICAgICAgPSBzdHJ1Y3QoKTsKcmVzdWx0'
        'cy50ZXh0ICAgID0gcGxhdGVUZXh0OwpyZXN1bHRzLmNoYXJzICAgPSBjaGFyczsKcmVzdWx0cy5zY29yZXMgID0gc2NvcmVzOwpyZXN1bHRzLn'
        'BsYXRlQm94PSBwbGF0ZUJveDsKcmVzdWx0cy5wbGF0ZUltYWdlID0gcGxhdGVDb2xvcjsKcmVzdWx0cy5jaGFySW1hZ2VzID0gY2hhckltYWdl'
        'czsKcmVzdWx0cy5mb3JtYXQgID0gZm10LmxhYmVsOwpyZXN1bHRzLmNoYXJDb3VudCA9IG51bWVsKGNoYXJJbWFnZXMpOwpyZXN1bHRzLmNvcn'
        'JlY3RJbmZvID0gY2luZm87CnJlc3VsdHMucmVhc29uICA9ICcnOwpyZXN1bHRzLnNjb3JlSW5mbyA9IHNjSW5mbzsKCmlmIG9wdC5TaG93Rmln'
        'dXJlCiAgICBzaG93UmVzdWx0KEksIHBsYXRlQm94LCBwbGF0ZUNvbG9yLCBid1BsYXRlLCBjaGFySW1hZ2VzLCBwbGF0ZVRleHQpOwplbmQKZW'
        '5kCgolID09PT09PT09PT09PT09PT09PT09PT09PSDku6XkuIvkuLrlsYDpg6jlh73mlbAgPT09PT09PT09PT09PT09PT09PT09PT09CgpmdW5j'
        'dGlvbiByID0gZW1wdHlSZXN1bHQoKQpyID0gc3RydWN0KCk7CnIudGV4dCA9ICcnOyByLmNoYXJzID0ge307IHIuc2NvcmVzID0gW107IHIucG'
        'xhdGVCb3ggPSBbXTsKci5wbGF0ZUltYWdlID0gW107IHIuY2hhckltYWdlcyA9IHt9OwpyLmZvcm1hdCA9ICcnOyByLmNoYXJDb3VudCA9IDA7'
        'CnIuY29ycmVjdEluZm8gPSBzdHJ1Y3QoJ21vZGUnLCAnbm9uZScsICdxdWFkJywgW10sICdza2V3JywgMCk7CnIucmVhc29uID0gJyc7IHIuc2'
        'NvcmVJbmZvID0gc3RydWN0KCk7CmVuZAoKZnVuY3Rpb24gSSA9IGVuc3VyZVJHQihJKQolIOe7n+S4gOaIkCB1aW50OCDkuInpgJrpgZPlm77l'
        'g48KaWYgaXNtYXRyaXgoSSkKICAgIEkgPSByZXBtYXQoSSwgMSwgMSwgMyk7CmVsc2VpZiBzaXplKEksIDMpID09IDIKICAgIEkgPSByZXBtYX'
        'QoSSg6LCA6LCAxKSwgMSwgMSwgMyk7CmVsc2VpZiBzaXplKEksIDMpID4gMwogICAgSSA9IEkoOiwgOiwgMTozKTsKZW5kCmlmIH5pc2EoSSwg'
        'J3VpbnQ4JykKICAgIEkgPSBpbTJ1aW50OChJKTsKZW5kCmVuZAoKZnVuY3Rpb24gSSA9IGxpbWl0U2l6ZShJLCBtYXhTaXplKQptID0gbWF4KH'
        'NpemUoSSwgMSksIHNpemUoSSwgMikpOwppZiBtID4gbWF4U2l6ZQogICAgSSA9IGltcmVzaXplKEksIG1heFNpemUgLyBtLCAnYmlsaW5lYXIn'
        'KTsKZW5kCmVuZAoKZnVuY3Rpb24gc2hvd1Jlc3VsdChJLCBib3gsIHBsYXRlQ29sb3IsIGJ3UGxhdGUsIGNoYXJJbWFnZXMsIHBsYXRlVGV4dC'
        'kKZmlndXJlKCdOYW1lJywgJ+i9pueJjOivhuWIq+e7k+aenCcsICdOdW1iZXJUaXRsZScsICdvZmYnKTsKCnN1YnBsb3QoMiwgMywgMSk7Cmlt'
        'c2hvdyhJKTsgdGl0bGUoJ+WOn+WbviArIOWumuS9jeahhicpOwpyZWN0YW5nbGUoJ1Bvc2l0aW9uJywgYm94LCAnRWRnZUNvbG9yJywgJ3knLC'
        'AnTGluZVdpZHRoJywgMik7CgpzdWJwbG90KDIsIDMsIDIpOwppbXNob3cocGxhdGVDb2xvcik7IHRpdGxlKCfmoKHmraPlkI7nmoTovabniYwn'
        'KTsKCnN1YnBsb3QoMiwgMywgMyk7Cmltc2hvdyhid1BsYXRlKTsgdGl0bGUoJ+S6jOWAvOWMliAo5a2X56ymPeeZvSknKTsKCnN1YnBsb3QoMi'
        'wgMywgWzQgNSA2XSk7CmlmIGlzZW1wdHkoY2hhckltYWdlcykKICAgIGF4aXMgb2ZmOyB0ZXh0KDAuMDUsIDAuNSwgJ+acquWIhuWJsuWHuuWt'
        'l+espicsICdGb250U2l6ZScsIDEyKTsKZWxzZQogICAgbW9zYWljID0gW107CiAgICBmb3IgayA9IDE6bnVtZWwoY2hhckltYWdlcykKICAgIC'
        'AgICBjaCA9IGNoYXJJbWFnZXN7a307CiAgICAgICAgaWYgaXNlbXB0eShtb3NhaWMpCiAgICAgICAgICAgIG1vc2FpYyA9IGNoOwogICAgICAg'
        'IGVsc2UKICAgICAgICAgICAgbW9zYWljID0gW21vc2FpYywgZmFsc2Uoc2l6ZShjaCwgMSksIDQpLCBjaF07ICUjb2s8QUdST1c+CiAgICAgIC'
        'AgZW5kCiAgICBlbmQKICAgIG1vc2FpYyA9IGltcmVzaXplKG1vc2FpYywgMywgJ25lYXJlc3QnKTsgICAlIOaUvuWkp+S+v+S6juafpeeciwog'
        'ICAgaW1zaG93KG1vc2FpYyk7CiAgICB0aXRsZShbJ+ivhuWIq+e7k+aenDogJyBwbGF0ZVRleHRdKTsKZW5kCmVuZAo='
    ),
    'core/normalizeChar.m': (
        'ZnVuY3Rpb24gaW1nID0gbm9ybWFsaXplQ2hhcihid0NoYXIsIG91dEgsIG91dFcsIG1hcmdpbiwgbW9kZSkKJU5PUk1BTElaRUNIQVIg5Y2V5L'
        'iq5a2X56ym5b2S5LiA5YyWOiDoo4HliLDmnIDlsI/lpJbmjqXnn6nlvaIgLT4g57yp5pS+IC0+IOWxheS4reaUvuWIsOWbuuWumueUu+W4gwol'
        'CiUgICBpbWcgPSBOT1JNQUxJWkVDSEFSKGJ3Q2hhcikgICAgICAgICAgICAgICAlIDMyeDE2LCDmi4nkvLjloavmu6EKJSAgIGltZyA9IE5PUk'
        '1BTElaRUNIQVIoYndDaGFyLCAzMiwgMTYsIDIsICdmaWxsJykKJQolICAgbW9kZSA9ICdmaWxsJyA6IOijgeaOieepuueZveWQjuebtOaOpeaL'
        'ieS8uOWIsCAob3V0SC0ybSkgeCAob3V0Vy0ybSnjgILmqKHmnb/nlKjns7vnu5/lrZfkvZPjgIEKJSAgICAgICAgICAgICAgICAgICDlrp7mtY'
        'vlrZfnrKbmnaXoh6rovabniYzlrZfkvZMsIOS4pOiAhemVv+WuveavlOW+gOW+gOS4jeWQjDsg5ouJ5Ly45aGr5ruh5Y+v5Lul5oqKCiUgICAg'
        'ICAgICAgICAgICAgICAg6ZW/5a695q+U5beu5byCIuW9kuS4gOWMliLmjoksIOWMuemFjeaYjuaYvuabtOeosyjmjqjojZAp44CCCiUgICBtb2'
        'RlID0gJ2tlZXAnIDog5L+d5oyB6ZW/5a695q+U562J5q+U57yp5pS+5bm25bGF5Lit44CCCiUKJSAgIOaooeadv+eUn+aIkOS4juWunua1i+Wt'
        'l+espuW/hemhu+eUqOWQjOS4gOWll+W9kuS4gOWMluWPguaVsCwg5ZCm5YiZ54m55b6B5LiN5Y+v5q+U44CCCgppZiBuYXJnaW4gPCAyIHx8IG'
        'lzZW1wdHkob3V0SCksICAgb3V0SCA9IDMyOyAgIGVuZAppZiBuYXJnaW4gPCAzIHx8IGlzZW1wdHkob3V0VyksICAgb3V0VyA9IDE2OyAgIGVu'
        'ZAppZiBuYXJnaW4gPCA0IHx8IGlzZW1wdHkobWFyZ2luKSwgbWFyZ2luID0gMjsgIGVuZAppZiBuYXJnaW4gPCA1IHx8IGlzZW1wdHkobW9kZS'
        'ksICAgbW9kZSA9ICdmaWxsJzsgZW5kCgppbWcgPSBmYWxzZShvdXRILCBvdXRXKTsKaWYgaXNlbXB0eShid0NoYXIpIHx8IH5hbnkoYndDaGFy'
        'KDopKSwgcmV0dXJuOyBlbmQKCltyLCBjXSA9IGZpbmQoYndDaGFyKTsKYndDaGFyID0gYndDaGFyKG1pbihyKTptYXgociksIG1pbihjKTptYX'
        'goYykpOwpbaCwgd10gPSBzaXplKGJ3Q2hhcik7CgphdmFpbEggPSBvdXRIIC0gMiAqIG1hcmdpbjsKYXZhaWxXID0gb3V0VyAtIDIgKiBtYXJn'
        'aW47CgppZiBzdHJjbXBpKG1vZGUsICdrZWVwJykKICAgIHMgID0gbWluKGF2YWlsSCAvIGgsIGF2YWlsVyAvIHcpOwogICAgbmggPSBtYXgoMS'
        'wgcm91bmQoaCAqIHMpKTsKICAgIG53ID0gbWF4KDEsIHJvdW5kKHcgKiBzKSk7CmVsc2UKICAgIG5oID0gYXZhaWxIOwogICAgbncgPSBhdmFp'
        'bFc7CmVuZAoKc21hbGwgPSBpbXJlc2l6ZShkb3VibGUoYndDaGFyKSwgW25oIG53XSwgJ2JpbGluZWFyJykgPiAwLjU7CgpyMCA9IGZsb29yKC'
        'hvdXRIIC0gbmgpIC8gMikgKyAxOwpjMCA9IGZsb29yKChvdXRXIC0gbncpIC8gMikgKyAxOwppbWcocjA6cjAgKyBuaCAtIDEsIGMwOmMwICsg'
        'bncgLSAxKSA9IHNtYWxsOwplbmQ='
    ),
    'core/plateFormat.m': (
        'ZnVuY3Rpb24gRiA9IHBsYXRlRm9ybWF0KG4pCiVQTEFURUZPUk1BVCDkuK3lm73ovabniYzliLblvI86IOWtl+espuS9jeaVsOS4juavj+S4gO'
        'S9jeWFgeiuuOWHuueOsOeahOWtl+espumbhgolCiUgICBGID0gUExBVEVGT1JNQVQobikgICAgIG4gPSDliIblibLlh7rnmoTlrZfnrKbmlbAo'
        '6YCa5bi4IDcg5oiWIDgpCiUKJSAgIOi/lOWbnue7k+aehDoKJSAgICAgICAubiAgICAgICDlrZfnrKbkvY3mlbAKJSAgICAgICAubGFiZWwgIC'
        'DliLblvI/lkI3np7Ao57uZ55WM6Z2iL+aXpeW/l+eUqCkKJSAgICAgICAuc2V0cyAgICAxeE4gY2VsbCwg5q+P5LiA5L2N5YWB6K645Ye6546w'
        '55qE5a2X56ymKOWtl+espuS4suW9ouW8jykKJQolICAg5L6d5o2uKOWFrOWuiemDqCBHQSAzNi0yMDE4KToKJSAgICAgICDmma7pgJrmsb3ova'
        'Yo6JOd5bqVL+m7hOW6leWNleaOkikgIDcg5L2NID0g55yB566A56ewICsg5Y+R54mM5py65YWz5a2X5q+NICsgNSDkvY3lrZfmr43mlbDlrZcK'
        'JSAgICAgICDmlrDog73mupDlsI/lnovovaYo57u/5bqV5Y2V5o6SKSAgIDgg5L2NID0g55yB566A56ewICsg5Y+R54mM5py65YWz5a2X5q+NIC'
        'sg5a2X5q+NICsgNSDkvY3lrZfmr43mlbDlrZcKJSAgIOWtl+avjeihqOmHjOS4jeWQqyBJIOWSjCBPIOKAlOKAlCDovabniYzkuI3kvb/nlKjo'
        'v5nkuKTkuKrlrZfmr40o5piT5LiOIDHjgIEwIOa3t+a3hinjgIIKJQolICAg6L+Z5Liq6KGo5pyJ5Lik5Liq55So5aSEOgolICAgICAxKSByZW'
        'NvZ25pemVDaGFycyDmjInkvY3nuqbmnZ/lgJnpgInmqKHmnb8sIOWHj+WwkSLmlbDlrZfkvY3orqTmiJDlrZfmr40i6L+Z57G76ZSZOwolICAg'
        'ICAyKSDnu5PmnpzmoKHpqow6IOiupOWHuuadpeeahOWtl+espuS4jeWcqOivpeS9jeWFgeiuuOmbhuWQiOmHjOaXtiwg6K+05piO6L+Z5LiA5L'
        '2N5Y+v55aR44CCCgpwcm92ICAgID0gJ+S6rOa0peWGgOaZi+iSmei+veWQiem7keayquiLj+a1meealumXvei1o+mygeixq+mEgua5mOeypOah'
        'gueQvOa4neW3nei0teS6keiXj+mZleeUmOmdkuWugeaWsCc7CmxldHRlcnMgPSAnQUJDREVGR0hKS0xNTlBRUlNUVVZXWFlaJzsgICAgICAgIC'
        'AgICUg5Y675o6JIEkgTwphbG51bSAgID0gW2xldHRlcnMsICcwMTIzNDU2Nzg5J107CgpGID0gc3RydWN0KCduJywgbiwgJ2xhYmVsJywgJycs'
        'ICdzZXRzJywge3t9fSk7CnN3aXRjaCBuCiAgICBjYXNlIDgKICAgICAgICBGLmxhYmVsID0gJ+aWsOiDvea6kCA4IOS9jSc7CiAgICAgICAgRi'
        '5zZXRzICA9IHtwcm92LCBsZXR0ZXJzLCBsZXR0ZXJzLCBhbG51bSwgYWxudW0sIGFsbnVtLCBhbG51bSwgYWxudW19OwogICAgY2FzZSA3CiAg'
        'ICAgICAgRi5sYWJlbCA9ICfmma7pgJogNyDkvY0nOwogICAgICAgIEYuc2V0cyAgPSB7cHJvdiwgbGV0dGVycywgYWxudW0sIGFsbnVtLCBhbG'
        '51bSwgYWxudW0sIGFsbnVtfTsKICAgIG90aGVyd2lzZQogICAgICAgICUg5rKh5pS25b2V55qE5L2N5pWwKOWPjOaOkum7hOeJjOWIh+mUmeOA'
        'geWbvueJh+aciemXrumimOetiSk6IOWPque7meS4quWuveadvuinhOWImSwg5LiN56Gs5aWXCiAgICAgICAgRi5sYWJlbCA9IHNwcmludGYoJy'
        'VkIOS9jSjmnKrmlLblvZXliLblvI8pJywgbik7CiAgICAgICAgRi5zZXRzICA9IGNlbGwoMSwgbik7CiAgICAgICAgaWYgbiA+PSAxLCBGLnNl'
        'dHN7MX0gPSBwcm92OyAgICBlbmQKICAgICAgICBpZiBuID49IDIsIEYuc2V0c3syfSA9IGxldHRlcnM7IGVuZAogICAgICAgIGZvciBrID0gMz'
        'puLCBGLnNldHN7a30gPSBhbG51bTsgZW5kCmVuZAplbmQK'
    ),
    'core/recognizeChars.m': (
        'ZnVuY3Rpb24gW3BsYXRlVGV4dCwgY2hhcnMsIHNjb3Jlc10gPSByZWNvZ25pemVDaGFycyhjaGFySW1hZ2VzLCB2YXJhcmdpbikKJVJFQ09HTk'
        'laRUNIQVJTIOWtl+espuivhuWIqyjmqKHmnb/ljLnphY0gKyDovabniYzliLblvI/mjInkvY3nuqbmnZ8pCiUKJSAgIFtwbGF0ZVRleHQsIGNo'
        'YXJzLCBzY29yZXNdID0gUkVDT0dOSVpFQ0hBUlMoY2hhckltYWdlcykKJSAgIFtwbGF0ZVRleHQsIGNoYXJzLCBzY29yZXNdID0gUkVDT0dOSV'
        'pFQ0hBUlMoY2hhckltYWdlcywgJ0lua0FSJywgaW5rQVIsICdGb3JtYXQnLCBGKQolCiUgICDlj6/pgInlj4LmlbA6CiUgICAgICAgJ1RlbXBs'
        'YXRlRmlsZScgIOaooeadv+aWh+S7tui3r+W+hCwg6buY6K6k55So5ZCM55uu5b2V55qEIHRlbXBsYXRlcy5tYXQKJSAgICAgICAnSW5rQVInIC'
        'AgICAgICAgMXhOLCDmr4/kuKrlrZfnrKbloqjov7nlpJbmjqXmoYbnmoTlrr3pq5jmr5Qoc2VnbWVudENoYXJzIOeahOesrCA0IOS4qui+k+WH'
        'uikKJSAgICAgICAnRm9ybWF0JyAgICAgICAgcGxhdGVGb3JtYXQoKSDnmoTov5Tlm57lgLwsIOaMieS9jemZkOWItuWAmemAieWtl+espumbhg'
        'olCiUgICDkvY3nva7nuqblrpoo5oyJ5Lit5Zu95aSn6ZmG6L2m54mMOyDnlLEgRm9ybWF0IOe7meWHuiwg5LiN5LygIEZvcm1hdCDml7bnlKjk'
        'uIvpnaLnmoTpu5jorqTop4TliJkpOgolICAgICAgIOesrCAxIOS9jSA6IOS4reaWh+ecgeS7veeugOensCgzMSDkuKopICAgLT4g55SoIGNoaW'
        '5lc2Ug5qih5p2/CiUgICAgICAg56ysIDIg5L2NIDog5Y+R54mM5py65YWz5Luj5Y+3KOWkp+WGmeWtl+avjSkgLT4g55SoIGxldHRlcnMg5qih'
        '5p2/CiUgICAgICAg56ysIDMg5L2N6LW3OiDlrZfmr43miJbmlbDlrZco5Y675o6JIEnjgIFPKSAtPiDnlKggYWxudW0g5qih5p2/CiUgICAgIC'
        'Ag5paw6IO95rqQIDgg5L2N54mM55qE56ysIDMg5L2N5Lmf5piv5a2X5q+NKOingSBwbGF0ZUZvcm1hdC5tKQolCiUgICDljLnphY3lvpfliIYg'
        'PSAwLjY1ICogSW9VKOS6jOWAvOS6pOW5tuavlCkgKyAwLjM1ICog55u45YWz57O75pWwCiUgICDmqKHmnb/mlofku7bkuI3lrZjlnKjml7boh6'
        'rliqjosIPnlKggYnVpbGRUZW1wbGF0ZXMg55Sf5oiQIHRlbXBsYXRlcy5tYXQKCnAgPSBpbnB1dFBhcnNlcjsKcC5hZGRQYXJhbWV0ZXIoJ1Rl'
        'bXBsYXRlRmlsZScsICcnKTsKcC5hZGRQYXJhbWV0ZXIoJ0lua0FSJywgW10pOwpwLmFkZFBhcmFtZXRlcignRm9ybWF0JywgW10pOwpwLnBhcn'
        'NlKHZhcmFyZ2luezp9KTsKb3B0ID0gcC5SZXN1bHRzOwoKaWYgaXNlbXB0eShvcHQuVGVtcGxhdGVGaWxlKQogICAgb3B0LlRlbXBsYXRlRmls'
        'ZSA9IGZ1bGxmaWxlKGZpbGVwYXJ0cyhtZmlsZW5hbWUoJ2Z1bGxwYXRoJykpLCAndGVtcGxhdGVzLm1hdCcpOwplbmQKUyA9IGxvYWRUZW1wbG'
        'F0ZXMob3B0LlRlbXBsYXRlRmlsZSk7CgpuID0gbnVtZWwoY2hhckltYWdlcyk7CmNoYXJzICA9IHJlcG1hdCh7Jz8nfSwgMSwgbik7CnNjb3Jl'
        'cyA9IHplcm9zKDEsIG4pOwoKZm9yIGsgPSAxOm4KICAgIGYgICA9IGNoYXJGZWF0dXJlKGNoYXJJbWFnZXN7a30pOwogICAgc2V0ID0gcG9zaX'
        'Rpb25TZXQoUywgb3B0LkZvcm1hdCwgayk7ICAgICAgJSDlj6rlnKjor6XkvY3lhYHorrjnmoTlrZfnrKbph4zmib7mnIDkvJgKICAgIFtsYiwg'
        'c2NdID0gYmVzdE1hdGNoKGYsIHNldCk7CgogICAgJSDmnoHnu4bplb/nmoTloqjov7nmrrXln7rmnKzlj6rlj6/og73mmK/mlbDlrZcgMSDigJ'
        'TigJQg6L2m54mM5LiK5ZSv5LiAIuWPiOeqhOWPiOmrmCLnmoTlrZfnrKblsLHmmK8gMSwKICAgICUg5a2X5q+NIEkg5bey6KKr5o6S6Zmk44CC'
        'bm9ybWFsaXplQ2hhciDnmoQgJ2ZpbGwnIOaooeW8j+S8muaKiui/meenjeeqhOauteaoquWQkeaLieWuveaIkOWunuW/gwogICAgJSDmlrnlnZ'
        'co5a6e5rWL55So5oi354Wn54mH6YeMIEFSPTAuMTMyIOeahCAxIOiiq+aLieWuveWQjuabtOWDjyA4LzQvMCwg5a+55qih5p2/ICIxIiDnmoTl'
        'vpfliIYKICAgICUg5Y+q5pyJIDAuMzEsIOiAjCA4IOaLv+WIsCAwLjUxKSwg5omA5Lul6L+Z6YeM5oyJ5a695bqm5YiG5Lik5qGj5aSE55CGOg'
        'ogICAgJSAgIEFSIDwgMC4yMiAgOiDnm7TmjqXliKTlrprkuLogMeOAgumAkOWbvuWunua1iyjop4EgUkVBRE1FIuW3suefpemZkOWItiIp5omA'
        '5pyJ5pWw5o2u6ZuG6YeMCiAgICAlICAgICAgICAgICAgICAgIOesrCAyIOS9jei1t+OAgUFSIDwgMC4yMiDnmoTmrrUgMTAwJSDpg73mmK8gMe'
        'OAggogICAgJSAgIDAuMjJ+MC4zMCAgOiDlj6rnu5kgIjEiIOS4gOS4quWwj+WKoOWIhiwg5LiN5by65Yi2IOKAlOKAlCDmlrDog73mupDniYzo'
        'oqvnurXlkJHljovnvKnml7YKICAgICUgICAgICAgICAgICAgICAgRC9RLzUvOS9GL0cg55qEIEFSIOS5n+S8muaOieWIsCAwLjI1IOW3puWPsy'
        'wg5by65Yi25Lya6K+v5Lyk5a6D5Lus44CCCiAgICBpZiBrID49IDIgJiYgbnVtZWwob3B0Lklua0FSKSA+PSBrICYmIG9wdC5JbmtBUihrKSA+'
        'IDAgJiYgb3B0Lklua0FSKGspIDwgMC4zMAogICAgICAgIHN1YiA9IHJlc3RyaWN0KHNldCwgJzEnKTsKICAgICAgICBpZiB+aXNlbXB0eShzdW'
        'IubGFiZWwpCiAgICAgICAgICAgIFtsYjEsIHNjMV0gPSBiZXN0TWF0Y2goZiwgc3ViKTsKICAgICAgICAgICAgaWYgb3B0Lklua0FSKGspIDwg'
        'MC4yMgogICAgICAgICAgICAgICAgbGIgPSBsYjE7ICBzYyA9IHNjMTsKICAgICAgICAgICAgZWxzZWlmIHNjMSArIDAuMDYgPiBzYwogICAgIC'
        'AgICAgICAgICAgbGIgPSBsYjE7ICBzYyA9IHNjMTsKICAgICAgICAgICAgZW5kCiAgICAgICAgZW5kCiAgICBlbmQKCiAgICAlIOmmluS9jeax'
        'ieWtl+e9ruS/oeW6pui/h+S9jiAtPiDnlKggKiDljaDkvY0sIOaPkOekuui/meS4gOS9jeS4jeWPr+S/oQogICAgaWYgayA9PSAxICYmIHNjID'
        'wgMC40NQogICAgICAgIGxiID0gJyonOwogICAgZW5kCgogICAgY2hhcnN7a30gID0gbGI7CiAgICBzY29yZXMoaykgPSBzYzsKZW5kCgpwbGF0'
        'ZVRleHQgPSBbY2hhcnN7On1dOwplbmQKCiUgPT09PT09PT09PT09PT09PT09PT09PT09IOWxgOmDqOWHveaVsCA9PT09PT09PT09PT09PT09PT'
        '09PT09PT0KCmZ1bmN0aW9uIHNldCA9IHBvc2l0aW9uU2V0KFMsIEYsIGspCiVQT1NJVElPTlNFVCDlj5bnrKwgayDkvY3lhYHorrjnmoTmqKHm'
        'nb/pm4blkIg6IOWFiOaMieS9jemAieaooeadv+e7hCwg5YaN55So5Yi25byP5a2X56ym5Liy5pS256qECmlmIGsgPT0gMQogICAgc2V0ID0gUy'
        '5jaGluZXNlOwplbHNlaWYgayA9PSAyCiAgICBzZXQgPSBTLmxldHRlcnM7CmVsc2UKICAgIHNldCA9IFMuYWxudW07CmVuZAppZiB+aXNlbXB0'
        'eShGKSAmJiBpc3N0cnVjdChGKSAmJiBpc2ZpZWxkKEYsICdzZXRzJykgJiYgLi4uCiAgICAgICAgayA8PSBudW1lbChGLnNldHMpICYmIH5pc2'
        'VtcHR5KEYuc2V0c3trfSkKICAgIG5hcnJvdyA9IHJlc3RyaWN0KHNldCwgRi5zZXRze2t9KTsKICAgIGlmIH5pc2VtcHR5KG5hcnJvdy5sYWJl'
        'bCkKICAgICAgICBzZXQgPSBuYXJyb3c7CiAgICBlbmQKZW5kCmVuZAoKZnVuY3Rpb24gc2V0ID0gcmVzdHJpY3Qoc2V0LCBhbGxvd2VkKQolUk'
        'VTVFJJQ1Qg5Y+q5L+d55WZ5qCH562+5Ye6546w5ZyoIGFsbG93ZWQg6YeM55qE5qih5p2/OyDml6DkuqTpm4bml7bljp/moLfov5Tlm54KaWYg'
        'aXNlbXB0eShhbGxvd2VkKSB8fCBpc2VtcHR5KHNldC5sYWJlbCksIHJldHVybjsgZW5kCmtlZXAgPSBmYWxzZSgxLCBudW1lbChzZXQubGFiZW'
        'wpKTsKZm9yIGkgPSAxOm51bWVsKHNldC5sYWJlbCkKICAgIGtlZXAoaSkgPSBhbnkoc2V0LmxhYmVse2l9ID09IGFsbG93ZWQpOwplbmQKaWYg'
        'YW55KGtlZXApCiAgICBzZXQubGFiZWwgICA9IHNldC5sYWJlbChrZWVwKTsKICAgIHNldC5mZWF0dXJlID0gc2V0LmZlYXR1cmUoa2VlcCk7Cm'
        'VuZAplbmQKCmZ1bmN0aW9uIFMgPSBsb2FkVGVtcGxhdGVzKGYpCmlmIGV4aXN0KGYsICdmaWxlJykgfj0gMgogICAgZnByaW50ZignICAgIOao'
        'oeadv+aWh+S7tuS4jeWtmOWcqCwg5q2j5Zyo55Sf5oiQ5qih5p2/OiAlc1xuJywgZik7CiAgICBidWlsZFRlbXBsYXRlcyhmKTsKZW5kClMgPS'
        'Bsb2FkKGYpOwppZiB+aXNmaWVsZChTLCAnYWxudW0nKQogICAgUy5hbG51bSA9IGNvbWJpbmVTZXRzKFMubGV0dGVycywgUy5kaWdpdHMpOwpl'
        'bmQKZW5kCgpmdW5jdGlvbiBzID0gY29tYmluZVNldHMoYSwgYikKcy5sYWJlbCAgID0gW2EubGFiZWwsIGIubGFiZWxdOwpzLmZlYXR1cmUgPS'
        'BbYS5mZWF0dXJlLCBiLmZlYXR1cmVdOwplbmQKCmZ1bmN0aW9uIFtsYWJlbCwgc2NvcmVdID0gYmVzdE1hdGNoKGYsIHNldCkKbGFiZWwgPSAn'
        'Pyc7IHNjb3JlID0gLWluZjsKJSDkuozlgLzljJbpmIjlgLzkv53mjIEgMC41KOaooeadv+S5n+eUqOWugynjgILmm77nu4/kuLrkuobmib7lm5'
        '7ooqvpmY3ph4fmoLci56Oo5rehIueahCAxIHB4IOe7huaoqueUuwolIOaKiuWug+mZjeWIsCAwLjM1fjAuNDUsIOS9huWunua1iyjop4EgUkVB'
        'RE1FIuW3suefpemZkOWItiIp5Zyo5L+u5aW95Y675aSW5qGG5LmL5ZCOOgolICAg6ZiI5YC8IDAuNTAgLT4gYmVuY2ggMC44NTAvMC45NzksIG'
        'hhcmQgMC42NjcvMC44MzMsIHJlYWwg5a2X56ymIDAuMDQ1CiUgICDpmIjlgLwgMC4zNSAtPiBiZW5jaCAwLjg1MC8wLjk3OSwgaGFyZCAwLjUw'
        'MC8wLjgwNSwgcmVhbCDlrZfnrKYgMC4wMDAKJSDpmY3pmIjlgLzlj6rkvJrmiornrJTnlLvlloLog5YoMyDlj5ggOOOAgTAg5Y+YIDkpLCDmiY'
        'Dku6Xnu7TmjIEgMC4144CCCmZiID0gZiA+IDAuNTsKZm9yIGkgPSAxOm51bWVsKHNldC5sYWJlbCkKICAgIGcgID0gc2V0LmZlYXR1cmV7aX07'
        'CiAgICBnYiA9IGcgPiAwLjU7CiAgICB1bmkgPSBubnooZmIgfCBnYik7CiAgICBpZiB1bmkgPT0gMCwgY29udGludWU7IGVuZAogICAgaW91ID'
        '0gbm56KGZiICYgZ2IpIC8gdW5pOwoKICAgIGEgPSBmIC0gbWVhbihmKTsKICAgIGIgPSBnIC0gbWVhbihnKTsKICAgIGQgPSBub3JtKGEpICog'
        'bm9ybShiKTsKICAgIGlmIGQgPCBlcHMKICAgICAgICBjYyA9IDA7CiAgICBlbHNlCiAgICAgICAgY2MgPSAoYSAqIGInKSAvIGQ7CiAgICBlbm'
        'QKCiAgICBzYyA9IDAuNjUgKiBpb3UgKyAwLjM1ICogY2M7CiAgICBpZiBzYyA+IHNjb3JlCiAgICAgICAgc2NvcmUgPSBzYzsKICAgICAgICBs'
        'YWJlbCA9IHNldC5sYWJlbHtpfTsKICAgIGVuZAplbmQKZW5kCg=='
    ),
    'core/segmentChars.m': (
        'ZnVuY3Rpb24gW2NoYXJJbWFnZXMsIGJ3UGxhdGUsIGJvdW5kcywgaW5rQVJdID0gc2VnbWVudENoYXJzKHBsYXRlR3JheSkKJVNFR01FTlRDSE'
        'FSUyDovabniYzlrZfnrKbliIblibIo5Z6C55u05oqV5b2x5rOVKQolCiUgICBbY2hhckltYWdlcywgYndQbGF0ZSwgYm91bmRzXSA9IFNFR01F'
        'TlRDSEFSUyhwbGF0ZUdyYXkpCiUgICBwbGF0ZUdyYXkgOiDlt7LmoKHmraPjgIHpq5jluqblvZLkuIDljJYoNjQp55qE54Gw5bqm6L2m54mMCi'
        'UgICBjaGFySW1hZ2VzOiAxeE4gY2VsbCwg5q+P5Liq5YWD57Sg5pivIDMyeDE2IGxvZ2ljYWwg55qE5b2S5LiA5YyW5a2X56ymCiUgICBid1Bs'
        'YXRlICAgOiDkuozlgLzljJbnu5Pmnpwo5a2X56ymPeeZvSksIOS+v+S6juiwg+ivleafpeeciwolICAgYm91bmRzICAgIDogTiB4IDIsIOavj+'
        'S4quWtl+espuWcqOi9pueJjOS4reeahOWIl+WMuumXtCBb6LW3IOatol0KJSAgIGlua0FSICAgICA6IDF4TiBkb3VibGUsIOavj+S4quWtl+es'
        'puWiqOi/ueWkluaOpeahhueahOWuvemrmOavlCjnu4bplb/mrrUgLT4g5pWw5a2XIDEpCiUKJSAgIOa1geeoizog5YWJ54Wn5Z2H6KGhIC0+IE'
        '90c3Ug5LqM5YC85YyWIC0+IOe7n+S4gOaegeaApyAtPiDljrvlpJbmoYYgLT4g5Y675ZmqCiUgICAgICAgICAtPiDlnoLnm7TmipXlvbHlj5bl'
        'rZfnrKbmrrUgLT4g6Ieq5qCh5YeG5a6a5a2X5pWw5bm25ouG5YiGL+WQiOW5tiAtPiDlvZLkuIDljJYKJQolICAg5YWz6ZSu5Y+C5pWw6YO95Z'
        'yo5LiL6Z2i5rOo6YeK6YeM5qCH5LqGLCDliIblibLkuI3lr7nml7bkvJjlhYjosIPov5nkuInlpIQ6CiUgICAgIDEpIG1lcmdlUnVucyDnmoTp'
        'l7TpmpnpmIjlgLwgICjmsYnlrZfooqvliIfmlaMgLT4g6LCD5aSnOyDnm7jpgrvlrZfnspjov54gLT4g6LCD5bCPKQolICAgICAyKSDliJfpmI'
        'jlgLwgdGhyICAgICAgICAgICAgKOeqhOeslOeUu+WmguaVsOWtlyAxIOeahOaSh+iiq+WIh+aOiSAtPiDosIPlsI8pCiUgICAgIDMpIGNob29z'
        'ZUNvdW50IOeahOWAmemAieiMg+WbtCAo55uu5YmNIDZ+OCwg5Y+M5o6S54mM5YiH6ZSZ5pe25Y+v5Li05pe25pS256qEKQoKaWYgc2l6ZShwbG'
        'F0ZUdyYXksIDMpID4gMQogICAgcGxhdGVHcmF5ID0gcmdiMmdyYXkocGxhdGVHcmF5KTsKZW5kCltILCBXXSA9IHNpemUocGxhdGVHcmF5KTsK'
        'CiUgLS0tLS0tLS0tLS0tLS0tLSAxLiDlhYnnhaflnYfooaEgKyDkuozlgLzljJYgLS0tLS0tLS0tLS0tLS0tLQpnID0gaW0yZG91YmxlKHBsYX'
        'RlR3JheSk7CmcgPSBhZGFwdGhpc3RlcShnLCAnTnVtVGlsZXMnLCBbNCA4XSwgJ0NsaXBMaW1pdCcsIDAuMDIpOwpidyA9IGltYmluYXJpemUo'
        'ZywgZ3JheXRocmVzaChnKSk7CgolIOi9pueJjOW6leiJsumdouenr+i/nOWkp+S6juWtl+espumdouenryAtPiDkv53or4Ei5a2X56ymID0g55'
        'm9KDEpIgppZiBubnooYncpID4gMC40NSAqIG51bWVsKGJ3KQogICAgYncgPSB+Ync7CmVuZAoKJSAtLS0tLS0tLS0tLS0tLS0tIDIuIOWOu+aO'
        'iei9pueJjOWkluahhiAtLS0tLS0tLS0tLS0tLS0tCmJ3ID0gcmVtb3ZlRnJhbWUoYncpOwoKJSAtLS0tLS0tLS0tLS0tLS0tIDMuIOWOu+WZqi'
        'AtLS0tLS0tLS0tLS0tLS0tCmJ3ID0gYndhcmVhb3BlbihidywgbWF4KDQsIHJvdW5kKDAuMDAxNSAqIEggKiBXKSkpOwpid1BsYXRlID0gYnc7'
        'CgolIC0tLS0tLS0tLS0tLS0tLS0gNC4g5Z6C55u05oqV5b2xIC0+IOWtl+espuWIl+WMuumXtCAtLS0tLS0tLS0tLS0tLS0tCnByb2ogPSBzdW'
        '0oYncsIDEpOwpwcm9qID0gbW92bWVhbihwcm9qLCAzKTsKCiUg5YiX6ZiI5YC85LiN6IO95aSq6auYOiDmlbDlrZcgMSDnmoTmkofjgIHlrZfm'
        'r40gSiDnmoTpkqnnrYnnqoTnrJTnlLvlj6rljaDlrZfnrKbpq5jluqbnmoQgNSV+OCUsCiUg55SoIDAuMTAqSCDkvJrmiorlroPku6zliIfmjo'
        'ksIOWtl+espuWPmOeqhOWQjuW9kuS4gOWMluWkseecnygxIOS8muiiq+ivr+WIpOaIkCA0LzgpCnRociAgPSBtYXgoMSwgMC4wNSAqIEgpOwpy'
        'dW5zID0gbG9naWNhbFJ1bnMocHJvaiA+IHRocik7CgolIOmXtOmamemYiOWAvOWPliAwLjAyKlc6IOimgeWkp+S6juaxieWtl+iiq+WIh+aVo+'
        'eahOe8nSgxfjMgcHgpLCDlsI/kuo7lrZfkuI7lrZfkuYvpl7TnmoTpl7TpmpkKcnVucyA9IG1lcmdlUnVucyhydW5zLCBtYXgoMiwgcm91bmQo'
        'MC4wMjAgKiBXKSkpOwoKJSDljrvmjonov4fnqoTnmoTlmarlo7DmrrUKdyA9IHJ1bnMoOiwgMikgLSBydW5zKDosIDEpICsgMTsKcnVucyA9IH'
        'J1bnModyA+PSBtYXgoMiwgcm91bmQoMC4wMTUgKiBXKSksIDopOwoKJSAtLS0tLS0tLS0tLS0tLS0tIDUuIOWumuWtl+aVsCwg5YaN5oyJ5a2X'
        '5pWw5ouG5YiGL+WQiOW5tiAtLS0tLS0tLS0tLS0tLS0tCiUg5LiN6KaB55SoIHNwYW4vVyDov5nnsbvlm7rlrprmr5TkvovkvLDlrZfmlbA6ID'
        'cg5L2N5pmu6YCa54mM5LiOIDgg5L2N5paw6IO95rqQ54mM6KOB57Sn5ZCOCiUgc3Bhbi9XIOmDveWcqCAwLjg3IOW3puWPsywg5Zu65a6a5q+U'
        '5L6L5b+F54S25oqKIDgg5L2N54mMKOaWsOiDvea6kOe7v+eJjCnlvZPmiJAgNyDkvY0sCiUg57uT5p6c5piv6KKr5by65Yi25ZCI5bm25o6J5L'
        'iA5Liq5a2XLCDmlbTniYzlhajplJnjgILmlLnmiJDnlKjliIfliIbnu5Pmnpzoh6rmoKHlh4Yo6KeBIGNob29zZUNvdW50KeOAggppZiB+aXNl'
        'bXB0eShydW5zKQogICAgcnVucyA9IGNob29zZUNvdW50KHJ1bnMsIHByb2opOwplbmQKYm91bmRzID0gcnVuczsKCiUgLS0tLS0tLS0tLS0tLS'
        '0tLSA2LiDovpPlh7rlvZLkuIDljJblrZfnrKYgLS0tLS0tLS0tLS0tLS0tLQpuID0gc2l6ZShydW5zLCAxKTsKY2hhckltYWdlcyA9IGNlbGwo'
        'MSwgbik7Cmlua0FSICAgICAgPSB6ZXJvcygxLCBuKTsKZm9yIGsgPSAxOm4KICAgIHNlZyA9IGJ3KDosIHJ1bnMoaywgMSk6cnVucyhrLCAyKS'
        'k7CiAgICBbcnIsIGNjXSA9IGZpbmQoc2VnKTsKICAgIGlmIH5pc2VtcHR5KHJyKQogICAgICAgIGlua0FSKGspID0gKG1heChjYykgLSBtaW4o'
        'Y2MpICsgMSkgLyAobWF4KHJyKSAtIG1pbihycikgKyAxKTsKICAgIGVuZAogICAgY2hhckltYWdlc3trfSA9IG5vcm1hbGl6ZUNoYXIoc2VnLC'
        'AzMiwgMTYsIDIsICdmaWxsJyk7CmVuZAplbmQKCiUgPT09PT09PT09PT09PT09PT09PT09PT09IOWxgOmDqOWHveaVsCA9PT09PT09PT09PT09'
        'PT09PT09PT09PT0KCmZ1bmN0aW9uIGJ3ID0gcmVtb3ZlRnJhbWUoYncpCiVSRU1PVkVGUkFNRSDmirnmjonovabniYzlpJbmoYYsIOWIhuS4ie'
        'atpToKJSAgIDEpIOa4heaOieacgOWkluWciCAyfjMg5YOP57SgKOi9pueJjOWkluahhumAmuW4uOe0p+i0tOi9pueJjOi+uee8mCkKJSAgIDIp'
        'IOaVtOWdl+WIoOaOiSLotLTnnYDlpJbmsr/nmoTnu4bplb/ov57pgJrln58iLCDkuInpobnlkIzml7bmiJDnq4vmiY3nrpfovrnmoYbmrovnlZ'
        'k6CiUgICAgICAgIChhKSDlpJbmjqXmoYbop6bliLDlpJblnIggcmluZysxIOS7peWGhSjovabniYzlpJbmoYYgLyDnmb3ovrnkuIDlrprotLTo'
        'vrkpCiUgICAgICAgIChiKSDmnIDplb/ovrkgPj0gMC4zMCpICiUgICAgICAgIChjKSDmu6HotrPku7vkuIAi57qk57uGIuWIpOaNrjoKJSAgIC'
        'AgICAgICAgICAg6Z2i56evIC8g5pyA6ZW/6L65IDw9IDQuNSBweCAgICjnu4bplb/mrovmrrUsIOWmguiiqyBKUEVHIOaJk+aWreeahOS4i+i+'
        'ueahhikKJSAgICAgICAgICAgICAg6Z2i56evIC8g5ZGo6ZW/ICAgPD0gMS41IHB4ICAgKOe6v+adoeeKtiwg5aaC6Zet5ZCI55qE55+p5b2i5a'
        'SW5qGG55m957q/KQolICAgICAg5Yik5o2u57uG6IqC6KeBIHJlbW92ZUVkZ2VTbGl2ZXJz44CCCiUgICAzKSDlhajlm77lho3lgZrkuIDmrKHp'
        'lb/nur/lvIDov5Dnrpco5rC05bmzIDAuNjAqVyAvIOWeguebtCAwLjg1KkgpLCDmuIXlrozmlbTnmoTlpJbmoYbnur8KJQolICAg56ysIDIg5q'
        '2l5Y6f5p2l5oyJIuWkluWciOeqhOW4puWGhea4hei2hemVv+eslOeUuyIo5Z6C55u0IDAuNjAqSCAvIOawtOW5syAwLjM1Klcp5Yik5patLCDm'
        'nInkuKTkuKrmr5vnl4U6CiUgICDkuIDmmK/lrp7mi43niYznhafnmoTovrnmoYbooqsgSlBFRyDkuI7mqKHns4rmiZPmlq3miJDlh6DmrrUsIO'
        'avj+S4gOautemDveefreS6jumYiOWAvCwg5pW05p2h6L655qGG55WZ5LqGCiUgICDkuIvmnaUo5a6e5rWLIHJlYWwwMSDnmoTkuIvovrnmoYbm'
        'lq3miJAgNTEgcHgg5ZKMIDE0MiBweCDkuKTmrrUpLCDmiormr4/kuKrlrZfnrKbnmoTloqjov7nljIXlm7Tnm5Lku44KJSAgIDQyIOihjOaSke'
        'WIsCA1OCDooYwsIOW9kuS4gOWMluaXtuWtl+espuiiq+e6teWQkeWOi+aJgSwg5qih5p2/5Yy56YWN5b6X5YiG5LuOIDAuNyDmjonliLAgMC4z'
        'OwolICAg5LqM5piv5a6D5Y+q55yLIuafkOS4gOihjCAvIOafkOS4gOWIl+eahOi/nue7reautSIsIOaWnOi+ueahhuavj+ihjOWPquWNoCAxfj'
        'IgcHgsIOmVv+W6puWIpOaNruagueacrOajgAolICAg5LiN5Ye65p2lLCDlj43ov4fmnaXlj4jkvJrlm6DkuLrmn5DooYznrJTnlLvplb/lsLHm'
        'iormlbTooYzmirnmjokg4oCU4oCUIOWQiOaIkOWbviBiZW5jaDAxIOW3puS4i+inkueVmeS4i+eahAolICAgMSBweCDlrr3jgIEyMyBweCDpq5'
        'jnmoTovrnmoYbmrovnq68sIOWwseaYr+iiq+Wug+a8j+aOieWQjuWPiOW9k+aIkOeLrOeri+Wtl+espiwg5L2/IDcg5L2N54mM5YiH5Ye6IDgg'
        '5q6144CCCiUgICDmlLnmiJDmjInov57pgJrln5/liKTmlq3lkI46IOi+ueahhuaui+a4o+WPiOmVv+WPiOiWhCjlrp7mtYvlpJrkuLogMX4yIH'
        'B4IOWOminkvJrooqvmlbTlnZfliKDmjok7IOWtl+espgolICAg56yU55S75pyJIDR+NiBweCDljposIOWNs+S9v+i0tOWIsOi+ueS5n+S8muaU'
        'vuihjOOAggolICAg5pWw5a2XIDEg55qE56uW57q/57qm5Y2gIDAuNzAqSCwg5L2G5a6D6JC95Zyo6L2m54mM5Lit6YOoLCDkuI3otLTlpJbmsr'
        '8sIOS4jeS8muiiq+ivr+WIoOOAggpbSCwgV10gPSBzaXplKGJ3KTsKcmluZyA9IG1heCgyLCByb3VuZCgwLjAzICogSCkpOwpidygxOnJpbmcs'
        'IDopID0gZmFsc2U7ICAgICAgYncoZW5kIC0gcmluZyArIDE6ZW5kLCA6KSA9IGZhbHNlOwpidyg6LCAxOnJpbmcpID0gZmFsc2U7ICAgICAgYn'
        'coOiwgZW5kIC0gcmluZyArIDE6ZW5kKSA9IGZhbHNlOwoKYncgPSByZW1vdmVFZGdlU2xpdmVycyhidywgcmluZywgMC4zMCAqIEgsIDQuNSwg'
        'MS41KTsKCmhMaW5lID0gaW1vcGVuKGJ3LCBzdHJlbCgnbGluZScsIG1heCg1LCByb3VuZCgwLjYwICogVykpLCAwKSk7ICAgJSDlrozmlbTmsL'
        'TlubPlpJbmoYYKdkxpbmUgPSBpbW9wZW4oYncsIHN0cmVsKCdsaW5lJywgbWF4KDUsIHJvdW5kKDAuODUgKiBIKSksIDkwKSk7ICAlIOWujOaV'
        'tOWeguebtOWkluahhgpmcmFtZSA9IGhMaW5lIHwgdkxpbmU7CmlmIGFueShmcmFtZSg6KSkKICAgIGZyYW1lID0gaW1kaWxhdGUoZnJhbWUsIH'
        'N0cmVsKCdzcXVhcmUnLCAzKSk7CiAgICBidyA9IGJ3ICYgfmZyYW1lOwplbmQKZW5kCgpmdW5jdGlvbiBidyA9IHJlbW92ZUVkZ2VTbGl2ZXJz'
        'KGJ3LCByaW5nLCBtaW5MZW4sIG1heFRoaWNrLCBtYXhMaW5lVCkKJVJFTU9WRUVER0VTTElWRVJTIOaVtOWdl+WIoOaOiSLotLTnnYDovabniY'
        'zlpJbmsr/jgIHlj4jplb/lj4jnu4Yi55qE6L+e6YCa5Z+fKOi9pueJjOWkluahhiAvIOeZvei+ueaui+eVmSkKJQolICAgYncgICAgICAgOiDl'
        't7Lnu4/muIXmjonmnIDlpJblnIggcmluZyDlg4/ntKDnmoTkuozlgLzovabniYwKJSAgIG1pbkxlbiAgIDog5pyA6ZW/6L6555+t5LqO5a6D55'
        'qE6L+e6YCa5Z+f5LiA5b6L5LiN5YqoKOWZqueCueOAgeeJjOeFp+S4iueahOWwj+mThumSieetiSkKJSAgIG1heFRoaWNrIDogIuW5s+Wdh+WO'
        'muW6piA9IOmdouenryAvIOacgOmVv+i+uSLkuI3otoXov4flroMsIOWIpOS4uue7humVv+aui+autQolICAgbWF4TGluZVQgOiAi57q/5a69ID'
        '0g6Z2i56evIC8g5ZGo6ZW/IuS4jei2hei/h+Wugywg5Yik5Li657q/5p2h54q2KOmXreWQiOWkluahhue6vykKJQolICAg5Yik5o2u6KaB5LiJ'
        '6aG55ZCM5pe25oiQ56uLOiDotLTovrkgKyDlpJ/plb8gKyDlpJ/nu4bjgILkuKTkuKrljprluqbliKTmja7mmK8i5oiWIueahOWFs+ezuzoKJS'
        'AgICAgKiDooqvmiZPmlq3nmoTkuIvovrnmoYbov5nnsbvnu4bplb/mrovmrrUgLT4g6Z2i56evL+acgOmVv+i+uSDlvojlsI87CiUgICAgICog'
        '6Zet5ZCI55qE55+p5b2i5aSW5qGG55m957q/ICAgICAgIC0+IOmdouenry/mnIDplb/ovrkg57qm5Li655yf5a6e57q/5a6955qEIDIg5YCNCi'
        'UgICAgICAgKOmVv+i+ueOAgeefrei+ueeahOmdouenr+mDveeul+i/m+WIhuWtkCwg5Y205Y+q6Zmk5Lul5LiA5p2h6ZW/6L65KSwg5a6e5rWL'
        'IDYuOCA+IDQuNSwKJSAgICAgICDljZXpnaDkuIrlvI/kvJrmlbTmnaHmvI/mjok7IOiAjCDpnaLnp68v5ZGo6ZW/IOWvueS7u+S9lee7hue6v+'
        'mDvee6puetieS6juecn+Wunue6v+WuvQolICAgICAgICjlrp7mtYvlpJbmoYYgMS4yNiwg5rGJ5a2X56yU55S7IDEuNjd+Mi4yKSwg5omA5Lul'
        '6KGl5LiK6L+Z5LiA5p2h44CCCiUgICDotLTovrnkvYblvojnspfnmoTov57pgJrln58gPSDlrZfnrKbooqvoo4HliLDkuobovrksIOaUvuihjO'
        'OAggpbSCwgV10gPSBzaXplKGJ3KTsKW0wsIG5dID0gYndsYWJlbChidywgOCk7CmlmIG4gPT0gMCwgcmV0dXJuOyBlbmQKc3QgPSByZWdpb25w'
        'cm9wcyhMLCAnQXJlYScsICdCb3VuZGluZ0JveCcsICdQZXJpbWV0ZXInKTsKZHJvcCA9IGZhbHNlKDEsIG4pOwpmb3IgaSA9IDE6bgogICAgYm'
        'IgPSBzdChpKS5Cb3VuZGluZ0JveDsgICAgICAgICAgICAgICAgICAlIFt4IHkgdyBoXSwg6L6555WM5Li65YOP57Sg6L6557yYCiAgICB4MCA9'
        'IGJiKDEpICsgMC41OyAgICAgIHgxID0gYmIoMSkgKyBiYigzKSAtIDAuNTsKICAgIHkwID0gYmIoMikgKyAwLjU7ICAgICAgeTEgPSBiYigyKS'
        'ArIGJiKDQpIC0gMC41OwogICAgdG91Y2hlcyA9IHgwIDw9IHJpbmcgKyAxIHx8IHkwIDw9IHJpbmcgKyAxIHx8IHgxID49IFcgLSByaW5nIHx8'
        'IHkxID49IEggLSByaW5nOwogICAgaWYgfnRvdWNoZXMsIGNvbnRpbnVlOyBlbmQKICAgIGxlbiA9IG1heChiYigzKSwgYmIoNCkpOwogICAgaW'
        'YgbGVuIDwgbWluTGVuLCBjb250aW51ZTsgZW5kCiAgICBhID0gc3QoaSkuQXJlYTsKICAgIGlmIGEgLyBsZW4gPD0gbWF4VGhpY2sgfHwgYSAv'
        'IG1heChzdChpKS5QZXJpbWV0ZXIsIDEpIDw9IG1heExpbmVUCiAgICAgICAgZHJvcChpKSA9IHRydWU7CiAgICBlbmQKZW5kCmlmIGFueShkcm'
        '9wKQogICAgYncgPSBidyAmIH5pc21lbWJlcihMLCBmaW5kKGRyb3ApKTsKZW5kCmVuZAoKZnVuY3Rpb24gcnVucyA9IGNob29zZUNvdW50KHJ1'
        'bnMsIHByb2opCiVDSE9PU0VDT1VOVCDlnKggNn44IOS5i+mXtOaMkeS4gOS4quacgOWQiOeQhueahOWtl+espuaVsAolICAg5Yik5o2uOiDnm7'
        'jpgrvlrZfnrKYi5Lit5b+D6Ze06LedIui2iuWdh+WMgOi2iuWlveOAguS4pOS4quWtl+iiq+W5tuaIkOS4gOautSjpl7Tot53nuqYgMiDlgI0p'
        'LAolICAg5oiW5LiA5Liq5a2X6KKr5YiH5oiQ5Lik5q61KOmXtOi3nee6piAwLjUg5YCNKSwg6YO95Lya6K6p6Ze06Led5b+95aSn5b+95bCPOy'
        'DmiYDku6Xpl7Tot53nmoQKJSAgIOWPmOW8guezu+aVsCjmoIflh4blt64v5Z2H5YC8KeacgOWwj+eahOmCo+S4quWAmemAiSwg5bCx5piv5pyA'
        '5Y+v6IO955qE55yf5a6e5a2X5pWw44CCCiUgICDov5nmoLcgNyDkvY3mma7pgJrniYzlkowgOCDkvY3mlrDog73mupDniYzpg73og73oh6rpgI'
        'LlupQsIOS4jeS+nei1liBzcGFuL1cg5Zu65a6a5q+U5L6LCiUgICAo6KOB57Sn5ZCO5Lik6ICF55qEIHNwYW4vVyDpg73lnKggMC44NyDlt6bl'
        'j7MsIOWbuuWumuavlOS+i+W/heeEtuaKiiA4IOS9jeeJjOW9k+aIkCA3IOS9jSnjgIIKJSAgIOivlei/h+aKiiLlkITmrrXlrr3luqbmmK/lkK'
        'blnYfljIAi5Lmf5Yqg6L+b5Yik5o2uOiDln7rlh4bpm4bkuI7pmr7pm4bnmoTlrZfnrKbmlbDmraPnoa7njofpg73lj5jlt64sCiUgICDor7Tm'
        'mI7kuozlgLzljJblkI7nrJTnlLvnspjov54v5pat6KOC5a+86Ie055qE5a695bqm5oqW5YqoLCDmr5Qi5a2X6KKr5YqI5oiQ5Lik5Y2KIuabtO'
        'W4uOingeOAggolICAg5YCZ6YCJ5L2N5pWw5YiG5Lik6L2uOiDlhYjlj6ror5UgNyAvIDgo5Lit5Zu95aSn6ZmG5Y2V5o6S6L2m54mM5Y+q5pyJ'
        '6L+Z5Lik56eN5Yi25byPOiDmma7pgJrniYwKJSAgIDcg5L2N44CB5paw6IO95rqQ54mMIDgg5L2NKSwg6YO95LiN5oiQ56uL5pe25omN6YCA5Z'
        'ueIDZ+OCDlhajojIPlm7TjgILlrp7mi43lm77ph4znrJTnlLvnspjov54vCiUgICDmlq3oo4LkvJrorqnljp/lp4vmrrXmlbDlgY/lsJEsIOiA'
        'jCLkuK3lv4Ppl7Tot53mnIDlnYfljIAi6L+Z5Liq5Yik5o2u5Zyo5q615pWw6LaK5bCR5pe26LaK5a655piT5YG254S2CiUgICDlj5bliLDlvo'
        'jlsI/nmoTlj5jlvILns7vmlbAg4oCU4oCUIOWunua1iyByZWFsMDEg5bCx6KKr6YCJ5oiQ5LqGIDYg5q6144CC5YWI55So5Yi25byP5YWI6aqM'
        '5pS256qE6IyD5Zu0CiUgICDog73mjKHkvY/ov5nnsbvpgIDljJY7IOaUtueqhOWQjuiLpeS4gOS4qumDveaLn+WQiOS4jeS4iihhZGp1c3RUb0'
        'NvdW50IOS8muS4u+WKqOaUvuW8g+iAjOS4jeaYrwolICAg56Gs5YiHKSwg5YaN6YCA5Zue5YWo6IyD5Zu0LCDkuI3kvJrmvI/mjonnibnmrorl'
        'j7fniYzjgIIKYmVzdFJ1bnMgPSB0cnlDb3VudHMocnVucywgcHJvaiwgNywgOCk7CmlmIGlzZW1wdHkoYmVzdFJ1bnMpCiAgICBiZXN0UnVucy'
        'A9IHRyeUNvdW50cyhydW5zLCBwcm9qLCA2LCA4KTsKZW5kCmlmIGlzZW1wdHkoYmVzdFJ1bnMpCiAgICBiZXN0UnVucyA9IHJ1bnM7CmVuZApy'
        'dW5zID0gYmVzdFJ1bnM7CmVuZAoKZnVuY3Rpb24gYmVzdFJ1bnMgPSB0cnlDb3VudHMocnVucywgcHJvaiwgbG8sIGhpKQpiZXN0UnVucyA9IF'
        'tdOyBiZXN0ID0gaW5mOwpmb3IgbiA9IGxvOmhpCiAgICByID0gYWRqdXN0VG9Db3VudChydW5zLCBwcm9qLCBuKTsKICAgIGlmIHNpemUociwg'
        'MSkgfj0gbiwgY29udGludWU7IGVuZAogICAgZCA9IGRpZmYobWVhbihyLCAyKSk7CiAgICBpZiBudW1lbChkKSA8IDMsIGNvbnRpbnVlOyBlbm'
        'QKICAgIGN2ID0gc3RkKGQpIC8gbWF4KGVwcywgbWVhbihkKSk7CiAgICBpZiBjdiA8IGJlc3QgLSAxZS05CiAgICAgICAgYmVzdCA9IGN2OyAg'
        'YmVzdFJ1bnMgPSByOwogICAgZW5kCmVuZAplbmQKCmZ1bmN0aW9uIHJ1bnMgPSBsb2dpY2FsUnVucyhhY3QpCiVMT0dJQ0FMUlVOUyDmiorpgL'
        'vovpHlkJHph4/kuK3nmoTov57nu60gdHJ1ZSDmrrXovazmiJAgW+i1tywg5q2iXSDliJfooagKYWN0ID0gYWN0KDopJzsKZCA9IGRpZmYoW2Zh'
        'bHNlLCBhY3QsIGZhbHNlXSk7CnMgPSBmaW5kKGQgPT0gMSk7CmUgPSBmaW5kKGQgPT0gLTEpIC0gMTsKcnVucyA9IFtzKDopLCBlKDopXTsKZW'
        '5kCgpmdW5jdGlvbiBydW5zID0gbWVyZ2VSdW5zKHJ1bnMsIG1heEdhcCkKJU1FUkdFUlVOUyDpl7TpmpnlsI/kuo4gbWF4R2FwIOeahOauteWQ'
        'iOW5tijkvovlpoIi5bedIiLmuZgi6KKr5YiH5pWj55qE5oOF5Ya1KQppZiBpc2VtcHR5KHJ1bnMpLCByZXR1cm47IGVuZApvdXQgPSBydW5zKD'
        'EsIDopOwpmb3IgaSA9IDI6c2l6ZShydW5zLCAxKQogICAgaWYgcnVucyhpLCAxKSAtIG91dChlbmQsIDIpIC0gMSA8PSBtYXhHYXAKICAgICAg'
        'ICBvdXQoZW5kLCAyKSA9IHJ1bnMoaSwgMik7CiAgICBlbHNlCiAgICAgICAgb3V0KGVuZCArIDEsIDopID0gcnVucyhpLCA6KTsgJSNvazxBR1'
        'JPVz4KICAgIGVuZAplbmQKcnVucyA9IG91dDsKZW5kCgpmdW5jdGlvbiBydW5zID0gYWRqdXN0VG9Db3VudChydW5zLCBwcm9qLCBuKQolQURK'
        'VVNUVE9DT1VOVCDorqnlrZfnrKbmrrXmlbDnrYnkuo7kvLDorqHlrZfmlbAgbgolICAg5q615pWw5YGP5bCRIC0+IOS7juacgOWuveeahOaute'
        'W8gOWniywg5Zyo5oqV5b2x6LC35bqV5YiH5byACiUgICDmrrXmlbDlgY/lpJogLT4g5ZCI5bm26Ze06ZqZ5pyA5bCP55qE5LiA5a+555u46YK7'
        '5q61CndoaWxlIHNpemUocnVucywgMSkgPCBuCiAgICB3ID0gcnVucyg6LCAyKSAtIHJ1bnMoOiwgMSkgKyAxOwogICAgW3dtLCB3aV0gPSBtYX'
        'godyk7CiAgICBpZiB3bSA8IDgsIGJyZWFrOyBlbmQKICAgIFtydW5zLCBva10gPSBzcGxpdFJ1bihydW5zLCB3aSwgcHJvaik7CiAgICBpZiB+'
        'b2ssIGJyZWFrOyBlbmQKZW5kCndoaWxlIHNpemUocnVucywgMSkgPiBuCiAgICBnYXAgPSBydW5zKDI6ZW5kLCAxKSAtIHJ1bnMoMTplbmQgLS'
        'AxLCAyKSAtIDE7CiAgICBbZ20sIGdpXSA9IG1pbihnYXApOwogICAgaWYgZ20gPiAwLjggKiBtZWFuKHJ1bnMoOiwgMikgLSBydW5zKDosIDEp'
        'ICsgMSksIGJyZWFrOyBlbmQKICAgIHJ1bnMoZ2ksIDIpID0gcnVucyhnaSArIDEsIDIpOwogICAgcnVucyhnaSArIDEsIDopID0gW107CmVuZA'
        'plbmQKCmZ1bmN0aW9uIFtydW5zLCBva10gPSBzcGxpdFJ1bihydW5zLCBpZHgsIHByb2opCiVTUExJVFJVTiDlnKjmrrXkuK3pg6jnmoTmipXl'
        'vbHmnIDlsI/lgLzlpITliIflvIAo5Lik56uv5ZCE55WZIDIwJSDkuI3lj4LkuI7mib7osLflupUpCm9rID0gZmFsc2U7CmEgPSBydW5zKGlkeC'
        'wgMSk7CmIgPSBydW5zKGlkeCwgMik7Cm0gPSByb3VuZCgwLjIwICogKGIgLSBhKSk7CnNwYW4gPSAoYSArIG0pOihiIC0gbSk7CmlmIG51bWVs'
        'KHNwYW4pIDwgNSwgcmV0dXJuOyBlbmQKW34sIGtdICA9IG1pbihwcm9qKHNwYW4pKTsKY3V0ICAgICA9IHNwYW4oayk7CnJ1bnMgICAgPSBbcn'
        'VucygxOmlkeCAtIDEsIDopOyBbYSwgY3V0XTsgW2N1dCArIDEsIGJdOyBydW5zKGlkeCArIDE6ZW5kLCA6KV07Cm9rID0gdHJ1ZTsKZW5kCg=='
    ),
    'core/templates.mat': (
        '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'
        'auR2omAwMAdksbVA=='
    ),
    'static/demo.jpg': (
        '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAQDAwMDAgQDAwMEBAQFBgoGBgUFBgwICQcKDgwPDg4MDQ0PERYTDxAVEQ0NExoTFRcYGRkZDxIbHR'
        'sYHRYYGRj/2wBDAQQEBAYFBgsGBgsYEA0QGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBj/wAARCAKo'
        'A+gDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhBy'
        'JxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKT'
        'lJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAA'
        'AAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRom'
        'JygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExc'
        'bHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD5zopcUYrYzEopcUYoASilxRigBKKXFGKAEopcUYoASinbeM0m'
        'KAEznpRS4FGKAEopcUYoASilxRgUAJRS4oxQAlBOKXFGKAEopcUYoASjjHvS4oxQAlFLijFACUUuKMUAJRS4oxQAlFOAxSYoASilxRigBKKXFG'
        'KAEopcUYoASilxRigBKKXFGKAEopcUYoASjFLijFACAEnAopcUYoASilxRigBKKXFLj3NADaKXFGKAEopcUYoASilxRigBKOvWlxRigBKKXFGK'
        'AEopcUYoASilxRigBKKXFGKAEopcUYoASilC+5NGKAEopcUYoASilxS7RjrQA2jnPtS4oxQAlFLijFACUUuKMUAJRS4oxQAlFLijFACUUuKMUA'
        'JRS4oxQAlFLijAoASilwAMk0YFACUUuKMUAJRjnNLijFACYJ6UUuBRigBKAcUuKMUAJRg4zjilxRigBKKXAoxQAlFLijFACUUuKMUAJRS4HrRi'
        'gBKKXFBHFACUUuKMUAJRgDoKcQO2aTFACUUuKMUAJRS4oxQAlFLiigB2KNpxntS0UAJijFLRQAmKBkelLRigBMUYNLRQAmKMUtFACYoxS0UAJi'
        'jFLRnnFACYoxS0UAJijFLRQAmKMUtFACYoxS0UAJijFLRQAmKMUtAOaAExRilooATFGKWigBMUhGBmnUUAJijFLRQAmKMUtFACYoxS0UAJijFL'
        'RQAmKMUtFACYoxS0UAJijFLRQAmKMUtFACYoxS0UAJijFLRQAmKMUtFACYoxS0UAJijFLRQAmKMUtFACYoxS0UAJg0YpaKAExx2oxS0UAJijFL'
        'RQAmOaMUtFACYoxS0UAJijFLgZz3ooATFGKWigBMUYpaKAExRilooATFGKWigBMUYpaKAExRilooATFGKWigBMUYpaKAExRilooATaSaMGlooA'
        'TFGKWigBMUYpaKAExRilooATFGKWigBMUEc8dKWigBMUYpaKAExRilooATFBHpS0UAJijFLRQAmKMUtFACYoxS0UAJijFLRQAmKMUtFACYoxS0'
        'UAJj3oxS0UAJiilooAdRS4FGBQAlFLgUYFACUUuBRgUAJRS4FGBQAlFLgUYFACUUuBRgUAJRS4FHAoASilwKMCgBKKXApcCgBtFLgUuB6UANop'
        '2BSYFACUUuBRgUAJRS4FGBQAlFLgUYFACUUuBRgUAJRTtuKTAoASinYFJgUAJRS4FGBQAlFLgUYFACUUuBRgUAJRS4FGBQAlFLgUuKAG0UuBRg'
        'UAJRS4FGBQAlFLgUuBQA2ilwKXFADaKdgUmBQAlFLgUcZxQAlFLgUYFACUUuBRgUAJQRmlwKMCgBKKXApcCgBtFLgUYFACUUuBRgUAJRS4FKAO'
        '4oAbRS4FGBQAlFLgUYFACUUuBRgUAJRS4FGBQAlFLgUYFACUUuBS4GKAG0UuBRgUAJRS4FGBQAlFLgUYFACUUuKMCgBKKXAowKAEopcc+1GBQA'
        'lFLgUYFACUU7ApMCgBKKXAowKAEo4pxGKTAoASilwKMCgBKKXAowKAEopcCjAoASinYFJgUAJRTsCkwKAEopcCjAoASilwKMCgBKKXAowKAEop'
        '2KTAoASilwKMCgBKKXAowKAEopcCigB2KMUtFACYoxSgY6DFFACYNGKWigBMUYpaKAExRilooATFGDS0UAJijFLS8+9ADcUhBxxTqKAEwaMU7B'
        'xmkoATFGKWjBoATFGKWigBMUYpaKAExRil/CigBMUYpaKAExRilooATFGKWigBMUYpaKAExRilooATFGKWigBMUYpaKAExRilooATFGKXFFACY'
        'oxS4oxzQAmKMUtFACYoxS0UAJijFLg0UAJijFLRQAmKMUtFACYoxS0pFADcUYpaKAExRilxRQAmKMUtFACYoxS0UAJijFLS49c0ANxRilooATF'
        'GKWigBMUYpaKAEwaMUtFACYoxS80UAJg0mGx2Bp1FACYoxS0UAJijFOxSUAJijBpaKAExRilooATFGKWigBMUYpaKAExRilooATFGKWigBMUYp'
        'aKAExRilowfSgBMUYpaMGgBMUYpaMGgBMUYpaOaAExRilooATFGKWigBMUYpaMUAJg0YpcUUAJijFLRQAmKMUtFACYoxS0UAJg0mDnqMU6jknm'
        'gBMUYpaKAExRS0UAOopcCjAoASinYGOlIBj3oASilwKMCgBKKXAowKAEopcCjAoASilwKU4PbFADR9MUUuBRgUAJRS4FLx6UANopcCjAoASilw'
        'KMCgBKKXAowKAEopcCjAoASilwKMCgBKKXApQB3FADaKXAowKAEozxilwKMCgBKKXAowKAEopcCjAoASilwKMCgBKKdgUmKAEopcCjAoASilwK'
        'MCgBKKXApcCgBo69M0Y5zS4FGKAEopcCjAoASilwKMCgBKKXAowKAEo7Zz+FLgUuBQA2ilwKMCgBKKUrxggijAoASinYFGB6UANop2B6UmBQAl'
        'FOC56DNJgUAJRTsCkwKAEoBx2zS4FGBQAlFLgUYFACUUuBRgUAJRS4FGBQAlFLgUYFACUUuBRgUAJRS4FGBQAlFLgUYFACUUuBRgUAJRS4FLjJ'
        '6UANop2BSYFACUUuBRgUAJRS4FGBQAlFLgUYFACUUuBRgUAJRS4FGBQAlFLgUYFACUUowenNGBQAlFLgUYFACUUuBRgUAJRS4FLjjOKAG0UuBR'
        'gUAJRTsCkwKAEopcCjAoASilwKMCgBKKXAowKAEopcCjAoASilwKMUAJiil4zjvRQA7FGKWjFACBSTgUYpeaKAExRilooATFGKWigBMUYpaKAE'
        'xRilooATFGKWjn0oATFGKXn0ooATFGKWigBMc0YpaKAExRilooATFGKWigBMUY96WigBMUYpaKAExRilowaAExQBkZpcGigBMUYpaKAEx70Ypa'
        'KAExRilooATFGKWigBMUYpe+KKAExRilooATHGKMH1pcGlAz7UANxRilooATFGKXHPNGKAExRilooAQDBzRilooATFGKXB7UUAJijFOHXkGkoA'
        'TFGKWigBMUYpfwooATFGKWigBMUYpaKAExRilooATFGKWigBMUYpaKAExRj3paKAExRilooATFGKWigBMUYpaMH0oATFBHFLRQAmKMUtGDQAmK'
        'MUoBxzRQAmKMUtFACYoxS0UAJijFLRQAmKMe9LS/QGgBuKMUtFACYoxS0UAJijFLRQAmKMUtFACYoxSgH3NHNACYoxSjOORRQAmKMU7HHfNJQA'
        'mKMUtFACYoxS0UAJijFLRQAmKMUtFACYoxS0UAJijFLRQAmKMUtFACYox70tFACYopaKAHUU6igBtFOooAbRTqKAG0U6igBtFOox7UANop1FAD'
        'aKdRQA2inUnFACUU6igBtFOwMUUANop2KKAG0U6igBtFLjmloAbRTqMUANop1FADaKdRQA2inUUANop1GKAG0Dp1zTsUfhQA3HGaKdRQA2il4z'
        'jiloAbRTqKAG0U6igBtFOooAbRTsDNGBQA2inUUANop1FADaKdRQA2inUUANop1FADaKdRQA2inUYFADaKdR2xQA2inUUANop1FADTwM0mOc0+'
        'igBtFOooAbRTqKAG0U6igBtFOooAbRSnA7UtADaKdR+FADaKdjjPFGPagBtFOooAbRTqKAG0U6igBtFOooAbRTqKAG0U78KKAG0U6igBtFOo/C'
        'gBtFOoAHegBtFOo49BQA2inUUANop1FADaKdRQA2il79KXj0FADRx3zR0NOooAbRTqKAG0U6igBtFOooAbRTqKAG0U6igBcUYp2OM/pSYNACYo'
        'xS4NGDQAmKMHuaXBowaAExRilwaMGgBMUYpcGjBoATFGKXBowaAExRinHntikwaAEx70YpcGjBoATFGKXBowaAExRilwaMGgBMUYpcGjBoATFG'
        'KXBpcGgBuKMUuDRigBMe9BGTkmnDjtmjBoAbijFLg0YNACYoxTgOfSjBoAbijFLigAj3oATFGKXBowaAExRil74owaAExRilwaMGgBMUYpcGjB'
        'oATFGOKXBowaAExRilowaAExRilwaMGgBMUYpcGjBoATFGKXBowaAExRinYNGKAG4ox70uDRg0AJijFLg0YNACYoxS4NGDQAmKMUuDRg0AJijF'
        'Lg0YNACYoxS4NGDQAmKMUuDRg0AJijFLg0YNACYoxS4NGDQAmKMUuDRg0AJijFLg0YNACYoxS4NGDQAmKMU4ZByKMGgBuKMUuDRg0AJijFLg0Y'
        'NACYoxz1pcGjBoATFGPelwaMGgBMUYpcGjBoATFGPelwaMGgBMUYpcGjBoATFGOMU7HGf0pMGgBMUYpcGjBoATFGKXBowaAExRilwaMGgBMUYp'
        'cGjBoAQDB9aMUuDRg0AJt96Me9Lg0YPYUAIV565oxS4NGDQAmKMUuDRigBMUYpcGl5PWgBoXAxmjFLg0YNACYoxS4NGDQAmKMUuDRg0AJijFLg'
        '0YNACYoxS4NGDQAgUk4HWinYNFAC0U6igBtFOooAbRTqKAG0U6igBtB+uadRQA2inUUANycYop3frRQA2inUUANop1FADaKdRQA2inUUANop1F'
        'ADaKdRQA2gjBxTqKAG0U6igBtFOooAbRTqOe9ADaKdRQA2inUUANop1FADaKdRQA2inUUANop1FADaKdRQA2inUUANop1FADaKdRQA3vjH40U6'
        'igBtAxnmnUUAJx70lOooAbRTqKAG0U6igBtFOooAbRTqKAG0U6igBtFOooAbRTscZooAbRTqKAG0U6igBtFOooAbRTqKAG0U6igBtFOJJ60UAN'
        'op1FADaKd1FFADaKdzRQA2inUUANop1FADaKdRQA2gDFOooAbRTqKAG0U6igBtFOooAbRTqKAG0U447UUANoH1xTqOaAG0U6igBtFOooAbRTqK'
        'AG0U6igBtFOooAbjgZIJop1FADaKdRQAuKMU7FGKAG4oxTsUYoAbijFOxRigBuKMU7FGKAG4oxTsUYoAbijFOxRigBuKMU7FGKAG4FGKdtI60Y'
        'oAbijFOwaMUANxRinYoxQA3FGKdijFADcUYp2KMUANxRinYoxQA3FGKdijFADcCjFOxRigBuKMU7FGKAG7RnPejFOxRigBuKMU7FGKAG4oxTsU'
        'YoAbijFOxRigBuKMU7FGKAG4oxTsUYoAbijFOwaMUANxRinYoxQA3FGKdijFADcUYp2OO1GKAG4oxS7TnOfwpcUANxRinYoxQA3FGKdg4xmjFA'
        'DcUYp2KMUAJtzSYp2KMUANxRinYoxQA3FGKdijFADcUYp2KMGgBuKMU7FGKAG4oxTsUYoAaVB9aMU7FGKAG4oxTsUYoAbijFOxRigBuKMU7FGK'
        'AG4oxTsUYNADcUYp2KMGgBuBRilxng0uKAG4oxTsUYoAbijFOxRigBuKMU7FGKAG4oxTsUYoAbijFOxRigBuKMU7FGDQA3AowKdijFADcUYp2K'
        'MUANxRinYoxQA3FGKdikxgUAJgUYFOxRigBuKMU7HHUUYoAaB60EADNOxRigBuBRinYoxQA3FAHHPWnYoxQA3FGKdijFADcUoXNLijFADcUYp2'
        'KMUANxRinYoxQA3FFOxRQAtHOfanUUANop2OM0UANop1FADaKdRQA2inUUANop1FADaKdRQA2inUUANop1FADaKdRkDqaAG0U6igBtGOc06igB'
        'tFOGO9FADaKdRQA38aKdRQA2inUUANop1FADaKdRQA2inUUANop1FADaKdRQA2inHHajmgBtFOo79KAG0U6igBtFOooAbRTqKAG0U6igBtFOoo'
        'AbRTqKAG0U6igBtFOooAbRTqKAG0UvOe2KWgBtFOooAbRTqO9ADaKcRk0UANop1FADaKdRQA2inUUANop1FADaKdiigBtBJJyadRQA2inUUANo'
        'p1FADaKdRQA2inUUANop1FADaKdRQA2inUUANop1FADaKdRQA2inUfhQA2inUUANop1FADaKdRQA2indaKAG8dqKdRQA2inUUANop1FADaKdRQ'
        'A38aKdRQA3HGaKdRQA2inUUALgUYFOxRigBuBS4FLijFADcCjAp2KMUANwKMCnYoxQA3AowKdijFADcCjAp2KMUANwPSjAp2KMGgBuBRgDGM8e'
        '9OxRigBMUmBTsUmOcZGaAEwKUjFLijFADcCjAp2KMUANwKMCnYNGKAG4FGBTsUYoAbgUYFOxRigBMCkwKdijFACDjpSYFOxRigBBwcikI56U7F'
        'GKAG4FGBTsGjFADcCjAp2KTBz1GKAEwKMCnYowaAG4FGBTsUbSTQA3AowKdg0YoAbgUYFOxRigBuBRgU7FGKAG4FGBTsUhBx1xQAmBRgU7BoxQ'
        'A3AowKdijFADcCjAp2KMUANwKMClwccUuDQA3AoIHpmnYoxQA3AowKdijFACDg5owKXFGKAG4FGBTsUYoAbgUYFOxRigBuBRgU7FGKAG4FGBTs'
        'UYoAbxnFGBTsUYoAbgUYFOxRigBMCkwKdijBoAbgUYFOxRigBMUmBTsUYoAbgUYFOxRigBuBS4FLijFADcCjAp2KMUANwKMCnYoxQA3AowKdg0'
        'YoAbgUYFOxRigBuBRgelOxRigBuBRgU7FGKAG4FGBTsUYoAaBxyKMCnYoxQA3AowKdijFADcCjAp2KMUAJgUYFLjjrzRigBuBRgU7FGKAG4FGB'
        'TsGjFADcCjAp2KMUANwKMCnYoxQAhGOopMCnYoxQA3ApcUuKMUANwKMCnYoxQA3AowKdijBoAbgUU7FFAC0Y9qdRk0ANop1FADaOadRQA3mg8D'
        'OKdRQA2inUUANwaKdRQA2inUUAN5oxTqKAG0Yp1FADaKdRQA2inUUANo5p1FADaKdRQA2inUUANop3bFIBj6fWgBKKdRQA2inUUANopw+tFADc'
        'ZGCKPwp1FADaMGnUUANo5p1FADaKdRQA2inUUANo/CnUUANxRTqKAG0U6igBtFOooAbRTqKAG0uDjODS0UANop1FADcGinfhRQA2inUUANop1F'
        'ADaKdRQA2inUUAN5pTknOKWigBuKKdRQA2inUUANIOOKMGnUUANop3GPeigBtFOooAbRTqKAG0U6igBtFOooAbRTqKAG0U6igBtH4U6igBuPai'
        'nUUANop1FADaMU6igBtFOooAbRTqKAG0U6igBtFOooAbSn2BpaKAGjOORRg07iigBtFOooAbRTqKAG0U6igBtHOenFOooAbRTqKAG0U6igBvfF'
        'FOooAXAowKdijFADcCjinYoxQA3AowKdijHvQA3AowKcF465oxQAnHpRtzwBzS4oxQAmKMClxRigBuBRgU7FGKAG4FGBTsUYoAbgUYFOAPejFA'
        'DcCjAp2KMUAN4NGBTsUYoAbgUYFOxRigBMCkwKdweho20ANwKMCnYoxQA3AowKdijFADcCjAp2KMUANwKMCnYowfWgBuBRgU7FGKAG4FGBTsUY'
        'oAbgUYFOxRigBuBRgU7FGKAG4FGBTsUYoAbgUYFOxRigBuBRgU7FGKAG4FGBTsUbQOlADcCjAp2KMUANwKMCnYoxQA3AowKdijFADcClxS4oxQ'
        'A3AowKdijFADcCjAp2KMUANwKMCnYoxQA3AowKdijHvQA3AowKdijFADcCjAp2KMUANwKMCnYoxQA3AowKdijFADcClxS4oxQA3AowKdijFADc'
        'CjAp2KMUANwKMCnYoxQA3AowKdijHOKAExmkwKdijFADcClwKXFGKAE49KMClxRg460ANwKMD0pwGR6UYoAbgUuBS4oxQA3AoxTsUYoAbgUYFO'
        'xRigBuBRgU7FGKAG4FGBTsUYoATApMU7FGPegBuBRgU7FGKAG4FGBTsUYoAbgUYFOxRigBuBRgU7FGKAG4FGBTsUYoAbgUuBS4ox70ANwKMCnY'
        'oxQA3AowKdijbQA3AowKdijFADcCjAp2KMUANwKKdiigBaKdRQA2inUUANop1FADaKdRQA2inUUANop1FACAE0lO4x70UANwaMGnUUANop1FAD'
        'aKdRjjNADaKdRQA2inUUANxRTqM0AN5op1FADaByMinUUAJgikp1FADaKdRQA2jFOooAbRTqKAG80U6igBtGOc806igBtFOooAbRTjyc0UANop'
        '1FADaKdRQA2inUUAN6DJop1FADaKdRQA2inUUANop1FADaKdRQA2l596U47UUANoPHWnUUANox7U6igBtFOooAbRTqKAG0U6igBtFOooAbRTqK'
        'AG0U6igBtFOooAbRTqKAEwcZpKdRQAmOKSnUUANpe2MfjS0UANop1FADaKdQPpmgBtFOooAbRTqKAG0U6igBtFOooAbR+FOooAbRTqKAG0fhTq'
        'KAG0fhTqKAG0U6igBtFOooAbRTqKAG0U6igBtFOooAbRj2p1GOc0ANop1FADaKdRQA6ilxRigBKKXHvRigBKMUuKMUAN49qXj0pcUYoASilxRi'
        'gBKKXFGKAEopcUY96AEopcUbfegBKKXFG0jrQAlAA9hS4oxQAlFLijFACflRS4oxQAlFLijFACUfhS4oxQAlFLijFACUUuKMUAJRS4oxQAlFLi'
        'jFACUcUuKMUAJRS4oxQA0jilxS4ox70AJ0NBx1xS7fejFACcUYpcUYoATj0opcUYoASilxRigBvfGPxpaXFGKAEopcUYoATj0o49MUuKMUAJRS'
        '4oxQAlFLijFACUUuKAD3NACUUuKMe9ACUUuKMUAJSfhTsUYoASilxRigBKKXFGKAEopcUYoASilxRigBMe1FLijFACUcUu3BwaMUAIcZ4FFLij'
        'FACUUuKMUAJRS4oxQAlFLijFACUUuKMUAJRS4oxQAlFLijFACYo4pcEdDRtB60AJRS4oxQAlFLijFACUmOadijFACUUuPejFACfhRS4oxQAlFL'
        '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'
        'igBuBRgU7BoxQA3AowKdijFACYFGB2FLijFADcCjAp2KMUANwKMCnYoxQA3AowKdijFADcCjAp2KMUANwKKdiigBaX8KWigBtFOHXmigBtFOoo'
        'Abg0U6igBtFOooAbRTqB9M0ANop1FADaKdRQA2inUZoAbzRyTTqKAG0U6igBuKADjuadRQA3FFOooAbRTqKAG4ox7U6igBtFOooAbRTqKAG0U6'
        'igBtFOooAbRTqB16ZoAbS/QGlooAbRTqKAG856cUU6igBtGD6U6igBtFOooAbRg06igBtFOooATH1pKdRQA2inUUANop1FADcGinUUANop1FAD'
        'aKdRQA2inUUANop1FADaKdRQA2inUUANop1FADfwop1FADaMGnUUAIQQcUlOooAbRTqKAG0YNOooAaAcc0U6igBtGKdRQAn4UlOooAbigDAxzT'
        'qKAG0U6igBtFOooAbRTqKAG0U6igBtFOooAbRTqM0ANop1FADcH0op1FADcGinUUANwcZwaKdRQA2inUUANop1FADaKdRQA2jnPQ06igBtFOoo'
        'AbzRTqKAFwKMCnYoxQA3ApenSlxRj3oAbgUYFOxRigBMCkwKdijFACYFIR6CnYoxQA3AowKdijFADcCjAp2KMUANwKMCnYoxQA3AowKdijFADc'
        'CjAp2KMUANwKMCnYoxQA3AowKdijFACdsUmBTsUYoAbgUYFOxRigBuBS8Y6HNLijFADcCjAp2KMUANwKMCnYoxQA3AowKdt96MUANwKMCnYoxQ'
        'A3AowKdijFADcCjAp2KMUANwKMCnYoxQA3AowKdijFADcCjAp2KMUAJgUmBTsUYoAbgUYFOxRigBuBRgU7FGKAEwKTAp2KMUANwKMCnYoxQA3A'
        'owKdj3oxQA3AowKdijFADcCjHtTsUYoAbgUYFOxRigBuBRgU7HvRigBuBRgU7FGKAG4FGBTsUYoAQAZowKXFGKAG8UYFOxRigBuBQBx607FGKA'
        'G4FGBTsUYoATFIR6U7FGKAG4FGBTsUYoAbgUYp2KMUANwKMCnYoxQA3AowKdijHvQA3AowKdijFADcCjAp2KMUANwKMCnYoxQA3ApcD0pcUYoA'
        'bgUYFOxRigBuBS4FLijFADcCjAp2KMUAJgY6c0mKdj3oxQA3AowKdijFADcCjAp2KMUANwKMCnYoxQA3AowKdijFADcCjAp3A6mjHvQA3AowKc'
        'V560YoAbgUYFOxRigBuBRgU7FGKAG4FGBTsUYoAbgUU7FFAC0U6igBPoKSnUUANoxTqKAG0U6igBtFOooAbRTqKAG0U78aKAG0U6igBtFOH1xR'
        'QA2inUUANop1FADaKdRQA2inUUANop1FADaKdRQA2inUUANop1FADaKdRQA2inUUAN5z04op1FADce1FOooAbRTqKAG45zjn1op1FADcc55op1'
        'FADaKdRQA2inUUANop1AzjmgBtHNOooAbRz6U6igBtFOooAbRTqKAG0U6igBtFOooAbRTqKAG0U6igBtFOooAbRTqKAGnjrRTqKAG0U6igBtFO'
        'oOM8UANop1FADeaKdRQA3BoxTqM5oAbRTs84ooAbRTqKAG0U7nPtSYBNACUU6igBtFOo7daAG0U6igBtFOooAbRTqKAG0U6igBtFOooAbRTqKA'
        'G0U6igBtFOooAbSnJ7UtFADcUU6igBtGKdRQA2inUUANowc96dRQA2inUUANop1FADjyc4opcUYoASilxRigBKKXaR1oxQAlFLijFACUUuKMUA'
        'JRS4oxQAlFLijFACUUuKNtACcUUoUAYFGKAEopcUYoASjj0pcUYoATbg5x1opcUYoASilxRigBKKXHNGKAEopcUYoASilxRigBKKXFGKAEopcU'
        'YoASilxRigBKMD0pcUYoAb+FLS4oxQAlFLijFACUUuKMUAJRgelLijFACYoHHTilAPejFACcUUbQDx3pcUAJRilxRigBMUUuKMUAJRS4oxQAlF'
        'LijFADcUvFKVyMZoxQAlFLt96MUAJQMZ6A0uKMUAJx6UUuKMUAJRx6UuKMUAJRS4oxQA3jOKWlxRigBKKXFGKAEopcUYoASjFLijFACUcelLij'
        'FACHGelFLijFACUUuKMUAJRxS4oxQAlFLijFACYopcH1oxQAlFLijFACUUuKMUAJRS4oxQAlFLijFACUUuKMUAJRS45oxQAlFLijFADeM44zS0'
        'uKNuD1oAQDJxRilxxRg9zQAlFLijFACUUuKMUAJRS4oxQAg4OcUUuKMUAJRS4oxQAlFLijFACUUuKMcYoASilxRQAuDRg06igBuDRg06igBMGk'
        'wadxiigBuDRg06igBuDRg06igBuDRg06igBuDRg06igBuDRg06igBuDRg06igBuDRg06igBuDRg56U6igBuDRg06jHGaAG4NGDTqKAG4NGDTqK'
        'AEwfSkwadRQA3BowadRQA3BpcGlooATFJg06igBuDS4NLRQA3BpQOeRS0UANwaMGnUUANwaXBpaKAG4NGDTqKAG4NGDTqKAG4owadRQA3Bowad'
        'RQAmOKTBp1FADcGjBp1FADcGjBp1FACYoAyfSlooAbg0YNOooAbg0YNOooAbg5owadRQAmDSYNOooAbg0YNOooAbg0YNOooAbg0YNOooAbg0YN'
        'OooAbg0YNOooAbg0uDS0dutACYpMGnUUANwaMH0p1FADcGjBp1FADcGjBp1FADcGjBp1FADcGjBp1FADcGjBp1FADcGjBp1FADcGjBp1FADcGj'
        'Bp1FADcGjBp1FADcGjBp1FADcGjBp1FADcGjBp1FADcGjBp1FACYNGD6UtFADcGjBp1FADcGjBp1FADcGl5paKAG4NGDTqKAG4NGDTqKAG4NGD'
        'TqKAG4NFOooAdRxj3pcUYFACUAAdKXFGKAEopcUYoASilxRigBKKXFGKAEPWilxRigBKKXFGKAEopdoPBowKAEopcUYoASilxRigBKKXFGKAEo'
        'pcUYoASilxRigBKKXFGKAEopcUYoATIHU0UuBRigBKKXFGKAEopcUYoASilxRigBKKXFGKAEopcUYoATrRS4oxQAlFLil2465oAb36UUuKMUAI'
        'frmilxRjnqaAE4x70UuBRigBKKXFGBQAlA4OaXFGKAEopcUY9zQAlFLijFACE5OaOMe9LijFACUUuKMCgBO3SjpS49zRigBKKXFGKAEopwXJxS'
        'YAoASilxRigBKKXFGKAEopcUYoATnHFFOxxjNJgUAJRS4oCgetACUUuBRigBKKXFGKAEopcUYoASilxRigBKKXFGKAEopcUYoASilxRigBKKXF'
        'GKAEopcUYoASilxRigBKKXFGKAEopcUYoASilwKMUAJRS4oxQAlFLijFACUUuKMUAJRS4FGKAEopcUYoASilxRigBKKXFGKAEopcUYoASilxRj'
        '3oASinY4xRQAuKMUtFACYNGKUEjocUUAJijFLRQAmDQQSc8UtFACYoxS0UAJijFLRQAmDRilooATFGKWigBMUYpcAdBRQAmKMUtFACYoxS44zR'
        'QAmKMUtFACYNIRinUUAJijFLRQAmKMUuCDyMUUAN285pcUtFACYowaWigBMUYpaKAEwaMUtFACYNGKWigBMUYpaKAExRilooATFGDS0UAJijBp'
        'aKAExRilooATFGKWigBMUYNLRkjpQAmKMUtFACYNIAcelOooATFGKWigBMcUYpaKAExRilooATFGKXjFFACYoxS0UAJijFLRQAmKMUtFACYoxS'
        '0UAJijFLRQAmKMUtFACYNGKWigBMUYpaKAExRilxxmigBMUYpaKAExRilooATFGKWigBMUYpaKAExRilooAaAfXNLilooATFGKWigBMUYpaKAE'
        'xRilooATFGKWigBMHGOKMUtFACYNGKWigBMGjFLRQAmKMUtFACYoxSkZGKKAExRilooATFGKWigBMUYpaKAExRilooATFGKWigBMUUtFADqO2M'
        'fjS4FGBQAlFLgUYFACUUuBRgUAJRS4FGBQAlFLgUY6YoASilwKMCgBKKXAowKAEopcdMUYFACUUuBRgUAJRS4FGBQAlFLgUYFACUUuBRgUAJRS'
        '4FLgUANopcCjAoASilwKMCgBKKXAowKAEopcClHBzQA2indsdqTAoASilwKMCgBKKXAowKAE79aKXFLgUANoHIzS4FGBQAlFLgUYFACUUuBRgU'
        'AJRS4FGBQAlFLgUYFACUUuBRgUAJRS4FGBQAlFLgUYFACUUuBRgUAJRnNLgUYFACUUuBRgUAJxiilwKMCgBKKXAowKAEopcCjAoASilwKMCgBK'
        'KdgUmBQAlFLgUEfhQAlFLgUYFACUYA4HT3pcCjAoASinYowKAG0UuBRgUAJRS4FGBQAlFLgUYFACY4zRS4FGBQAlFLgUYFACUUuBSkYoAbRS4F'
        'GBQAlFLgUuB6UANJzRS4FLgUANopcCjAoASilwKMCgBKKXAowKAEoxg88UuBS4FADaKXAowKAEopcCjAoASjFOwKCMHFADaKXAowKAEopcCjAo'
        'ASilwKMCgBKKXAowKAEopcCjAoASilwKKAHYoxS0UAJg0YpSD2owfSgBMUYpaKAExRilooATFGKWigBMUYpaKAExRilooATFGKWigBMUAfQ0tF'
        'ACYoxS0UAJijBpec9DRQAmKMUtFACYoxS0UAJijFLS8jpQA3FGKWigBMUYpaKAExRg0tFACYNGKWigBMUYpaKAExRj6UuDRQAmKMGlooATFGKW'
        'jBoATFBBPpS0UAJijFLR+BoATFGKXBz3ooATFGKWigBMUYpaKAG7ec8UuKWigBMUYpaKAExSEHHXFOooATBoKkelLRQAmKMUtFACYoxS0pBFAD'
        'cUYpaKAExRilooATFGKWigBMUYpaKAExRilooATFGKWigBMUYNLRQA0A460uKXFGKAExRilooATFGKWigBMGjFO5xjnFJQAmKMUtFACYoxS0UA'
        'JijFLRQAmKMUtFACYoxS0UAJijFLRQAmKMUtFACYoxS0EZGOaAExRilxRQAmKMUuDRQAmKMUuKKAExQBz1paKAExRilooATFGKWigBMUYpaKAE'
        'waMGlooATFGKWigBMUYpaKAExRS0UAOopcCjAoASilwKMCgBKKXAowKAEopSPwowKAEopcCjAoASinYFJgUAJRS4FGBQAlFLgUFQeozQAlFLgU'
        'YFACUUuBRgUAJRxS4FGBQAlFLgUYFACUUuBRgUAJRS4FGBQAlFLgUuKAG0UuBRgUAJRS4FGBQAlFLgUYFACUUuBRgUAJRS4FGBQAlFLgUuBQA2'
        'ilwKMCgBKKXApcCgBtFOwPSjA9KAG0UuBRgUAJRS4FL2xQA2ilwKMCgBKKXAowKAEopcCjAoASilwKMCgBKKXAowKAEopcCjAoASinYFGBQA2i'
        'nbeM44pMCgBKKdikwKAEopcCloAb+FFLgUYFACUUuBRgUAJRS4FGBQAlFLgUYFACUUuBRgUAJRS4FGBQAlFLgUYFACUUuBRgUAJRS4FLgUANop'
        'cCjAoASilwKMCgBKKdgY96TAoASilwKMCgBKKXAowKAEopcCjAoASilwKMCgBKKXAowKAE6HIopcCjAoASgkDrS4FGBQAlFLgUYFACUUuBRgUA'
        'JRS4FGBQAlFLgUYFACUUuBRgUAJjPWilA4owKAE5z7UUuBRgUAJRS4FFADsUYpaKAExRilooATFGKWigBMe9GKWigBMUYpaKAExRilooATFGKW'
        'igBMUYpaKAExRilooATFGKWigBCMDNGKWigBMUYpaKAExRil59KKAExRilo59KAExRil7dDRQAmKMUtFACYoxS0UAJijFO5xikoATFGKWigBMU'
        'YpaPwoATFGKWigBMUYpefSigBMUYpaKAEwD0NGKXFFACYoxS0UAJijFLR+BoATFGPelooATFGKWjFACYoxS0UAJj3oxS0UAJijFLRigBMUYpaO'
        'aAExRilooATFGKWigBMe9GKWj8KAExRilooATFGKWigBMUYpaCM9RmgBMUY5paKAExRilooATHvRilAPeigBMUYpaKAExRilooATFGKWigBMUF'
        'eOuKWigBMe9GKWigBMUYpaKAExRilooATFGKWigBMUYpaKAExRilooATFGKWj8KAExRilooATFGKWigBMUYpaKAExQBzS0UAJt5zmjFLRQAmKM'
        'UtH4UAJijFLRQAmKMUtFACYoxTsdaSgBMUYpaKAExRS0UAOop1FADaKdRQA2inUUANop3FFADaKUdOmKXFADaKdRQA2inUUANop1FADaKdRQA2'
        'inUUANop1FADaKdRQA2inUUANop1FADaKdRigBtFOooAacdqKdRQA2inUUANop3HpRQA2jHOadRQA2incUUANop1HHpQA2inUcelADaKXjOOKW'
        'gBtFOxRQA2inY46UUANop1FADaKdSDB6YNACUU6igBtFOxRQA2inUY+lADaKdiigBtFOooAbRTqKAG0U6igBtFOooAbRTqKAG0U6igBtFOooAb'
        'RTqKAG0U6igBtFOooAbRTqKAG0U6igBueMUU6igBo6dc0UpHFLigBtFOooAbRTqKAG0U6igBtFOooAbR1p1FADaKdRQA2inUYFADaKdRQA2inU'
        'mBnNACUU7FFADaKdRQA2inUUANo7Yp3HpRQA2inUUANop1FADaKdRQA2inUUALijFLg0YNACYoxS4NGDQAmKMUuDRg0AJtB60YpcGjBoATFGKX'
        'BowaAExRilwaMGgBMUYpcGjBoATFGKXBowaAExRilwaMGgBMUYpcGjBoATFGKXBowaAExRilwaXBoAbijFOIIpMGgBMUYpQMUYNACYoxS4NGDQ'
        'AmKMUuDRg0AJijFLg0uDQA3FGKXBowaAExRilwaMGgBNuO9GKXBowaAExRilwaMGgBMUYpcGjBoATFGKXBowaAExRilwaMGgBMUYpcGjBoATFG'
        'KXBoIyMUAJijFLg0uKAG4B6GjFLg0YNACYoxS4NGDQAmKMUuDRg0AJj3oxS4NGDQAmKMUuDRg0AJijFLg0YNACYoxS4NGDQAgAzzRilwaMGgBM'
        'UYpcGjBoATFGKXBowaAExRilwaMGgBMUYpcGjBoATFGKdzSYNACYoxS4NGDQAmKMUuDRg0AJijFLg0vOMUANxRilwaMGgBMUYpcGjBoATFGKdg'
        '0mKAE2+9GKXBowaAExRilwaMGgBMUYpcGjBoATFGKXBowaAExRilwaMGgBMe9G33pcGjBoATFGKXBoxQAmKMUuDS4NADcUYpcGjBoATFGKXBow'
        'aAExRinYNJg0AJijFLg0YNACYoxTsGkwaAExRilwaMGgBMUYpcGjBoATFGKXBowaAExRS4NFADqKKKACiiigAooooAKKKKACiiigAooooAKKKK'
        'ACggjrRRQAUUUUAFFFFABRRRQAUUUUAFFFFAAPpmiiigAooooAM84ooooAKKKKADt0ooooAKKKKACiiigAooooAKMjOM80UUAFJwRRRQAtGKKK'
        'ACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKK'
        'KKACiiigAooooAKKKKACiiigAooooAAQRkUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAZNFFFABRRRQAUUUUAFFFFABjB60UUUAFFFFABRRRQB//'
        '2Q=='
    ),
    'static/index.html': (
        'PCFET0NUWVBFIGh0bWw+CjxodG1sIGxhbmc9InpoLUNOIj4KPGhlYWQ+CjxtZXRhIGNoYXJzZXQ9InV0Zi04Ij4KPG1ldGEgbmFtZT0idmlld3'
        'BvcnQiIGNvbnRlbnQ9IndpZHRoPWRldmljZS13aWR0aCwgaW5pdGlhbC1zY2FsZT0xIj4KPHRpdGxlPui9pueJjOivhuWIqyDCtyBNQVRMQUIg'
        'TFBSPC90aXRsZT4KPHN0eWxlPgogIDpyb290ewogICAgLS1iZzojMGUxMTE2OyAtLXBhbmVsOiMxNjFiMjM7IC0tcGFuZWwyOiMxZDIzMmQ7IC'
        '0tbGluZTojMmEzMjNkOwogICAgLS1mZzojZTZlZGYzOyAtLWRpbTojOGI5NmE1OyAtLWFjYzojNGM4ZGZmOyAtLW9rOiMzZmI5NTA7IC0td2Fy'
        'bjojZDI5OTIyOyAtLWVycjojZjg1MTQ5OwogIH0KICAqe2JveC1zaXppbmc6Ym9yZGVyLWJveH0KICBib2R5e21hcmdpbjowO2JhY2tncm91bm'
        'Q6cmFkaWFsLWdyYWRpZW50KDEyMDBweCA2MDBweCBhdCAyMCUgLTEwJSwjMTYyMzNhIDAlLHZhcigtLWJnKSA1NSUpOwogICAgICAgY29sb3I6'
        'dmFyKC0tZmcpO2ZvbnQ6MTVweC8xLjYgIlNlZ29lIFVJIiwiTWljcm9zb2Z0IFlhSGVpIixzeXN0ZW0tdWksc2Fucy1zZXJpZjttaW4taGVpZ2'
        'h0OjEwMHZofQogIC53cmFwe21heC13aWR0aDoxMTgwcHg7bWFyZ2luOjAgYXV0bztwYWRkaW5nOjI2cHggMjBweCA2MHB4fQogIGhlYWRlcntk'
        'aXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxNHB4O2ZsZXgtd3JhcDp3cmFwO21hcmdpbi1ib3R0b206MjJweH0KICBoMXtmb2'
        '50LXNpemU6MjFweDttYXJnaW46MDtmb250LXdlaWdodDo2NTA7bGV0dGVyLXNwYWNpbmc6LjVweH0KICBoMSBzcGFue2NvbG9yOnZhcigtLWFj'
        'Yyl9CiAgLnBpbGx7ZGlzcGxheTppbmxpbmUtZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjdweDtwYWRkaW5nOjVweCAxMnB4O2JvcmRlci'
        '1yYWRpdXM6OTk5cHg7CiAgICAgICAgYmFja2dyb3VuZDp2YXIoLS1wYW5lbCk7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtmb250LXNp'
        'emU6MTNweDtjb2xvcjp2YXIoLS1kaW0pfQogIC5kb3R7d2lkdGg6OHB4O2hlaWdodDo4cHg7Ym9yZGVyLXJhZGl1czo1MCU7YmFja2dyb3VuZD'
        'p2YXIoLS1kaW0pO2ZsZXg6bm9uZX0KICAuZG90LmlkbGV7YmFja2dyb3VuZDp2YXIoLS1vayk7Ym94LXNoYWRvdzowIDAgOHB4IHZhcigtLW9r'
        'KX0KICAuZG90LmJ1c3l7YmFja2dyb3VuZDp2YXIoLS13YXJuKTtib3gtc2hhZG93OjAgMCA4cHggdmFyKC0td2Fybik7YW5pbWF0aW9uOmJsaW'
        '5rIDFzIGluZmluaXRlfQogIC5kb3Qub2Zme2JhY2tncm91bmQ6dmFyKC0tZXJyKX0KICBAa2V5ZnJhbWVzIGJsaW5rezUwJXtvcGFjaXR5Oi4y'
        'NX19CiAgLmdyaWR7ZGlzcGxheTpncmlkO2dyaWQtdGVtcGxhdGUtY29sdW1uczptaW5tYXgoMzIwcHgsMWZyKSBtaW5tYXgoMzIwcHgsMS4xNW'
        'ZyKTtnYXA6MjBweH0KICBAbWVkaWEobWF4LXdpZHRoOjg4MHB4KXsuZ3JpZHtncmlkLXRlbXBsYXRlLWNvbHVtbnM6MWZyfX0KICAuY2FyZHti'
        'YWNrZ3JvdW5kOnZhcigtLXBhbmVsKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JvcmRlci1yYWRpdXM6MTRweDtwYWRkaW5nOjE4cH'
        'g7CiAgICAgICAgYm94LXNoYWRvdzowIDhweCAyNnB4IHJnYmEoMCwwLDAsLjI4KX0KICAuY2FyZCBoMnttYXJnaW46MCAwIDE0cHg7Zm9udC1z'
        'aXplOjE0cHg7Zm9udC13ZWlnaHQ6NjAwO2NvbG9yOnZhcigtLWRpbSk7bGV0dGVyLXNwYWNpbmc6MXB4fQogICNkcm9we2JvcmRlcjoycHggZG'
        'FzaGVkICMzNTQwNTE4MDtib3JkZXItcmFkaXVzOjEycHg7cGFkZGluZzozMHB4IDE2cHg7dGV4dC1hbGlnbjpjZW50ZXI7CiAgICAgICAgY3Vy'
        'c29yOnBvaW50ZXI7dHJhbnNpdGlvbjouMThzO2JhY2tncm91bmQ6IzEyMTcxZn0KICAjZHJvcDpob3ZlciwjZHJvcC5vdmVye2JvcmRlci1jb2'
        'xvcjp2YXIoLS1hY2MpO2JhY2tncm91bmQ6IzE1MjAzM30KICAjZHJvcCBie2NvbG9yOnZhcigtLWFjYyl9CiAgI2Ryb3Agc21hbGx7Y29sb3I6'
        'dmFyKC0tZGltKTtkaXNwbGF5OmJsb2NrO21hcmdpbi10b3A6OHB4O2ZvbnQtc2l6ZToxMi41cHg7bGluZS1oZWlnaHQ6MS43fQogICNwcmV2aW'
        'V3e2Rpc3BsYXk6bm9uZTttYXJnaW4tdG9wOjE0cHh9CiAgI3ByZXZpZXcgaW1ne3dpZHRoOjEwMCU7Ym9yZGVyLXJhZGl1czoxMHB4O2Rpc3Bs'
        'YXk6YmxvY2s7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKX0KICAucm93e2Rpc3BsYXk6ZmxleDtnYXA6MTBweDttYXJnaW4tdG9wOjE0cH'
        'g7ZmxleC13cmFwOndyYXB9CiAgYnV0dG9ue2ZvbnQ6aW5oZXJpdDtib3JkZXI6MDtib3JkZXItcmFkaXVzOjEwcHg7cGFkZGluZzoxMXB4IDIw'
        'cHg7Y3Vyc29yOnBvaW50ZXI7dHJhbnNpdGlvbjouMTVzfQogICNnb3tiYWNrZ3JvdW5kOnZhcigtLWFjYyk7Y29sb3I6I2ZmZjtmb250LXdlaW'
        'dodDo2MDA7ZmxleDoxO21pbi13aWR0aDoxNTBweH0KICAjZ286aG92ZXI6bm90KDpkaXNhYmxlZCl7YmFja2dyb3VuZDojM2Q3ZWYwfQogICNn'
        'bzpkaXNhYmxlZHtiYWNrZ3JvdW5kOiMyYjM0NDI7Y29sb3I6IzZkNzY4NDtjdXJzb3I6bm90LWFsbG93ZWR9CiAgLmdob3N0e2JhY2tncm91bm'
        'Q6dmFyKC0tcGFuZWwyKTtjb2xvcjp2YXIoLS1mZyk7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKX0KICAuZ2hvc3Q6aG92ZXJ7YmFja2dy'
        'b3VuZDojMjMyYjM2fQogIC5wbGF0ZXtmb250OjcwMCA0MHB4LzEuMjUgIkNvbnNvbGFzIiwiTWljcm9zb2Z0IFlhSGVpIixtb25vc3BhY2U7bG'
        'V0dGVyLXNwYWNpbmc6NXB4OwogICAgICAgICBiYWNrZ3JvdW5kOmxpbmVhci1ncmFkaWVudCgxODBkZWcsIzJiNmZkNiwjMWI0ZWEwKTtjb2xv'
        'cjojZmZmO3RleHQtYWxpZ246Y2VudGVyOwogICAgICAgICBwYWRkaW5nOjIwcHggMTJweDtib3JkZXItcmFkaXVzOjEycHg7Ym9yZGVyOjNweC'
        'Bzb2xpZCAjZjJmMmYyOwogICAgICAgICB0ZXh0LXNoYWRvdzowIDJweCA2cHggcmdiYSgwLDAsMCwuMzUpO3dvcmQtYnJlYWs6YnJlYWstYWxs'
        'fQogIC5wbGF0ZS5lbXB0eXtiYWNrZ3JvdW5kOiMxYjIwMmE7Ym9yZGVyLWNvbG9yOiMyYTMyM2Q7Y29sb3I6IzVkNjY3Mztmb250LXNpemU6Mj'
        'JweDtsZXR0ZXItc3BhY2luZzoycHh9CiAgLmNoYXJze2Rpc3BsYXk6ZmxleDtnYXA6OHB4O2ZsZXgtd3JhcDp3cmFwO21hcmdpbi10b3A6MTZw'
        'eH0KICAuY2hpcHtiYWNrZ3JvdW5kOnZhcigtLXBhbmVsMik7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtib3JkZXItcmFkaXVzOjEwcH'
        'g7cGFkZGluZzo4cHggNnB4OwogICAgICAgIHdpZHRoOjY2cHg7dGV4dC1hbGlnbjpjZW50ZXJ9CiAgLmNoaXAgaW1ne3dpZHRoOjEwMCU7Ym9y'
        'ZGVyLXJhZGl1czo1cHg7YmFja2dyb3VuZDojMDAwO2Rpc3BsYXk6YmxvY2t9CiAgLmNoaXAgLmN7Zm9udDo3MDAgMTlweC8xLjUgQ29uc29sYX'
        'MsbW9ub3NwYWNlO2NvbG9yOnZhcigtLWZnKX0KICAuYmFye2hlaWdodDo0cHg7Ym9yZGVyLXJhZGl1czozcHg7YmFja2dyb3VuZDojMmMzNTQy'
        'O292ZXJmbG93OmhpZGRlbjttYXJnaW46NHB4IDNweCAzcHh9CiAgLmJhciBpe2Rpc3BsYXk6YmxvY2s7aGVpZ2h0OjEwMCU7YmFja2dyb3VuZD'
        'psaW5lYXItZ3JhZGllbnQoOTBkZWcsIzNmYjk1MCwjZDI5OTIyKTtib3JkZXItcmFkaXVzOjNweH0KICAuY2hpcCAuc3tmb250LXNpemU6MTFw'
        'eDtjb2xvcjp2YXIoLS1kaW0pfQogIC5tZXRhe2Rpc3BsYXk6ZmxleDtnYXA6MTZweDtmbGV4LXdyYXA6d3JhcDtjb2xvcjp2YXIoLS1kaW0pO2'
        'ZvbnQtc2l6ZToxM3B4O21hcmdpbi10b3A6MTRweH0KICAubWV0YSBie2NvbG9yOnZhcigtLWZnKTtmb250LXdlaWdodDo2MDB9CiAgLmNyb3B7'
        'bWFyZ2luLXRvcDoxNHB4fQogIC5jcm9wIGltZ3t3aWR0aDoxMDAlO2JvcmRlci1yYWRpdXM6OXB4O2JvcmRlcjoxcHggc29saWQgdmFyKC0tbG'
        'luZSk7ZGlzcGxheTpibG9ja30KICAuY3JvcCBkaXYsLmpzb24gc3VtbWFyeXtjb2xvcjp2YXIoLS1kaW0pO2ZvbnQtc2l6ZToxMi41cHg7bWFy'
        'Z2luLWJvdHRvbTo2cHh9CiAgLmpzb257bWFyZ2luLXRvcDoxNHB4fQogIC5qc29uIHByZXtiYWNrZ3JvdW5kOiMwYzEwMTc7Ym9yZGVyOjFweC'
        'Bzb2xpZCB2YXIoLS1saW5lKTtib3JkZXItcmFkaXVzOjlweDtwYWRkaW5nOjEycHg7CiAgICAgICAgICAgIGZvbnQ6MTIuNXB4LzEuNiBDb25z'
        'b2xhcyxtb25vc3BhY2U7Y29sb3I6IzlmYjRjZjtvdmVyZmxvdzphdXRvO21heC1oZWlnaHQ6MjYwcHg7bWFyZ2luOjB9CiAgI3NwaW57ZGlzcG'
        'xheTpub25lO2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweDttYXJnaW4tdG9wOjE0cHg7Y29sb3I6dmFyKC0tZGltKTtmb250LXNpemU6MTMu'
        'NXB4fQogICNzcGluLm9ue2Rpc3BsYXk6ZmxleH0KICAubGRye3dpZHRoOjE2cHg7aGVpZ2h0OjE2cHg7Ym9yZGVyOjJweCBzb2xpZCAjMzM0MD'
        'VhO2JvcmRlci10b3AtY29sb3I6dmFyKC0tYWNjKTtib3JkZXItcmFkaXVzOjUwJTsKICAgICAgIGFuaW1hdGlvbjpzcGluIC44cyBsaW5lYXIg'
        'aW5maW5pdGU7ZmxleDpub25lfQogIEBrZXlmcmFtZXMgc3Bpbnt0b3t0cmFuc2Zvcm06cm90YXRlKDM2MGRlZyl9fQogICNtc2d7bWFyZ2luLX'
        'RvcDoxNHB4O3BhZGRpbmc6MTFweCAxNHB4O2JvcmRlci1yYWRpdXM6MTBweDtmb250LXNpemU6MTMuNXB4O2Rpc3BsYXk6bm9uZX0KICAjbXNn'
        'LmVycntkaXNwbGF5OmJsb2NrO2JhY2tncm91bmQ6IzNhMWExZDtib3JkZXI6MXB4IHNvbGlkICM2YjJiMzE7Y29sb3I6I2ZmYjRiMH0KICAjbX'
        'NnLndhcm57ZGlzcGxheTpibG9jaztiYWNrZ3JvdW5kOiMzYTMxMWE7Ym9yZGVyOjFweCBzb2xpZCAjNmI1YTJiO2NvbG9yOiNmMmQ5OGF9CiAg'
        'I2hpc3R7bWFyZ2luLXRvcDoyMnB4O2Rpc3BsYXk6Z3JpZDtnYXA6OHB4fQogIC5ocm93e2Rpc3BsYXk6Z3JpZDtncmlkLXRlbXBsYXRlLWNvbH'
        'VtbnM6NzZweCAxZnIgYXV0bztnYXA6MTJweDthbGlnbi1pdGVtczpjZW50ZXI7CiAgICAgICAgYmFja2dyb3VuZDp2YXIoLS1wYW5lbCk7Ym9y'
        'ZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtib3JkZXItcmFkaXVzOjEwcHg7cGFkZGluZzo4cHggMTJweDtmb250LXNpemU6MTMuNXB4fQogIC'
        '5ocm93IGltZ3toZWlnaHQ6MzBweDtib3JkZXItcmFkaXVzOjRweH0KICAuaHJvdyAudHtmb250OjYwMCAxNXB4IENvbnNvbGFzLG1vbm9zcGFj'
        'ZTtsZXR0ZXItc3BhY2luZzoycHh9CiAgLmhyb3cgLmZ7Y29sb3I6dmFyKC0tZGltKTtmb250LXNpemU6MTIuNXB4O292ZXJmbG93OmhpZGRlbj'
        't0ZXh0LW92ZXJmbG93OmVsbGlwc2lzO3doaXRlLXNwYWNlOm5vd3JhcH0KICBmb290ZXJ7Y29sb3I6dmFyKC0tZGltKTtmb250LXNpemU6MTIu'
        'NXB4O21hcmdpbi10b3A6MzBweDtsaW5lLWhlaWdodDoxLjl9CiAgY29kZXtiYWNrZ3JvdW5kOiMxZDIzMmQ7cGFkZGluZzoycHggNnB4O2Jvcm'
        'Rlci1yYWRpdXM6NXB4O2ZvbnQtc2l6ZToxMi41cHh9Cjwvc3R5bGU+CjwvaGVhZD4KPGJvZHk+CjxkaXYgY2xhc3M9IndyYXAiPgogIDxoZWFk'
        'ZXI+CiAgICA8aDE+6L2m54mM6K+G5YirIDxzcGFuPsK3IE1BVExBQjwvc3Bhbj48L2gxPgogICAgPHNwYW4gY2xhc3M9InBpbGwiPjxpIGNsYX'
        'NzPSJkb3QiIGlkPSJkb3QiPjwvaT48c3BhbiBpZD0ic3RhdCI+5q2j5Zyo6L+e5o6l4oCmPC9zcGFuPjwvc3Bhbj4KICAgIDxzcGFuIGNsYXNz'
        'PSJwaWxsIiBpZD0icGxhdCI+6K+G5Yir5byV5pOO77yaTUFUTEFCIDxjb2RlPmxwcl9tYWluPC9jb2RlPjwvc3Bhbj4KICA8L2hlYWRlcj4KCi'
        'AgPGRpdiBjbGFzcz0iZ3JpZCI+CiAgICA8IS0tIOW3pu+8muS4iuS8oCAtLT4KICAgIDxkaXYgY2xhc3M9ImNhcmQiPgogICAgICA8aDI+MSDC'
        'tyDpgInmi6novabniYznhafniYc8L2gyPgogICAgICA8ZGl2IGlkPSJkcm9wIj4KICAgICAgICA8Yj7ngrnlh7vpgInmi6k8L2I+IOaIluaKiu'
        'WbvueJh+aLluWIsOi/memHjAogICAgICAgIDxzbWFsbD7mlK/mjIEgSlBHIC8gUE5HIC8gQk1QIC8gVElG77yM5Y2V5bygIOKJpCAyME1CPGJy'
        'PuS5n+WPr+S7peebtOaOpSA8Yj5DdHJsK1Y8L2I+IOeymOi0tOaIquWbvjwvc21hbGw+CiAgICAgIDwvZGl2PgogICAgICA8aW5wdXQgdHlwZT'
        '0iZmlsZSIgaWQ9ImZpbGUiIGFjY2VwdD0iaW1hZ2UvKiIgaGlkZGVuPgogICAgICA8ZGl2IGNsYXNzPSJyb3ciPjxidXR0b24gY2xhc3M9Imdo'
        'b3N0IiBpZD0iZGVtbyI+55So56S65L6L54Wn54mHPC9idXR0b24+PC9kaXY+CgogICAgICA8ZGl2IGlkPSJwcmV2aWV3Ij4KICAgICAgICA8aW'
        '1nIGlkPSJwaW1nIiBhbHQ9IumihOiniCI+CiAgICAgIDwvZGl2PgoKICAgICAgPGRpdiBjbGFzcz0icm93Ij4KICAgICAgICA8YnV0dG9uIGlk'
        'PSJnbyIgZGlzYWJsZWQ+5byA5aeL6K+G5YirPC9idXR0b24+CiAgICAgICAgPGJ1dHRvbiBjbGFzcz0iZ2hvc3QiIGlkPSJjbHIiPua4heepuj'
        'wvYnV0dG9uPgogICAgICA8L2Rpdj4KCiAgICAgIDxkaXYgaWQ9InNwaW4iPjxpIGNsYXNzPSJsZHIiPjwvaT48c3BhbiBpZD0ic3BpblR4dCI+'
        '6K+G5Yir5Lit4oCmPC9zcGFuPjwvZGl2PgogICAgICA8ZGl2IGlkPSJtc2ciPjwvZGl2PgogICAgPC9kaXY+CgogICAgPCEtLSDlj7PvvJrnu5'
        'PmnpwgLS0+CiAgICA8ZGl2IGNsYXNzPSJjYXJkIj4KICAgICAgPGgyPjIgwrcg6K+G5Yir57uT5p6cPC9oMj4KICAgICAgPGRpdiBjbGFzcz0i'
        'cGxhdGUgZW1wdHkiIGlkPSJwbGF0ZSI+562J5b6F5LiK5Lyg54Wn54mH4oCmPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9ImNoYXJzIiBpZD0iY2'
        'hhcnMiPjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJtZXRhIiBpZD0ibWV0YSIgc3R5bGU9ImRpc3BsYXk6bm9uZSI+CiAgICAgICAgPHNwYW4+'
        '5a2X56ym5pWwIDxiIGlkPSJtTiI+LTwvYj48L3NwYW4+CiAgICAgICAgPHNwYW4+6K+G5Yir6ICX5pe2IDxiIGlkPSJtVCI+LTwvYj48L3NwYW'
        '4+CiAgICAgICAgPHNwYW4+56uv5Yiw56uvIDxiIGlkPSJtRSI+LTwvYj48L3NwYW4+CiAgICAgICAgPHNwYW4gdGl0bGU9IuefqeW9ouW6piAv'
        'IOi9pueJjOW6leiJsuWNoOavlOOAguS4pOiAheWQjOaXtui+vuagh+aJjeWIpOWumuS4uuecn+i9pueJjCwg55So5p2l5oyh5L2P5Zmq5aOw6J'
        'Od5aSp6Lev6Z2i5LiA57G755qE5YGH5YCZ6YCJIj7lrprkvY3otKjph48gPGIgaWQ9Im1RIj4tPC9iPjwvc3Bhbj4KICAgICAgPC9kaXY+CiAg'
        'ICAgIDxkaXYgY2xhc3M9ImNyb3AiIGlkPSJjcm9wQm94IiBzdHlsZT0iZGlzcGxheTpub25lIj4KICAgICAgICA8ZGl2PuWumuS9jeW5tuagoe'
        'ato+WQjueahOi9pueJjDwvZGl2PgogICAgICAgIDxpbWcgaWQ9ImNpbWciIGFsdD0i6L2m54mMIj4KICAgICAgPC9kaXY+CiAgICAgIDxkZXRh'
        'aWxzIGNsYXNzPSJqc29uIiBpZD0ianNvbkJveCIgc3R5bGU9ImRpc3BsYXk6bm9uZSI+CiAgICAgICAgPHN1bW1hcnk+5Y6f5aeLIEpTT04g57'
        'uT5p6cPC9zdW1tYXJ5PgogICAgICAgIDxwcmUgaWQ9Impzb24iPjwvcHJlPgogICAgICA8L2RldGFpbHM+CiAgICA8L2Rpdj4KICA8L2Rpdj4K'
        'CiAgPGRpdiBpZD0iaGlzdCI+PC9kaXY+CgogIDxmb290ZXI+CiAgICDor4bliKvmtYHnqIvvvJrovabniYzlrprkvY0g4oaSIOWAvuaWnOagoe'
        'atoyDihpIg5a2X56ym5YiG5YmyIOKGkiDlrZfnrKbor4bliKvvvIjmnKzmnLogTUFUTEFCIOWunuaXtuiuoeeul++8jOWbvueJh+S4jeS4iuS8'
        'oOS6keerr++8ieOAgjxicj4KICAgIOivhuWIq+S4jeWHhuaXtu+8jOWPr+WcqCBNQVRMQUIg6YeM6L+Q6KGMIDxjb2RlPmRlYnVnX29uZSgn5Z'
        'u+54mH6Lev5b6EJyk8L2NvZGU+IOafpeeci+avj+S4gOatpeeahOS4remXtOe7k+aenOOAgjxicj4KICAgIOaPkOekuuacquajgOa1i+WIsOi9'
        'pueJjOaXtuS8mue7meWHuuWFt+S9k+WOn+WboDog5YCZ6YCJ5Yy65Z+f55qE55+p5b2i5bqm5LiO6L2m54mM5bqV6Imy5Y2g5q+U5rKh5pyJ5Z'
        'CM5pe26L6+5qCHLCDlsLHkuI3kvJrnoaznjJzkuIDkuKrovabniYzlj7fjgIIKICA8L2Zvb3Rlcj4KPC9kaXY+Cgo8c2NyaXB0Pgpjb25zdCAk'
        'ID0gaWQgPT4gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoaWQpOwpsZXQgY3VyRmlsZSA9IG51bGw7CmxldCBwb2xsaW5nID0gbnVsbDsKCi8qIC'
        '0tLS0tLS0tLS0g5byV5pOO54q25oCBIC0tLS0tLS0tLS0gKi8KYXN5bmMgZnVuY3Rpb24gcG9sbFN0YXR1cygpewogIHRyeXsKICAgIGNvbnN0'
        'IHIgPSBhd2FpdCBmZXRjaCgnL2FwaS9zdGF0dXMnKTsKICAgIGNvbnN0IHMgPSBhd2FpdCByLmpzb24oKTsKICAgICQoJ2RvdCcpLmNsYXNzTm'
        'FtZSA9ICdkb3QgJyArIChzLndvcmtlciA9PT0gJ2lkbGUnID8gJ2lkbGUnIDogcy53b3JrZXIgPT09ICdidXN5JyA/ICdidXN5JyA6ICdvZmYn'
        'KTsKICAgICQoJ3N0YXQnKS50ZXh0Q29udGVudCA9IHMud29ya2VyID09PSAnaWRsZScgPyAn5byV5pOO56m66Zey77yM5Y+v5Lul6K+G5YirJw'
        'ogICAgICAgICAgICAgICAgICAgICAgICAgIDogcy53b3JrZXIgPT09ICdidXN5JyA/ICfmraPlnKjor4bliKvigKYnCiAgICAgICAgICAgICAg'
        'ICAgICAgICAgICAgOiAn5byV5pOO5pyq5ZCv5Yqo77yI6aaW5qyh6K+G5Yir5Lya6Ieq5Yqo5ZCv5Yqo77yJJzsKICAgIGlmIChzLndvcmtlci'
        'A9PT0gJ2J1c3knKSAkKCdzcGluVHh0JykudGV4dENvbnRlbnQgPSAnTUFUTEFCIOato+WcqOivhuWIq+KApic7CiAgfWNhdGNoKGUpewogICAg'
        'JCgnZG90JykuY2xhc3NOYW1lID0gJ2RvdCBvZmYnOyAkKCdzdGF0JykudGV4dENvbnRlbnQgPSAn5peg5rOV6L+e5o6l5ZCO56uvJzsKICB9Cn'
        '0KcG9sbFN0YXR1cygpOwpwb2xsaW5nID0gc2V0SW50ZXJ2YWwocG9sbFN0YXR1cywgMjAwMCk7CgovKiAtLS0tLS0tLS0tIOmAieWbviAtLS0t'
        'LS0tLS0tICovCmZ1bmN0aW9uIHBpY2soZmlsZSl7CiAgaWYoIWZpbGUgfHwgIWZpbGUudHlwZS5zdGFydHNXaXRoKCdpbWFnZS8nKSl7IHNob3'
        'dNc2coJ+ivt+mAieaLqeWbvueJh+aWh+S7ticsICdlcnInKTsgcmV0dXJuOyB9CiAgY3VyRmlsZSA9IGZpbGU7CiAgJCgncGltZycpLnNyYyA9'
        'IFVSTC5jcmVhdGVPYmplY3RVUkwoZmlsZSk7CiAgJCgncHJldmlldycpLnN0eWxlLmRpc3BsYXkgPSAnYmxvY2snOwogICQoJ2dvJykuZGlzYW'
        'JsZWQgPSBmYWxzZTsKICAkKCdtc2cnKS5jbGFzc05hbWUgPSAnJzsgJCgnbXNnJykuc3R5bGUuZGlzcGxheSA9ICdub25lJzsKICAkKCdwbGF0'
        'ZScpLmNsYXNzTmFtZSA9ICdwbGF0ZSBlbXB0eSc7ICQoJ3BsYXRlJykudGV4dENvbnRlbnQgPSAn562J5b6F6K+G5Yir4oCmJzsKICAkKCdjaG'
        'FycycpLmlubmVySFRNTCA9ICcnOyAkKCdjcm9wQm94Jykuc3R5bGUuZGlzcGxheSA9ICdub25lJzsKICAkKCdtZXRhJykuc3R5bGUuZGlzcGxh'
        'eSA9ICdub25lJzsgJCgnanNvbkJveCcpLnN0eWxlLmRpc3BsYXkgPSAnbm9uZSc7CiAgY2xlYXJJbnRlcnZhbChwb2xsaW5nKTsgcG9sbFN0YX'
        'R1cygpOwp9CiQoJ2Ryb3AnKS5vbmNsaWNrID0gKCkgPT4gJCgnZmlsZScpLmNsaWNrKCk7CiQoJ2ZpbGUnKS5vbmNoYW5nZSA9IGUgPT4gcGlj'
        'ayhlLnRhcmdldC5maWxlc1swXSk7ClsnZHJhZ2VudGVyJywnZHJhZ292ZXInXS5mb3JFYWNoKHQgPT4gJCgnZHJvcCcpLmFkZEV2ZW50TGlzdG'
        'VuZXIodCwgZSA9PiB7CiAgZS5wcmV2ZW50RGVmYXVsdCgpOyAkKCdkcm9wJykuY2xhc3NMaXN0LmFkZCgnb3ZlcicpOyB9KSk7ClsnZHJhZ2xl'
        'YXZlJywnZHJvcCddLmZvckVhY2godCA9PiAkKCdkcm9wJykuYWRkRXZlbnRMaXN0ZW5lcih0LCBlID0+IHsKICBlLnByZXZlbnREZWZhdWx0KC'
        'k7ICQoJ2Ryb3AnKS5jbGFzc0xpc3QucmVtb3ZlKCdvdmVyJyk7IH0pKTsKJCgnZHJvcCcpLmFkZEV2ZW50TGlzdGVuZXIoJ2Ryb3AnLCBlID0+'
        'IHsgaWYoZS5kYXRhVHJhbnNmZXIuZmlsZXNbMF0pIHBpY2soZS5kYXRhVHJhbnNmZXIuZmlsZXNbMF0pOyB9KTsKZG9jdW1lbnQuYWRkRXZlbn'
        'RMaXN0ZW5lcigncGFzdGUnLCBlID0+IHsKICBmb3IoY29uc3QgaXQgb2YgKGUuY2xpcGJvYXJkRGF0YT8uaXRlbXMgfHwgW10pKXsKICAgIGlm'
        'KGl0LnR5cGUuc3RhcnRzV2l0aCgnaW1hZ2UvJykpeyBwaWNrKGl0LmdldEFzRmlsZSgpKTsgYnJlYWs7IH0KICB9Cn0pOwokKCdjbHInKS5vbm'
        'NsaWNrID0gKCkgPT4gewogIGN1ckZpbGUgPSBudWxsOyAkKCdwcmV2aWV3Jykuc3R5bGUuZGlzcGxheSA9ICdub25lJzsgJCgnZ28nKS5kaXNh'
        'YmxlZCA9IHRydWU7CiAgJCgncGxhdGUnKS5jbGFzc05hbWUgPSAncGxhdGUgZW1wdHknOyAkKCdwbGF0ZScpLnRleHRDb250ZW50ID0gJ+etie'
        'W+heS4iuS8oOeFp+eJh+KApic7CiAgJCgnY2hhcnMnKS5pbm5lckhUTUwgPSAnJzsgJCgnY3JvcEJveCcpLnN0eWxlLmRpc3BsYXkgPSAnbm9u'
        'ZSc7CiAgJCgnbWV0YScpLnN0eWxlLmRpc3BsYXkgPSAnbm9uZSc7ICQoJ2pzb25Cb3gnKS5zdHlsZS5kaXNwbGF5ID0gJ25vbmUnOwogICQoJ2'
        '1zZycpLmNsYXNzTmFtZSA9ICcnOyAkKCdtc2cnKS5zdHlsZS5kaXNwbGF5ID0gJ25vbmUnOwogICQoJ2ZpbGUnKS52YWx1ZSA9ICcnOwp9OwoK'
        'LyogLS0tLS0tLS0tLSDnpLrkvovlm74gLS0tLS0tLS0tLSAqLwokKCdkZW1vJykub25jbGljayA9IGFzeW5jICgpID0+IHsKICB0cnl7CiAgIC'
        'Bjb25zdCByID0gYXdhaXQgZmV0Y2goJy9zdGF0aWMvZGVtby5qcGcnKTsKICAgIGlmKCFyLm9rKSB0aHJvdyBuZXcgRXJyb3IoJ+ekuuS+i+Wb'
        'vueJh+S4jeWcqCBzdGF0aWMvZGVtby5qcGcnKTsKICAgIGNvbnN0IGIgPSBhd2FpdCByLmJsb2IoKTsKICAgIHBpY2sobmV3IEZpbGUoW2JdLC'
        'AnZGVtb19wbGF0ZS5qcGcnLCB7dHlwZTonaW1hZ2UvanBlZyd9KSk7CiAgfWNhdGNoKGUpeyBzaG93TXNnKCfnpLrkvovlm77niYfliqDovb3l'
        'pLHotKXvvJonICsgZS5tZXNzYWdlLCAnZXJyJyk7IH0KfTsKCi8qIC0tLS0tLS0tLS0g5o+Q56S6IC0tLS0tLS0tLS0gKi8KZnVuY3Rpb24gc2'
        'hvd01zZyh0LCBraW5kKXsKICAvLyDms6jmhI86IOi/memHjOeahCBzdHlsZS5kaXNwbGF5IOW/hemhu+a4heepuiwg5ZCm5YiZ5Lya6KKr5LmL'
        '5YmN6K6+6L+H55qECiAgLy8g5YaF6IGUIGRpc3BsYXk6bm9uZSDopobnm5YsIOaPkOekuuaWh+Wtl+awuOi/nOaYvuekuuS4jeWHuuadpShXM0'
        'Mg5LyY5YWI57qnOiDlhoXogZQgPiDnsbsp44CCCiAgY29uc3QgbSA9ICQoJ21zZycpOwogIG0udGV4dENvbnRlbnQgPSB0OwogIG0uY2xhc3NO'
        'YW1lID0ga2luZCB8fCAnJzsKICBtLnN0eWxlLmRpc3BsYXkgPSB0ID8gJycgOiAnbm9uZSc7Cn0KCi8qIC0tLS0tLS0tLS0g6K+G5YirIC0tLS'
        '0tLS0tLS0gKi8KJCgnZ28nKS5vbmNsaWNrID0gYXN5bmMgKCkgPT4gewogIGlmKCFjdXJGaWxlKSByZXR1cm47CiAgJCgnZ28nKS5kaXNhYmxl'
        'ZCA9IHRydWU7CiAgJCgnc3BpbicpLmNsYXNzTGlzdC5hZGQoJ29uJyk7CiAgJCgnc3BpblR4dCcpLnRleHRDb250ZW50ID0gJ+ato+WcqOaPkO'
        'S6pOKApic7CiAgJCgnbXNnJykuY2xhc3NOYW1lID0gJyc7ICQoJ21zZycpLnN0eWxlLmRpc3BsYXkgPSAnbm9uZSc7CgogIC8vIOmmluasoeiv'
        'huWIq+imgeetiSBNQVRMQUIg5ZCv5Yqo77yM6L+Z6YeM5qC55o2u5byV5pOO54q25oCB57uZ5Ye65o+Q56S6CiAgY29uc3QgaGludHMgPSBzZX'
        'RJbnRlcnZhbChhc3luYyAoKSA9PiB7CiAgICB0cnl7CiAgICAgIGNvbnN0IHMgPSBhd2FpdCAoYXdhaXQgZmV0Y2goJy9hcGkvc3RhdHVzJykp'
        'Lmpzb24oKTsKICAgICAgJCgnc3BpblR4dCcpLnRleHRDb250ZW50ID0gcy53b3JrZXIgPT09ICdvZmYnCiAgICAgICAgPyAn5q2j5Zyo5ZCv5Y'
        'qoIE1BVExBQiDor4bliKvlvJXmk47vvIjpppbmrKHnuqYgMTAg56eS77yJ4oCmJwogICAgICAgIDogcy53b3JrZXIgPT09ICdidXN5JyA/ICdN'
        'QVRMQUIg5q2j5Zyo6K+G5Yir77yI5a6a5L2N4oaS5qCh5q2j4oaS5YiG5Ymy4oaS6K+G5Yir77yJ4oCmJwogICAgICAgIDogJ+ato+WcqOetie'
        'W+hee7k+aenOKApic7CiAgICB9Y2F0Y2goZSl7fQogIH0sIDkwMCk7CgogIHRyeXsKICAgIGNvbnN0IHQwID0gcGVyZm9ybWFuY2Uubm93KCk7'
        'CiAgICBjb25zdCByZXMgPSBhd2FpdCBmZXRjaCgnL2FwaS9yZWNvZ25pemUnLCB7CiAgICAgIG1ldGhvZDonUE9TVCcsCiAgICAgIGhlYWRlcn'
        'M6eydYLUZpbGVuYW1lJzogZW5jb2RlVVJJQ29tcG9uZW50KGN1ckZpbGUubmFtZSl9LAogICAgICBib2R5OiBjdXJGaWxlCiAgICB9KTsKICAg'
        'IGNvbnN0IGRhdGEgPSBhd2FpdCByZXMuanNvbigpOwogICAgcmVuZGVyKGRhdGEsIChwZXJmb3JtYW5jZS5ub3coKSAtIHQwKSAvIDEwMDApOw'
        'ogIH1jYXRjaChlKXsKICAgIHNob3dNc2coJ+ivt+axguWksei0pe+8micgKyBlLm1lc3NhZ2UsICdlcnInKTsKICAgICQoJ3BsYXRlJykuY2xh'
        'c3NOYW1lID0gJ3BsYXRlIGVtcHR5JzsgJCgncGxhdGUnKS50ZXh0Q29udGVudCA9ICfor4bliKvlpLHotKUnOwogIH1maW5hbGx5ewogICAgY2'
        'xlYXJJbnRlcnZhbChoaW50cyk7CiAgICAkKCdzcGluJykuY2xhc3NMaXN0LnJlbW92ZSgnb24nKTsKICAgICQoJ2dvJykuZGlzYWJsZWQgPSBm'
        'YWxzZTsKICAgIHBvbGxTdGF0dXMoKTsKICAgIGxvYWRIaXN0b3J5KCk7CiAgfQp9OwoKLyogLS0tLS0tLS0tLSDmuLLmn5MgLS0tLS0tLS0tLS'
        'AqLwpmdW5jdGlvbiByZW5kZXIoZCwgd2FsbCl7CiAgaWYoZC5maWxlKSBkLmZpbGUgPSBkZWNvZGVVUklDb21wb25lbnQoZC5maWxlKTsKICBp'
        'ZighZC5vayl7CiAgICAkKCdwbGF0ZScpLmNsYXNzTmFtZSA9ICdwbGF0ZSBlbXB0eSc7ICQoJ3BsYXRlJykudGV4dENvbnRlbnQgPSBkLnRleH'
        'QgfHwgJ+ivhuWIq+Wksei0pSc7CiAgICBzaG93TXNnKGQuZXJyb3IgPyAoJ+WHuumUme+8micgKyBkLmVycm9yKSA6IChkLndhcm5pbmcgfHwg'
        'J+acquivhuWIq+WIsOi9pueJjCcpLCAnZXJyJyk7CiAgICByZXR1cm47CiAgfQogIGlmKCFkLnRleHQpewogICAgJCgncGxhdGUnKS5jbGFzc0'
        '5hbWUgPSAncGxhdGUgZW1wdHknOyAkKCdwbGF0ZScpLnRleHRDb250ZW50ID0gJ+acquajgOa1i+WIsOi9pueJjCc7CiAgICBzaG93TXNnKGQu'
        'd2FybmluZyB8fCAn5pyq5qOA5rWL5Yiw6L2m54mM77ya6K+35o2i5LiA5byg6L2m54mM5pu05riF5pmw44CB5pu05bGF5Lit55qE54Wn54mHJy'
        'wgJ3dhcm4nKTsKICB9ZWxzZXsKICAgICQoJ3BsYXRlJykuY2xhc3NOYW1lID0gJ3BsYXRlJzsgJCgncGxhdGUnKS50ZXh0Q29udGVudCA9IGQu'
        'dGV4dDsKICAgIGlmKChkLnNjb3JlcyB8fCBbXSkuc29tZShzID0+IHMgPCAwLjYpKQogICAgICBzaG93TXNnKCfpg6jliIblrZfnrKbnva7kv6'
        'HluqblgY/kvY7vvIzlu7rorq7kurrlt6XmoLjlr7nvvIjnuqLoibIv5L2O5YiG5a2X56ym77yJJywgJ3dhcm4nKTsKICB9CgogICQoJ2NoYXJz'
        'JykuaW5uZXJIVE1MID0gJyc7CiAgKGQuZGV0YWlscyB8fCBbXSkuZm9yRWFjaChjID0+IHsKICAgIGNvbnN0IHBjdCA9IE1hdGgucm91bmQoKG'
        'Muc2NvcmUgfHwgMCkgKiAxMDApOwogICAgY29uc3QgZWwgPSBkb2N1bWVudC5jcmVhdGVFbGVtZW50KCdkaXYnKTsKICAgIGVsLmNsYXNzTmFt'
        'ZSA9ICdjaGlwJzsKICAgIGVsLmlubmVySFRNTCA9CiAgICAgIChjLnBuZyA/ICc8aW1nIHNyYz0iZGF0YTppbWFnZS9wbmc7YmFzZTY0LCcgKy'
        'BjLnBuZyArICciIGFsdD0iIj4nIDogJycpICsKICAgICAgJzxkaXYgY2xhc3M9ImMiPicgKyAoYy5jaGFyIHx8ICc/JykgKyAnPC9kaXY+JyAr'
        'CiAgICAgICc8ZGl2IGNsYXNzPSJiYXIiPjxpIHN0eWxlPSJ3aWR0aDonICsgcGN0ICsgJyUiPjwvaT48L2Rpdj4nICsKICAgICAgJzxkaXYgY2'
        'xhc3M9InMiPicgKyBwY3QgKyAnJTwvZGl2Pic7CiAgICAkKCdjaGFycycpLmFwcGVuZENoaWxkKGVsKTsKICB9KTsKCiAgJCgnbWV0YScpLnN0'
        'eWxlLmRpc3BsYXkgPSAnZmxleCc7CiAgJCgnbVEnKS50ZXh0Q29udGVudCA9IGQucXVhbGl0eQogICAgICA/IChkLnF1YWxpdHkuZXh0ZW50IH'
        'x8IDApLnRvRml4ZWQoMikgKyAnIC8gJyArIChkLnF1YWxpdHkuY29sb3JGcmFjIHx8IDApLnRvRml4ZWQoMikKICAgICAgOiAnLSc7CiAgJCgn'
        'bU4nKS50ZXh0Q29udGVudCA9IChkLm5DaGFycyA/PyAoZC5jaGFycyB8fCBbXSkubGVuZ3RoKTsKICAkKCdtVCcpLnRleHRDb250ZW50ID0gKG'
        'QuZWxhcHNlZCA/IGQuZWxhcHNlZC50b0ZpeGVkKDIpIDogJy0nKSArICcgcyc7CiAgJCgnbUUnKS50ZXh0Q29udGVudCA9IHdhbGwudG9GaXhl'
        'ZCgyKSArICcgcyc7CgogIGlmKGQucGxhdGVJbWFnZVBuZyl7CiAgICAkKCdjaW1nJykuc3JjID0gJ2RhdGE6aW1hZ2UvcG5nO2Jhc2U2NCwnIC'
        'sgZC5wbGF0ZUltYWdlUG5nOwogICAgJCgnY3JvcEJveCcpLnN0eWxlLmRpc3BsYXkgPSAnYmxvY2snOwogIH0KICBjb25zdCBicmllZiA9IE9i'
        'amVjdC5hc3NpZ24oe30sIGQpOwogIGlmKGJyaWVmLnBsYXRlSW1hZ2VQbmcpIGJyaWVmLnBsYXRlSW1hZ2VQbmcgPSAnPGJhc2U2NCBQTkcg5b'
        'ey55yB55WlPic7CiAgaWYoYnJpZWYuZGV0YWlscykgYnJpZWYuZGV0YWlscyA9IChicmllZi5kZXRhaWxzfHxbXSkubWFwKHggPT4gKHtjaGFy'
        'OnguY2hhciwgc2NvcmU6TnVtYmVyKCh4LnNjb3JlfHwwKS50b0ZpeGVkKDMpKX0pKTsKICAkKCdqc29uJykudGV4dENvbnRlbnQgPSBKU09OLn'
        'N0cmluZ2lmeShicmllZiwgbnVsbCwgMik7CiAgJCgnanNvbkJveCcpLnN0eWxlLmRpc3BsYXkgPSAnYmxvY2snOwp9CgovKiAtLS0tLS0tLS0t'
        'IOWOhuWPsiAtLS0tLS0tLS0tICovCmFzeW5jIGZ1bmN0aW9uIGxvYWRIaXN0b3J5KCl7CiAgdHJ5ewogICAgY29uc3QgciA9IGF3YWl0IGZldG'
        'NoKCcvYXBpL2hpc3RvcnknKTsKICAgIGNvbnN0IHtpdGVtc30gPSBhd2FpdCByLmpzb24oKTsKICAgIGNvbnN0IGJveCA9ICQoJ2hpc3QnKTsK'
        'ICAgIGJveC5pbm5lckhUTUwgPSAnJzsKICAgIGlmKCFpdGVtcyB8fCAhaXRlbXMubGVuZ3RoKSByZXR1cm47CiAgICBjb25zdCBoID0gZG9jdW'
        '1lbnQuY3JlYXRlRWxlbWVudCgnZGl2Jyk7CiAgICBoLmNsYXNzTmFtZSA9ICdjYXJkJzsKICAgIGguaW5uZXJIVE1MID0gJzxoMj7or4bliKvo'
        'rrDlvZU8L2gyPic7CiAgICBpdGVtcy5mb3JFYWNoKGl0ID0+IHsKICAgICAgY29uc3QgZWwgPSBkb2N1bWVudC5jcmVhdGVFbGVtZW50KCdkaX'
        'YnKTsKICAgICAgZWwuY2xhc3NOYW1lID0gJ2hyb3cnOwogICAgICBlbC5pbm5lckhUTUwgPQogICAgICAgICc8c3BhbiBjbGFzcz0iZiI+JyAr'
        'IChpdC50aW1lIHx8ICcnKSArICc8L3NwYW4+JyArCiAgICAgICAgJzxzcGFuIGNsYXNzPSJ0Ij4nICsgKGl0LnRleHQgfHwgJ+acquivhuWIqy'
        'cpICsgJzwvc3Bhbj4nICsKICAgICAgICAnPHNwYW4gY2xhc3M9ImYiPicgKyAoaXQuZmlsZSB8fCAnJykgKyAnPC9zcGFuPic7CiAgICAgIGgu'
        'YXBwZW5kQ2hpbGQoZWwpOwogICAgfSk7CiAgICBib3guYXBwZW5kQ2hpbGQoaCk7CiAgfWNhdGNoKGUpe30KfQpsb2FkSGlzdG9yeSgpOwo8L3'
        'NjcmlwdD4KPC9ib2R5Pgo8L2h0bWw+'
    ),
    'worker/worker_lpr.m': (
        'ZnVuY3Rpb24gd29ya2VyX2xwcihqb2JzRGlyLCBwcm9qRGlyKQolV09SS0VSX0xQUiDovabniYzor4bliKvluLjpqbvlt6XkvZzov5vnqIso6K'
        'KrIHNlcnZlci9zZXJ2ZXIucHkg572R56uZ5ZCO56uv6LCD55SoKQolCiUgICDmiYvliqjlkK/liqg6ICBtYXRsYWIgLWJhdGNoICJjZCgnPHNl'
        'cnZlciDnm67lvZU+Jyk7IHdvcmtlcl9scHIiCiUgICDpgJrluLjnlLEgc2VydmVyLnB5IOiHquWKqOaLiei1tywg5LiN55So5omL5Yqo6L+Q6K'
        'GM44CCCiUKJSAgIOW3peS9nOaWueW8jzog6L2u6K+iIGpvYnMvaW5jb21pbmcg5LiL55qE5Zu+54mHIC0+IOiwgyBscHJfbWFpbiDor4bliKsg'
        'LT4KJSAgICAgICAgICAgICDmiornu5PmnpzlhpnmiJAgSlNPTiDliLAgam9icy9yZXN1bHRzLzxpZD4uanNvbiwg5YaN5Yig5o6J6L6T5YWl5p'
        'aH5Lu244CCCiUgICBKU09OIOmHjOW4puedgOi9pueJjOWbvi/lrZfnrKblm77nmoQgYmFzZTY0LCDliY3nq6/lj6/ku6Xnm7TmjqXmmL7npLrj'
        'gIIKJSAgIOmAgOWHujog5ZyoIGpvYnMg55uu5b2V5LiL5pS+5LiA5Liq5ZCN5Li6IHN0b3Ag55qE56m65paH5Lu244CCCgppZiBuYXJnaW4gPC'
        'AxIHx8IGlzZW1wdHkoam9ic0RpcikKICAgIGpvYnNEaXIgPSBmdWxsZmlsZShmaWxlcGFydHMobWZpbGVuYW1lKCdmdWxscGF0aCcpKSwgJ2pv'
        'YnMnKTsKZW5kCmlmIG5hcmdpbiA8IDIgfHwgaXNlbXB0eShwcm9qRGlyKQogICAgcHJvakRpciA9IGRlZmF1bHRQcm9qRGlyKGZpbGVwYXJ0cy'
        'htZmlsZW5hbWUoJ2Z1bGxwYXRoJykpKTsKZW5kCgppbkRpciAgID0gZnVsbGZpbGUoam9ic0RpciwgJ2luY29taW5nJyk7CnJlc0RpciAgPSBm'
        'dWxsZmlsZShqb2JzRGlyLCAncmVzdWx0cycpOwpmb3IgZCA9IHtpbkRpciwgcmVzRGlyfQogICAgaWYgZXhpc3QoZHsxfSwgJ2RpcicpIH49ID'
        'csIG1rZGlyKGR7MX0pOyBlbmQKZW5kCnN0YXR1c0ZpbGUgPSBmdWxsZmlsZShqb2JzRGlyLCAnc3RhdHVzLmpzb24nKTsKc3RvcEZpbGUgICA9'
        'IGZ1bGxmaWxlKGpvYnNEaXIsICdzdG9wJyk7CgphZGRwYXRoKGdlbnBhdGgocHJvakRpcikpOwppZiBleGlzdCgnbHByX21haW4nLCAnZmlsZS'
        'cpIH49IDIKICAgIGVycm9yKCd3b3JrZXJfbHByOm5vUHJvamVjdCcsICfmib7kuI3liLAgbHByX21haW4ubSwg6K+35qOA5p+l5bel56iL55uu'
        '5b2VOiAlcycsIHByb2pEaXIpOwplbmQKCmZwcmludGYoJ1t3b3JrZXJdIOWwsee7qiB8IOW3peeoi+ebruW9lSAlc1xuJywgcHJvakRpcik7Cm'
        'ZwcmludGYoJ1t3b3JrZXJdIOebkeWQrOS4rSAlc1xuJywgaW5EaXIpOwp3cml0ZVN0YXR1cyhzdGF0dXNGaWxlLCAnaWRsZScsICcnLCAnJyk7'
        'CgpsYXN0QmVhdCA9IDA7CndoaWxlIHRydWUKICAgIGlmIGV4aXN0KHN0b3BGaWxlLCAnZmlsZScpID09IDIKICAgICAgICBkZWxldGUoc3RvcE'
        'ZpbGUpOwogICAgICAgIHdyaXRlU3RhdHVzKHN0YXR1c0ZpbGUsICdzdG9wcGVkJywgJycsICcnKTsKICAgICAgICBmcHJpbnRmKCdbd29ya2Vy'
        'XSDmlLbliLAgc3RvcCDkv6Hlj7csIOmAgOWHulxuJyk7CiAgICAgICAgYnJlYWs7CiAgICBlbmQKCiAgICBkID0gW2RpcihmdWxsZmlsZShpbk'
        'RpciwgJyouanBnJykpOyAgZGlyKGZ1bGxmaWxlKGluRGlyLCAnKi5qcGVnJykpOyAuLi4KICAgICAgICAgZGlyKGZ1bGxmaWxlKGluRGlyLCAn'
        'Ki5wbmcnKSk7ICBkaXIoZnVsbGZpbGUoaW5EaXIsICcqLmJtcCcpKTsgLi4uCiAgICAgICAgIGRpcihmdWxsZmlsZShpbkRpciwgJyoudGlmJy'
        'kpOyAgZGlyKGZ1bGxmaWxlKGluRGlyLCAnKi50aWZmJykpXTsKICAgIGQgPSBkKH5bZC5pc2Rpcl0pOwoKICAgIGlmIGlzZW1wdHkoZCkKICAg'
        'ICAgICBwYXVzZSgwLjE1KTsKICAgICAgICBpZiBub3cgLSBsYXN0QmVhdCA+IDIgLyA4NjQwMAogICAgICAgICAgICB3cml0ZVN0YXR1cyhzdG'
        'F0dXNGaWxlLCAnaWRsZScsICcnLCAnJyk7CiAgICAgICAgICAgIGxhc3RCZWF0ID0gbm93OwogICAgICAgIGVuZAogICAgICAgIGNvbnRpbnVl'
        'OwogICAgZW5kCgogICAgW34sIGtdICA9IG1pbihbZC5kYXRlbnVtXSk7ICAgICAgICAgICUg5YWI5Yiw5YWI5aSE55CGCiAgICBpbkZpbGUgID'
        '0gZnVsbGZpbGUoaW5EaXIsIGQoaykubmFtZSk7CiAgICBbfiwgaWRdID0gZmlsZXBhcnRzKGQoaykubmFtZSk7CiAgICBmcHJpbnRmKCdbd29y'
        'a2VyXSDlpITnkIYgJXNcbicsIGQoaykubmFtZSk7CiAgICB3cml0ZVN0YXR1cyhzdGF0dXNGaWxlLCAnYnVzeScsIGlkLCAnJyk7CgogICAgdD'
        'AgID0gdGljOwogICAgb3V0ID0gc3RydWN0KCk7CiAgICBvdXQuaWQgPSBpZDsKICAgIHRyeQogICAgICAgIHIgPSBscHJfbWFpbihpbkZpbGUs'
        'ICdTaG93RmlndXJlJywgZmFsc2UpOwogICAgICAgIG91dC5vayAgICAgICA9IHRydWU7CiAgICAgICAgb3V0LnRleHQgICAgID0gci50ZXh0Ow'
        'ogICAgICAgIG91dC5jaGFycyAgICA9IHIuY2hhcnM7CiAgICAgICAgb3V0LnNjb3JlcyAgID0gci5zY29yZXM7CiAgICAgICAgb3V0LnBsYXRl'
        'Qm94ID0gci5wbGF0ZUJveDsKICAgICAgICBvdXQubkNoYXJzICAgPSBudW1lbChyLmNoYXJzKTsKICAgICAgICBvdXQucGxhdGVJbWFnZVBuZy'
        'A9IGltZzJiNjQodXBzY2FsZVBsYXRlKHIucGxhdGVJbWFnZSkpOwogICAgICAgIG91dC5kZXRhaWxzICA9IGJ1aWxkRGV0YWlscyhyLmNoYXJz'
        'LCByLnNjb3Jlcywgci5jaGFySW1hZ2VzKTsKICAgICAgICBvdXQud2FybmluZyAgPSAnJzsKICAgICAgICBvdXQucmVhc29uICAgPSAnJzsKIC'
        'AgICAgICBvdXQucXVhbGl0eSAgPSBxdWFsaXR5T2Yocik7CiAgICAgICAgaWYgaXNlbXB0eShyLnRleHQpCiAgICAgICAgICAgIG91dC5yZWFz'
        'b24gPSByLnJlYXNvbjsKICAgICAgICAgICAgaWYgaXNlbXB0eShvdXQucmVhc29uKQogICAgICAgICAgICAgICAgb3V0Lndhcm5pbmcgPSAn5p'
        'yq5qOA5rWL5Yiw6L2m54mMLCDor7fmjaLkuIDlvKDovabniYzmm7TmuIXmmbDjgIHmm7TlsYXkuK3nmoTnhafniYcnOwogICAgICAgICAgICBl'
        'bHNlCiAgICAgICAgICAgICAgICBvdXQud2FybmluZyA9IFsn5pyq5qOA5rWL5Yiw6L2m54mMOiAnIG91dC5yZWFzb24gJ+OAguW7uuiuruaNou'
        'S4gOW8oOi9pueJjOabtOa4heaZsOOAgeabtOWxheS4reeahOeFp+eJhyddOwogICAgICAgICAgICBlbmQKICAgICAgICBlbmQKICAgIGNhdGNo'
        'IGVycgogICAgICAgIG91dC5vayAgICAgID0gZmFsc2U7CiAgICAgICAgb3V0LnRleHQgICAgPSAnJzsKICAgICAgICBvdXQuY2hhcnMgICA9IH'
        't9OwogICAgICAgIG91dC5zY29yZXMgID0gW107CiAgICAgICAgb3V0LmRldGFpbHMgPSB7fTsKICAgICAgICBvdXQud2FybmluZyA9ICcnOwog'
        'ICAgICAgIG91dC5yZWFzb24gID0gJyc7CiAgICAgICAgb3V0LnF1YWxpdHkgPSBxdWFsaXR5T2YoW10pOwogICAgICAgIG91dC5lcnJvciAgID'
        '0gZXJyLm1lc3NhZ2U7CiAgICBlbmQKICAgIG91dC5lbGFwc2VkID0gdG9jKHQwKTsKCiAgICB0bXBGID0gZnVsbGZpbGUocmVzRGlyLCBbaWQg'
        'Jy5qc29uLnRtcCddKTsKICAgIG91dEYgPSBmdWxsZmlsZShyZXNEaXIsIFtpZCAnLmpzb24nXSk7CiAgICBmaWQgID0gZm9wZW4odG1wRiwgJ3'
        'cnLCAnbicsICdVVEYtOCcpOwogICAgaWYgZmlkIDwgMAogICAgICAgIHdhcm5pbmcoJ3dvcmtlcl9scHI6d3JpdGVGYWlsJywgJ+aXoOazleWG'
        'meWFpSAlcycsIHRtcEYpOwogICAgZWxzZQogICAgICAgIGZ3cml0ZShmaWQsIGpzb25lbmNvZGUob3V0KSwgJ2NoYXInKTsKICAgICAgICBmY2'
        'xvc2UoZmlkKTsKICAgICAgICBtb3ZlZmlsZSh0bXBGLCBvdXRGLCAnZicpOyAgICAgICAlIOWOn+WtkOabv+aNojog5pyN5Yqh5Zmo6KeB5Yiw'
        'IC5qc29uIOWNs+WujOaIkAogICAgZW5kCiAgICBkZWxldGUoaW5GaWxlKTsKICAgIGZwcmludGYoJ1t3b3JrZXJdIOWujOaIkCAlcyAtPiAlcy'
        'AoJS4yZiBzKVxuJywgaWQsIG91dC50ZXh0LCBvdXQuZWxhcHNlZCk7CiAgICB3cml0ZVN0YXR1cyhzdGF0dXNGaWxlLCAnaWRsZScsICcnLCBv'
        'dXQudGV4dCk7CiAgICBsYXN0QmVhdCA9IG5vdzsKZW5kCmVuZAoKJSA9PT09PT09PT09PT09PT09PT09PT09PT0g5bGA6YOo5Ye95pWwID09PT'
        '09PT09PT09PT09PT09PT09PT09PQoKZnVuY3Rpb24gd3JpdGVTdGF0dXMoZiwgc3RhdGUsIGpvYklkLCBsYXN0VGV4dCkKcGlkID0gMDsKdHJ5'
        'LCBwaWQgPSBmZWF0dXJlKCdnZXRwaWQnKTsgY2F0Y2gsIGVuZApzID0gc3RydWN0KCdzdGF0ZScsIHN0YXRlLCAnam9iJywgam9iSWQsICdsYX'
        'N0VGV4dCcsIGxhc3RUZXh0LCAuLi4KICAgICAgICAgICAndGltZScsIHBvc2l4dGltZShkYXRldGltZSgnbm93JywgJ1RpbWVab25lJywgJ2xv'
        'Y2FsJykpLCAncGlkJywgcGlkKTsKdG1wID0gW2YgJy50bXAnXTsKZmlkID0gZm9wZW4odG1wLCAndycsICduJywgJ1VURi04Jyk7CmlmIGZpZC'
        'A+PSAwCiAgICBmd3JpdGUoZmlkLCBqc29uZW5jb2RlKHMpLCAnY2hhcicpOwogICAgZmNsb3NlKGZpZCk7CiAgICBtb3ZlZmlsZSh0bXAsIGYs'
        'ICdmJyk7CmVuZAplbmQKCmZ1bmN0aW9uIEQgPSBidWlsZERldGFpbHMoY2hhcnMsIHNjb3JlcywgY2hhckltYWdlcykKJUJVSUxEREVUQUlMUy'
        'Dmr4/kuKrlrZfnrKY6IOivhuWIq+e7k+aenCArIOe9ruS/oeW6piArIOS6jOWAvOWbvihiYXNlNjQgUE5HKQpEID0gY2VsbCgxLCBudW1lbChj'
        'aGFycykpOwpmb3IgayA9IDE6bnVtZWwoY2hhcnMpCiAgICBlID0gc3RydWN0KCk7CiAgICBlLmNoYXIgID0gY2hhcnN7a307CiAgICBpZiBrID'
        'w9IG51bWVsKHNjb3JlcyksIGUuc2NvcmUgPSBzY29yZXMoayk7IGVsc2UsIGUuc2NvcmUgPSAwOyBlbmQKICAgIGUucG5nID0gJyc7CiAgICBp'
        'ZiBrIDw9IG51bWVsKGNoYXJJbWFnZXMpICYmIH5pc2VtcHR5KGNoYXJJbWFnZXN7a30pCiAgICAgICAgYmlnID0gaW1yZXNpemUoY2hhckltYW'
        'dlc3trfSwgNiwgJ25lYXJlc3QnKTsKICAgICAgICByZ2IgPSByZXBtYXQodWludDgoMjU1KSAqIHVpbnQ4KGJpZyksIDEsIDEsIDMpOwogICAg'
        'ICAgIGUucG5nID0gaW1nMmI2NChyZ2IpOwogICAgZW5kCiAgICBEe2t9ID0gZTsKZW5kCmVuZAoKZnVuY3Rpb24gYmlnID0gdXBzY2FsZVBsYX'
        'RlKGltZykKJVVQU0NBTEVQTEFURSDnvZHpobXkuIropoHmiorovabniYzlm77mlL7lpKfmmL7npLosIOWFiOWcqCBNQVRMQUIg6YeM5Y+M5LiJ'
        '5qyh5pS+5aSnLCDmr5TmtY/op4jlmajmi4nkvLjmuIXmmbAKYmlnID0gaW1nOwppZiBpc2VtcHR5KGltZykgfHwgc2l6ZShpbWcsIDIpID49ID'
        'UwMCwgcmV0dXJuOyBlbmQKYmlnID0gaW1yZXNpemUoaW1nLCA2MDAgLyBzaXplKGltZywgMiksICdiaWN1YmljJyk7CmVuZAoKZnVuY3Rpb24g'
        'cSA9IHF1YWxpdHlPZihyKQolUVVBTElUWU9GIOWumuS9jei0qOmHj+aMh+aghyjnn6nlvaLluqYv5bqV6Imy5Y2g5q+UL+mVv+WuveavlCksIO'
        'WJjeerr+WPr+eUqOadpeWIpOaWree7k+aenOaYr+WQpuWPr+mdoApxID0gc3RydWN0KCdzY29yZScsIDAsICdleHRlbnQnLCAwLCAnY29sb3JG'
        'cmFjJywgMCwgJ2FzcGVjdCcsIDAsICdzb3VyY2UnLCAnJyk7CmlmIGlzZW1wdHkocikgfHwgfmlzZmllbGQociwgJ3Njb3JlSW5mbycpLCByZX'
        'R1cm47IGVuZApzID0gci5zY29yZUluZm87CmZvciBmbiA9IHsnc2NvcmUnLCAnZXh0ZW50JywgJ2NvbG9yRnJhYycsICdhc3BlY3QnfQogICAg'
        'aWYgaXNmaWVsZChzLCBmbnsxfSkgJiYgfmlzZW1wdHkocy4oZm57MX0pKSwgcS4oZm57MX0pID0gZG91YmxlKHMuKGZuezF9KSk7IGVuZAplbm'
        'QKaWYgaXNmaWVsZChzLCAnc291cmNlJyksIHEuc291cmNlID0gcy5zb3VyY2U7IGVuZAplbmQKCmZ1bmN0aW9uIGI2NCA9IGltZzJiNjQoaW1n'
        'KQolSU1HMkI2NCDlm77lg48gLT4gYmFzZTY0KFBORyksIOeUqOS6juWhnui/myBKU09OIOe7mee9kemhteaYvuekugpiNjQgPSAnJzsKaWYgaX'
        'NlbXB0eShpbWcpLCByZXR1cm47IGVuZAp0bXAgPSBbdGVtcG5hbWUgJy5wbmcnXTsKdHJ5CiAgICBpbXdyaXRlKGltZywgdG1wKTsKICAgIGZp'
        'ZCA9IGZvcGVuKHRtcCwgJ3InKTsKICAgIHJhdyA9IGZyZWFkKGZpZCwgaW5mLCAnKnVpbnQ4Jyk7CiAgICBmY2xvc2UoZmlkKTsKICAgIGRlbG'
        'V0ZSh0bXApOwogICAgYjY0ID0gY2hhcihtYXRsYWIubmV0LmJhc2U2NGVuY29kZShyYXcpKTsKY2F0Y2gKICAgIGI2NCA9ICcnOwplbmQKZW5k'
        'CgoKZnVuY3Rpb24gZCA9IGRlZmF1bHRQcm9qRGlyKGhlcmUpCiVERUZBVUxUUFJPSkRJUiDlrprkvY0gTUFUTEFCIOivhuWIq+WGheaguOaJgO'
        'WcqOebruW9lSAo5YW85a65IG1hdGxhYi8g5LiOIGxwcl9tYXRsYWIvIOS4pOenjeebruW9leWQjSkKY2FuZHMgPSB7ZnVsbGZpbGUoaGVyZSwg'
        'Jy4uJywgJ21hdGxhYicpLCBmdWxsZmlsZShoZXJlLCAnLi4nLCAnbHByX21hdGxhYicpfTsKZm9yIGsgPSAxOm51bWVsKGNhbmRzKQogICAgaW'
        'YgZXhpc3QoZnVsbGZpbGUoY2FuZHN7a30sICdscHJfbWFpbi5tJyksICdmaWxlJykgPT0gMgogICAgICAgIGQgPSBjYW5kc3trfTsKICAgICAg'
        'ICByZXR1cm4KICAgIGVuZAplbmQKZCA9IGNhbmRzezF9OwplbmQK'
    ),
}

# ============================ 配置 ============================
HOST      = os.environ.get('LPR_HOST', '0.0.0.0')
PORT      = int(os.environ.get('LPR_PORT', '8765'))
TOKEN     = os.environ.get('LPR_TOKEN', '').strip()
PUBLIC    = os.environ.get('LPR_PUBLIC', '') == '1'
MATLAB    = os.environ.get('LPR_MATLAB') or shutil.which('matlab') or ''
MAX_BYTES = 20 * 1024 * 1024        # 单张图片上限 20MB
TIMEOUT   = 120                     # 等 MATLAB 出结果的最长时间(秒)
ALLOWED   = {'.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff'}
HISTORY   = []
HIST_MAX  = 20

STARTUP_GRACE   = 90                # worker 预热宽限期: 期间绝不重复拉起 MATLAB
LAUNCH_COOLDOWN = 15                # 两次拉起的最小间隔, 防止崩溃时开出几十个 MATLAB

_worker        = None
_last_launch   = 0.0
_launch_count  = 0
_shutting_down = False      # 关机中: 请求线程不许再拉起新的 MATLAB
_lock          = threading.Lock()
_tunnel       = None

BASE = os.path.dirname(os.path.abspath(__file__))


# ============================ 释放内嵌资源 ============================
def extract_payload():
    '''把打包进来的网页/MATLAB 内核/worker 释放到临时目录'''
    root = os.path.join(tempfile.gettempdir(), 'lpr_runtime_' + PKG_ID)
    if os.path.isfile(os.path.join(root, '.extracted')):
        return root
    for name in list(PAYLOAD_B64):
        p = os.path.join(root, name.replace('/', os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'wb') as f:
            f.write(base64.b64decode(PAYLOAD_B64[name]))
    with open(os.path.join(root, '.extracted'), 'w', encoding='utf-8') as f:
        f.write(PKG_ID)
    return root


ROOT     = extract_payload()
CORE_DIR = os.path.join(ROOT, 'core')
JOBS     = os.path.join(ROOT, 'jobs')
IN_DIR   = os.path.join(JOBS, 'incoming')
RES_DIR  = os.path.join(JOBS, 'results')
STATUS_F = os.path.join(JOBS, 'status.json')
STOP_F   = os.path.join(JOBS, 'stop')
LOG_F    = os.path.join(JOBS, 'worker.log')
WORKER_M = os.path.join(ROOT, 'worker', 'worker_lpr.m')
PAGE_HTML = os.path.join(ROOT, 'static', 'index.html')
DEMO_JPG  = os.path.join(ROOT, 'static', 'demo.jpg')


def local_ips():
    '''列出本机在局域网里的 IPv4 地址(只做路由查询, 不真的联网)'''
    ips = set()
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.connect(('8.8.8.8', 80))
        ips.add(sk.getsockname()[0])
        sk.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    return sorted(i for i in ips if not i.startswith('127.'))

# ============================ MATLAB worker 管理 ============================
def worker_state():
    '''返回 (state, job, lastText, 心跳年龄秒)'''
    try:
        with open(STATUS_F, 'r', encoding='utf-8') as f:
            st = json.load(f)
        age = time.time() - os.path.getmtime(STATUS_F)   # 用文件时间算心跳, 避免时钟/时区不一致
        if age > 8:
            return 'off', '', '', age
        return st.get('state', 'off'), st.get('job', ''), st.get('lastText', ''), age
    except Exception:
        return 'off', '', '', 1e9


def _kill_worker():
    global _worker
    if _worker is not None and _worker.poll() is None:
        try:
            _worker.terminate()
        except Exception:
            pass
        try:
            _worker.wait(timeout=6)
        except Exception:
            try:
                _worker.kill()
            except Exception:
                pass
    _worker = None


def _launch_worker():
    global _worker, _last_launch, _launch_count
    os.makedirs(JOBS, exist_ok=True)
    for d in (IN_DIR, RES_DIR):
        os.makedirs(d, exist_ok=True)
    if os.path.exists(STOP_F):
        try:
            os.remove(STOP_F)
        except OSError:
            pass
    q = lambda p: p.replace("'", "''")
    code = "addpath('%s'); worker_lpr('%s','%s')" % (
        q(os.path.dirname(WORKER_M)), q(JOBS), q(CORE_DIR))
    cmd = [MATLAB, '-batch', code]
    log = open(LOG_F, 'ab')
    log.write(('\n==== %s 启动 worker ====\n' % time.strftime('%Y-%m-%d %H:%M:%S')).encode('utf-8'))
    log.flush()
    _worker = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    _last_launch = time.time()
    _launch_count += 1
    print('[server] 已启动 MATLAB worker (pid=%s, 本进程第 %d 次), 预热约 10 秒...'
          % (_worker.pid, _launch_count))


def ensure_worker(force=False):
    '''确保 MATLAB worker 在跑; 正在预热时不会被重复拉起'''
    if _shutting_down:
        return              # 已经在关机了, 别再开新的 MATLAB, 否则会留下杀不掉的孤儿进程
    state, _, _, _ = worker_state()
    if state in ('idle', 'busy') and not force:
        return
    with _lock:
        state, _, _, _ = worker_state()
        if state in ('idle', 'busy') and not force:
            return
        if _worker is not None and _worker.poll() is None:
            alive_for = time.time() - _last_launch
            if alive_for < STARTUP_GRACE:
                return                  # 正在预热: 耐心等, 绝不另开一个 MATLAB
            if not force:
                return
            print('[server] worker 启动 %.0f 秒仍无心跳, 判定卡死, 重启' % alive_for)
            _kill_worker()
        if _last_launch and time.time() - _last_launch < LAUNCH_COOLDOWN:
            return                      # 刚拉起来就死了: 冷却期内不再重复拉
        if not MATLAB:
            raise RuntimeError('找不到 matlab 可执行文件, 请设置环境变量 LPR_MATLAB')
        _launch_worker()


def begin_shutdown(httpd):
    '''收到关机请求: 先立标志(挡住请求线程重启 MATLAB), 再停 HTTP 服务'''
    global _shutting_down
    _shutting_down = True
    threading.Thread(target=httpd.shutdown, daemon=True).start()


def stop_worker(wait=12):
    '''关掉 worker(含上一次 server 留下的孤儿 worker)'''
    global _last_launch
    try:
        with open(STOP_F, 'w', encoding='utf-8') as f:
            f.write('stop')
    except Exception:
        pass
    _kill_worker()
    deadline = time.time() + wait
    while time.time() < deadline:
        state, _, _, age = worker_state()
        if state == 'off' or age > 8:
            break
        time.sleep(0.3)
    try:
        if os.path.exists(STOP_F):
            os.remove(STOP_F)
    except Exception:
        pass
    _last_launch = 0.0


def recognize(data, filename):
    os.makedirs(IN_DIR, exist_ok=True)
    os.makedirs(RES_DIR, exist_ok=True)
    ensure_worker()

    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED:
        ext = '.jpg'
    jid = uuid.uuid4().hex[:12]

    part = os.path.join(IN_DIR, jid + ext + '.part')
    with open(part, 'wb') as f:                       # 先写 .part 再改名, 避免 worker 读到半个文件
        f.write(data)
    os.replace(part, os.path.join(IN_DIR, jid + ext))

    res_path = os.path.join(RES_DIR, jid + '.json')
    deadline = time.time() + TIMEOUT
    t_start = time.time()
    warned = False
    while time.time() < deadline:
        if os.path.exists(res_path):
            for _ in range(30):
                try:
                    with open(res_path, 'r', encoding='utf-8') as f:
                        res = json.load(f)
                    break
                except Exception:
                    res = None
                    time.sleep(0.1)
            try:
                os.remove(res_path)
            except Exception:
                pass
            if res is None:
                raise RuntimeError('读取识别结果失败')
            res['file'] = filename
            res['jobId'] = jid
            res['total'] = round(time.time() - t_start, 3)
            HISTORY.insert(0, {'time': time.strftime('%H:%M:%S'), 'file': filename,
                               'text': res.get('text', ''), 'ok': bool(res.get('ok')),
                               'thumbs': res.get('plateImagePng', '')})
            del HISTORY[HIST_MAX:]
            return res
        state, _, _, _ = worker_state()
        if state == 'off':
            if not warned:
                print('[server] worker 未就绪或掉线, 正在拉起...')
                warned = True
            ensure_worker()
        time.sleep(0.1)
    raise TimeoutError('识别超时(%.0f 秒)。首次识别需要启动 MATLAB, 请重试' % TIMEOUT)

# ============================ HTTP 服务 ============================
LOGIN_HTML = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>车牌识别 · 需要访问口令</title><style>
body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
background:#16181d;color:#e6e8ee;font:14px/1.6 -apple-system,"Microsoft YaHei",sans-serif}
.box{background:#1e2129;border:1px solid #2c303a;border-radius:14px;padding:28px;width:min(340px,86vw)}
h1{font-size:16px;margin:0 0 6px}p{margin:0 0 16px;color:#8b93a7;font-size:13px}
input{width:100%;box-sizing:border-box;padding:10px 12px;border-radius:8px;border:1px solid #333846;
background:#14161b;color:#e6e8ee;font-size:14px}
button{margin-top:12px;width:100%;padding:10px;border:0;border-radius:8px;background:#3b6ef6;
color:#fff;font-size:14px;cursor:pointer}
.err{color:#ffb4b0;font-size:13px;margin-bottom:10px}
</style></head><body><div class="box">
<h1>车牌识别 · 需要访问口令</h1><p>这是一个私人服务，请输入口令后使用。</p>
@ERR@
<form method="get" action="/">
<input name="token" type="password" placeholder="访问口令" autofocus><button>进入</button>
</form></div></body></html>'''


class Handler(BaseHTTPRequestHandler):
    server_version = 'lpr-portable/1.0'

    def log_message(self, fmt, *args):
        pass

    def auth_ok(self, query=''):
        '''没设口令就全部放行; 设了口令要 cookie 或 ?token= 匹配'''
        if not TOKEN:
            return True
        if ('lpr_token=' + TOKEN) in (self.headers.get('Cookie') or ''):
            return True
        return urllib.parse.parse_qs(query).get('token', [''])[0] == TOKEN

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, ctype, extra=None):
        with open(path, 'rb') as f:
            body = f.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_redirect(self, to, extra=None):
        self.send_response(302)
        self.send_header('Location', to)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def send_login(self, err):
        body = LOGIN_HTML.replace('@ERR@', '<div class="err">%s</div>' % err if err else '')
        body = body.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------- GET ----------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, parsed.query
        try:
            if path in ('/', '/index.html'):
                if not self.auth_ok(query):
                    bad = urllib.parse.parse_qs(query).get('token')
                    return self.send_login('口令不正确，请重试' if bad else '')
                if urllib.parse.parse_qs(query).get('token'):
                    # 口令对了就把口令存进 cookie 并跳转, 免得一直挂在地址栏
                    return self.send_redirect('/', {'Set-Cookie':
                            'lpr_token=%s; Path=/; Max-Age=604800' % TOKEN})
                return self.send_file(PAGE_HTML, 'text/html; charset=utf-8')

            if not self.auth_ok(query):
                return self.send_json({'error': '需要访问口令'}, 401)

            if path == '/static/demo.jpg':
                return self.send_file(DEMO_JPG, 'image/jpeg')
            if path == '/api/status':
                state, job, last, age = worker_state()
                return self.send_json({'worker': state, 'job': job, 'lastText': last,
                                       'age': round(age, 1), 'matlab': MATLAB,
                                       'launches': _launch_count, 'host': HOST,
                                       'port': PORT, 'token': bool(TOKEN),
                                       'core': CORE_DIR})
            if path == '/api/history':
                return self.send_json({'items': HISTORY})
            if path == '/api/shutdown':
                self.send_json({'bye': True})
                begin_shutdown(self.server)
                return
            if path == '/favicon.ico':
                self.send_response(204)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            return self.send_json({'error': 'not found'}, 404)
        except Exception as e:
            return self.send_json({'error': str(e)}, 500)

    # ---------- POST ----------
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not self.auth_ok():
            return self.send_json({'ok': False, 'error': '需要访问口令'}, 401)
        if path == '/api/stop':
            stop_worker()
            return self.send_json({'ok': True})
        if path != '/api/recognize':
            return self.send_json({'error': 'not found'}, 404)

        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return self.send_json({'ok': False, 'error': '没有收到图片数据'}, 400)
        if n > MAX_BYTES:
            return self.send_json({'ok': False, 'error': '图片太大(上限 20MB)'}, 413)
        data = b''
        remaining = n
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 1 << 20))
            if not chunk:
                break
            data += chunk
            remaining -= len(chunk)
        if not data:
            return self.send_json({'ok': False, 'error': '读取上传数据失败'}, 400)

        filename = self.headers.get('X-Filename', 'upload.jpg')
        try:
            return self.send_json(recognize(data, filename))
        except Exception as e:
            return self.send_json({'ok': False, 'error': str(e)}, 500)

# ============================ 公网隧道 (可选) ============================
CF_EXE  = 'cloudflared-windows-amd64.exe' if os.name == 'nt' else 'cloudflared-linux-amd64'
CF_URL  = 'https://github.com/cloudflare/cloudflared/releases/latest/download/' + CF_EXE
CF_NAME = 'cloudflared.exe' if os.name == 'nt' else 'cloudflared'
CF_MIN  = 20 * 1024 * 1024      # 真正的 cloudflared 有 40MB+; 比这小的一律当成没下完的残file删掉


def _ok_cf(path):
    '''文件存在而且大小像个真 cloudflared(半截文件运行会报 WinError 193)'''
    try:
        return os.path.isfile(path) and os.path.getsize(path) >= CF_MIN
    except OSError:
        return False


def find_cloudflared():
    env = os.environ.get('LPR_CD', '').strip()      # 也可以自己指定路径
    if _ok_cf(env):
        return env
    p = shutil.which('cloudflared')
    if p:
        return p
    for c in (os.path.join(BASE, CF_NAME), os.path.join(ROOT, CF_NAME)):
        if _ok_cf(c):
            return c
        if os.path.isfile(c):                       # 上次下载被中断留下的残file, 删掉重下
            print('[tunnel] 发现不完整的 cloudflared (%.1f MB), 已删除, 稍后重新下载'
                  % (os.path.getsize(c) / 1e6))
            try:
                os.remove(c)
            except OSError:
                pass
    return None


def download_cloudflared():
    '''下载 cloudflared(约 40MB)。网络太慢或下不动时, 给出手动放置的办法'''
    dst = os.path.join(ROOT, CF_NAME)
    print('[tunnel] 没找到 cloudflared, 开始下载(约 40MB, 只下一次)...')
    print('[tunnel] 也可以自己下载好放到 lpr_server.py 同目录, 或设置 LPR_CD=<路径>')
    got = 0
    try:
        import urllib.request
        req = urllib.request.Request(CF_URL, headers={'User-Agent': 'lpr-portable'})
        with urllib.request.urlopen(req, timeout=30) as r, open(dst, 'wb') as f:
            total = int(r.headers.get('Content-Length') or 0)
            mark = 0
            while True:                     # socket 超时 30 秒, 卡住会抛异常而不是一直等
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if got - mark >= 5 * 1024 * 1024:
                    mark = got
                    print('[tunnel] 已下载 %.1f MB%s'
                          % (got / 1e6, (' / %.1f MB' % (total / 1e6)) if total else ''))
        if got < 5 * 1024 * 1024:
            raise IOError('下载到的文件太小(%d 字节), 可能被网络拦截' % got)
        if os.name != 'nt':
            os.chmod(dst, 0o755)
        print('[tunnel] 下载完成: %s' % dst)
        return dst
    except Exception as e:
        print('[tunnel] 下载失败: %s' % e)
        print('[tunnel] 公网模式先跳过(局域网模式照常可用)。想用公网模式, 手动下载 cloudflared:')
        print('         下载地址: %s' % CF_URL)
        print('         放到这里: %s' % dst)
        print('         或者执行: winget install --id Cloudflare.cloudflared')
        try:
            if os.path.exists(dst):
                os.remove(dst)              # 删掉半截文件, 免得下次被当成完整的
        except Exception:
            pass
        return None


def start_public_tunnel():
    '''用 cloudflared 快速隧道拿一个临时公网地址(https://xxx.trycloudflare.com)'''
    global _tunnel
    exe = find_cloudflared() or download_cloudflared()
    if not exe:
        return None
    try:
        _tunnel = subprocess.Popen(
            [exe, 'tunnel', '--url', 'http://127.0.0.1:%d' % PORT, '--no-autoupdate'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception as e:
        print('[tunnel] 启动失败: %s' % e)
        if os.path.join(BASE, CF_NAME) in exe or os.path.join(ROOT, CF_NAME) in exe:
            print('[tunnel] 本地 cloudflared 可能是坏的/没下完, 已删除, 请重试或手动下载')
            try:
                os.remove(exe)
            except OSError:
                pass
        return None

    box = {'url': None}
    pat = re.compile(rb'https://[a-zA-Z0-9-]+\.trycloudflare\.com')

    def reader():
        for line in iter(_tunnel.stdout.readline, b''):
            if box['url'] is None:
                m = pat.search(line)
                if m:
                    box['url'] = m.group(0).decode()
    threading.Thread(target=reader, daemon=True).start()

    deadline = time.time() + 45
    while time.time() < deadline and box['url'] is None and _tunnel.poll() is None:
        time.sleep(0.3)
    return box['url']


# ============================ 启动 ============================
def main():
    print('=' * 64)
    print(' 单文件版车牌识别网站')
    print('   本机地址 : http://127.0.0.1:%d' % PORT)
    if HOST not in ('127.0.0.1', 'localhost'):
        for ip in local_ips():
            print('   局域网   : http://%s:%d' % (ip, PORT))
        print('   ^ 手机/其他电脑连同一个 WiFi, 浏览器里输入上面这个地址')
    else:
        print('   局域网   : 已关闭(只允许本机, 想开放设 LPR_HOST=0.0.0.0)')
    if TOKEN:
        print('   访问口令 : %s   (打开页面后输入这个口令)' % TOKEN)
    print('   MATLAB   : %s' % (MATLAB or '未找到! 请设置环境变量 LPR_MATLAB'))
    print('   运行时   : %s' % ROOT)
    print('   停止服务 : 在本窗口按 Ctrl+C')
    print('=' * 64)
    if not MATLAB:
        print('[警告] 找不到 matlab 可执行文件, 识别会失败。')

    try:
        httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        print('[错误] 端口 %d 起不来: %s' % (PORT, e))
        print('       可能是上一个服务还没关掉, 换个端口: set LPR_PORT=8766')
        return 1

    if PUBLIC:
        url = start_public_tunnel()
        if url:
            print('   公网地址 : %s%s' % (url, ('?token=' + TOKEN) if TOKEN else ''))
            print('   ^ 这个地址任何网络(含手机 4G)都能打开; 关掉本窗口即失效')
        else:
            print('[tunnel] 公网隧道没起来, 继续用局域网地址访问')

    print('[server] 正在预热 MATLAB 识别引擎(约 10 秒, 之后每张 0.2~0.5 秒)...')
    try:
        ensure_worker()
    except Exception as e:
        print('[server] 预热失败: %s' % e)

    if os.environ.get('LPR_NO_BROWSER') != '1':
        try:
            threading.Timer(1.0, lambda: webbrowser.open('http://127.0.0.1:%d' % PORT)).start()
        except Exception:
            pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\n[server] 收到 Ctrl+C, 正在退出...')
    finally:
        _shutting_down = True
        stop_worker()
        if _tunnel is not None and _tunnel.poll() is None:
            try:
                _tunnel.terminate()
            except Exception:
                pass
        httpd.server_close()
        print('[server] 已停止')
    return 0


if __name__ == '__main__':
    sys.exit(main())
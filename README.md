# astrbot_plugin_ticketmaker2

> 本地 **PIL 重绘**版火车票报销凭证生成插件（AstrBot）。
> 完全离线出图，**不依赖任何第三方网站和浏览器**。

与 1 代 `astrbot_plugin_ticketmaker`（Playwright 打开网页截图）相比，本插件用 Python +
Pillow 把整个票面重画了一遍，功能对齐、出图逐像素一致，但不再需要目标网站可访问、也不吃内存。

## ✨ 特点

- **纯本地渲染**：无网络请求、无浏览器、无 Playwright，速度快、稳定。
- **出图与原版一致**：布局 / 字体 / 字号 / 字距 / 虚线框 / 箭头 / 圆点 / 二维码 / 蓝红两版底图逐条对齐，
  与网页 canvas 逐像素对比平均色差约 3/255（>99% 像素几乎一致）。
- **原生分辨率输出**：809×509（蓝）/ 800×535（红），比 1 代的 CSS 拉伸截图更清晰。
- **二维码与网页完全一致**：复刻了 node-qrcode 的最优分段与掩码评分（60 组用例 60/60 一致）。
- **中文/英文排印对齐浏览器**：中文回退 Microsoft YaHei，字距用 HarfBuzz 计算。
- OCR 识别（百度 OCR + 可选 DeepSeek 增强）为可选功能，未配置也不影响手动生成。

## 📦 安装

AstrBot 插件目录（`data/plugins/`）下克隆即可：

```bash
cd <你的 AstrBot>/data/plugins
git clone https://github.com/<你的用户名>/astrbot_plugin_ticketmaker2.git
```

或在 AstrBot 插件市场/管理面板里通过仓库地址安装。依赖会在插件加载时按 `requirements.txt` 自动安装；
也可手动：`pip install -r requirements.txt`。

依赖：`Pillow`、`uharfbuzz`、`requests`、`openai`（后两者仅 OCR/增强需要）。

## 🚀 使用

```
/ticket help
/ticket <发站> <到站> <车次> <日期> <时间> <车厢号> <座位号> <座别> <票价> [姓名] [身份证] [检票信息] [颜色]
/ticket ocr [颜色]      # 同一条消息附上车票截图，自动识别后生成
```

颜色：`蓝`/`blue`/`蓝色`/`蓝票`、`红`/`red`/`红色`/`红票`，默认蓝色。
省略姓名/身份证时用默认值（姓名“旅客”，身份证 `101111200001010001`）。

示例：

```
/ticket 深圳东 九江 K1020 2024-01-25 18:55 11 104 二等座 163.5 张三 110101199001011234 检票:候车室5 红
```

## ⚙️ 配置

首次运行会在插件目录生成 `config.json`（见 `config.example.json`）。OCR 与大模型增强都可选：

```json
{
  "baidu_ocr_api_key": "",
  "baidu_ocr_secret_key": "",
  "deepseek_api_key": "",
  "deepseek_base_url": "https://api.deepseek.com/v1"
}
```

- **改完直接重发命令即可生效**，无需重启插件（配置是惰性读取的）。
- 报错会区分「未配置密钥」与「鉴权失败（附百度返回的 error）」。
- 也可用环境变量覆盖：`BAIDU_OCR_API_KEY` / `BAIDU_OCR_SECRET_KEY` / `DEEPSEEK_API_KEY`。

`main.py` 顶部可调：`MATCH_ORIGINAL_PLUGIN_SIZE`（是否模拟 1 代截图尺寸）、`DEFAULT_*`（默认值）。

## 📁 目录结构

```
main.py           插件主体（命令、OCR、参数解析）
tm2_renderer.py   PIL 绘票核心（1:1 移植网页 canvas 绘图逻辑）
tm2_qrnode.py     复刻 node-qrcode 的最优分段 + 掩码评分
tm2_qrgen.py      二维码位图生成（scale=4 + 平滑缩放，透明亮块）
_vendor/segno/    内置纯 Python 二维码库（BSD-3，提供 ECC/排布，免安装）
assets/           蓝票、红票底图（含 alpha）
stations.json     车站 → 拼音 数据表
config.example.json / metadata.yaml / requirements.txt
```

## 🔍 已知的极细微差异

与网页 canvas 逐像素对比，平均色差约 **3/255**，残余差异来自浏览器（Skia）与 Pillow（FreeType）
在字形抗锯齿、二维码缩放插值上的实现不同，肉眼不可分辨（文字位置、字号、间距、二维码图案均一致）。

## 🙏 来源与致谢

- 绘图布局、蓝/红底图、车站拼音表参考/取自原插件所依赖的网页生成器（`fog.vicc.wang`）。
  **若你是相关资源的权利人且不希望被使用，请提 issue，我会移除。**
- 二维码编码内置 [segno](https://github.com/heuer/segno)（BSD-3-Clause），见 `_vendor/segno/LICENSE`。
- 字距/字形排布使用 [uharfbuzz](https://github.com/harfbuzz/uharfbuzz)。

## ⚠️ 免责声明

本项目仅供**学习、纪念与娱乐**用途。请勿用于任何伪造票据、虚构报销或其它违法用途，
由此产生的一切后果由使用者自行承担。

## 📄 许可

[MIT](LICENSE)

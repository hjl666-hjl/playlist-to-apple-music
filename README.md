# playlist-to-apple-music

免费、开源、无 500 首上限的本地歌单迁移工具：支持将 **QQ 音乐** 公开歌单或任意标准 **CSV 歌单**（网易云音乐、Spotify、酷狗等导出的曲目表）精准匹配并批量导入 **Apple Music**（完美支持中国大陆区 `cn` 及其他区服），支持一键标为 **「喜爱歌曲」** 或写入自定义播放列表。

---

## 核心特性

- **零第三方收费与曲目数限制**：不走 TuneMyMusic / Soundiiz 付费限额，不依赖 Apple Developer `.p8` 开发者密钥。
- **直接对接国区 Apple Music 曲库**：绕开旧版 iTunes Search API 在国区对华语歌曲返回空结果的问题，直接解析 `music.apple.com` 曲库结果。
- **高准确度智能匹配**：
  - 自动解码 HTML 实体、简繁转换、去除冗余别名；
  - 跨区服验证中英文艺人译名（如 `Avril Lavigne` ↔ `艾薇儿`）；
  - 严格拦截同名不同歌手、伴奏、翻唱、DJ 版、纯音乐等误匹配。
- **可视化核对报告 (`match_report.html`)**：离线生成带搜索和状态筛选（`已匹配 / 待确认 / 未匹配`）的 HTML 报告，可直接点击试听核对。
- **自动化浏览器导入 + 断点续传**：通过 Playwright 拉起本机 Edge/Chrome，仅需手动登录一次 Apple ID，每完成一首即时落盘进度，随时中断可从断点继续。

---

## 环境准备

需要 **Python 3.10+** 以及本机已安装的 **Microsoft Edge** 或 **Google Chrome**。

```bash
git clone https://github.com/hjl666-hjl/playlist-to-apple-music.git
cd playlist-to-apple-music
pip install -r requirements.txt
```

> 如需使用 Playwright 自带 Chromium 内核（可选）：`playwright install chromium`

---

## 使用流程（三步完成迁移）

### 第 1 步：准备歌单数据（二选一）

#### 方式 A：从 QQ 音乐公开歌单导出

支持直接传入歌单 ID、完整链接、短链或直接粘贴整段分享文案：

```bash
python export_qq_playlist.py "https://y.qq.com/n/ryqq_v2/playlist/<playlist_id>"
```

产物默认输出至 `output/<disstid>_<歌单名>/`，包含 `tracks_all.csv`、`tracks_all.txt`、`raw.json` 及每 500 首自动分批文件。

#### 方式 B：从其他音乐平台（网易云 / Spotify / 酷狗等）导出 CSV

只要整理成标准 UTF-8（无 BOM）CSV，包含表头 `Track name,Artist name,Album` 即可直接进入第 2 步（示例格式见 [example_tracks.csv](example_tracks.csv)）：

```csv
Track name,Artist name,Album
晴天,周杰伦,叶惠美
十年,陈奕迅,黑白灰
Viva La Vida,Coldplay,Viva La Vida
```

可通过 [GoMusic](https://github.com/Bistutu/GoMusic)、[Playlist Out](https://github.com/LengxiQwQ/playlistout) 或 Spotify 的 Exportify 导出歌单后再转换/整理为上述列名。

---

### 第 2 步：匹配 Apple Music 曲库

```bash
python match_apple_cn.py <path/to/tracks_all.csv> --storefront cn
```

常用参数：
- `--storefront cn`：目标区服代码（默认 `cn`，也可指定 `us`、`hk`、`tw`、`jp` 等）
- `--concurrency 5`：并发请求数（默认 `5`）
- `--limit 10`：仅匹配前 N 首（调试用）
- `--no-resume`：忽略已有 `matched_cn.json` 重新匹配

完成后会在 CSV 同级目录生成：
- `match_report.html`：可筛选「已匹配 / 待确认 / 未匹配」及搜索核对的可视化报告
- `matched_urls.txt`：所有高置信度匹配的 Apple Music 歌曲链接
- `matched_cn.json`：完整匹配数据与断点记录

---

### 第 3 步：自动写入 Apple Music

#### 命令行模式

```bash
# 默认模式：将匹配曲目标记为「喜爱歌曲」(若省略路径参数则自动选用最新 output/*/matched_cn.json)
python import_to_apple_music.py --mode favorite --limit 3   # 建议先试跑 3 首验证
python import_to_apple_music.py --mode favorite             # 全量跑完剩余曲目

# 可选模式：写入指定自定义歌单（不存在时自动创建）
python import_to_apple_music.py <path/to/matched_cn.json> --mode playlist --playlist-name "我的歌单"
```

#### Windows 一键批处理（可选）

Windows 用户完成第 1、2 步后，也可以直接双击项目根目录的批处理文件启动可见 Edge 窗口：
- `1_试跑前3首_喜爱歌曲.bat`：首次运行弹出 Edge 引导手动登录一次 Apple ID，并试导入前 3 首；
- `2_全量导入_喜爱歌曲.bat`：自动续跑剩余全部曲目。

常用参数：
- `--mode favorite|playlist`：写入方式（默认 `favorite` 标记喜爱歌曲；`playlist` 加入指定歌单）
- `--include-uncertain`：同时导入「待确认」档曲目（建议先在 `match_report.html` 核对后再启用）
- `--delay 0.8`：每首歌之间的等待秒数（默认 `0.8`）
- `--login-timeout 300`：等待手动登录 Apple ID 的最长秒数（默认 `300`）
- `--browser msedge|chrome|chromium`：指定浏览器（默认 `msedge`，不可用时自动回退 `chrome`）

---

## 隐私与安全说明

- 本工具完全在本地运行，不会向任何第三方服务器上传你的歌单或账号信息。
- Apple ID 登录（含双重认证）均由你自己在弹出的本地浏览器窗口中手动完成，脚本不会读取、存储或传输密码。
- 登录后的本地浏览器会话保存在 `.browser_profile/`，个人歌单与迁移进度保存在 `output/`，两者均已在 `.gitignore` 中排除，不会进入版本库。

## License

[MIT](LICENSE)

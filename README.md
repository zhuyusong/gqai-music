# GQAI Music

本仓库同时托管 GQAI Studio Windows 便携版的[公开下载](https://github.com/zhuyusong/gqai-music/releases)。下载完整 ZIP 并解压，双击 `GQAI Studio.exe`；校验文件和离线验证报告随每次发行提供。

Music 源码公开；Roughcut 与 Core 源码仓库保持私有。公开安装包只分发这两个系统的编译模块及必要界面资源，不附带明文 Python 源码、测试或开发文档。编译模块仍可能被逆向。构建机具备同级私有项目和运行依赖时，可运行 `build-studio.ps1 -Aoci <aoci.exe> -Browser <chrome-headless-shell.exe>`，产物保存在本仓库 `dist/releases/`。

GQAI Music 是独立的音频后期系统，不属于粗剪页面，也不负责字幕或视觉特效。它统一处理背景音乐和与画面事件绑定的一次性音效。

它接收粗剪或其他系统交接的视频，管理背景音乐素材和混音参数，输出：

- `music-master.mp4`：保留原画面和人声，加入背景音乐后的成片；
- `gqai-music-manifest.json`：记录输入哈希、音乐与音效哈希、音量、入点、循环、淡入淡出、人声避让和输出哈希；
- `gqai-audio-plan-1.0`：把音乐、环境声和事件音效从画面执行 JSON 中分离，事件音效通过稳定的 `anchor_event_id` 跟随文字动画等画面事件。

## 能力边界

- 独立 FastAPI 服务，默认监听 `127.0.0.1:8877`；
- 独立运行目录，默认是项目下的 `runtime/`；
- 支持 MP3、M4A、WAV、AAC、FLAC、OGG 背景音乐；
- 支持音乐入点、成片进入时间、循环、音量、淡入淡出和人声自动避让；
- 兼容拆分旧执行表中的 `audio.sfx_events`，并把绝对触发帧转换为相对画面事件的偏移；
- 文字动画移动时重新解析音频计划，绑定音效会同步移动；动画删除后悬空音效会被拒绝；
- 使用 FFmpeg 混音，视频轨直接复制，不重新编码画面；
- 不覆盖上游视频，每次渲染生成新版本。

第一版只接收用户本地拥有或已获授权的音乐，不内置来源不明的曲库，也不从参考视频复制音乐。

## 事件音效自由编排

`python -m gqai_music.audio_plan --catalog` 返回五个本地原创合成短音预设：whoosh、pop、click、chime、impact。程序以确定性PCM合成，不下载素材或复制参考声音；每次记录来源、采样率、时长与 SHA-256。

`python -m gqai_music.audio_plan --plan <音效计划> --visual <执行表> --source <视觉成片> --output <新母版>` 将事件相对音效计划解析为实际落点，输出音效母版、materialized JSON、素材哈希清单和 `sfx-only.wav`。SFX-only模式不需要背景音乐；保留视频位流与人声增益，以人声侧链压低音效，限幅器不自动抬高音量。解码证据逐事件记录真实音效能量、输出变化、音频峰值和视频位流一致性，不冒称主观听感验证。输出必须为新文件，源视频不覆盖。

## 启动

```powershell
.\install-local.ps1
.\start-gqai-music.cmd
```

浏览器打开 `http://127.0.0.1:8877`。也可以通过粗剪系统的“进入音乐系统”按钮，把当前成片交接过来。

## 配置

- `GQAI_MUSIC_RUNTIME`：任务与输出目录；
- `GQAI_MUSIC_HOST`：监听地址，默认 `127.0.0.1`；
- `GQAI_MUSIC_PORT`：监听端口，默认 `8877`；
- `GQAI_FFMPEG` / `GQAI_FFPROBE`：媒体工具命令或绝对路径。

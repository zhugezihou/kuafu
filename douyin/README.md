# 🎬 夸父抖音自动发布工作流

> 基于 Playwright 浏览器自动化，实现抖音视频的自动登录、上传、发布。

## 快速开始

### 1. 安装依赖

```bash
pip install playwright pyyaml
playwright install chromium
```

### 2. 首次登录（扫码）

```bash
python douyin/cli.py login
```

浏览器会自动打开抖音创作者平台登录页，用手机抖音扫码登录。
登录成功后 Cookie 会自动保存，下次无需重复登录。

### 3. 准备视频素材

将你要发布的视频文件放入 `douyin/templates/` 目录：
- 支持格式：`.mp4` `.mov` `.avi` `.mkv` `.flv` `.wmv`

### 4. 发布视频

**发布一个视频：**
```bash
python douyin/cli.py publish --video douyin/templates/my_video.mp4 --title "我的视频标题" --tags "日常,生活"
```

**发布今日推荐（自动选最旧未发布的）：**
```bash
python douyin/cli.py today
```

**批量发布：**
```bash
python douyin/cli.py batch --dir douyin/templates --title "{name}"
```

### 5. 查看状态

```bash
python douyin/cli.py status
```

## 自动化发布

夸父的 cron 定时任务已配置（每日 10:00 自动发布）：
- 检查 `douyin/templates/` 中是否有未发布的视频
- 如果有，自动发布最旧的一个

## 目录结构

```
douyin/
├── cli.py              # 命令行入口
├── publisher.py         # 核心发布引擎（Playwright自动化）
├── cookie_manager.py    # Cookie管理
├── content_manager.py   # 视频内容管理
├── config.yaml          # 配置
├── skill.json           # 夸父技能描述
├── templates/           # 视频素材目录
│   └── demo.mp4         # ← 放你的视频文件
├── cookies.json         # 登录态（自动生成）
└── publish_history.json # 发布记录（自动生成）
```

## 注意事项

1. **Cookie 有效期**：约 7 天，过期后需重新 `python douyin/cli.py login`
2. **首次必须手动登录**：因为抖音有反爬机制
3. **发布频率**：建议每天不超过 3-5 条，避免限流
4. **视频质量**：建议 1080p 以上，竖屏 9:16 最佳

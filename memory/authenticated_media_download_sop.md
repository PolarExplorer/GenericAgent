# Authenticated Media Download SOP

## Struct Header
- Reader: GA 总控
- When to read: 需要借登录态下载需要权限的媒体文件时
- Trigger: 下载付费/会员/私有视频、音频、课程录像、会议录像等
- Inputs: 目标 URL/ID、登录态 cookie/tab、输出目录
- Outputs: 合流后的媒体文件（mp4/mp3/mkv）
- Tools: authenticated_fetch_skill(K1), yt-dlp, ffmpeg, aria2c(可选)
- Side effects: 网络下载、磁盘写入（可能大文件）
- Risk: R1
- Failure path: cookie过期→引导重新登录；流解析失败→降级画质；下载中断→断点续传；合流失败→检查 ffmpeg 版本
- Review: None

## 核心流程: 解析 → 下载 → 合流 → 验证

### Phase 1: 流解析
1. 用 K1 拉取播放页 API 获取流地址（DASH/HLS manifest）
2. 选择最佳画质（优先 1080P，可配置）
3. 分离视频流 URL + 音频流 URL

### Phase 2: 并发下载
1. 优先用 yt-dlp（已集成多平台解析）
2. yt-dlp 不支持时，用 Range 分块 + 多线程下载
3. cookie 通过 `--cookies-from-browser chrome` 或手动传入

### Phase 3: 合流
```bash
ffmpeg -i video.m4s -i audio.m4s -c copy output.mp4
```
- 视频+音频分开下载后用 ffmpeg 无损合流
- HLS 直接用 ffmpeg 拼接 ts 分片

### Phase 4: 验证
- 检查文件大小、时长、视频/音频轨是否完整
- 报告结果给用户

## 平台适配指南

| 平台 | 工具 | cookie 来源 | 备注 |
|------|------|------------|------|
| B站 | bilibili_dl.py / yt-dlp | SESSDATA | 1080P需大会员 |
| YouTube | yt-dlp | 浏览器自动 | 私有视频需登录 |
| 网课平台 | 手动拉流 | 手动提取 | 往往有 DRM |
| 会议录像 | 手动拉流 | 手动提取 | Zoom/腾讯会议等 |

## 关键约束

1. **优先 yt-dlp**: 能用 yt-dlp 的场景禁止手写解析（已支持 1000+ 站点）
2. **cookie 安全**: cookie 仅传入下载工具，禁止明文存储或日志输出
3. **磁盘空间**: 下载前检查目标盘空间，大文件预警
4. **超时控制**: 单视频默认 600s 超时，长视频可调整
5. **DRM 边界**: 遇到 DRM 保护内容时告知用户无法下载，不尝试破解

## 常见坑

- yt-dlp 版本过旧导致解析失败 → `pip install -U yt-dlp`
- B站 SESSDATA 过期快（~30天）→ 失败时提示用户更新
- ffmpeg 不在 PATH → 用绝对路径或先检查
- 部分平台限制并发连接数 → 并发数限制在 3-5

# Chaoxing Agent CLI

供 Agent 使用的学习通作业抓取工具。课程发现 → 作业列表 → 题目与作答 → JSON / Markdown。支持 Python 3.10+、Playwright Chromium，提供稳定 JSON 协议和批量断点续传。

基于 [xxt-homework-fetcher](https://github.com/PYFGDUF/xxt-homework-fetcher)（MIT）开发，沿用并改进课程卡片、作业入口和题目容器的解析经验。CLI 独立安装，专门处理脚本与 Agent 调用。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m playwright install chromium
xxt doctor
xxt schema
```

Windows 使用 `.venv\Scripts\activate`。Linux CI 可运行 `python -m playwright install --with-deps chromium`。开发验证记录见 [VALIDATION.md](VALIDATION.md)。

## 登录

```bash
xxt auth login
xxt auth status
xxt courses
```

`auth login` 打开独立 Chromium 窗口供用户扫码，默认等待 180 秒。Agent 可通过退出码 3 判断何时需要用户登录。`auth status` 检查本地会话可读性，`courses` 验证在线访问。

也可以从本地 Playwright `storage_state` JSON 或 Cookie JSON 数组导入：

```bash
xxt auth import /path/to/storage-state.json
```

复用原 macOS 客户端登录态：

```bash
xxt auth import "$HOME/Library/Application Support/XxtApp/state.json" \
  --legacy-key "$HOME/Library/Application Support/XxtApp/login.key"
```

会话以 AES-256-GCM 加密，默认存放在 `~/.local/share/xxt-agent`，密钥与会话文件权限为 `0600`。可用 `XXT_STATE_DIR` 或全局参数 `--state-dir` 调整。导入时保留学习通域名 Cookie；浏览器登录保存标准 Playwright 会话。

## Agent 工作流

所有全局参数放在子命令前。常规命令在 stdout 输出一个 JSON 对象；诊断与进度写 stderr。`--help`、`--version` 为文本输出。

```bash
# 返回 items，每个课程有 id、title、teacher、url、ended
xxt courses

# 使用 courses 返回的完整 URL，保留签名参数
xxt homeworks --course-url "$COURSE_URL"

# 使用 homeworks 返回的完整作业 URL
xxt fetch --url "$HOMEWORK_URL" --output out/work.json --markdown out/work.md

# 每份作业保存为 work_<稳定ID>.json/.md，manifest.json 汇总成功与失败
xxt sync --course-url "$COURSE_URL" --output-dir out/course

# 重新抓取已缓存作业
xxt sync --course-url "$COURSE_URL" --output-dir out/course --refresh

# 加载已有 HTML，用于离线调试页面适配
xxt parse saved.html --kind questions --base-url https://mooc1.chaoxing.com/
```

`sync` 为顺序抓取，失败项会记录到清单。重跑时复用完整且状态相同的已完成/已批阅作业，继续抓取未交、失败或状态发生变化的作业。已完成内容后续有修订时使用 `--refresh`。

### JSON 协议

```json
{"ok":true,"schema_version":1,"data":{"items":[],"count":0}}
```

```json
{"ok":false,"schema_version":1,"error":{"code":"AUTH_REQUIRED","message":"请运行 xxt auth login 或 xxt auth import。"}}
```

题目字段：`id`、`index`、`type`、`title`、`options`、`correct_answer`、`student_answer`、`analysis`、`attachments`。页面未展示的内容返回 `null`；图片转为 Markdown URL，附件保留标题与 URL。JSON 与 Markdown 按需保存到本机。

作业按课程、班级、作业及作答标识生成稳定 ID，签名轮换保持 ID 稳定。同名作业保留独立记录，互评入口附加到 `peer_review`。`time_text` 保留网站的时间提示原文。

分页结果含 `pages`、`reported_total`、`complete`；作业列表还含 `stop_reason`。达到 `--max-pages`、总量校验失败或翻页停滞时，以结构化错误返回。完整性错误的 `error.details` 保留已提取数据。单份 `fetch` 指定输出路径时也会保存部分结果供检查。

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 1 | 运行错误 |
| 2 | 参数错误 |
| 3 | 需要登录或人工验证 |
| 4 | 页面适配、分页或完整性错误 |
| 5 | 批量部分失败 |
| 130 | 用户中断 |

`xxt schema` 输出机器可读协议说明。Agent 调用示例见 [AGENT.md](AGENT.md)。

## 页面适配与数据

目前适配个人空间课程卡片、课程“作业”iframe、`work/task` / `work/view`、`.questionLi` / `.singleQuesId` / `.TiMu` 等详情结构。作业入口可通过网站重定向进入详情；需手动开始或人工验证的页面返回错误供 Agent 处理。

抓取流程仅执行页面导航、作业标签切换和下一页操作。标准答案与学生作答独立输出。时间提示、代码换行、图片及附件引用尽量保留。图片与附件下载可由后续 Agent 根据返回 URL 完成；链接有效期受平台登录态和签名影响。离线解析以 HTML 中已经保存的内容为准。

仅将代码、合成测试和文档放入公开仓库。会话、签名 URL、抓取内容及个人信息应保存在本机受控目录。请用于获授权课程内容的学习整理和个人备份。

## 开发

```bash
pip install -e '.[dev]'
python -m playwright install chromium
pytest -q
python -m build
```

测试覆盖同名作业、互评入口、签名轮换、答案区分、图片与代码、会话加密、JSON 错误协议，以及 Chromium 中的分页和验证页面。GitHub Actions 在 Linux / Python 3.10、3.12、3.13 运行。

## License

MIT。原客户端版权声明保留于 [LICENSE](LICENSE)。

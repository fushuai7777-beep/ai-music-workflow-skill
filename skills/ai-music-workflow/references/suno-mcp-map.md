# Suno MCP 工具对照

核对日期：2026-11-10。本表来自本轮宿主已加载的 Suno 插件工具定义，共 32 项；本轮只读取定义，没有调用这些工具验证账号权限或实际产出。工具名为去掉宿主前缀的逻辑名；执行时必须重新读取当前 schema，不能把此快照当作固定接口。

入口：[官方 MCP 介绍](https://suno.com/mcp) | [功能速查](suno-feature-guide.md) | [提交与恢复](suno-workflow.md)。

## 连接方法

支持自定义 MCP 的宿主中选择添加服务器，名称 Suno，URL `https://api.suno.com/mcp`，按官方流程完成 OAuth。无需给本 Skill 配 API Key，也无需为了 MCP 安装 Chrome 控制扩展；菜单文字可能随宿主版本变化。授权页只由用户处理，令牌留在宿主管理范围，不复制到项目或素材库。

验收分三层：工具已加载；最小 `account_me({})` 成功；具体所需功能实际成功。账号关联成功不代表后二者。OAuth 错误或账号不符合资格时保存脱敏原因，不反复扩大授权猜测解决。

## 32 项能力目录

“读”不意味着可以全库采集；始终以用户指定对象和范围为限。“写/消费”不意味着每次必收费，执行前读实际返回及批准范围。

| 逻辑工具 | 用途与关键输入 | 边界 |
| --- | --- | --- |
| `account_me` | 当前登录账号最小核对 | 读；不把账号资料写入公共手册 |
| `account_usage` | 当前套餐、生成/下载额度和重置 | 读；余额不硬编码为用户默认值 |
| `account_upgrade` | 获取升级/补点入口 | 仅用户要求；不替用户付款 |
| `audio_create` | 从描述或 Custom 歌词/风格生成 | 写/消费；模式不能混用 |
| `audio_covers_create` | `id` 源作品，改编风格和歌词 | 写/消费；保旋律非保证 |
| `audio_extensions_create` | `id` 与 `continue_at` 续写 | 写/消费；只产出续段 |
| `audio_full_song` | 选定末段 `id` 拼完整续写链 | 写；当前定义说不收生成点数 |
| `audio_mashups_create` | `id` + `additional_audio_id` 融合两首 | 写/消费；不是原样混音 |
| `audio_upload_create` | 获准文件或新上传会话 | 外发；直传与会话模式分开 |
| `audio_upload_complete` | 完成用户上传会话的处理 | 写；不在文件未传完时盲调 |
| `audio_retrieve` | 按 `id` 取作品状态/资料 | 读；可播放不等于最终完成 |
| `audio_wait` | `id` 或最多 4 个 `ids`，等 playable/complete | 读；不是下载转码等待器 |
| `audio_analysis` | `id` 的音频标签、节拍、段落 | 模型分析；可能部分完成，见下文 |
| `audio_lyrics_alignment` | `id` 的逐词时间对齐 | 读/按来源分析；时间不是漏唱证明 |
| `audio_edit` | `id`、`title` / `lyrics` | 元数据写入；不当作重新演唱 |
| `audio_download` | `id`、`format`，必要时 `confirm` | 下载及可能消费；不自动确认 |
| `audio_like` | 点赞指定歌 | 偏好写入；试听不等于授权点赞 |
| `audio_unlike` | 取消指定歌点赞 | 偏好写入；不能用来清理差评记录 |
| `library_list` | 按条件及工作区列完成的自有歌曲 | 读；翻页、权限和过滤要核对 |
| `library_search` | 按题名/风格等定向找歌 | 读；同名不是同一首 |
| `library_liked` | 已点赞歌曲列表 | 读；不把点赞推断为长期画像采纳 |
| `workspaces_list` | 查询自己工作区 | 读；核对精确名称、ID 和分页 |
| `workspaces_create` | `name`，可选说明 | 创建；超时先查，不重复创建 |
| `workspaces_update_clips` | 工作区 `id`、`add` / `remove` | 移动归属；检查 skipped 并回读 |
| `playlists_list` | 查自己的歌单 | 读；不是工作区列表 |
| `playlists_list_clips` | 读取指定歌单歌曲 | 读；需要时翻页 |
| `playlists_create` | 新建名称/说明/可见性 | 写；默认私人，公开另行批准 |
| `playlists_add_clips` | 加歌并检查失败项 | 写；不改变工作区归属 |
| `playlists_remove_clips` | 从歌单移出歌曲 | 写；不是删除歌曲，核实范围 |
| `voices_list` | 查可用声音与状态 | 读；使用真实 ready Voice ID |
| `voices_start` | 返回创建本人声音的官方步骤 | 用户完成验证；不先索取声音文件 |
| `speech_create` | `script` 或 `prompt` 二选一 | 写/消费；不是歌曲演唱 |

## 高频参数与陷阱

### 生成、Cover、Extend

- Custom：`title`、`style`、`lyrics`、`instrumental` 来自已批准材料；`lyrics_id` 是本地标识，不发平台。Simple 用 `description`，不要和 Custom 输入混搭。
- 新项目首次生成前核实工作区；支持 `workspace_id` 的创建请求传入本项目 ID，之后回读全部返回歌曲。按本次结果数登记，不固定假设两首。
- Cover：`id` 必须是已完成源；当前 schema 没有 `title`，继承源标题。省略 `lyrics` 不保证沿用原唱词，工具说明无 Voice 等情况会成为器乐；人声目标显式提供已批准歌词。
- Extend：`continue_at` 是相对直接源片段的秒数，不是续写链全局位置。后续 `audio_full_song(id=选定末段ID)` 才拼完整链；当前链最多 20 个片段。多候选先选分支，不全拼。
- Mashup 当前需要两个源；不能假定所有生成工具都支持同一组字段或 Voice。可见性、Remix 权利与源完成状态分别检查。
- 某些创建操作的 `duration` 当前为 15–360 秒目标范围，具体以该工具 schema 为准；网页“最长 8 分钟”不是所有 API 字段上限。时长指导不等于精确长度承诺。
- `audio_influence`、`style_influence`、`weirdness` 当前数值为 0–1；省略使用平台默认，0 不是默认。`vocal_gender` 是 male/female；Voice 需实际可用 ID，不从歌名猜。
- `max_mode` 不能由“质量高一点”推导开启。高级参数保存脱敏请求说明，但不把它们塞进本地五字段 package 使严格校验失效；现有脚本不负责校验所有高级字段。
- 当前幂等说明：键 1–255 字节、账号作用域、至少保留 24 小时；同键不同请求可能 409。它仍不是自动重发许可。本地 uncertain 和一次批准规则不变。

### 上传与等待

- `audio_upload_create(file=获准绝对路径, ...)` 在支持宿主附加文件时直接处理；以实际返回确认是否已有 clip。无 `file` 路线建立上传会话，交用户完成官方上传后才 `audio_upload_complete(id=会话ID, ...)`。不要混合两条流程。
- 上传地址可能限时，属于敏感签名链接；不放入日志或素材库。只上传获准的具体文件，不能为分析整盘扫描。上传成功不授权后续 Cover 或入库。
- `audio_wait` 单次可能约 50 秒；返回 pending 后只查询已有 ID，不重建。库列表通常只列已完成作品，空列表不证明没有生成请求。
- 下载 `processing` 用 `audio_download` 再查，不用 `audio_wait`；频率、终态和速率限制按当前定义。失败原因不明不切网页补扣点。

### 分析与歌词定位

- `audio_analysis(id=...)` 描述来自音频模型；描述、节拍、结构分别运行，可能先返回部分。当前说明首次请求不收费，重复不重新开跑；自己的 Suno 库需相应读取授权，最多 5 首并发。
- `processing` 可继续有限轮询；`complete` 表示至少一部分成功，不是全部；`unavailable` 表示无可用结果，停止。段落标签和调性可能不完整，不推断空值。
- `audio_lyrics_alignment(id=...)` 逐词 `success: false` 代表估算；自己的 Suno 库读取已有对齐，未建立结果时可能 unavailable，新歌可能暂 processing。不要用它保证唱词正确。
- 报告明确“工具模型分析”与“实际试听”各自证据；后者没做就写未试听。不得仅凭提示词声称听到了某乐器。

### 下载与组织

- `audio_download(id=..., format="mp3"|"wav")` 是自有歌曲、用户 OAuth 授权路线；API Key 拒绝不是应当偷偷换私有接口的理由。
- 首次不传 `confirm`；`requires_credit` 先问本次消费，得到明确批准才确认。按官方 2026-09-09 说明，同曲跨格式/重复下载共计一次歌曲额度；第二格式异常再要求扣点时暂停核对。[计额依据](https://help.suno.com/en/articles/13926145)
- 402 额度不足停下；403 查权益/授权；429 退避，日限额等待重置。不给用户自动购买或把播放流保存成文件。链接可用不代表文件已落盘。
- `workspaces_update_clips` 加入目标区会离开原区。检查 `skipped` 并用带 `workspace_id` 的库查询核验，注意只列完成的自有歌曲，不能用于断言合作者作品不存在。
- 歌单 add 检查失败；remove 回执可能包含原本不存在的 ID，必要时回读，不能用“返回了 ID”证明原本删除了真实条目。
- Speech 恰选 `script` 或 `prompt`；只在脚本模式使用该模式支持的风格，`backing_music` 默认关闭。严格时长可能截断脚本，不保证一字不漏。

## 当前没有暴露的专用工具

Replace Section / 音频重唱、Remaster、分轨生成、Get MIDI、Add Vocals / Add Instrumentals、Inspire、Sounds、MIDI Create、Studio 时间线/自动化/导出、Custom Models 训练、工作区重命名/删除/邀请：本轮 32 项里未见相应专用工具，按[功能指南](suno-feature-guide.md)走网页或交用户处理。

账户 feature flag、库筛选的 `origin=remaster`、网页有按钮，都不等于当前宿主暴露了相应调用能力。未来出现新工具时重新核验；不要把此列表写成 Suno 永久不支持。

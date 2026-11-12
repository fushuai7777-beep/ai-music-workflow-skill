# Suno 功能速查与操作指南

核对日期：2026-11-10。用途：先按创作目标选功能，再按实际工具和授权执行。本文是官方文档的中文操作摘要，不是官方全文，也不代表所有功能已在本账号实测。

配套：[MCP 工具与参数](suno-mcp-map.md) | [官方来源与版本差异](suno-official-sources.md) | [提交、恢复、归档协议](suno-workflow.md)。只查询功能或准备提示词，不必连接平台、询问保存目录或创建歌曲项目；真的写入材料时再确认位置。

## 一、先说目标，再选功能

“MCP”表示本次会话已加载相应工具定义，不代表用户账号已获该功能权限。网页入口可能改名；必须观察当前界面，不能照旧菜单盲点。

| 你想做什么 | 选什么功能 | 当前执行入口 | 必要输入与验收重点 |
| --- | --- | --- | --- |
| 从主题或歌词创作新歌 | Simple / Custom | MCP `audio_create`；网页 Create | 有定稿词用 Custom；先确认完整段落、人声、风格，再批准一次生成。 |
| 保留已有 Demo 的旋律，换编曲 | Upload + Cover | MCP 上传、`audio_covers_create`；网页 Remix/Edit > Cover | 获准上传的音频、源 ID、新编曲和歌词；保旋律是目标，需对照听审。 |
| 只沿用旧提示词重做 | Reuse Prompt | 网页；或读取后准备新的 Custom 请求 | 是重新生成，不保证旧旋律、人声或长度。 |
| 改一句词，并让音频真的重新唱 | Replace Section / Edit Lyrics | 网页 Song Editor | 精确歌曲、时间区间、哪一遍、新词；试听局部和两端衔接。不是 `audio_edit`。 |
| 只改标题或展示歌词 | 元数据编辑 | MCP `audio_edit` | 不据此声称音频已重唱；不能用它代替局部生成。 |
| 续写尾声并拿到整首歌 | Extend + Get Whole Song | MCP `audio_extensions_create` + `audio_full_song`；网页 | 核实续接点、选择续段分支、再拼全曲；续段不是整曲。 |
| 混合两首素材的创意 | Mashup | MCP `audio_mashups_create` | 两个源 ID、权限、目标词曲；生成融合结果，不是无损叠轨。 |
| 尽量保持作品，只改善制作质感 | Remaster | 网页 Remix/Edit | 选模型和变化强度；不是修一个错字或大改曲风的首选。 |
| 给器乐添加演唱 | Add Vocals | 网页 Remix/Edit | 器乐来源、词、声音和编曲；当前 MCP 未见同名专用工具。 |
| 给现有人声加伴奏 | Add Instrumentals | 网页现有入口待现场核对 | 官方定价页列出能力，本轮未核实详细步骤；不能假装已有专用 MCP。 |
| 沿用已准备好的音色 | Voices / Style Personas | MCP `voices_list`、生成请求的 `voice_id`；网页 | 实际可用 Voice ID，不靠性别或名称推断同音色；仍要试听。 |
| 建自己的声音 | Create Voice | MCP `voices_start` 返回官方流程；网页 Voices | 用户本人完成声音与身份验证；不替用户过验证，不克隆他人声音。 |
| 分析库内作品的配器与段落 | Audio Analysis | MCP `audio_analysis` | 明确歌曲 ID；输出模型分析及缺项，不伪称人工试听。 |
| 查歌词对应的演唱时间 | Lyrics Alignment | MCP `audio_lyrics_alignment` | 逐词时间可不准确；对齐成功不等于无漏唱。 |
| 给每个项目分组 | Workspaces | MCP 工作区工具；网页工作区选择器 | 新项目首次生成前建同名工作区，后续复用；核实所有新歌归属。 |
| 编排试听歌单或参考合集 | Playlists / Inspire | MCP 歌单；Inspire 走网页 | 歌单不是工作区；Inspire 汲取合集特征创作新歌，不承诺保原旋律。 |
| 下载 MP3 / WAV | Download | MCP `audio_download`；网页歌曲菜单 | 同一选定 ID 两种格式分别取文件；按歌曲计下载额度，不是按格式。 |
| 分离人声、鼓、贝斯等 | Get Stems | 网页曲库/工作区；Studio | 先确认方案与成本；分离可能有串音，不能承诺录音棚原始分轨。 |
| 精确音量起伏、剪辑、混音 | Studio | 网页 Studio | 轨道/片段范围、自动化、效果；导出回听，不把音量变化等同重新演唱。 |
| 由 MIDI 旋律创作 | MIDI in Create | 桌面网页 Create | 获准的 .mid/.midi；当前 MCP 未暴露该上传生成路线。 |
| 做循环或单次音效 | Sounds | 网页 Create > Sounds | Loop / One-shot、节奏调性与音色目标；不是完整歌曲生成的别名。 |
| 生成口播 | Speech | MCP `speech_create`；网页 Speech | 明确原样脚本或自由生成、声音与时长；硬时长可能截断。 |
| 用自己多首作品训练专属模型 | Custom Models | 网页模型选择器 | 独立授权训练范围和作品权利；私人素材库入库不等于同意训练。 |

## 二、创作输入与声音控制

### 新歌：Create

- 网页 Simple 适合主题描述；Custom 把 Lyrics、Styles、Title 分开。已有完整词稿不交给 Simple 随意改写。官方入口见 [自有歌词](https://help.suno.com/en/articles/2415873)。
- 先确认顺序、重复、桥段和力度变化，再展开所有歌词。Styles 写曲风、人声、核心配器、鼓和低音、空间与动态；不要把整首歌词塞入 Styles。段落标签是制作提示，不是确定性时间轴命令。
- [Vocal Gender](https://help.suno.com/en/articles/10153473) 选男/女声；具体气质仍需 Styles 表达。[Exclude](https://help.suno.com/en/articles/3161921) 用于排除不希望出现的乐器或风格，不保证完全排除。
- [Creative Sliders](https://help.suno.com/en/articles/6141377)：Weirdness 控制探索程度，Style Influence 控制风格约束，带音频时另有 Audio Influence。网页百分比与 MCP 的 0–1 数值不能直接照搬；50% 不等于所有项目都适用。
- [v6 系列](https://help.suno.com/en/articles/13924737) 与具体账户可见模型现场核对，不自动换成更贵模式。网页单次时长上限与 MCP 某操作的 `duration` 范围不是同一个限制。

### 只有提示词修改时

沿用本曲已确认方向，只改用户指定维度。“第二遍副歌前半不弱、后半才弱”必须定位到歌词分界；不误改成第二遍主歌，不让整个副歌一起减弱。先明确减弱的是声压、配器密度还是演唱力度。

可用的局部英文补充示例（制作建议，不是官方硬控制语法）：

> In the second chorus, keep the first half at full intensity through its final line. Reduce the arrangement only when the second half begins, preserving the vocal melody and lyric clarity.

用户只要粘贴稿：交付 Styles、完整歌词标签和必要设置即可；不调用 Suno、不创建生成批次。用户要填网页但先不生成：只按授权填入并回读，不点 Create。已经生成且只要求音量曲线时，可用 Studio 自动化；要改变演唱表情则需局部重生成并试听。

### 声音、合集与训练不是一回事

- [Voices](https://help.suno.com/en/articles/11362369)：实际音色身份依赖可用 Voice，不是“女声”“少年感”等形容词。`voices_start` 应交给用户完成官方验证；本 Skill 不收集验证码或复制登录状态。
- [Style Personas](https://help.suno.com/en/articles/11362433) 入口随版本变化；旧文档写特定模型，不据此强制降级。
- [Inspire](https://help.suno.com/en/articles/6882753)：从自己作品组成的歌单提取方向，适合探索而非保曲改编。
- [Custom Models](https://help.suno.com/en/articles/11362497)：官方当前说明为 Pro/Premier、至少 6 首自有权利歌曲、最多 3 个模型；训练与发布、共享分别确认。限制以后执行时重查。
- 制作人素材库是本地批准的风格资料与经验，不会自动变成平台 Voice、歌单或训练集；只有明确批准的材料才可外发。

## 三、已有歌曲怎样修改

### Cover：保旋律、换制作

依据：[Cover](https://help.suno.com/en/articles/2872257)、[Remix](https://help.suno.com/en/articles/6050497)，结合当前 MCP 定义。

1. 核对源音频及获准上传/改编范围。库内已有准确 ID 时不用重复上传；本地 Demo 才走上传流程，确认处理完成。
2. 写清保留主旋律、哪些段落、歌词和人声；改编方向单独记录。调用 `audio_covers_create(id=源ID, ...)`，使用已批准词稿。当前工具省略歌词可能得到器乐，不假定会自动保留唱词。
3. 新项目先准备同名工作区；生成传 `workspace_id`，本地记录一次授权和完整返回身份。当前 Cover 继承源标题，不臆造 `title` 参数。
4. 对照源 Demo 试听旋律、结构、发音和新编曲；音频完成不证明逐音保留。修改失败也不擅自追加生成。

### Replace Section：改词并重唱

依据：[Replace Section](https://help.suno.com/en/articles/3271873)、[Song Editor](https://help.suno.com/en/articles/6141505)。

1. 先确认准确歌曲、要改哪一遍、时间范围、原句和新句；不知道位置先试听定位，不猜秒数。
2. 网页歌曲菜单进入 Edit / Replace Section，或 Song Editor 的 Edit Lyrics；选区并填写该段。Pro/Premier 权益、页面当前成本现场核对。
3. 得到本次局部生成批准后才 Recreate Section；试听候选，确认后采用并核对完整歌曲版本。不要把仍在候选预览里的片段当已应用。
4. 验收新句确实唱出、头尾自然衔接、非目标段落符合保留要求。`audio_edit` 只按元数据编辑处理，绝不把文本更新当作音频重唱证据。

### Extend：续写，然后拼成整曲

依据：[Extend](https://help.suno.com/en/articles/2409601) 与当前工具定义。

1. 确定源 ID、续接秒点、新尾声歌词或器乐目标；`continue_at` 相对本次直接源片段，不是整个续写链的全局时间。
2. 一次批准后调用 `audio_extensions_create`。返回的是续段；若返回多个候选，先让用户选定一条分支。
3. 请求包含交付整曲时，用选定末段 ID 调用 `audio_full_song`，网页对应 Get Whole Song。当前工具声明拼接不收生成点数，但不能因此自动拼所有分支。
4. 核验新整曲 ID、完成状态、开头保留范围、续接处和结尾，再按需要下载。只拿到末段不得说整曲完成。

### 其他改编选择

- [Reuse Prompt](https://help.suno.com/en/articles/2417409)：复用输入再创作，不是音频编辑，也不锁定音色和旋律。
- [Remaster](https://help.suno.com/en/articles/8105281)：适合相对保守的制作变化；强度越大越可能改变细节。大改曲风先考虑 Cover。
- [Add Vocals](https://help.suno.com/en/articles/6882817)：给器乐加人声，歌词和整体伴奏风格一起说明。不能把 `audio_create` 宣称为相同的专用功能。
- Mashup：当前 MCP 需要恰好两个已完成源，返回重新融合的作品；不是分轨相加或无损合成。

## 四、分析、定位与后期

### 音频分析：先注明证据类型

当前 MCP `audio_analysis(id=歌曲ID)` 可返回模型判断的乐器、人声、曲风、情绪与混音标签，以及节拍、BPM、拍号、调性、段落和时长。它不是只读取 Styles；但这些仍是自动分析，不是人工听感，也不提供可靠的逐轨乐器清单。适合先做诊断或候选对比。

首次请求可能启动分析；当前工具声明不收费，重复查询不重新启动。同一分析的描述、节拍、结构分别完成，允许部分先返回。`complete` 只代表至少一部分成功；逐项列出哪些有值、哪些缺失。`unavailable` 是终态，停止轮询。重复段落标签不能直接断言就是副歌，调性为空不补猜。

`audio_lyrics_alignment` 返回逐词时间，`success: false` 表示估算位置；对自己的 Suno 库作品读取平台已保存结果，未曾对齐的旧歌可能直接 unavailable。它不能证明没有漏唱。报告区分：平台元数据、工具音频分析、实际试听、用户反馈。未听过写“未试听”；已有模型结果可以写“模型分析完成/部分完成”，不混为全部未分析。

现有 Suno 曲库作品可走这条路线，不必先下载再传给 Gemini。本地未上传素材要另行取得向 Suno 上传的授权；分析请求不授权保存到长期素材库。

### 分轨与 MIDI

依据：[Get Stems](https://help.suno.com/en/articles/13925185)、[Advanced Stem Separation](https://help.suno.com/en/articles/12702337)。曲库或工作区歌曲菜单 Get Stems；Studio 中右键片段 Split Stems。

- Auto 可分多种常见声部；当前文档列 50 生成点数/次。Split from Mix 和 Advanced 按 stem 计价，文档有单条 10 与成对合计 20 的不同表达，执行前看实际报价。
- Advanced 在当前文档属于 Premier。选不存在的乐器不保证有用输出；先确认选择，不盲目批量分离。
- 分轨是 AI 分离结果，听审串音、相位、尾音与对齐；导出多轨不代表拿到了原始工程。
- [MIDI in Create](https://help.suno.com/en/articles/14594177) 是用 MIDI 输入生成，和 Studio 从 stem 提取 MIDI 不同；后者当前文档列 10 生成点数/次。都没有本轮已加载的专用 MCP 工具。

### Studio：更精确的剪辑与动态

依据：[Studio 2.0](https://help.suno.com/en/articles/13670529)、[Automation](https://help.suno.com/en/articles/13674305)、[Export](https://help.suno.com/en/articles/13925249)。当前官方定位为 Premier 多轨制作环境；普通 Workspace 不等于 Studio 项目。

- Library 中打开到 Studio，核对目标工程、轨道和片段。双击片段进入编辑；裁剪、循环、音高/时间变化会影响结果，保留原素材。
- 精确力度：Shift+A 显示自动化，或对支持的旋钮选择 Automate；把点放到真实小节/时间位置，试听完整过渡。要保持副歌前半，必须让该范围包络不下降。
- 效果链包括 EQ、压缩、混响等；顺序会影响结果。平台内置效果不等同支持第三方 VST/AU。[Remove FX](https://help.suno.com/en/articles/13671105) 是估算去效果的新 take，不是恢复原始干声。
- Studio Chat 可根据当前选中对象执行编辑，不是外部 MCP 设置入口。任何生成、批量修改仍按用户授权范围。
- 导出时区分 Full Song、Selected Range、Multitrack；确认范围、轨道、尾音与实际格式。当前较新导出文档主要写 WAV，旧概览提及 MP3；以本次导出界面为准。

## 五、工作区、下载与归档

### 项目对象不能混用

| 对象 | 用途 | 不是 |
| --- | --- | --- |
| 本地 PROJECT | 词稿、批准、批次、音频、交付清单 | Suno 云工作区或 Skill 安装目录 |
| Batch / song ID | 一次生成请求及每首实际结果身份 | 文件名、排序或“A版”文本 |
| Suno Workspace | 按项目收纳歌曲，每首当前归属一个工作区 | 多轨工程、可任意重名的身份标识 |
| Playlist | 多首作品的列表、参考合集 | 改变歌曲工作区归属 |
| Studio Project | 可编辑的多轨时间线与混音 | 曲库列表或下载文件夹 |
| 私人制作人素材库 | 已批准的经验与参考资料 | 自动上传的训练集 |

新项目在首次生成前，以已确认项目名查找/创建专属工作区并核实 ID；同名歧义先确认。同项目后续沿用，支持时把 `workspace_id` 放进每次生成请求，完成后核对全部新歌归属。移入工作区会从原工作区移出；不要只为了查功能移动历史歌曲。依据：[Workspaces](https://help.suno.com/en/articles/4326849)。

协作邀请、分享或公开是单独动作。[Shareable Workspaces](https://help.suno.com/en/articles/14616961) 当前介绍付费所有者最多 10 名协作者；这不表示 MCP 已提供邀请接口。[Link Only](https://help.suno.com/en/articles/2565761) 不等于保密保险箱，持有链接的人可能查看。

### 下载点数和生成点数分开

依据：[计额方式](https://help.suno.com/en/articles/13926145)、[套餐额度](https://help.suno.com/en/articles/13926209)、[格式](https://help.suno.com/en/articles/13926081)。

- 核对日官方说明：Pro 每月 20 首，Premier 每月 60 首；按账单周期重置，不把“年付”理解成一年只给一次，不把每月额度理解成自然月必定 1 号更新。
- **同一首歌首次下载计 1 个下载点数，之后重复下载或换 MP3/WAV 格式不另计。** 两首不同候选即使同名，也按两个歌曲身份计数。本地两个格式的候选 UUID 不是两个计费歌曲。
- WAV 需要相应付费权益；MP3 支持不代表免费账户无限下载。失败/中断按官方说明不计下载额度。免费试用额度因账号资格而异，不承诺人人有 7 首。
- Studio 导出当前说明不占普通月度下载额度，但分轨、MIDI 和生成动作仍可能消耗生成点数；不能为绕过下载限制擅自换通道，也不把 Studio 导出直接当商用权利证明。
- 每次实际下载先核对 `audio_download` 返回；`requires_credit` 须消费确认，不能因为这份表就自动 `confirm: true`。同曲第二种格式若异常再次要求付费，暂停核查，不机械重复批准。
- 下载 URL、文件落盘、格式检查、完整解码、试听通过和归档分别记录。签名链接不写入长期日志。不得抓播放流或录屏转音频绕过官方渠道。

### 对外使用前的权利检查

只摘要操作边界，不给法律结论。当前[付费权益说明](https://help.suno.com/en/articles/9601665)与[服务条款](https://suno.com/terms)把获准下载纳入商业使用条件；不能只凭“付费期间生成”作保证。2026-10-06 的 [Remix 说明](https://help.suno.com/en/articles/9604993)又区分自己原创改编与他人作品，并提及付费下载使部分旧作符合条件。

对外发行前逐首核对：素材和歌词权利、原作身份、生成及下载记录、获准下载渠道、当时适用条款、发行平台要求。允许 Remix 不等于允许商业使用他人作品；付费或下载也不保证版权成立。旧文章与新说明有冲突时保留证据，必要时向 Suno 支持确认，不自己扩张许可。本文不替用户公开发布、移除来源标记或处理版权争议。

## 六、执行前后最小核对

1. 先选对功能：用户要的是改输入、改音频、拿文件，还是只查询？缺功能时明确换网页，不用名字相似的 MCP 顶替。
2. 只读取本次所需材料。公开资料整理不扫描私人曲库；查单首不整库导出。
3. 执行前重新发现工具和费用；创作、上传、移动、下载消费、训练、分享分别核对授权。Max Mode 仅在用户明确要求该模式时开启。
4. 生成前通过本地门禁并记录 uncertain；结果不明先查原结果，不换通道补发。补缺步骤见恢复协议。
5. 核实真实输出身份和目标表面，注明试听/文件/权利的未完成边界。资料检索通过不等于平台功能实测通过。

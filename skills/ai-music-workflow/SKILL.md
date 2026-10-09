---
name: ai-music-workflow
description: 管理歌曲创作的本地材料、歌词版本、Suno 制作包、生成批次、人工听审和定稿归档。用户要创作歌曲、用现成歌词继续 Suno 制作、修改后再生成、恢复中断的生成或整理候选音频时使用；不用于一般音乐问答或独立录音混音。
metadata:
  version: "0.1.0-skill-preview.20261010.1"
---

# AI Music Workflow

由当前助手负责创作与判断，宿主浏览器工具负责网页，本 Skill 的标准库脚本只保存可核对的记录。无需启动旧应用、配置模型 API 或安装 Python 依赖。运行基线为 Python 3.12。自动化测试不替代真实 Suno 操作、人工听审或安装后的自动发现验收。

## 首次使用：先确认总项目目录

开始创作或写入材料前，先区分三个位置：Skill 安装目录保存工具；`ROOT` 是用户保存所有歌曲的总项目目录；`PROJECT` 是其中本次歌曲的独立目录。`ROOT` 不是 Suno 网页工作区，不能默认采用开发者的盘符、用户名、当前工作目录或 Skill 安装位置。

- 新用户未指定位置时，先问：“你希望把所有歌曲项目保存在哪个文件夹？请提供完整路径，后续每首歌会在里面单独建文件夹。”等待用户回答，不先初始化项目或把材料写进任意默认目录。只提供相对位置或说“随便放”时，给出一个适合当前机器的完整候选路径，得到确认后再写入。
- 用户已经明确给出完整路径时，回显并核对即可，不重复询问相同信息。先检查它是目录而非文件、是否存在及实际解析位置；不能把 Skill 安装目录或其子目录作为数据目录。完成下节本地启动检查后，只创建所需目录，使用唯一命名的临时文件验证写入与回读，然后仅删除该探针。没有访问权限时按宿主规则申请该路径的必要授权或请用户选别处，不静默换盘、放宽权限或覆盖已有内容。
- 在确认的 `ROOT` 下确定本次 `PROJECT`，回显完整路径；也要单独核对其解析位置与写入能力，不能用总目录可写代替子目录检查。解析后的 `PROJECT` 必须在 `ROOT` 内且在 Skill 安装目录之外；链接或目录联接改变实际落点时先说明并确认，不静默跟随。新歌曲使用文件系统允许的独立目录名；同名目录已有项目时先问是继续还是另建，不覆盖或重新初始化。探针删除后，只有不存在或为空的单曲目录才可 `init`；其他非空目录先核对用途。
- 初始化用的 `BRIEF` 先放在 `ROOT` 内、`PROJECT` 外的本次唯一临时输入目录，不要预先写进待初始化的歌曲目录。`init` 会复制内容到 `PROJECT/brief.md`；成功后回读核对，再仅删除本次临时 brief 和已为空的临时目录。失败时报告并保留临时材料，不清理已有用户文件。后续输入可以放在 `PROJECT/inputs/`。
- 本次任务记录与交接回复保留 `ROOT` 和 `PROJECT` 的实际绝对路径；不要写回分发 Skill 或擅自保存成全局偏好。继续已有任务时核对先前确认的位置和真实 `project.json`，无需每一步重问。换机器、目录不可访问或路径有歧义时重新确认，不能在别处创建空项目冒充恢复；迁移旧文件另行取得授权。

## 按阶段做轻量检查

首次使用、更新 Skill 或更换机器/解释器时，在确认目录后、创建材料前，定位本次实际 Python 与 `scripts/project.py`。用同一解释器依次执行 `python --version` 和 `python -I -S "SCRIPT" --help`（替换为实际命令与绝对路径），核对版本、退出码和帮助输出。帮助检查不传 `--project`，不写项目、不联网；不能用 `init` 或真实生成测试连通性。Python 3.12 是已验证基线，其他版本须说明尚未验证。无可用解释器、脚本缺失或命令失败时停止本地记录流程，报告最小错误和修复选项；不自动安装、改 PATH 或权限。仍可在对话中讨论创作，但不能声称已保存项目。

| 进入的环节 | 通过的证据 | 能力不足时 |
| --- | --- | --- |
| 本地材料 | 启动检查通过，且 ROOT / PROJECT 的目录核对和写入回读通过 | 停止写入，明确缺的是运行环境还是目录访问；不换盘。 |
| Suno 网页 | 当前授权工具实际读到目标页面，并可核对所需字段；已装扩展、旧截图不算连通 | 保留本地制作包和批次状态，交由用户操作；缺浏览器不阻止仅写词。 |
| 下载导入 | 当前工具确有下载接口/能力且获授权，或用户负责下载；每种格式实际落盘后才算取得 | 交接准确歌曲和所需格式，请用户提供文件；不自动改下载设置、重试生成或承诺已取得文件。 |

浏览器和下载检查只在进入相应环节时做，不为本地创作额外打开网页；按宿主文档核对能力，不伪造“浏览器已就绪”参数交给本地脚本。当前环境不变时无需每步重复启动检查；连接中断后重新核对。只回报本次已验阶段、证据与阻塞项，未执行的项目标为未检查。安装、调用和候选版边界见 [独立使用说明](README.md)。

## 确定从哪里继续

1. 完成上述目录确认后，确认用户要交付什么及已有材料。旧应用项目不会自动迁移。已有项目先运行 `status` 并读取对应材料，不能重新初始化或只靠聊天记忆。
2. 没有项目时整理最小 brief：主题、语言、人声或器乐、用途与已确认限制。已有定稿歌词直接保存，不强迫重写。用户只要改词时，完成新版本和对比即可，不建立生成批次。
3. 把用户的真实批准分别落到歌词、制作包及本次生成范围。普通“继续整理”不等于同意消费点数。不能编造批准文本、歌曲 ID、页面来源、听审结论。
4. 涉及网页时先读 [Suno 操作与恢复](references/suno-workflow.md)，使用宿主提供且已授权的工具。没有浏览器或下载能力时交付制作包及当前状态，请用户补回结果，不调用旧 CDP、私有 API 或自行登录。

## 本地材料与命令

把下面的 `SCRIPT` 替换为本 Skill `scripts/project.py` 的绝对路径，`PROJECT` 为已确认的单首歌曲目录，不是总目录 `ROOT`；`ROOT` 只是助手使用的路径称呼，不是新增 CLI 参数。其他大写参数均为真实文件路径或前一步输出的稳定 ID，不是要照抄的值。含空格的路径按当前 shell 正确引用。命令成功输出 JSON；失败返回 2 并输出短错误。用 UTF-8 文件传入长中文，不拼接进 shell 命令。

```text
python SCRIPT init --project PROJECT --title TITLE --brief-file BRIEF
python SCRIPT status --project PROJECT
python SCRIPT add-lyrics --project PROJECT --file LYRICS
python SCRIPT approve-lyrics --project PROJECT --lyrics-id LYRIC_ID --approval-file APPROVAL
python SCRIPT prepare-batch --project PROJECT --package-file PACKAGE --authorization-file AUTHORIZATION
python SCRIPT record-submission --project PROJECT --batch-id BATCH_ID --observation-file OBSERVATION
python SCRIPT import-existing-batch --project PROJECT --package-file PACKAGE --authorization-file AUTHORIZATION --observation-file OBSERVATION
python SCRIPT add-candidate --project PROJECT --batch-id BATCH_ID --song-id SONG_ID --file AUDIO --source-page PAGE_URL
python SCRIPT review --project PROJECT --decision-file DECISION
python SCRIPT archive --project PROJECT
```

`BRIEF`、`LYRICS`、`APPROVAL` 为文本文件。批准文件记录本次用户明确同意的版本与意见，不是助手自行签署。歌词只做换行规范化，保留合法重复副歌和空行。新修改用 `add-lyrics` 新建版本，再审批，不直接编辑批准文件。

其余输入为 JSON 对象，字段严格如下：

| 文件 | 字段和约束 |
| --- | --- |
| PACKAGE | `title`、`style`、`lyrics_id`、`lyrics`、`instrumental`。人声：模式 false，歌词 ID 已批准，正文与批准版逐字相同。器乐：模式 true、ID null、正文空字符串。 |
| AUTHORIZATION | `approval` 为实际批准说明；`max_generation_clicks` 为整数 1。只授权一次生成点击，不假定产出首数、点数或价格。 |
| OBSERVATION | `status` 为 `uncertain` / `submitted` / `not_submitted`；`song_ids` 为观察到的 ID 列表；`evidence` 为脱敏核对事实。只有 submitted 必须有 ID，其他状态必须空列表。 |
| DECISION | `notes` 必填；`selected_ids`、`rejected_ids` 可选，均为本地候选 UUID。`required_formats` 可选，例如 `["mp3","wav"]`，对每首选定歌曲分别检查。省略这些字段保留旧选择和格式要求，显式空列表清空。选择与拒绝不可重叠。拒绝先前选择时同时明确新选择或清空。 |

制作包例子仅说明格式；正文应从本项目批准文件读取，不把示例当作用户材料：

```json
{"title":"示例标题","style":"已确认的编曲方向","lyrics_id":"前一步返回的 ID","lyrics":"[Verse]\n已确认的歌词\n","instrumental":false}
```

项目中 `project.json` 关联 `brief.md`、`lyrics/`、`batches/`、`audio/`、`final/`。以清单 ID 和哈希为准，不按文件名或“01/02”推断身份，不扫描 final 副本来新增候选。项目材料属于用户本地数据，不能打进 Skill 分发包。

同一歌曲 ID 的 MP3 和 WAV 分别拥有候选 UUID；两者必须对应同一批次。同格式、同哈希重复导入返回原 UUID，同格式内容不同则拒绝覆盖。选定一个歌曲版本后，需把其所要求格式的 UUID 一并写入 `selected_ids`，并保存 `required_formats`。后补格式不会自动加入最终选择。

`status` 分开返回 `next_action` 和 `delivery_status`：旧批次可能仍需 `reconcile_suno`，而另一个已核对批次的本地交付已为 `archived`。`missing_formats` 给出各首选定歌曲缺少的格式。不要把“交付完成”解释成“可以再次生成”。

## 生成、听审与交付门槛

- 新批次默认 `prepared`。先把该批写成 `uncertain` 并确认成功，再点击一次生成。结果不明保持 uncertain；`status` 提示 `reconcile_suno` 时不得再次生成或另建项目绕开保护。
- `submitted` 表示确认已提交及歌曲身份，不证明音频已完成或可用。先完整核对本次所有歌曲 ID 再记录；结束批次的身份不能随意改写。若只看到部分结果，保持 uncertain 等待核对。
- `not_submitted` 必须有明确未提交证据，不能仅根据超时或页面空白判断。该批终结；再次生成需要新的用户授权批次。prepared 可以在尚未点击时取消。
- 同一状态可以追加新的核对证据；完全相同的状态、歌曲 ID 和证据重复提交不会重复记录。
- `import-existing-batch` 仅补登记已经发生、已有明确歌曲 ID 和独立核对证据的结果。沿用该次真实授权和准确制作包，OBSERVATION 必须为 submitted；它不生成歌曲、不授权再次点击，也不解除旧 prepared/uncertain 批次的限制。不要把新结果冒认为旧批次结果；证据不足时不导入。详见恢复说明。
- 先在 Suno 听审并确认选定歌曲 ID，再按操作说明整理歌曲同名工作区、下载所需格式。默认交付 MP3 和 WAV；用户明确只要一种时按其要求。下载由宿主工具或用户完成，之后分别导入。脚本只检查基本结构和哈希；完整解码、播放、人声与艺术质量需另行验收，没有听过就说没有听过。
- 用户说“选 01”时，先在本批映射到明确歌曲与候选 UUID；多个批次存在 01 时问清楚，不猜。没有选定候选不能归档。
- `archive` 只复制最终选择的音频及对应歌词、制作包、审核意见和清单。缺少任何要求的格式时拒绝归档。回读输出 manifest、文件哈希与来源对应关系，并在实际播放器打开音频；只完成离线检查时不得说已听审或已发布。旧项目没有 required_formats 时保持原单格式兼容；新任务必须记录用户的格式要求。

## 失败与安全边界

- `repair_files`：材料缺失或被修改。暂停后续操作，恢复正确文件或建立新版本，不能手改哈希装作通过。
- `schema`：项目记录缺少字段或状态损坏，停止生成并核对原始记录，不重置项目来绕过保护。`size`：清单超过 2 MiB，拒绝本次保存并保留旧记录，先交由用户决定后续整理，不静默删除历史。
- 锁错误：可能另一进程正在写入。核对该进程和清单完整性，未经确认不删除或抢占陈旧锁。
- 写入失败：Windows 短时文件占用导致的原子替换或目录更名最多尝试 5 次，总等待不超过 0.75 秒；不更改权限，不重试网络或生成操作。持续失败时旧清单与旧导出保留，可能有未登记的残留。归档错误中的 `residual_paths` 给出本次残留相对目录，包含已更名但未登记的输出。检查这些位置及本次 UUID 对应文件，报告残留但不算完成，不自动删除用户文件。
- 不读取或保存凭证、Cookie、签名下载链接、私人风格库或内部日志。不把账号登录当成 Skill 配置。脚本的模式检测只防常见误带，不是完整秘密扫描；外发材料仍需人工审核。音频元数据也可能含私人信息，本脚本不会自动清洗。
- 公开发布、全局安装、旧项目迁移、删除与旧应用退役需要另外授权。本地归档不等于公开发布，也不等于用户拥有所有素材的发布权利。

收尾说明做到哪一层、项目路径、最终文件及尚缺的用户判断；真实流程未跑时明确标为“本地候选版”。

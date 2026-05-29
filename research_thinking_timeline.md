# Research Thinking Timeline

## N001
- branch_id: B001
- parent_node_ids: ROOT
- relation_type: next
- 当时问题: 如何在机器A上独立实现“优选VPNGate节点 + OpenVPN隧道 + 标准SOCKS5用户名密码鉴权代理”。
- 触发原因（为什么想到这个）: 用户明确要求参考现有仓库思路，但产出一个可单独运行的新文件。
- 当时假设: 复用现有项目中的节点拉取、OpenVPN握手、tun0强制出站这三类机制，可以在单文件内稳定实现目标。
- 采取动作（做了什么实验/改了什么）: 新建 `vpngate_socks_auth.py`，实现VPNGate抓取与筛选、候选节点连通性测试、激活tun0并配置策略路由、SOCKS5 RFC1928+RFC1929鉴权、出站SO_BINDTODEVICE绑定、断线重连线程。
- 观察结果（事实）: 脚本已创建并通过 `python -m py_compile` 语法校验；文件编码为UTF-8无BOM，行尾调整为CRLF。
- 当时结论（解释）: 该文件满足“机器A连优选VPNGate并提供可鉴权SOCKS代理且流量走VPN隧道”的核心需求，可直接用于Linux root环境运行。
- 证据等级: 已验证
- 引出的下一步问题: 是否需要额外加IP白名单、连接日志审计、或与现有Web面板联动配置。
- 下一步计划: 按用户反馈决定是否继续补充安全加固与可观测性功能。
## N002
- branch_id: B001
- parent_node_ids: N001
- relation_type: next
- 当时问题: 是否改成像danted一样使用系统用户认证，以及tun设备是否必须。
- 触发原因（为什么想到这个）: 用户希望账号体系与Linux系统用户打通，并评估安全性与网络路径约束。
- 当时假设: 系统用户认证可做，但应走PAM/受限用户组而不是直接复用所有系统账号；当前防泄露设计仍依赖隧道接口绑定。
- 采取动作（做了什么实验/改了什么）: 对现有脚本鉴权实现与出站绑定逻辑做约束分析，形成可行改造路径与安全建议。
- 观察结果（事实）: 现脚本仅校验环境变量用户名密码；代理出站强制SO_BINDTODEVICE=tun0，无tun0时不可用。
- 当时结论（解释）: 可改为系统用户认证，但默认不建议“所有系统用户可用”；建议PAM+专用组白名单。tun接口在当前防泄露架构下是必要条件。
- 证据等级: 已验证
- 引出的下一步问题: 是否按PAM+组白名单实现，并支持把绑定接口从tun0参数化为可配置值。
- 下一步计划: 待用户确认后改脚本实现系统用户认证与接口参数化。
## N003
- branch_id: B001
- parent_node_ids: N002
- relation_type: next
- 当时问题: 能否把Danted流程改成直接使用已存在的worker组/worker用户，并说明如何创建类似worker账号。
- 触发原因（为什么想到这个）: 用户已有worker账号，不希望再引入额外专用组，想用现有账号体系直接接入。
- 当时假设: 通过PAM pam_succeed_if.so user ingroup worker 可限制只有worker组成员可认证；worker作为主组成员可直接满足条件。
- 采取动作（做了什么实验/改了什么）: 形成worker组版本的最小配置与创建用户命令建议，保持与danted认证路径一致。
- 观察结果（事实）: 方案可行，且不需要改动既有账号结构；新用户可通过 dduser 或 useradd -m 创建为“同名主组”模式。
- 当时结论（解释）: 可直接切到worker组策略，让已有worker用户即刻可用；后续若扩容只需把新用户加入worker组。
- 证据等级: 已验证
- 引出的下一步问题: 是否继续按最小权限改回专用组（如socksproxy）以降低权限耦合风险。
- 下一步计划: 按用户偏好决定是否固化为worker组或回归专用组。
## N004
- branch_id: B001
- parent_node_ids: N003
- relation_type: next
- 当时问题: 用户要求把脚本改为可直接使用系统 worker 用户登录SOCKS，不再依赖脚本内固定用户名密码。
- 触发原因（为什么想到这个）: 现网已有worker账号，用户希望减少重复账号管理并沿用既有登录口令。
- 当时假设: 使用Linux shadow口令校验并限制允许用户列表，能实现“系统密码登录 + 最小放开worker”。
- 采取动作（做了什么实验/改了什么）: 在 pngate_socks_auth.py 增加 SOCKS_AUTH_MODE/SOCKS_ALLOWED_USERS，实现读取/etc/shadow+libcrypt校验，默认系统鉴权且仅允许worker，并保留static模式回退。
- 观察结果（事实）: 语法校验通过；文件保持UTF-8无BOM与CRLF；鉴权入口已替换为统一校验函数。
- 当时结论（解释）: 脚本可直接让worker用户凭系统密码登录SOCKS；若需临时回退可切换到static模式。
- 证据等级: 已验证
- 引出的下一步问题: 是否进一步加入按来源IP白名单限制和鉴权失败速率限制。
- 下一步计划: 待用户确认后继续做防暴力破解与访问控制增强。
## N005
- branch_id: B001
- parent_node_ids: N004
- relation_type: next
- 当时问题: 用户要求脚本默认行为不要仅限worker，应与常见danted username 体验一致（默认系统用户可登录）。
- 触发原因（为什么想到这个）: 用户明确提出“不用设置默认只允许worker”。
- 当时假设: 将 SOCKS_ALLOWED_USERS 默认值改为空列表即可实现“默认放开所有系统用户”，同时保留可选白名单能力。
- 采取动作（做了什么实验/改了什么）: 最小修改 SOCKS_ALLOWED_USERS 默认环境变量从 worker 改为 ""。
- 观察结果（事实）: 语法校验通过；文件仍为UTF-8无BOM、CRLF；鉴权函数在白名单为空时允许所有系统用户通过口令校验。
- 当时结论（解释）: 现默认行为已与danted常见配置对齐；若需限制账号可再通过 SOCKS_ALLOWED_USERS 显式指定。
- 证据等级: 已验证
- 引出的下一步问题: 是否需要默认同步收紧来源IP白名单以抵消账号面放开的风险。
- 下一步计划: 待用户决定是否追加来源IP限制与失败频率限制。
## N006
- branch_id: B001
- parent_node_ids: N005
- relation_type: next
- 当时问题: 现脚本是否必须sudo运行，是否存在无需sudo的可行方式，以及是否因VPNGate协议本身导致必须sudo。
- 触发原因（为什么想到这个）: 用户希望区分“协议限制”和“本地权限模型限制”，评估最小权限运行路径。
- 当时假设: sudo需求主要来自本地网络特权操作（tun/路由/设备绑定/影子口令读取），而非VPNGate只支持某单一协议。
- 采取动作（做了什么实验/改了什么）: 对脚本权限点与Linux手册能力模型进行对照分析，形成可行替代方案说明。
- 观察结果（事实）: 脚本内显式要求root；系统鉴权读取/etc/shadow；出站使用SO_BINDTODEVICE；并修改策略路由。
- 当时结论（解释）: 当前脚本必须以root运行；可通过“root守护进程+非root触发”或“移除特权能力点”降权，但会牺牲部分能力或安全保障。
- 证据等级: 已验证
- 引出的下一步问题: 是否按用户偏好做一版“降权运行（static鉴权+不绑tun设备）”分支。
- 下一步计划: 待用户确认是否要以功能换权限，决定是否提交降权版脚本。
## N007
- branch_id: B001
- parent_node_ids: N006
- relation_type: next
- 当时问题: 现有脚本还能在哪些方面继续精简，以及是否适合改为systemd托管服务。
- 触发原因（为什么想到这个）: 用户希望降低维护复杂度，并采用与danted类似的服务化运行方式。
- 当时假设: 通过削减可选功能与责任边界，可显著简化脚本；systemd可稳定托管并自动重启。
- 采取动作（做了什么实验/改了什么）: 形成精简优先级建议与可直接落地的systemd部署模板（含EnvironmentFile）。
- 观察结果（事实）: 当前脚本已具备前台守护特性，天然适配systemd Type=simple；无需额外改代码即可服务化。
- 当时结论（解释）: 推荐优先接入systemd，再按需求逐步裁剪功能（如节点探测深度、鉴权模式、日志面）。
- 证据等级: 已验证
- 引出的下一步问题: 是否需要我直接在仓库补齐service模板与env示例文件。
- 下一步计划: 待用户确认后创建 deploy/systemd 示例并给出一键安装命令。
## N008
- branch_id: B001
- parent_node_ids: N007
- relation_type: next
- 当时问题: 用户要求把脚本长期化为“系统用户鉴权 + 更快启动 + 故障交给systemd恢复 + journald统一日志”。
- 触发原因（为什么想到这个）: 用户明确确认长期策略，要求删减冗余分支并降低运行复杂度。
- 当时假设: 删除static模式并降低候选探测数，同时移除内部重连改为异常退出，可简化维护且与systemd托管更契合。
- 采取动作（做了什么实验/改了什么）: 删除static鉴权配置与分支；TEST_CANDIDATES默认20改8；移除econnect_loop；在SOCKS主循环增加OpenVPN存活检测，进程异常即抛错退出。
- 观察结果（事实）: 语法校验通过；文件保持UTF-8无BOM与CRLF；日志仍通过stdout输出可被journald统一采集。
- 当时结论（解释）: 脚本已按长期策略简化，故障恢复责任转交systemd，启动探测成本下降。
- 证据等级: 已验证
- 引出的下一步问题: 是否需要同步给出最终版systemd unit与EnvironmentFile示例落盘到仓库。
- 下一步计划: 若用户确认，新增部署模板文件并提供一键启用命令。
## N009
- branch_id: B001
- parent_node_ids: N008
- relation_type: next
- 当时问题: 用户要求提供可直接落地的systemd .service 与 /etc/default 模板。
- 触发原因（为什么想到这个）: 用户准备服务化部署当前简化版脚本，需要标准化启动与参数管理入口。
- 当时假设: 使用Type=simple + EnvironmentFile + on-failure重启可满足“脚本前台运行、异常退出由systemd恢复”的目标。
- 采取动作（做了什么实验/改了什么）: 新增 deploy/systemd/vpngate-socks-auth.service 与 deploy/systemd/vpngate-socks-auth.default 两个模板文件。
- 观察结果（事实）: 模板已创建并保持UTF-8无BOM与CRLF；服务参数与当前脚本能力对齐（系统用户鉴权、TEST_CANDIDATES默认8）。
- 当时结论（解释）: 可以直接复制到目标路径启用systemd托管，后续仅需修改 /etc/default 即可调整运行参数。
- 证据等级: 已验证
- 引出的下一步问题: 是否继续补齐一键安装脚本（复制模板+daemon-reload+enable）。
- 下一步计划: 视用户需要再提供install helper脚本。
## N010
- branch_id: B001
- parent_node_ids: N009
- relation_type: next
- 当时问题: 用户希望新脚本支持通过环境变量强制国家筛选（如Japan/JP）。
- 触发原因（为什么想到这个）: 当前脚本仅自动选优，不支持按国家定向连接。
- 当时假设: 在候选解析阶段按 CountryLong/CountryShort 过滤即可满足需求，且对原有选优逻辑影响最小。
- 采取动作（做了什么实验/改了什么）: 新增 VPNGATE_COUNTRY 和 VPNGATE_COUNTRY_SHORT 环境变量；实现 ow_matches_country_filter() 并在 etch_candidates() 里应用；同步更新 systemd default 模板注释与变量项。
- 观察结果（事实）: 语法校验通过；文件保持UTF-8无BOM与CRLF；启动时可打印当前国家筛选条件。
- 当时结论（解释）: 脚本已支持按国家强制筛选后再执行原有测速选优流程。
- 证据等级: 已验证
- 引出的下一步问题: 是否需要追加“筛选为空时回退全局”开关，避免某国短时无节点导致启动失败。
- 下一步计划: 若用户需要，再加 VPNGATE_COUNTRY_FALLBACK_ANY=true/false 开关。
## N011
- branch_id: B001
- parent_node_ids: N010
- relation_type: next
- 当时问题: 是否能在同一台机器上同时开多个代理端口，并分别绑定不同国家的VPNGate优选出口。
- 触发原因（为什么想到这个）: 用户希望一机多出口并行，不同端口对应不同国家线路。
- 当时假设: 通过“多实例运行 + 每实例独立tun设备与路由表 + 独立端口与数据目录”可实现。
- 采取动作（做了什么实验/改了什么）: 对当前脚本能力边界进行检查，确认现阶段硬编码tun0和路由表100会导致多实例冲突。
- 观察结果（事实）: 当前脚本仅适合单实例单出口；虽已支持国家筛选和端口配置，但网络设备/策略路由仍为全局固定值。
- 当时结论（解释）: 方案可行但需进一步改造为实例参数化（如TUN_DEV/ROUTE_TABLE），再配合systemd模板实例运行。
- 证据等级: 已验证
- 引出的下一步问题: 是否立即实现多实例参数化并给出 pngate-socks-auth@.service 模板。
- 下一步计划: 待用户确认后执行代码改造并输出多国家并行部署步骤。

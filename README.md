# AimiliVPN 🌐

文档语言：中文

---

## 中文

[![Telegram](https://img.shields.io/badge/TG交流群-arestemple-2CA5E0?style=flat-square&logo=telegram&logoColor=white)](https://t.me/arestemple)
[![Forum](https://img.shields.io/badge/交流论坛-339936.xyz-orange?style=flat-square&logo=discourse&logoColor=white)](https://339936.xyz)
[![YouTube](https://img.shields.io/badge/视频教程-YouTube-red?style=flat-square&logo=youtube&logoColor=white)](https://www.youtube.com/watch?v=s-ATfXR8BpI)
[![Email](https://img.shields.io/badge/Bug反馈-yaohunse7@gmail.com-red?style=flat-square&logo=gmail&logoColor=white)](mailto:yaohunse7@gmail.com)


---

**AimiliVPN** 是一个专为 Linux VPS（如 Ubuntu）设计的智能 VPN 代理网关管理器。它能够自动采集 VPNGate 开放节点，进行多线程可用性测试与延迟过滤，利用 OpenVPN 隧道与策略路由（Policy Routing）实现出站网络，并在本地提供高性能的 HTTP/SOCKS5 代理网关服务，适合用作 Xray 的落地出站代理。

---

### 🚀 快速开始

在您的 **Ubuntu** VPS 机器上，复制并运行以下一行指令即可完成自动安装部署：

```bash
bash <(curl -Ls https://raw.githubusercontent.com/baoweise-bot/aimili-vpngate/main/install.sh)
```

---

### 🛠️ 快捷命令行 (CLI)

安装成功后，系统会在全局注册 `ml` 快捷管理指令，直接运行 `ml` 可打开图形化交互终端，也可通过以下指令执行：
* **`ml status`** 或 **`ml`**：查看当前运行状态（代理端口、活动 VPN 节点、直连延迟、网页后台登录地址等）。
* **`ml start`**：启动 AimiliVPN 服务。
* **`ml stop`**：停止 AimiliVPN 服务（并自动清理策略路由与 OpenVPN 进程）。
* **`ml restart`**：重启服务。
* **`ml logs`**：查看实时的 Systemd 服务运行日志。
* **`ml web`**：切换网页绑定地址（127.0.0.1 仅本地，或 0.0.0.0 允许公网访问）与重置安全后缀。
* **`ml port`**：修改网页管理控制台监听端口。
* **`ml password`**：生成新的 12 位安全管理密码。
* **`ml uninstall`**：完全卸载服务并清理相关环境。

#### 💡 首次安装与常见报错解决（小白必看）

##### 1. 极简系统缺少依赖（Ubuntu 18-26 / Debian 首次安装）
如果系统是全新纯净版，可能会因为缺少 `curl` 或 `ca-certificates` 导致一键安装脚本下载失败。请在安装前执行以下命令补充依赖：
```bash
sudo apt-get update && sudo apt-get install -y curl ca-certificates
```

##### 2. Debian 系统兼容运行方法
本脚本一键包默认限制在 Ubuntu 系统运行。Debian 用户如需运行，可先下载并用 `sed` 临时将系统类型限制替换为 `"ubuntu"` 后再执行安装：
```bash
curl -Ls https://raw.githubusercontent.com/baoweise-bot/aimili-vpngate/main/install.sh -o install.sh
sed -i 's/"${ID:-}"/"ubuntu"/g' install.sh
sudo bash install.sh
```

##### 3. 包管理器被占用（Apt 锁冲突报错解决）
若一键安装提示 `Could not get lock /var/lib/dpkg/lock-frontend` 等“无法获得锁”的报错，可运行以下命令解除占用并重新安装：
```bash
# 1. 停止自动更新服务并终止相关进程
sudo systemctl stop unattended-upgrades 2>/dev/null
sudo killall apt apt-get dpkg 2>/dev/null

# 2. 清理残留锁文件
sudo rm -f /var/lib/dpkg/lock* /var/lib/apt/lists/lock /var/cache/apt/archives/lock

# 3. 修复受损包并重新更新源
sudo dpkg --configure -a
sudo apt-get update
```
执行完毕后，重新运行一键安装脚本即可。

---

### ⚙️ 系统架构

```
   [ 3x-ui / Xray ] 
         │ (HTTP / SOCKS5)
         ▼
   [ 本地代理服务器 ] (Port 7928) ──(强制绑定 SO_BINDTODEVICE)──► [ tun0 虚拟网卡 ]
         │                                                            │
         │ (SSH, Web UI, etc. 依然走物理路由)                           │ (策略路由表 100)
         ▼                                                            ▼
   [ 物理网卡 eth0 ] ◄───────────────────────────────────────── [ OpenVPN 加密隧道 ]
         │                                                            │
         ▼ (真实服务器 IP 出站)                                         ▼ (VPNGate 落地节点出站)
    (国内直连流量)                                               (解锁流媒体、锁区网站)
```

---

## 功能亮点

### ✨ 主要特性

1. ⚡ **自动采集与多线程探测**
   * 周期性从 VPNGate 拉取候选节点。
   * 并发执行延迟与握手测试，维护高质量节点池。
2. 🔒 **防失联策略路由（Policy Routing）**
   * 将虚拟网卡 `tun0` 的流量导向自定义路由表（Table 100），不修改系统默认网关。
   * VPN 切换时不会影响 SSH 会话与服务器管理面板访问。
3. 🚫 **失效即阻断，防止出口泄露**
   * 本地代理服务的出站连接通过 `SO_BINDTODEVICE` 强制绑定到 `tun0`。
   * 一旦 VPN 断开，代理请求会立即返回 `502 Bad Gateway`，不会回落到 VPS 物理公网 IP。
4. 🖥️ **现代化 Web 控制台**
   * 支持深浅色切换的响应式界面（默认端口 `8787`）。
   * 可实时查看地理位置、ISP、ASN、延迟、IP 类型（住宅/机房）。
   * 支持手动切换节点、重置黑名单、代理测速、日志查询。
   * 通过随机安全路径后缀（如 `/EJsW2EeBo9lY/`）+ 密码认证进行保护。
5. 🛠️ **CLI 工具（`ml`）**
   * 提供菜单式命令行管理入口。
   * 支持快捷查看状态、启停服务、重置密码、修改绑定地址等操作。

---

## `vpngate_socks_auth.py` 使用说明（单代理与多代理）

### 中文速览

1. 单代理：使用 `vpngate-socks-auth.service` + `/etc/default/vpngate-socks-auth`。
2. 多代理：使用 `vpngate-socks-auth@.service` + `/etc/default/vpngate-socks-auth-<instance>`。
3. 每个实例必须设置不同的 `SOCKS_PORT`、`VPN_TUN_DEV`、`VPN_ROUTE_TABLE`、`VPNGATE_DATA_DIR`。
4. 国家筛选可用 `VPNGATE_COUNTRY` 或 `VPNGATE_COUNTRY_SHORT`（例如 `JP`、`US`）。
5. SOCKS5 用户名密码来自系统用户（`/etc/shadow` 校验），可用 `SOCKS_ALLOWED_USERS` 限制账号白名单。

该脚本是一个独立的 SOCKS5 网关：

1. 从 VPNGate 拉取候选节点并探测出可用的最优节点。
2. 在 Linux 上启动 OpenVPN（`tun` 设备）。
3. 提供基于 Linux 系统用户账号密码认证的本地 SOCKS5 代理。
4. 通过策略路由和 `SO_BINDTODEVICE` 强制流量走 VPN 接口，降低出口 IP 泄露风险。

### 运行要求

1. Linux 主机（推荐 Ubuntu / Debian）。
2. 依赖：`python3`、`openvpn`、`iproute2`。
3. 需要 root 权限（涉及 `/etc/shadow` 认证校验与 tun/路由操作）。

### 环境变量

通用参数：

1. `SOCKS_HOST` 默认 `0.0.0.0`
2. `SOCKS_PORT` 默认 `7928`
3. `SOCKS_ALLOWED_USERS` 默认空（允许所有系统用户）；示例：`worker` 或 `worker,alice`
4. `TEST_CANDIDATES` 默认 `8`
5. `MAX_SCAN_ROWS` 默认 `300`
6. `OPENVPN_TEST_TIMEOUT_SECONDS` 默认 `15`
7. `OPENVPN_AUTH_USER`/`OPENVPN_AUTH_PASS` 默认 `vpn`/`vpn`（VPNGate 公共默认账号）
8. `VPNGATE_COUNTRY` 可选，国家全称（例如 `Japan`）
9. `VPNGATE_COUNTRY_SHORT` 可选，国家简称（例如 `JP`）

多实例关键参数（每个实例必须唯一）：

1. `VPN_TUN_DEV` 默认 `tun0`（示例：`tun10`、`tun11`）
2. `VPN_ROUTE_TABLE` 默认 `100`（示例：`110`、`111`）
3. `VPNGATE_DATA_DIR` 每个实例应独立目录（例如 `/var/lib/vpngate-socks-auth/jp`）

### 单代理（直接运行）

```bash
cd /opt/aimilivpn
export SOCKS_HOST=0.0.0.0
export SOCKS_PORT=1080
export SOCKS_ALLOWED_USERS=worker
export VPNGATE_COUNTRY_SHORT=JP
export VPN_TUN_DEV=tun10
export VPN_ROUTE_TABLE=110
export VPNGATE_DATA_DIR=/var/lib/vpngate-socks-auth/jp
sudo -E python3 vpngate_socks_auth.py
```

### 单代理（systemd 服务）

使用固定名称的 service 文件：

1. `deploy/systemd/vpngate-socks-auth.service`
2. `deploy/systemd/vpngate-socks-auth.default`

```bash
sudo cp deploy/systemd/vpngate-socks-auth.service /etc/systemd/system/
sudo cp deploy/systemd/vpngate-socks-auth.default /etc/default/vpngate-socks-auth
sudo systemctl daemon-reload
sudo systemctl enable --now vpngate-socks-auth
sudo systemctl status vpngate-socks-auth
```

### 多代理（systemd `@` 实例）

使用模板 service 文件：

1. `deploy/systemd/vpngate-socks-auth@.service`
2. 全局默认配置：`deploy/systemd/vpngate-socks-auth.default`
3. 实例示例：
   `deploy/systemd/vpngate-socks-auth-jp.default.example`
   `deploy/systemd/vpngate-socks-auth-us.default.example`

```bash
sudo cp deploy/systemd/vpngate-socks-auth@.service /etc/systemd/system/
sudo cp deploy/systemd/vpngate-socks-auth.default /etc/default/vpngate-socks-auth
sudo cp deploy/systemd/vpngate-socks-auth-jp.default.example /etc/default/vpngate-socks-auth-jp
sudo cp deploy/systemd/vpngate-socks-auth-us.default.example /etc/default/vpngate-socks-auth-us

sudo systemctl daemon-reload
sudo systemctl enable --now vpngate-socks-auth@jp
sudo systemctl enable --now vpngate-socks-auth@us
```

每个实例建议使用不同的：

1. `SOCKS_PORT`
2. `VPNGATE_COUNTRY` 或 `VPNGATE_COUNTRY_SHORT`
3. `VPN_TUN_DEV`
4. `VPN_ROUTE_TABLE`
5. `VPNGATE_DATA_DIR`

### 检查与验证

```bash
sudo systemctl status vpngate-socks-auth@jp
sudo systemctl status vpngate-socks-auth@us
sudo journalctl -u vpngate-socks-auth@jp -f
sudo journalctl -u vpngate-socks-auth@us -f
ss -lntp | grep -E '1080|1081'
```

测试代理认证：

```bash
curl --proxy socks5h://worker:YOUR_PASSWORD@127.0.0.1:1080 https://api.ipify.org
```

### 认证说明

1. 认证来源是 Linux 系统用户（`/etc/shadow` 哈希校验）。
2. `SOCKS_ALLOWED_USERS` 为空时，任意有效系统用户都可认证使用。
3. 若只允许 `worker` 使用，请设置 `SOCKS_ALLOWED_USERS=worker`。
4. 脚本不会保存明文代理密码，只会与系统用户密码哈希进行比对。

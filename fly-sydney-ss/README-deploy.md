# Fly.io 澳大利亚悉尼节点部署指南

本项目采用极其轻量且高性能的 **Shadowsocks-rust** 镜像，通过 [Fly.io](https://fly.io/) 平台部署在悉尼数据中心（`syd`）。

---

## 部署前准备工作

### 1. 注册 Fly.io 账号
如果您还没有 Fly.io 账号，请访问 [Fly.io 官网](https://fly.io/) 并注册一个账号。
> **提示**：Fly.io 提供免费的 Hobby 额度（包含 3 台免费虚拟机和 100GB 月流量），但注册时需要绑定一张外币信用卡进行身份验证（不会产生实际扣费）。

### 2. 在本地安装 flyctl 命令行工具
打开您的 PowerShell（或 CMD），运行以下命令安装：
```powershell
pwsh -Command "iwr https://fly.io/install.ps1 -useb | iex"
```
（或者使用 `iwr https://fly.io/install.ps1 -useb | iex`）

安装完成后，关闭并重新打开终端，输入 `fly version` 以确保安装成功。

---

## 部署步骤

### 第一步：登录您的 Fly.io 账号
在终端运行：
```bash
fly auth login
```
终端会自动打开浏览器，完成登录确认。

### 第二步：修改配置文件（非常重要）
1. 打开当前文件夹内的 `fly.toml` 文件。
2. 将第三行的 `app = "sydney-ss-unique-name"` 改为您自己的**全球唯一名字**（例如 `my-sydney-ss-2026`）。
3. 打开 `Dockerfile` 文件。
4. 将 `SS_PASSWORD` 修改为您的**自定义安全密码**（避免使用默认密码）。

### 第三步：一键部署
确保终端处于当前 `fly-sydney-ss` 目录中，然后运行：
```bash
fly deploy
```
Fly.io 会自动在云端根据 `Dockerfile` 构建镜像，并将其运行在悉尼（`syd`）机房中。

---

## 客户端连接配置

部署成功后，Fly.io 会为您的应用程序分配一个外部公网 IP。
您可以运行以下命令查看应用的运行状态和外部 IP：
```bash
fly status
```

使用以下配置导入您的代理客户端（如 Shadowrocket、Clash、V2rayN 等）：
- **服务器 IP (Address)**: 您的应用分配到的外部 IP
- **端口 (Port)**: `8388`
- **加密方式 (Method)**: `aes-256-gcm`
- **密码 (Password)**: 您在 `Dockerfile` 中设置的密码

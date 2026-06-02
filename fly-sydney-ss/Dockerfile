FROM shadowsocks/shadowsocks-rust:latest

# 设置默认环境变量（用户部署后可通过 fly secrets 或 fly.toml 修改）
ENV SS_PASSWORD=my_secure_password_123456
ENV SS_METHOD=aes-256-gcm
ENV SS_PORT=8388

# 启动 shadowsocks-rust 服务器，启用 TCP 和 UDP (--uopt)
CMD ssserver -s "0.0.0.0:${SS_PORT}" -k "${SS_PASSWORD}" -m "${SS_METHOD}" --uopt

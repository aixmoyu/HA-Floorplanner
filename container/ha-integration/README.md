# floorplan_proxy — HA 自定义集成（Container 部署用）

Home Assistant Container 没有 Ingress，floorplan3d 容器只能用自己的登录。
这个集成把楼层图容器反代到 HA 自己的 HTTP 服务下（`/floorplan/`），让它出现在
**每一个 HA 的访问地址**上（局域网 IP:8123、反代域名、Tailscale …），并且与
仪表盘同源：楼层图的 SSO 会读取 HA 前端 localStorage 的令牌，已登录 HA 的
浏览器打开楼层图**无需再次登录**。

## 安装

```sh
mkdir -p config/custom_components
cp -r container/ha-integration/floorplan_proxy config/custom_components/
```

前提：floorplan3d 容器在 `127.0.0.1:8100`（见 `container/docker-compose.yml`）。
端口不同时改 `__init__.py` 里的 `TARGET`。重启 Home Assistant 后，仪表盘卡片：

```yaml
type: iframe
url: /floorplan/?card=1&theme=dark   # 相对路径！每个入口都指向自己的同源
aspect_ratio: 16:9
```

首次在某个浏览器使用且未登录过 HA 时，会走一次 HA 的标准登录；之后 30 天免登录。

# Wuziqi (Vue3 + FastAPI) - 联机骨架

目标：在 Radmin VPN 下，两台电脑直接联机进行双人对局。

实现方式（最小可跑通）：
- **房主机器**运行 FastAPI（WebSocket 房间服务）。
- **另一台机器**通过 Radmin VPN 的虚拟局域网 IP 直接连接房主的 WebSocket。

## 目录
- `backend/`：FastAPI + WebSocket（2 人房间、转发 move/chat）
- `frontend/`：Vue3 + Vite 单页（连接 WS、点格子发送 move、日志窗口）

## 启动（Windows）

## 让同学“只输 URL 就能玩”（房主机托管前端）

如果你希望另一位同学不需要下载代码、不需要运行任何程序，只要在浏览器输入一个 URL：

1) 在**房主机器**（有代码的那台）在项目根目录运行：
```powershell
.\host.ps1
```
它会：打包前端 → 拷贝到 `backend/static` → 启动后端并托管网页。

2) 同学在浏览器访问：

注意：需要在房主 Windows 防火墙里允许 `8000` 端口入站（至少对 Radmin VPN 网络）。

### 1) 启动后端（房主机器）

方式 A：脚本一键（推荐）
```powershell
cd backend
.\run_dev.ps1
```

方式 B：手动
```powershell
cd backend
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

验证：浏览器打开 `http://127.0.0.1:8000/health` 应返回 `{ "status": "ok" }`。

### 2) 启动前端（任意机器）
```powershell
cd frontend
.\run_dev.ps1
```
然后用浏览器打开终端提示的地址（默认 `http://localhost:5173`）。

## 两台电脑如何连（Radmin VPN）

- 房主：运行后端，确保 Windows 防火墙允许 `8000` 端口入站（Radmin VPN 网络下）。
- 客户端：在前端页面里把 **Host** 填成房主的 Radmin VPN IP（例如 `26.x.x.x`），Port 填 `8000`。
- 两边 **Room** 填同一个房间名（例如 `room1`），PlayerId 必须不同，Name 随意。
- 点 `Connect` 后即可互相看到 move/chat 日志。

## WebSocket 协议（当前最小版）

连接：`ws://<host>:8000/ws/<roomId>`

## 新规则：自定义获胜形状

- 开局在 **5×5** 网格内点选 **5 个格子**，作为自己的“获胜形状”。双方形状可以不同。
- 主机可在开局前选择先手：主机先手 / 客户端先手 / 随机先手；客户端可看到该选项。
	- 为避免“谁先打开网页谁成主机”的歧义：运行后端的那台机器在网页上勾选“我是主机”。
- 双方都点“Confirm Shape”后开始对局。
- 对局中轮流在棋盘落子；当某一方的棋子在棋盘上形成了自己的获胜形状（允许 90/180/270 度旋转匹配），立即获胜并结束。

## 棋子颜色与高亮

- 双方可以自由选择棋子颜色，但服务端会强制两边颜色不能相同（相同会返回 error）。
- 获胜时，前端会将触发胜利的那 5 个格子涂成赢家颜色。

## WebSocket 协议（当前最小版）

客户端 -> 服务端：
- 首包必须是：`{ "type": "hello", "playerId": "p1", "name": "Alice" }`
- 主机设置先手（仅 lobby 阶段，且仅主机可设置）：`{"type":"set_turn_mode","mode":"host"|"client"|"random"}`
- 设置并确认形状（必须 5 个点，坐标 0..4）：
	`{"type":"set_shape","points":[{"x":0,"y":0},{"x":1,"y":0},{"x":2,"y":0},{"x":3,"y":0},{"x":4,"y":0}]}`
- 落子：`{ "type": "move", "x": 7, "y": 7 }`（服务端会校验回合/占用/边界）
- 聊天：`{ "type": "chat", "text": "hi" }`

服务端 -> 客户端：
- `welcome`：包含 `phase/turn/boardSize/ready` 等房间状态
- `player_ready`：某玩家已确认形状
- `game_start`：双方都确认后进入对局，同时下发双方 shape
- `move_applied`：服务端接受了某一步落子，并广播 `turn/winner/phase`
- `player_joined` / `player_left` / `chat`

> 说明：现在已经有最小版规则与服务端胜负判定；断线重连/观战/复盘等暂未实现。

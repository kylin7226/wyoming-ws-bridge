# vLLM Wyoming WS Bridge

> Home Assistant 自定义组件，将 vLLM/vLLM-Omni 的 WebSocket 音频 API 桥接为 Wyoming 协议 TCP 服务。

---

## 一、 架构拓扑

```
HA Assist Pipeline (Wyoming Client)
        ↕ TCP 127.0.0.1:10300（本地异步事件驱动）
[wyoming_ws_bridge 自定义实例] Wyoming Server（路由中心）
        ↕ WebSocket ws://<vllm-ip>:<port>/v1/audio/*（网络）
vLLM / vLLM-Omni 0.20.0 推理服务器
```

| 特性 | 实现说明 |
|------|----------|
| **会话隔离** | 每个 TCP 连接分配独立 WS 连接，按 `session_id` 天然支持多设备并发 |
| **异步模型** | 全量 `async/await`，零阻塞事件循环 |
| **路由机制** | 仅实现 Wyoming TCP Server，由 HA `wyoming` 核心组件自动路由音频流，**不注册 `tts`/`stt` 平台** |
| **多实例** | 每个实例仅提供一种服务（TTS 或 STT），可多次添加以分别配置 |

---

## 二、 安装

### 方式 1：HACS（推荐）

1. 打开 HACS → 集成 → 右上角三点菜单 → 自定义仓库
2. 添加仓库地址：`https://github.com/kylin7226/wyoming-ws-bridge`
3. 搜索 `vLLM Wyoming WS Bridge` 并安装
4. 重启 Home Assistant

### 方式 2：手动安装

```bash
# 将此仓库的 custom_components/wyoming_ws_bridge 目录复制到
# <ha_config_dir>/custom_components/wyoming_ws_bridge
# 然后重启 Home Assistant
```

---

## 三、 配置

安装重启后，进入 **设置 → 设备与服务 → 添加集成**，搜索 `vLLM Wyoming WS Bridge`。

**每个实例仅提供一种服务**。如需同时使用 TTS 和 STT，请分别添加两个实例，绑定不同端口：

| 实例 | 服务类型 | Wyoming 端口 | 用途 |
|------|----------|-------------|------|
| 实例 1 | TTS | 10300 | HA 播报回答（文字 → 语音） |
| 实例 2 | STT | 10301 | 麦克风识别（语音 → 文字） |

### 第一步：选择服务类型

| 选项 | 说明 |
|------|------|
| **TTS（语音合成）** | 文字转语音，用于 HA 播报回答 |
| **STT（语音识别）** | 语音转文字，用于麦克风识别用户输入 |

### 第二步：配置参数

根据第一步的选择，表单会显示对应的配置项。

#### 通用设置（始终显示）

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `ws_url` | `ws://192.168.1.100:8000/v1` | vLLM WebSocket 地址 |
| `connect_timeout` | `10` | WS 握手超时（秒） |
| `wyoming_host` | `0.0.0.0` | Wyoming TCP 监听地址 |
| `wyoming_port` | `10300` | Wyoming TCP 监听端口（多实例需区分） |
| `max_concurrent` | `8` | 最大并发语音会话 |
| `audio_buffer_size` | `64` | STT 音频块队列深度 |
| `json_key_map` | `{}` | JSON 对象，覆盖默认 vLLM 键名 |
| `health_mode` | `ws_connect` | `ws_connect` / `http_health` |
| `health_interval` | `30` | 探测间隔（秒） |
| `failure_threshold` | `3` | 连续失败次数后标记离线 |

#### TTS 设置（仅 TTS 实例显示）

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `tts_model` | `qwen3-tts` | vLLM 注册的 TTS 模型名 |
| `output_sample_rate` | `16000` | 输出采样率（16000 / 22050 / 24000） |

#### STT 设置（仅 STT 实例显示）

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `stt_model` | `qwen3-asr` | vLLM 注册的 STT 模型名 |
| `enable_partial` | ✅ 启用 | 启用增量识别回传 |

---

## 四、 Wyoming 协议映射

### 服务发现

| HA 事件 | 插件响应 |
|---------|----------|
| `info.InfoRequest` | 返回标准 `info.InfoResponse`，仅包含当前实例的服务描述（TTS 实例返回 `tts`，STT 实例返回 `stt`），`protocol_version:"1.8"` |

### TTS 下行流（TTS 实例）

| 阶段 | HA 事件 | 插件动作 | vLLM 响应 | 返回 HA |
|------|---------|----------|-----------|---------|
| 发起 | `tts.StreamingTtsRequest` | 解析参数 | `{"model":"...","input":"...","voice":"...","response_format":"pcm"}` | - |
| 流式 | - | 监听 WS 二进制帧 | `Binary PCM Chunk` | `audio.AudioChunk(session_id, audio)` |
| 结束 | - | 解析终态帧 | `{"status":"done"}` | `tts.TtsStreamingStopped(session_id)` |
| 异常 | - | 捕获错误 | - | `error.Error(code, message, session_id)` |

### STT 上行流（STT 实例）

| 阶段 | HA 事件 | 插件动作 | vLLM 响应 | 返回 HA |
|------|---------|----------|-----------|---------|
| 发起 | `stt.StreamingSttRequest` | 提取采样率 | `{"model":"...","sample_rate":16000}` | - |
| 推送 | `audio.AudioChunk` | 透传二进制数据 | `Binary Frame` | - |
| 增量 | - | 解析 JSON | `{"partial":"...","is_final":false}` | `stt.TextChunk(session_id, text)` |
| 结束 | `stt.StreamingSttStop` | 停止推送 | `{"text":"...","is_final":true}` | `stt.Transcript(session_id, text)` → `SttStreamingStopped` |

---

## 五、 健康状态实体

每个实例自动创建独立的 `binary_sensor.wyoming_ws_bridge_status` 实体：

| 属性 | 说明 |
|------|------|
| `device_class` | `connectivity` |
| `entity_category` | `diagnostic` |
| `health_state` | `online` / `offline` / `degraded` / `checking` |
| `latency_ms` | 上次探测延迟（毫秒） |
| `consecutive_failures` | 当前连续失败次数 |

---

## 六、 部署验证

### 验证 Wyoming TCP 端口

```bash
# 检查端口是否监听
ss -tlnp | grep 10300

# 手动发送 InfoRequest 验证（会返回当前实例的服务类型）
echo '{"type":"info"}' | nc 127.0.0.1 10300
```

### 验证 vLLM WebSocket

```bash
# 使用 wscat 测试 WS 连接
wscat -c ws://192.168.1.100:8000/v1/audio/speech

# 发送 TTS 请求测试
echo '{"model":"qwen3-tts","input":"你好","voice":"default","response_format":"pcm","sample_rate":16000,"stream":true}' | \
  wscat -c ws://192.168.1.100:8000/v1/audio/speech
```

### 日志过滤

```bash
# 查看本组件日志（HA 容器内执行）
docker logs homeassistant 2>&1 | grep -i "wyoming_ws_bridge"

# 或在 HA 的 configuration.yaml 中开启 DEBUG 级日志：
# logger:
#   logs:
#     custom_components.wyoming_ws_bridge: debug
```

---

## 七、 常见问题

**Q: HA Assist Pipeline 无法发现本集成？**

确认 `wyoming_host` 绑定的是 `0.0.0.0`（HAOS 容器内），或绑定 HA `wyoming` 组件可达的 IP。

**Q: 需要同时使用 TTS 和 STT？**

添加两个实例，分别选择 TTS 和 STT 服务类型，并绑定不同端口（如 10300 和 10301）。HA `wyoming` 核心组件会自动发现两个端口。

**Q: 如何添加多个相同类型的实例？**

直接进入 **设置 → 设备与服务 → 添加集成** 再次添加即可，每次绑定不同端口。

**Q: 多设备并发唤醒时音频串流？**

每个 `session_id` 独立隔离。确保 `max_concurrent` 值足够覆盖设备数量。

**Q: TTS 播放卡顿或断断续续？**

检查 `output_sample_rate` 是否与 vLLM 模型实际输出采样率一致。不一致会导致 HA 播放器降速/加速。

**Q: vLLM 断开后如何恢复？**

健康探针自动标记 `offline`，重连后自动恢复 `online`，无需手动重启集成。

---

## 八、 目录结构

```
custom_components/wyoming_ws_bridge/
├── __init__.py          # 入口：后台 TCP 服务器 + 健康探针 + 生命周期管理
├── binary_sensor.py     # 连通性传感器（vLLM 健康状态）
├── config_flow.py       # ConfigFlow（两步：选类型 → 配参数）+ OptionsFlow
├── const.py             # 常量、默认值、HealthState 枚举
├── coordinator.py       # DataUpdateCoordinator：ws_connect / http_health 双模式探测
├── server.py            # Wyoming TCP 服务器 + VLLMHandler 每连接会话
├── vllm_client.py       # WebSocket 客户端：TTS 二进制流 + STT 双向流
├── manifest.json        # 集成元数据
├── hacs.json            # HACS 分发元数据
└── translations/
    ├── en.json          # 英文界面
    └── zh.json          # 中文界面
```

---

## 九、 依赖

- Home Assistant `2026.5.x`
- Python `3.14.2+`
- `websockets >= 13.0`
- `wyoming >= 1.8.0`
- vLLM / vLLM-Omni `0.20.0`

# 桌面控制台

桌面控制台用于连接用户自己的服务器，查看 Bot、NapCat 和运行日志，编辑 AI 与语音配置。

## 下载与打开

下载 [Windows x64 控制台](https://github.com/yht758/im-bridge/releases/download/v3.0.1/im-bridge-console-v3.0.1-win-x64.zip)，解压完整文件夹，运行 `IMBridgeConsole.exe`。`_internal` 文件夹是程序运行依赖，请与 exe 保持在同一目录。

控制台无需额外安装 Python。连接服务器需要 Windows OpenSSH 客户端。公开下载包不含服务器档案、QQ 账号、API Key、SSH 私钥、语音权重或已配置音色。

## 连接服务器

1. 在服务器准备 `/opt/im-bridge` 项目、Python 虚拟环境和运行配置。
2. 提前安装并登录 NapCat，启用供 Bot 连接的 OneBot WebSocket。
3. 在“云服务器”页填写服务器地址、SSH 端口、用户和自己的私钥路径，测试连接并保存。
4. 在“AI 配置”页填写自己的模型接口、模型名称、密钥、Bot QQ 与管理员账号。
5. 从“QQ Bot”页启动服务，在日志页检查实际连接状态。

控制台按 `im-bridge.service` 管理默认实例，按 `im-bridge@实例ID.service` 管理其他实例。使用其他系统服务名称或部署目录时，需要相应调整服务端配置和管理命令。

## 语音

支持三种来源：

- **本机直接合成**：使用用户自己的 GPT-SoVITS HTTP API，本地 API 需提供 `GET /health` 和 `POST /tts`，返回 WAV。填写独立解释器与 API 脚本路径后，控制台可以启动本机模型并建立 SSH 反向隧道。
- **云端底音 + 本机 RVC**：用户自行准备基础 TTS、RVC 环境、转换脚本、模型和索引，再填写相关路径与音色 ID。
- **云端语音**：使用用户自己的 OpenAI 兼容 TTS 接口。

本地模型只监听回环地址，反向隧道将云端回环端口转到本机。连接状态与实际合成分别验证；可输入文字试听。关闭控制台或本机服务不可达时，默认仅保留文字回复。

公开版不附带默认音色、训练模型或推理环境。接入自己的声音服务后才会产生语音。Bot 的文字聊天模型和本地语音模型是两套独立配置。

## 从源码运行

```powershell
python -m pip install -r requirements-desktop.txt
python launcher/desktop_qt.py
```

## 构建下载包

```powershell
python launcher/build_desktop.py --edition public
```

产物位于 `app/IMBridgeConsole-Public/`。公开构建只使用纯色图标和公共界面，不复制个人插画。两个外观配置共用连接、配置保存、进程管理与语音代码。

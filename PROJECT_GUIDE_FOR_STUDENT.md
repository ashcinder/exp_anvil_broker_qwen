可以。你现在有的是**解压后的镜像目录**，所以先把它重新打成 tar，再导入 Docker。下面除最后一步外，**全部在 Windows PowerShell** 执行，每完成一步再做下一步。

### 1. 确认 Docker 已启动、文件位置正确

先打开 Docker Desktop，等它显示正在运行，然后执行：

```powershell
docker info
Test-Path 'D:\BaiduNetdiskDownload\starshell-dev-1.0.2\index.json'
Test-Path 'D:\BaiduNetdiskDownload\exp_anvil_broker_qwen\exp_anvil_broker_qwen\experiments\exp001_relay_vs_broker\run.py'
```

`docker info` 应有 **Server** 信息，两个 `Test-Path` 都应显示 `True`。有一项不符合就先不要往下运行。

### 2. 把镜像目录打成 tar，并导入 Docker

```powershell
tar.exe -cf 'D:\BaiduNetdiskDownload\starshell-dev-1.0.2.tar' -C 'D:\BaiduNetdiskDownload\starshell-dev-1.0.2' blobs index.json manifest.json oci-layout
```

等它结束后执行：

```powershell
docker load -i 'D:\BaiduNetdiskDownload\starshell-dev-1.0.2.tar'
docker images starshell-dev
```

最后一条应显示镜像版本 `1.0.2`。我用同结构的小镜像验证过“重新打包后导入”的方法；Docker 也支持从 tar 导入镜像。[Docker 官方说明](https://docs.docker.com/reference/cli/docker/image/load/)

### 3. 创建容器并挂载你的项目

下面是**一整行命令**：

```powershell
docker run -d --name brokerlab-dev --restart unless-stopped -p 127.0.0.1:2222:22 --mount "type=bind,source=D:\BaiduNetdiskDownload\exp_anvil_broker_qwen\exp_anvil_broker_qwen,target=/workspace" starshell-dev:1.0.2
```

它会把你的 D 盘项目映射为容器中的 `/workspace`；实验结果也会写回 D 盘项目目录。[Docker 挂载说明](https://docs.docker.com/engine/storage/bind-mounts/)

检查是否成功：

```powershell
docker ps --filter name=brokerlab-dev
docker exec brokerlab-dev ls /workspace/experiments
```

第一条应显示 `Up`，第二条应列出 `exp001_...` 等实验目录。

### 4. SSH 进入容器，试跑实验一

仍在 Windows PowerShell 中执行：

```powershell
ssh -p 2222 starshell@127.0.0.1
```

密码是老师给你的 `starshell_password`。进入后，终端就变成**容器内的 Linux**，执行：

```bash
cd /workspace
python -m brokerlab doctor --config experiments/exp001_relay_vs_broker/config.yaml
cd experiments/exp001_relay_vs_broker
python run.py
```

`doctor` 通过后再运行实验。这里 Windows 使用的是 **2222 端口**，容器内部仍是 **22 端口**；以后 VS Code 的 SSH 配置也要把 `Port` 写成 `2222`。

如果任何一步报错，先停在那一步，把**命令和完整报错**发给我；尤其不要反复执行 `docker run`，否则可能遇到“容器名称已存在”。
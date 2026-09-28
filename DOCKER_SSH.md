# Docker + VS Code Remote-SSH

The supplied `starshell-dev:1.0.2` image already contains OpenSSH, Python 3.11,
Conda, and Foundry/Anvil. The project directory is mounted at `/workspace`.

## Start

From PowerShell in this directory:

```powershell
.\start-docker.ps1
```

The script loads `..\..\starshell-dev-1.0.2.tar` when necessary, starts the
container, installs BrokerLab in editable mode, runs the environment doctor,
and runs the unit tests.

## SSH / VS Code

- Host: `localhost`
- Port: `22`
- User: `starshell`
- Password: `starshell_password`
- Project path in the container: `/workspace`

Example `%USERPROFILE%\.ssh\config` entry:

```sshconfig
Host brokerlab-docker
    HostName localhost
    Port 22
    User starshell
```

In VS Code, install **Remote - SSH**, connect to `brokerlab-docker`, and open
`/workspace`.

## Useful commands

```powershell
docker compose ps
docker compose logs brokerlab
docker exec -it --user starshell brokerlab-dev bash
docker compose down
```

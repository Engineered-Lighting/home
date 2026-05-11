# One-command stack control from the Windows operator workstation.
#
# Usage:
#   .\stack.ps1            # default: 'up' — ensure stack running + healthy
#   .\stack.ps1 up
#   .\stack.ps1 down
#   .\stack.ps1 restart
#   .\stack.ps1 status
#   .\stack.ps1 logs vllm
#
# Just SSHes into the Ubuntu AI box and runs scripts/stack.sh on the
# deployed copy at /opt/home-ai-voice/. The deploy.sh keeps that copy in
# sync with C:\Claude\HomeAIVoice on this machine.

param(
    [Parameter(Position=0)] [string] $Cmd = "up",
    [Parameter(Position=1)] [string] $Arg = ""
)

$ssh_host = "<ai-box-host>"   # alias in ~/.ssh/config (Tailscale -> Ubuntu box)
$remote_script = "/opt/home-ai-voice/scripts/stack.sh"

$cmdline = "bash $remote_script $Cmd $Arg".Trim()
Write-Host ("==> ssh $ssh_host '$cmdline'") -ForegroundColor DarkGray
& ssh $ssh_host $cmdline
exit $LASTEXITCODE

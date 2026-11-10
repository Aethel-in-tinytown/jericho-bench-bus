# Bench Bus runner for the model host.
#
# The post office lives at jerichoreed.com. The models live here.
# This script is the whole bridge: it polls the post office for
# executable mail addressed to the bench voices, runs each message
# against the local Ollama one at a time (one GPU, one voice, that
# is the design), and posts the reply back. Then it idles.
#
# ================================================================
#   THE ON/OFF SWITCH
#
#   To STOP the bus:  create an empty file named  BUS.OFF  in this
#   folder. The runner checks for it every cycle and exits.
#   To START it again: delete BUS.OFF and relaunch this script.
#
#   Or set $BUS_ENABLED = $false below before launching.
#
#   When the switch is off, this script does nothing at all: no
#   polling, no model loads, no GPU use. The desktop is never
#   churned without warning.
# ================================================================
#
# Setup, once:
#   1. Put the bus token in  bus.token  next to this script (one
#      line, nothing else). Friday has the token. Never commit it.
#   2. Make sure Ollama is running locally (the bench models must
#      be pulled: anton-coder, qwen3.8:27b, qwen3-vl:8b, qwen3:4b).
#   3. Run:  powershell -ExecutionPolicy Bypass -File runner.ps1
#      Add -Once for a single poll cycle (good for a scheduled task).

param([switch]$Once)

$BUS_ENABLED = $true

$BusBase     = "https://jerichoreed.com/api/benchbus"
$OllamaBase  = "http://localhost:11434"
$PollSeconds = 20
$RunnerName  = "bench-runner"
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$OffFile     = Join-Path $ScriptDir "BUS.OFF"
$TokenFile   = Join-Path $ScriptDir "bus.token"

# The roster mirrors memory/roster.json in the repo. Keep them in
# step: name, model, context, token budget, and the voice's brief.
$Roster = [ordered]@{
  anton = @{ Model = "anton-coder"; NumCtx = 32768; MaxTokens = 700;
             Role = "coder";
             Extra = "You are the bench coder. Answer code questions directly, show the code, and flag bugs plainly when you review work." }
  hollis = @{ Model = "qwen3.8:27b"; NumCtx = 8192; MaxTokens = 700;
              Role = "generalist pen";
              Extra = "You are the bench's drafting pen. Write clean, usable prose in your own voice and keep drafts tight." }
  vera = @{ Model = "qwen3-vl:8b"; NumCtx = 8192; MaxTokens = 500;
            Role = "vision QA";
            Extra = "You are the bench's vision QA. Describe exactly what you see, name defects specifically, and do not invent details that are not in front of you." }
  pip = @{ Model = "qwen3:4b"; NumCtx = 4096; MaxTokens = 1200;
           Role = "dispatcher";
           Extra = "You are the bench dispatcher. Route requests to the right voice and extract structured facts. Be terse and exact." }
}

function Get-BusToken {
  if (-not (Test-Path $TokenFile)) {
    throw "bus.token not found next to runner.ps1. Ask Friday for the token; the runner cannot authenticate without it."
  }
  return (Get-Content $TokenFile -Raw).Trim()
}

function Bus-Headers {
  return @{ "X-Bus-Token" = Get-BusToken; "Content-Type" = "application/json" }
}

function System-Brief($Voice) {
  $bench = "Anton (coder); Hollis (generalist pen); Vera (vision qa); Pip (dispatcher)"
  $v = $Roster[$Voice]
  return "You are $($Voice.Substring(0,1).ToUpper() + $Voice.Substring(1)), the $($v.Role) on a bench of " +
         "local AI models run by Jericho Reed. The bench: $bench. " +
         "You are speaking over the bench bus, a message system where bench " +
         "members exchange addressed, logged text messages. Messages may come " +
         "from Friday (the coordinator) or from another bench member, and are " +
         "labeled with their sender. You have no tools and no shell access; " +
         "you answer with text only. $($v.Extra) Keep answers focused and reasonably brief."
}

function Invoke-Voice($Msg) {
  $v = $Roster[$Msg.to]
  $user = @{ role = "user"; content = $Msg.body }
  if ($Msg.image) { $user["images"] = @($Msg.image) }
  $payload = @{
    model = $v.Model
    messages = @(
      @{ role = "system"; content = System-Brief $Msg.to },
      $user
    )
    stream = $false
    think = $false
    options = @{ num_ctx = $v.NumCtx; num_predict = $v.MaxTokens }
  }
  $json = $payload | ConvertTo-Json -Depth 6
  $resp = Invoke-RestMethod -Uri "$OllamaBase/api/chat" -Method Post `
            -ContentType "application/json" -Body $json -TimeoutSec 1500
  return $resp.message.content
}

function Post-Reply($Msg, $Body, $Status) {
  $reply = @{ in_reply_to = $Msg.id; from = $Msg.to; body = $Body }
  if ($Status) { $reply["status"] = $Status }
  Invoke-RestMethod -Uri "$BusBase/reply" -Method Post -Headers (Bus-Headers) `
    -Body ($reply | ConvertTo-Json -Depth 4) | Out-Null
}

function Run-Cycle {
  $pending = (Invoke-RestMethod -Uri "$BusBase/pending" -Headers (Bus-Headers)).pending
  if (-not $pending -or $pending.Count -eq 0) { return $false }
  $msg = $pending[0]
  Write-Host "[bus] $($msg.kind) for $($msg.to) (thread $($msg.thread_id))"
  try {
    Invoke-RestMethod -Uri "$BusBase/claim" -Method Post -Headers (Bus-Headers) `
      -Body (@{ id = $msg.id; runner = $RunnerName } | ConvertTo-Json) | Out-Null
  } catch {
    Write-Host "[bus] claim failed (another runner took it?): $($_.Exception.Message)"
    return $true
  }
  try {
    $text = Invoke-Voice $msg
    Post-Reply $msg $text $null
    Write-Host "[bus] replied for $($msg.to)"
  } catch {
    Write-Host "[bus] voice call failed for $($msg.to): $($_.Exception.Message)"
    try { Post-Reply $msg "call failed: $($_.Exception.Message)" "error" } catch {}
  }
  return $true
}

if (-not $BUS_ENABLED) { Write-Host "[bus] disabled by switch. Exiting."; exit 0 }
if (Test-Path $OffFile) { Write-Host "[bus] BUS.OFF present. The bus is off. Exiting."; exit 0 }
Write-Host "[bus] runner up. Post office: $BusBase. To stop: create BUS.OFF in this folder."

while ($true) {
  if (Test-Path $OffFile) { Write-Host "[bus] BUS.OFF appeared. Stopping."; break }
  try {
    $worked = Run-Cycle
  } catch {
    Write-Host "[bus] poll failed: $($_.Exception.Message)"
    $worked = $false
  }
  if ($Once) { break }
  if (-not $worked) { Start-Sleep -Seconds $PollSeconds }
}
Write-Host "[bus] runner down."

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$taskName = "HeatMap-WindowsIntegration-$([Guid]::NewGuid().ToString('N'))"
$registered = $false
$failure = $null

function Assert-IntegrationCondition {
    param(
        [Parameter(Mandatory)]
        [bool]$Condition,

        [Parameter(Mandatory)]
        [string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

try {
    Assert-IntegrationCondition `
        -Condition ($taskName -ne "HWMonitorOverlay") `
        -Message "The integration task must not use the production task name."

    $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Assert-IntegrationCondition `
        -Condition ($null -eq $existingTask) `
        -Message "Unexpected task-name collision: $taskName"

    $repositoryRoot = Split-Path -Parent $PSScriptRoot
    $venvPython = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
    $python = if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $venvPython
    }
    else {
        (Get-Command python.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    }
    $buildScript = @'
import base64
import overlay

user_id = overlay._current_user_id()
if not user_id:
    raise SystemExit("could not resolve current Windows SID")
xml_bytes = overlay._build_autostart_task_xml(user_id)
print(user_id)
print(base64.b64encode(xml_bytes).decode("ascii"))
'@
    $generated = @(& $python -c $buildScript)
    Assert-IntegrationCondition `
        -Condition ($LASTEXITCODE -eq 0 -and $generated.Count -eq 2) `
        -Message "Failed to generate production HeatMap task XML."
    $currentSid = $generated[0]
    $productionXml = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($generated[1]))

    # The exact production XML is registered under a disposable external task
    # name and is never executed. No privileged XML handoff file is created.
    Register-ScheduledTask -TaskName $taskName -Xml $productionXml -ErrorAction Stop | Out-Null
    $registered = $true

    $exportedXmlText = Export-ScheduledTask -TaskName $taskName -ErrorAction Stop
    [xml]$exportedXml = $exportedXmlText
    $runLevelNode = $exportedXml.SelectSingleNode(
        "/*[local-name()='Task']/*[local-name()='Principals']/*[local-name()='Principal']/*[local-name()='RunLevel']"
    )
    # Task Scheduler omits RunLevel when it is the least-privilege default.
    $runLevel = if ($null -eq $runLevelNode) {
        "LeastPrivilege"
    }
    else {
        $runLevelNode.InnerText
    }
    $command = $exportedXml.SelectSingleNode(
        "/*[local-name()='Task']/*[local-name()='Actions']/*[local-name()='Exec']/*[local-name()='Command']"
    ).InnerText
    $arguments = $exportedXml.SelectSingleNode(
        "/*[local-name()='Task']/*[local-name()='Actions']/*[local-name()='Exec']/*[local-name()='Arguments']"
    ).InnerText
    $logonTrigger = $exportedXml.SelectSingleNode(
        "/*[local-name()='Task']/*[local-name()='Triggers']/*[local-name()='LogonTrigger']"
    )

    Assert-IntegrationCondition `
        -Condition ($runLevel -eq "LeastPrivilege") `
        -Message "Exported task run level was '$runLevel', expected LeastPrivilege."
    Assert-IntegrationCondition `
        -Condition ($exportedXmlText -notmatch "HighestAvailable") `
        -Message "Exported task unexpectedly requested elevated execution."
    Assert-IntegrationCondition `
        -Condition ($null -ne $logonTrigger) `
        -Message "Exported task did not retain its logon trigger."

    $env:HEATMAP_INTEGRATION_XML = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($exportedXmlText))
    $env:HEATMAP_INTEGRATION_TASK_NAME = $taskName
    $classifyScript = @'
import base64
import dataclasses
import json
import os
import overlay

xml_text = base64.b64decode(os.environ["HEATMAP_INTEGRATION_XML"]).decode("utf-16-le")
definition = overlay._parse_autostart_task_xml(xml_text)
user_id = overlay._current_user_id()
accepted = tuple(filter(None, (user_id, overlay._current_user_account(), overlay._interactive_user_account())))
print(json.dumps({
    "classification": overlay._classify_autostart_task(
        definition,
        user_id,
        accepted_trigger_user_ids=accepted,
        task_name=os.environ["HEATMAP_INTEGRATION_TASK_NAME"],
    ),
    "definition": dataclasses.asdict(definition),
}))
'@
    $classificationResult = ((& $python -c $classifyScript) | ConvertFrom-Json)
    $classification = $classificationResult.classification
    Remove-Item Env:HEATMAP_INTEGRATION_XML -ErrorAction SilentlyContinue
    Remove-Item Env:HEATMAP_INTEGRATION_TASK_NAME -ErrorAction SilentlyContinue
    Assert-IntegrationCondition `
        -Condition ($LASTEXITCODE -eq 0 -and $classification -eq "safe_current") `
        -Message "Exported production task classification was '$classification': $($classificationResult.definition | ConvertTo-Json -Compress)"

    [pscustomobject]@{
        TaskName = $taskName
        UserId = $currentSid
        Classification = $classification
        RunLevel = $runLevel
        RunLevelWasDefaulted = ($null -eq $runLevelNode)
        Command = $command
        Arguments = $arguments
        RegisteredProductionXmlInMemory = $true
    } | ConvertTo-Json -Compress | Write-Output
}
catch {
    $failure = $_
}
finally {
    if ($registered -or (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
    }

    $remainingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -ne $remainingTask) {
        throw "Cleanup failed: disposable task still exists: $taskName"
    }
}

if ($null -ne $failure) {
    throw $failure
}

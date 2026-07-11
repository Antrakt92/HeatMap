[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

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

function Read-CaptureRecords {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return @()
    }

    return @(Get-Content -LiteralPath $Path | Where-Object { $_ } | ForEach-Object {
        $fields = $_ -split "\|", 7
        if ($fields.Count -ne 7) {
            throw "Malformed fake PowerShell capture record."
        }

        $decode = {
            param([string]$Value)
            if (-not $Value) {
                return $null
            }
            return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Value))
        }
        $arguments = if ($fields[0]) {
            @($fields[0] -split "," | ForEach-Object { & $decode $_ })
        }
        else {
            @()
        }
        [pscustomobject]@{
            args = $arguments
            pyw = & $decode $fields[1]
            overlay = & $decode $fields[2]
            app_dir = & $decode $fields[3]
            precheck_log = & $decode $fields[4]
            process_path = & $decode $fields[5]
            displayed_text = & $decode $fields[6]
        }
    })
}

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$launcherSource = Join-Path $repositoryRoot "run_as_admin.bat"
$actualSystemRoot = $env:SystemRoot
$systemPython = (
    Get-Command python.exe -CommandType Application -ErrorAction Stop |
        Select-Object -First 1
).Source
$systemPythonDirectory = Split-Path -Parent $systemPython
$systemPythonw = Join-Path $systemPythonDirectory "pythonw.exe"
Assert-IntegrationCondition `
    -Condition (Test-Path -LiteralPath $systemPythonw -PathType Leaf) `
    -Message "The launcher fixture requires pythonw.exe adjacent to python.exe."

$fixtureRoot = Join-Path $env:TEMP (
    "HeatMap launcher O'Brien & Unicode Ж $([Guid]::NewGuid().ToString('N'))"
)
$checkout = Join-Path $fixtureRoot "checkout O'Brien & Ж"
$fixtureTemp = Join-Path $fixtureRoot "temp O'Brien & Ж"
$fixtureLocalAppData = Join-Path $fixtureRoot "local O'Brien & Ж"
$shimDirectory = Join-Path $fixtureRoot "first invalid Python & shims"
$fakePowerShellDirectory = Join-Path $fixtureRoot "fake PowerShell endpoint O'Brien & Ж"
$fakePowerShell = Join-Path $fakePowerShellDirectory "powershell.exe"
$fakePowerShellSourcePath = Join-Path $fixtureRoot "FakePowerShellCapture.cs"
$brokenPythonSourcePath = Join-Path $fixtureRoot "BrokenPython.cs"
$capturePath = Join-Path $fixtureRoot "PowerShell capture O'Brien & Ж.txt"
$preflightTracePath = Join-Path $fixtureRoot "preflight trace O'Brien & Ж.txt"

$originalEnvironment = @{
    PATH = $env:PATH
    TEMP = $env:TEMP
    TMP = $env:TMP
    LOCALAPPDATA = $env:LOCALAPPDATA
    HEATMAP_FIXTURE_CAPTURE = $env:HEATMAP_FIXTURE_CAPTURE
    HEATMAP_FIXTURE_POWERSHELL = $env:HEATMAP_FIXTURE_POWERSHELL
    HEATMAP_FIXTURE_PREFLIGHT_MODE = $env:HEATMAP_FIXTURE_PREFLIGHT_MODE
    HEATMAP_FIXTURE_PREFLIGHT_TRACE = $env:HEATMAP_FIXTURE_PREFLIGHT_TRACE
}

try {
    New-Item -ItemType Directory -Force -Path @(
        $checkout,
        $fixtureTemp,
        $fixtureLocalAppData,
        $shimDirectory,
        $fakePowerShellDirectory
    ) | Out-Null
    $launcher = Join-Path $checkout "run_as_admin.bat"
    Copy-Item -LiteralPath $launcherSource -Destination $launcher
    $launcherText = Get-Content -Raw -LiteralPath $launcher
    $productionPowerShellAssignment = 'set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"'
    $fixturePowerShellAssignment = 'set "POWERSHELL_EXE=%HEATMAP_FIXTURE_POWERSHELL%"'
    Assert-IntegrationCondition `
        -Condition ($launcherText.Contains($productionPowerShellAssignment)) `
        -Message "Launcher PowerShell assignment changed; fixture injection must be updated explicitly."
    $launcherText = $launcherText.Replace(
        $productionPowerShellAssignment,
        $fixturePowerShellAssignment
    )
    [IO.File]::WriteAllText($launcher, $launcherText, [Text.UTF8Encoding]::new($false))
    New-Item -ItemType File -Path (Join-Path $checkout "overlay.py") | Out-Null

    @'
import os
import sys

mode = os.environ.get("HEATMAP_FIXTURE_PREFLIGHT_MODE")
with open(os.environ["HEATMAP_FIXTURE_PREFLIGHT_TRACE"], "w", encoding="utf-8") as trace:
    trace.write(repr({"mode": mode, "args": sys.argv[1:]}))
if mode == "warning":
    print("WARNING: fixture degraded state")
    raise SystemExit(0)
if mode == "failure":
    print("fixture dependency failure")
    raise SystemExit(9)
raise SystemExit(0)
'@ | Set-Content -LiteralPath (Join-Path $checkout "setup.py") -Encoding utf8

    $fakePowerShellSource = @'
using System;
using System.IO;
using System.Text;

public static class FakePowerShellCapture
{
    private static string Encode(string value)
    {
        return value == null
            ? ""
            : Convert.ToBase64String(Encoding.UTF8.GetBytes(value));
    }

    public static int Main(string[] args)
    {
        string[] encodedArgs = new string[args.Length];
        for (int index = 0; index < args.Length; index++)
        {
            encodedArgs[index] = Encode(args[index]);
        }

        string precheckLog = Environment.GetEnvironmentVariable("HEATMAP_PRECHECK_LOG");
        string displayedText = precheckLog != null && File.Exists(precheckLog)
            ? File.ReadAllText(precheckLog, Encoding.UTF8)
            : null;
        string[] fields = new string[]
        {
            string.Join(",", encodedArgs),
            Encode(Environment.GetEnvironmentVariable("HEATMAP_PYW_EXE")),
            Encode(Environment.GetEnvironmentVariable("HEATMAP_OVERLAY_PATH")),
            Encode(Environment.GetEnvironmentVariable("HEATMAP_APP_DIR")),
            Encode(precheckLog),
            Encode(Environment.GetCommandLineArgs()[0]),
            Encode(displayedText),
        };
        File.AppendAllText(
            Environment.GetEnvironmentVariable("HEATMAP_FIXTURE_CAPTURE"),
            string.Join("|", fields) + Environment.NewLine,
            new UTF8Encoding(false)
        );
        return 0;
    }
}
'@
    Set-Content -LiteralPath $fakePowerShellSourcePath -Value $fakePowerShellSource -Encoding utf8
    $compiler = Join-Path $actualSystemRoot "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
    Assert-IntegrationCondition `
        -Condition (Test-Path -LiteralPath $compiler -PathType Leaf) `
        -Message "The launcher fixture requires the built-in .NET Framework C# compiler."
    & $compiler /nologo /target:exe "/out:$fakePowerShell" $fakePowerShellSourcePath
    Assert-IntegrationCondition `
        -Condition ($LASTEXITCODE -eq 0) `
        -Message "Failed to compile the fake PowerShell capture endpoint."

    @'
public static class BrokenPython
{
    public static int Main(string[] args)
    {
        return 23;
    }
}
'@ | Set-Content -LiteralPath $brokenPythonSourcePath -Encoding utf8
    $brokenPython = Join-Path $shimDirectory "python.exe"
    & $compiler /nologo /target:exe "/out:$brokenPython" $brokenPythonSourcePath
    Assert-IntegrationCondition `
        -Condition ($LASTEXITCODE -eq 0) `
        -Message "Failed to compile the broken first Python candidate."
    New-Item -ItemType File -Path (Join-Path $shimDirectory "pythonw.exe") | Out-Null

    $env:PATH = "$shimDirectory;$systemPythonDirectory;$actualSystemRoot\System32"
    $env:TEMP = $fixtureTemp
    $env:TMP = $fixtureTemp
    $env:LOCALAPPDATA = $fixtureLocalAppData
    $env:HEATMAP_FIXTURE_CAPTURE = $capturePath
    $env:HEATMAP_FIXTURE_POWERSHELL = $fakePowerShell
    $env:HEATMAP_FIXTURE_PREFLIGHT_TRACE = $preflightTracePath

    $expectedOverlay = Join-Path $checkout "overlay.py"
    $expectedAppDirectory = "$checkout\"
    $expectedLaunchCommand = 'try { $arg = [char]34 + $env:HEATMAP_OVERLAY_PATH + [char]34; Start-Process -FilePath $env:HEATMAP_PYW_EXE -ArgumentList $arg -WorkingDirectory $env:HEATMAP_APP_DIR -Verb RunAs -ErrorAction Stop | Out-Null; exit 0 } catch { Write-Error $_; exit 1 }'

    $env:HEATMAP_FIXTURE_PREFLIGHT_MODE = "warning"
    $warningOutput = @(& $launcher 2>&1)
    $warningExitCode = $LASTEXITCODE
    Assert-IntegrationCondition `
        -Condition ($warningExitCode -eq 0) `
        -Message "Warning-only launcher fixture exited with $warningExitCode."

    $warningRecords = Read-CaptureRecords -Path $capturePath
    Assert-IntegrationCondition `
        -Condition ($warningRecords.Count -eq 1) `
        -Message "Warning-only fixture expected one fake PowerShell call, got $($warningRecords.Count)."
    $warningRecord = $warningRecords[0]
    $expectedPowerShellArgs = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        $expectedLaunchCommand
    )
    Assert-IntegrationCondition `
        -Condition ((ConvertTo-Json @($warningRecord.args) -Compress) -eq (ConvertTo-Json $expectedPowerShellArgs -Compress)) `
        -Message "Launcher did not pass the exact expected PowerShell arguments."
    Assert-IntegrationCondition `
        -Condition ($warningRecord.pyw -eq $systemPythonw) `
        -Message "Launcher selected '$($warningRecord.pyw)' instead of second PATH candidate '$systemPythonw'."
    Assert-IntegrationCondition `
        -Condition ($warningRecord.overlay -eq $expectedOverlay) `
        -Message "Special-character overlay path was not preserved."
    Assert-IntegrationCondition `
        -Condition ($warningRecord.app_dir -eq $expectedAppDirectory) `
        -Message "Special-character working directory was not preserved."

    $warningFile = Join-Path $fixtureLocalAppData "HeatMap\last_preflight_warning.txt"
    $preflightTrace = Get-Content -Raw -LiteralPath $preflightTracePath
    Assert-IntegrationCondition `
        -Condition ($preflightTrace -match "'mode': 'warning'.*'args': \['--preflight'\]") `
        -Message "Fake preflight did not receive the expected warning mode and exact args: $preflightTrace"
    Assert-IntegrationCondition `
        -Condition (Test-Path -LiteralPath $warningFile -PathType Leaf) `
        -Message "Warning-only preflight did not preserve its actionable warning. Launcher output: $($warningOutput -join ' | ')"
    Assert-IntegrationCondition `
        -Condition ((Get-Content -Raw -LiteralPath $warningFile) -match "WARNING: fixture degraded state") `
        -Message "Preserved warning did not contain the preflight output."

    Remove-Item -LiteralPath $capturePath -Force
    $env:HEATMAP_FIXTURE_PREFLIGHT_MODE = "failure"
    & $launcher
    $failureExitCode = $LASTEXITCODE
    Assert-IntegrationCondition `
        -Condition ($failureExitCode -eq 1) `
        -Message "Dependency-failure launcher fixture exited with $failureExitCode instead of 1."

    $failureRecords = Read-CaptureRecords -Path $capturePath
    Assert-IntegrationCondition `
        -Condition ($failureRecords.Count -eq 1) `
        -Message "Dependency-failure fixture expected one fake error-display call, got $($failureRecords.Count)."
    Assert-IntegrationCondition `
        -Condition ($null -eq $failureRecords[0].pyw) `
        -Message "Dependency failure reached the elevation launch endpoint."
    Assert-IntegrationCondition `
        -Condition ($failureRecords[0].args[-1] -notmatch "Start-Process") `
        -Message "Dependency failure unexpectedly invoked the elevation command."
    Assert-IntegrationCondition `
        -Condition ($failureRecords[0].displayed_text -match "could not find a usable Python interpreter" -and $failureRecords[0].displayed_text -match "Candidate failed preflight" -and $failureRecords[0].displayed_text -match "fixture dependency failure") `
        -Message "Dependency failure did not display its actionable preflight diagnostics."
    $preflightArtifacts = @(
        Get-ChildItem -LiteralPath $fixtureTemp -File |
            Where-Object { $_.Name -like "HeatMap_preflight_*" }
    )
    Assert-IntegrationCondition `
        -Condition ($preflightArtifacts.Count -eq 0) `
        -Message "Dependency failure left temporary preflight artifacts: $(@($preflightArtifacts | ForEach-Object Name) -join ', ')"

    Remove-Item -LiteralPath $capturePath -Force
    $env:PATH = "$shimDirectory;$actualSystemRoot\System32"
    $noPythonOutput = @(& $launcher 2>&1)
    $noPythonExitCode = $LASTEXITCODE
    Assert-IntegrationCondition `
        -Condition ($noPythonExitCode -eq 1) `
        -Message "Missing-interpreter launcher fixture exited with $noPythonExitCode instead of 1."

    $noPythonRecords = Read-CaptureRecords -Path $capturePath
    Assert-IntegrationCondition `
        -Condition ($noPythonRecords.Count -eq 1) `
        -Message "Missing-interpreter fixture expected one fake error-display call, got $($noPythonRecords.Count)."
    $noPythonRecord = $noPythonRecords[0]
    Assert-IntegrationCondition `
        -Condition ($null -eq $noPythonRecord.pyw) `
        -Message "Missing-interpreter fixture reached the elevation launch endpoint."
    Assert-IntegrationCondition `
        -Condition ($noPythonRecord.args[-1] -notmatch "Start-Process") `
        -Message "Missing-interpreter fixture unexpectedly invoked the elevation command."
    Assert-IntegrationCondition `
        -Condition ($noPythonRecord.displayed_text -match "could not find a usable Python interpreter" -and $noPythonRecord.displayed_text -match "python -m pip install -r requirements.txt -c constraints-known-good.txt") `
        -Message "Missing-interpreter fixture did not display actionable recovery text. Output: $($noPythonOutput -join ' | ')"
    $launcherArtifacts = @(
        Get-ChildItem -LiteralPath $fixtureTemp -File |
            Where-Object { $_.Name -like "HeatMap_launcher_*" -or $_.Name -like "HeatMap_preflight_*" }
    )
    Assert-IntegrationCondition `
        -Condition ($launcherArtifacts.Count -eq 0) `
        -Message "Missing-interpreter fixture left temporary artifacts: $(@($launcherArtifacts | ForEach-Object Name) -join ', ')"

    [pscustomobject]@{
        InvalidFirstPathCandidate = (Join-Path $shimDirectory "python.exe")
        SelectedSecondPathCandidate = $systemPython
        WarningExitCode = $warningExitCode
        FailureExitCode = $failureExitCode
        NoPythonExitCode = $noPythonExitCode
        TemporaryArtifactsRemaining = $launcherArtifacts.Count
        SpecialCharacterCheckout = $checkout
        UacEndpoint = $fakePowerShell
    } | ConvertTo-Json -Compress | Write-Output
}
finally {
    foreach ($entry in $originalEnvironment.GetEnumerator()) {
        if ($null -eq $entry.Value) {
            Remove-Item -Path "Env:$($entry.Key)" -ErrorAction SilentlyContinue
        }
        else {
            Set-Item -Path "Env:$($entry.Key)" -Value $entry.Value
        }
    }

    if (Test-Path -LiteralPath $fixtureRoot) {
        Remove-Item -LiteralPath $fixtureRoot -Recurse -Force
    }
}

# The final fixture intentionally returns 1. Do not leak that expected child
# status as the integration helper's own process status after all assertions pass.
exit 0

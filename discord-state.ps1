$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$buttonType = [System.Windows.Automation.PropertyCondition]::new(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Button
)
$muteName = [System.Windows.Automation.OrCondition]::new(
    [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::NameProperty, 'Mute'
    ),
    [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::NameProperty, 'Unmute'
    )
)
$condition = [System.Windows.Automation.AndCondition]::new($buttonType, $muteName)

while ($true) {
    $muted = $null
    $reason = $null
    try {
        $process = Get-Process Discord -ErrorAction SilentlyContinue |
            Where-Object { $_.MainWindowHandle -ne 0 } |
            Select-Object -First 1
        if (!$process) {
            $reason = 'Discord window unavailable'
        }
        else {
            $root = [System.Windows.Automation.AutomationElement]::FromHandle($process.MainWindowHandle)
            $buttons = @($root.FindAll(
                [System.Windows.Automation.TreeScope]::Descendants, $condition
            ) | Where-Object {
                $_.GetCurrentPropertyValue(
                    [System.Windows.Automation.AutomationElement]::IsTogglePatternAvailableProperty
                )
            })
            if ($buttons.Count -ne 1) {
                $reason = 'Discord mute control unavailable; enable renderer accessibility and use English UI'
            }
            else {
                $toggle = $buttons[0].GetCurrentPropertyValue(
                    [System.Windows.Automation.TogglePattern]::ToggleStateProperty
                )
                if ($toggle -eq [System.Windows.Automation.ToggleState]::On) {
                    $muted = $true
                }
                elseif ($toggle -eq [System.Windows.Automation.ToggleState]::Off) {
                    $muted = $false
                }
                else {
                    $reason = 'Discord mute control is indeterminate'
                }
            }
        }
    }
    catch [System.Windows.Automation.ElementNotAvailableException] {
        $reason = 'Discord window changed while reading mute state'
    }
    catch [System.InvalidOperationException] {
        $reason = "Discord accessibility read failed: $($_.Exception.Message)"
    }
    @{ muted = $muted; error = $reason } | ConvertTo-Json -Compress
    Start-Sleep -Milliseconds 500
}

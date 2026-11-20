param([string]$Source, [string]$Target)
$ErrorActionPreference = 'Stop'
$word = $null
$doc = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    $doc = $word.Documents.Open($Source, $false, $true)
    $doc.SaveAs2($Target, 16)
} finally {
    if ($doc) { $doc.Close(0) }
    if ($word) { $word.Quit() }
}

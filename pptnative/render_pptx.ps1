param(
    [Parameter(Mandatory = $true)][string]$Pptx,
    [Parameter(Mandatory = $true)][string]$OutDir,
    [int]$Page = 0,          # 0 = 全部页面
    [int]$Width = 1280,
    [int]$Height = 720
)

$ErrorActionPreference = 'Stop'
$src = (Resolve-Path $Pptx).Path
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }
$dst = (Resolve-Path $OutDir).Path
$stem = [System.IO.Path]::GetFileNameWithoutExtension($src)

function Get-TextMetrics($shapes, [double]$scale) {
    foreach ($shape in $shapes) {
        if ($shape.Type -eq 6) { Get-TextMetrics $shape.GroupItems $scale }
        if ($shape.HasTextFrame -eq -1 -and $shape.TextFrame.HasText -eq -1) {
            $range = $shape.TextFrame2.TextRange
            [ordered]@{
                id = $shape.Id
                text = $range.Text
                frame = @($shape.Left, $shape.Top, $shape.Width, $shape.Height) | ForEach-Object { [math]::Round($_ * $scale, 2) }
                rendered_bounds = @($range.BoundLeft, $range.BoundTop, $range.BoundWidth, $range.BoundHeight) | ForEach-Object { [math]::Round($_ * $scale, 2) }
            }
        }
    }
}

$app = New-Object -ComObject PowerPoint.Application
try {
    # ReadOnly, Untitled, WithWindow
    $pres = $app.Presentations.Open($src, $true, $false, $false)
    $count = $pres.Slides.Count
    $targets = if ($Page -gt 0) { @($Page) } else { 1..$count }
    $metrics = @()

    foreach ($i in $targets) {
        if ($i -lt 1 -or $i -gt $count) { Write-Warning "跳过第 $i 页（共 $count 页）"; continue }
        $out = Join-Path $dst ("{0}_p{1}.png" -f $stem, $i)
        $pres.Slides.Item($i).Export($out, "PNG", $Width, $Height)
        $metrics += @{ page = $i; objects = @(Get-TextMetrics $pres.Slides.Item($i).Shapes ($Width / $pres.PageSetup.SlideWidth)) }
        Write-Output "导出 $out"
    }
    $metricsJson = @{ slides = $metrics; width = $Width; height = $Height } | ConvertTo-Json -Depth 8
    [System.IO.File]::WriteAllText((Join-Path $dst 'text-metrics.json'), $metricsJson, [System.Text.UTF8Encoding]::new($false))
    $pres.Close()
}
finally {
    $app.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($app) | Out-Null
}
Write-Output "完成：$count 页（导出 $(if ($Page -gt 0) { 1 } else { $count }) 张）"

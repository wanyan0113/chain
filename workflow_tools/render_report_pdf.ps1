param([Parameter(Mandatory=$true)][string]$PdfPath,[Parameter(Mandatory=$true)][string]$OutputDir)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
[Windows.Data.Pdf.PdfDocument,Windows.Data.Pdf,ContentType=WindowsRuntime] | Out-Null
[Windows.Data.Pdf.PdfPageRenderOptions,Windows.Data.Pdf,ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.Streams.InMemoryRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime] | Out-Null
$script:asTaskGeneric = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
$asTaskAction = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and -not $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.FullName -eq 'Windows.Foundation.IAsyncAction'
} | Select-Object -First 1
function Await-Operation($Operation,[Type]$ResultType) {
    $task = $script:asTaskGeneric.MakeGenericMethod(@($ResultType)).Invoke($null,@($Operation))
    return $task.GetAwaiter().GetResult()
}
$absolutePdf = (Resolve-Path -LiteralPath $PdfPath).Path
$storageFile = Await-Operation ([Windows.Storage.StorageFile]::GetFileFromPathAsync($absolutePdf)) ([Windows.Storage.StorageFile])
$document = Await-Operation ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($storageFile)) ([Windows.Data.Pdf.PdfDocument])
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
for ($pageNumber = 0; $pageNumber -lt $document.PageCount; $pageNumber++) {
    $page = $document.GetPage($pageNumber)
    $stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
    $options = New-Object Windows.Data.Pdf.PdfPageRenderOptions
    $options.DestinationWidth = 1600
    $options.DestinationHeight = 900
    $renderTask = $asTaskAction.Invoke($null,@($page.RenderToStreamAsync($stream,$options)))
    $renderTask.GetAwaiter().GetResult() | Out-Null
    $stream.Seek(0)
    $netStream = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($stream)
    $imagePath = Join-Path $OutputDir ('page-{0:D2}.png' -f ($pageNumber+1))
    $fileStream = [System.IO.File]::Create($imagePath)
    $netStream.CopyTo($fileStream)
    $fileStream.Dispose()
    $netStream.Dispose()
    $stream.Dispose()
    $page.Dispose()
}
Write-Output ('Rendered pages: ' + $document.PageCount)

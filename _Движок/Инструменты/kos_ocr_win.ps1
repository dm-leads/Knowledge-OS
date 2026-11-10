# Распознать текст на картинках папки встроенным OCR Windows (Windows.Media.Ocr) — для kos_ocr_pdf.py.
# Запускать в Windows PowerShell 5.1 (powershell.exe): в PowerShell 7 типов WinRT нет.
# На каждый <имя>.png пишет рядом <имя>.txt (UTF-8 без BOM) и печатает строку «<имя>.png<TAB><символов>».
param([Parameter(Mandatory = $true)][string]$InDir, [string]$Lang = "ru")
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) {
  $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result
}
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new($Lang))
if ($null -eq $engine) { Write-Error "OCR Windows: язык '$Lang' не установлен"; exit 2 }
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Get-ChildItem -LiteralPath $InDir -Filter *.png | Sort-Object Name | ForEach-Object {
  $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($_.FullName)) ([Windows.Storage.StorageFile])
  $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
  $bmp = $null
  try {
    $dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bmp = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $max = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension
    if ($bmp.PixelWidth -gt $max -or $bmp.PixelHeight -gt $max) {
      throw "картинка $($_.Name) больше предела OCR ($max пикс.)"
    }
    $res = Await ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
    $txt = ($res.Lines | ForEach-Object { $_.Text }) -join "`n"
  } finally {
    if ($null -ne $bmp) { $bmp.Dispose() }
    $stream.Dispose()
  }
  $outPath = [System.IO.Path]::ChangeExtension($_.FullName, '.txt')
  [System.IO.File]::WriteAllText($outPath, $txt, [System.Text.UTF8Encoding]::new($false))
  Write-Output ("{0}`t{1}" -f $_.Name, $txt.Length)
}

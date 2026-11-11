Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = "Select Image Folder - AI Image Studio"
$dialog.ShowNewFolderButton = $true
# To make it topmost on Windows:
$form = New-Object System.Windows.Forms.Form
$form.TopMost = $true
$form.StartPosition = "Manual"
$form.Location = New-Object System.Drawing.Point(-2000, -2000)
$form.Size = New-Object System.Drawing.Size(1, 1)
$form.Show()
$form.Focus()
$form.BringToFront()

# Let's inspect form and close it
Write-Output "Form created with TopMost = $($form.TopMost)"
$form.Close()

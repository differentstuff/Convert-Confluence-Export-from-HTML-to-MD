# Configuration
. .\config.ps1
$PYTHONSCRIPT = "converter.py" # Do not change

# Function to show header
function Show-StepHeader {
    param (
        [string]$stepNumber,
        [string]$description
    )
    Write-Host
    Write-Host "=== Step $($stepNumber): $description ===" -ForegroundColor Cyan
    Write-Host
}

# Step 1: Create Output folders (if they don't exist)
Show-StepHeader "1" "Preparing Environment"
if (-not (Test-Path $($Config.OutputFolder))) {
    New-Item -ItemType Directory -Path $($Config.OutputFolder) -Force | Out-Null
    Write-Host "Created directory: $($Config.OutputFolder)" -ForegroundColor Green
}
else{
    Write-Host "Directory already exists: $($Config.OutputFolder)" -ForegroundColor Green
    Write-Host

    $hasContent = Get-ChildItem $($Config.OutputFolder) | Where-Object {$_.Name -ne "logs"}
    if($hasContent.Length -gt 0){
        Write-Host "Directory not empty!" -ForegroundColor Red
        Write-Host "> Are you sure you want to continue?" -ForegroundColor Red
        Write-Host "(0) Abort [default]" -ForegroundColor Gray
        Write-Host "(1) Ignore and continue" -ForegroundColor Gray
        Write-Host "(2) Clean folder and continue [Deletes all content! Use with caution]" -ForegroundColor Gray
        $userResponse = Read-Host -Prompt "Please choose an Option"

        switch($userResponse){
            # continue
            1{
                Write-Host
                Write-Host "User choice: " -ForegroundColor Gray -NoNewline
                Write-Host "Continue" -ForegroundColor Magenta
                Write-Host
                Write-Host "Continue Script without removing content?" -ForegroundColor Red
                Write-Host "Overwrite existing content [Y/1]" -ForegroundColor Gray
                Write-Host "Abort conversion script [N/2]" -ForegroundColor Gray
                $userConfirmation1 = Read-Host -Prompt "[Y/N]"
                # continue
                if(($userConfirmation1.ToLower() -eq "y") -or ($userConfirmation1.ToLower() -eq "1")){
                    Write-Host "User choice: " -ForegroundColor Gray -NoNewline
                    Write-Host "Continue" -ForegroundColor Magenta
                    continue
                }
                # abort
                else{
                    Write-Host
                    Write-Host "User choice: " -ForegroundColor Gray -NoNewline
                    Write-Host "Abort" -ForegroundColor Magenta
                    Write-Host
                    Write-Host "`nPress Enter to quit..." -ForegroundColor Yellow
                    $null = Read-Host
                    exit 0
                }
            }
            # delete
            2{
                Write-Host
                Write-Host "User choice: " -ForegroundColor Gray -NoNewline
                Write-Host "Clean" -ForegroundColor Magenta
                Write-Host

                Write-Host "Are you sure to DELETE all content in folder: $($Config.OutputFolder)?" -ForegroundColor Red
                $userConfirmation2 = Read-Host -Prompt "[Y/1] or [N/2]"
                # delete
                if(($userConfirmation2.ToLower() -eq "y") -or ($userConfirmation2.ToLower() -eq "1")){
                    # Remove the directory and its contents
                    Remove-Item -Path $($Config.OutputFolder) -Recurse -Force -ErrorAction SilentlyContinue | Out-Null

                    # Verify the directory is gone
                    if (Test-Path $($Config.OutputFolder)) {
                        # If it still exists, try more aggressive removal
                        Get-ChildItem -Path $($Config.OutputFolder) -Recurse -Force | Remove-Item -Force -Recurse
                        Remove-Item -Path $($Config.OutputFolder) -Force
                    }

                    # Create a new empty directory
                    New-Item -ItemType Directory -Path $($Config.OutputFolder) -Force | Out-Null

                    # Final verification
                    if (Test-Path $($Config.OutputFolder)) {
                        $fileCount = (Get-ChildItem -Path $($Config.OutputFolder) -Recurse -Force).Count
                        if ($fileCount -eq 0) {
                            Write-Verbose "Directory is empty and ready for use."
                        } else {
                            Write-Warning "Directory still contains $fileCount items! If Errors occur, try to remove the folder manually before a new run."
                        }
                    }

                    Write-Host
                    Write-Host "Cleanded directory: $($Config.OutputFolder)" -ForegroundColor Green
                }
                # continue or abort
                else{
                    Write-Host
                    Write-Host "> Continue Script without removing content?" -ForegroundColor Yellow
                    Write-Host "Overwrite existing content [Y/1]" -ForegroundColor Gray
                    Write-Host "Abort conversion script [N/2]" -ForegroundColor Gray
                    $userConfirmation22 = Read-Host -Prompt "[Y/N]"
                    # continue
                    if(($userConfirmation22.ToLower() -eq "y") -or ($userConfirmation22.ToLower() -eq "1")){
                        Write-Host
                        Write-Host "User choice: " -ForegroundColor Gray -NoNewline
                        Write-Host "Continue" -ForegroundColor Magenta
                        continue
                    }
                    # abort
                    else{
                        Write-Host
                        Write-Host "User choice: " -ForegroundColor Gray -NoNewline
                        Write-Host "Abort" -ForegroundColor Magenta
                        Write-Host "`nPress Enter to quit..." -ForegroundColor Yellow
                        $null = Read-Host
                        exit 0
                    }
                }
            }
            # exit
            default {
                Write-Host "User choice: " -ForegroundColor Gray -NoNewline
                Write-Host "Abort" -ForegroundColor Magenta
                Write-Host "`nPress Enter to quit..." -ForegroundColor Yellow
                $null = Read-Host
                exit 0
            }
        }
    }
}

# Step 2: Activate virtual environment
try{
    Show-StepHeader "2" "Activating virtual environment"
    .\venv\Scripts\Activate.ps1
    Write-Host "Virtual Environment activated successfully" -ForegroundColor Green
}
catch{
    Write-Host "Virtual Environment not found" -ForegroundColor Red
    Write-Host "> Please run install.bat first" -ForegroundColor Red
    Write-Host "`nPress Enter to quit..." -ForegroundColor Yellow
    $null = Read-Host
    exit 1
}

# Step 3: Activate virtual environment and run Python conversion
try {    
    Show-StepHeader "3" "Converting HTML to Markdown"
    Write-Host "Starting conversion process..." -ForegroundColor Yellow
    # Build the command arguments
    $pythonArgs = @(
        $PYTHONSCRIPT,
        "--input", $Config.InputFolder,
        "--output", $Config.OutputFolder,
        "--base-url", $Config.ConfluenceBaseUrl
    )
    
    # Add optional flags
    if ($Config.RenameAllFiles) {
        $pythonArgs += "--rename-all"
    }
    
    if ($Config.LogLinkMapping) {
        $pythonArgs += "--debug-link-mapping"
    }
    
    if ($Config.UseUnderscoreInFilenames) {
        $pythonArgs += "--use-underscore"
    }
    
    # Execute the Python script with the arguments
    python $pythonArgs
    
    if ($LASTEXITCODE -ne 0) {
        throw "Python script failed with exit code $LASTEXITCODE"
    }
}
catch {
    Write-Host "`nError: $_" -ForegroundColor Red
    Write-Host "`nPress Enter to quit..." -ForegroundColor Yellow
    $null = Read-Host
}
# Step 4: Complete and Cleanup
finally {
    Show-StepHeader "4" "Completion"
    # Deactivate virtual environment
    Write-Host "Deactivating virtual environment..." -ForegroundColor Yellow
    deactivate
    
    Write-Host "Conversion process completed successfully!" -ForegroundColor Green
    Write-Host "Output location: $($Config.OutputFolder)" -ForegroundColor Blue

    Write-Host "`nPress Enter to quit..." -ForegroundColor Yellow
    $null = Read-Host
    # Ensure virtual environment is deactivated
    if (Get-Command deactivate -ErrorAction SilentlyContinue) {
        deactivate
    }
}
# Configuration
$INPUTFOLDER = "in"                                           # Input Folder to use
$OUTPUTFOLDER = "out"                                         # Output Folder to use
$CONFLUENCE_BASE_URL = "https://confluence.myCompany.com"     # Your Companies Confluence URL

$RENAME_ALL = $true                                           # Must not be changed // Optional // Default = $true
$PYTHONSCRIPT = "converter.py"                                # Do not change

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
if (-not (Test-Path $OUTPUTFOLDER)) {
    New-Item -ItemType Directory -Path $OUTPUTFOLDER -Force | Out-Null
    Write-Host "Created directory: $OUTPUTFOLDER" -ForegroundColor Green
}

# Step 2: Activate virtual environment
try{
    Show-StepHeader "2" "Activating virtual environment"
    .\venv\Scripts\Activate.ps1
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
    if($RENAME_ALL){
        python $PYTHONSCRIPT --input $INPUTFOLDER --output $OUTPUTFOLDER --base-url $CONFLUENCE_BASE_URL --rename-all
    }
    else{
        python $PYTHONSCRIPT --input $INPUTFOLDER --output $OUTPUTFOLDER --base-url $CONFLUENCE_BASE_URL
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Python script failed with exit code $LASTEXITCODE"
    }

}
catch {
    Write-Host "`nError: $_" -ForegroundColor Red
    Write-Host "`nPress Enter to quit..." -ForegroundColor Yellow
    $null = Read-Host
}
# Step 3: Complete and Cleanup
finally {
    Show-StepHeader "3" "Completion"
    # Deactivate virtual environment
    Write-Host "Deactivating virtual environment..." -ForegroundColor Yellow
    deactivate
    
    Write-Host "Conversion process completed successfully!" -ForegroundColor Green
    Write-Host "Output location: $OUTPUTFOLDER" -ForegroundColor Blue

    Write-Host "`nPress Enter to quit..." -ForegroundColor Yellow
    $null = Read-Host
    # Ensure virtual environment is deactivated
    if (Get-Command deactivate -ErrorAction SilentlyContinue) {
        deactivate
    }
}
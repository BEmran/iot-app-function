#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------------
# Deploy Azure Function App: fa-iot-ta
# Classic Python Azure Functions folder-based deployment
# ------------------------------------------------------------

RG="rg-iot-ta-prod"
APP="fa-iot-ta"
ZIP_NAME="fa-iot-ta-deploy.zip"

echo "============================================================"
echo "Deploying Function App"
echo "Resource Group: $RG"
echo "Function App:   $APP"
echo "Working Dir:    $(pwd)"
echo "============================================================"

# 1. Basic validation
if [ ! -f "host.json" ]; then
  echo "ERROR: host.json not found. Run this script from the root of the Function App repo."
  exit 1
fi

if [ ! -f "requirements.txt" ]; then
  echo "ERROR: requirements.txt not found."
  exit 1
fi

if [ ! -d "ValidateEmployeeRegistration" ]; then
  echo "ERROR: ValidateEmployeeRegistration folder not found."
  exit 1
fi

if [ ! -f "ValidateEmployeeRegistration/function.json" ]; then
  echo "ERROR: ValidateEmployeeRegistration/function.json not found."
  exit 1
fi

if [ ! -f "ValidateEmployeeRegistration/__init__.py" ]; then
  echo "ERROR: ValidateEmployeeRegistration/__init__.py not found."
  exit 1
fi

# 2. Clean old package files
echo "Cleaning old deployment artifacts..."
rm -rf .python_packages
rm -f "../${ZIP_NAME}"

# 3. Install dependencies locally into Azure Functions expected path
echo "Installing Python packages..."
python3 -m pip install \
  --target=".python_packages/lib/site-packages" \
  -r requirements.txt

# 4. Create zip package
echo "Creating deployment zip..."
zip -r "../${ZIP_NAME}" . \
  -x "*.git*" \
  -x "__pycache__/*" \
  -x "*.pyc" \
  -x ".venv/*" \
  -x "venv/*" \
  -x "*.zip"

# 5. Deploy zip to Azure Function App
echo "Deploying zip to Azure..."
az functionapp deployment source config-zip \
  --resource-group "$RG" \
  --name "$APP" \
  --src "../${ZIP_NAME}"

# 6. Restart Function App
echo "Restarting Function App..."
az functionapp restart \
  --resource-group "$RG" \
  --name "$APP"

# 7. List available functions
echo "Listing deployed functions..."
az functionapp function list \
  --resource-group "$RG" \
  --name "$APP" \
  --query "[].{name:name, url:invokeUrlTemplate}" \
  -o table

echo "============================================================"
echo "Deployment completed."
echo "Test URLs:"
echo "Ping:"
echo "https://fa-iot-ta-djc6hqdda4dgeegm.qatarcentral-01.azurewebsites.net/api/ping"
echo
echo "Employee validation:"
echo "https://fa-iot-ta-djc6hqdda4dgeegm.qatarcentral-01.azurewebsites.net/api/validate-employee-registration"
echo "============================================================"
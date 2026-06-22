#!/bin/bash
# Fix EAS project ID
# Usage: EXPO_TOKEN=<token> bash fix-eas-project.sh

cd /home/asus/kuafu/kuafu-app

# First try to init a real project
echo "Creating EAS project..."
npx eas project:init --non-interactive

# If that fails, create one manually via API
if [ $? -ne 0 ]; then
  echo "project:init failed, trying to get existing projects..."
  npx eas project:list --non-interactive
fi
